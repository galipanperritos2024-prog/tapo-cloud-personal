"""Per-camera orchestration.

A :class:`CameraWorker` owns *everything* related to one camera:

* registering its streams in the MediaMTX relay;
* health checks (online/offline, bitrate, codec/resolution/FPS detection);
* deciding which jobs must run for the configured recording mode/schedule
  (continuous recorder, pre-event ring buffer, motion detector, ONVIF events);
* turning detections into event "episodes" (one DB event per activity, not
  one per frame), with thumbnails and links to the clip that captured them;
* manual recordings, snapshots and live previews.

Workers share nothing but the database, so one camera failing (bad
password, Wi-Fi drop, FFmpeg crash) cannot affect the others.
"""

from __future__ import annotations

import hashlib
import json
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tapocloud.camera_service.detectors import LocalMotionDetector, OnvifEventListener
from tapocloud.camera_service.prebuffer import EventClipRecorder
from tapocloud.camera_service.recorder import ContinuousRecorder, ManualRecording, StreamParams
from tapocloud.camera_service.schedule import is_active
from tapocloud.config import get_settings
from tapocloud.core import events as events_mod
from tapocloud.core import ffmpeg, settings_store
from tapocloud.core.db import session_scope
from tapocloud.core.mediamtx import MediaMTXClient, MediaMTXError
from tapocloud.core.models import Camera, CameraStatus, EventType, MotionSource, RecordingMode
from tapocloud.core.rtsp import build_camera_url, relay_path_name, relay_url

log = logging.getLogger(__name__)

SMART_TYPES = {"person", "vehicle", "animal"}
EPISODE_TIMEOUT = 60.0  # close an episode if no update for this long

_snap_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="snap")


@dataclass(frozen=True)
class CameraConfig:
    """Immutable snapshot of a camera row + decrypted credentials."""

    id: int
    name: str
    slug: str
    ip: str
    rtsp_port: int
    onvif_port: int
    main_path: str
    sub_path: str | None
    username: str | None
    password: str | None = field(repr=False)
    enabled: bool
    onvif_enabled: bool
    mode: str
    auto_record: bool
    segment_minutes: int
    pre_s: int
    post_s: int
    motion_source: str
    sensitivity: int
    record_audio: bool
    schedule: tuple

    @classmethod
    def from_row(cls, cam: Camera) -> CameraConfig:
        from tapocloud.core.crypto import SecretError, decrypt

        user = pw = None
        if cam.credentials:
            user = cam.credentials.username
            try:
                pw = decrypt(cam.credentials.password_encrypted)
            except SecretError:
                log.error("Cannot decrypt credentials for camera %s", cam.slug)
        return cls(
            id=cam.id, name=cam.name, slug=cam.slug, ip=cam.ip, rtsp_port=cam.rtsp_port, onvif_port=cam.onvif_port,
            main_path=cam.main_stream_path, sub_path=cam.sub_stream_path, username=user, password=pw,
            enabled=cam.enabled, onvif_enabled=cam.onvif_enabled, mode=cam.recording_mode, auto_record=cam.auto_record,
            segment_minutes=cam.segment_minutes, pre_s=cam.pre_event_seconds, post_s=cam.post_event_seconds,
            motion_source=cam.motion_source, sensitivity=cam.motion_sensitivity, record_audio=cam.record_audio,
            schedule=tuple(json.dumps(w, sort_keys=True) for w in (cam.schedule or [])),
        )  # fmt: skip

    def fingerprint(self, *fields_: str) -> str:
        d = asdict(self)
        blob = json.dumps({k: d[k] for k in fields_} if fields_ else d, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def camera_url(self, quality: str = "main") -> str | None:
        path = self.main_path if quality == "main" else self.sub_path
        if not path:
            return None
        return build_camera_url(self.ip, self.rtsp_port, path, self.username, self.password)

    @property
    def schedule_list(self) -> list[dict[str, Any]]:
        return [json.loads(w) for w in self.schedule]


@dataclass
class Episode:
    event_id: int
    type: str
    started: float
    last: float


class CameraWorker(threading.Thread):
    TICK = 5.0
    OFFLINE_AFTER = 20.0  # seconds without a ready stream before declaring offline

    def __init__(self, config: CameraConfig, mediamtx: MediaMTXClient) -> None:
        super().__init__(name=f"worker-cam{config.id}", daemon=True)
        self.cfg = config
        self.mtx = mediamtx
        self._stop_evt = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()

        self.status = CameraStatus.UNKNOWN
        self.status_detail: str | None = None
        self.stream_info: dict[str, Any] = {}
        self.sub_info: dict[str, Any] = {}
        self.using_relay = True
        self._relay_fp: str | None = None
        self._last_ready = 0.0
        self._last_diag = time.monotonic() - 45  # first diagnosis ~15 s after start
        self._bytes_prev: tuple[float, int] | None = None
        self._bitrate_kbps: int | None = None

        self.continuous: ContinuousRecorder | None = None
        self.clip_recorder: EventClipRecorder | None = None
        self.motion: LocalMotionDetector | None = None
        self.onvif_events: OnvifEventListener | None = None
        self.manual: ManualRecording | None = None
        self._jobs_fp: str | None = None

        self.episodes: dict[str, Episode] = {}
        self._preview_cache: tuple[float, bytes] | None = None

    # ------------------------------------------------------------------ API
    def update_config(self, cfg: CameraConfig) -> None:
        with self._lock:
            self.cfg = cfg
        self._wake.set()

    def stop(self) -> None:
        self._stop_evt.set()
        self._wake.set()

    def summary(self) -> dict[str, Any]:
        def job(j) -> dict[str, Any] | None:
            if j is None:
                return None
            return {
                "running": j.is_running(),
                "restarts": j.restarts,
                "last_error": j.last_error,
                "pid": j.proc.pid if j.proc else None,
            }

        return {
            "camera_id": self.cfg.id,
            "name": self.cfg.name,
            "status": self.status.value,
            "status_detail": self.status_detail,
            "using_relay": self.using_relay,
            "stream": self.stream_info,
            "jobs": {
                "continuous": job(self.continuous),
                "prebuffer": job(self.clip_recorder),
                "motion": job(self.motion),
                "onvif_events": None
                if self.onvif_events is None
                else {
                    "running": self.onvif_events.is_alive(),
                    "connected": self.onvif_events.connected,
                    "last_error": self.onvif_events.last_error,
                },
            },
            "manual_recording": None
            if not (self.manual and self.manual.active)
            else {"recording_id": self.manual.recording_id, "elapsed_seconds": round(self.manual.elapsed_seconds)},
            "active_episodes": [e.type for e in self.episodes.values()],
        }

    # --------------------------------------------------------------- thread
    def run(self) -> None:
        log.info("Worker started for camera %s (%s)", self.cfg.name, self.cfg.ip)
        while not self._stop_evt.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("Worker tick failed (camera %s)", self.cfg.slug)
            self._wake.wait(self.TICK)
            self._wake.clear()
        self._shutdown()

    def _tick(self) -> None:
        cfg = self.cfg
        if not cfg.enabled:
            self._stop_jobs()
            self._unregister_relay()
            self._set_status(CameraStatus.DISABLED, "Cámara desactivada")
            return
        self._ensure_relay()
        self._health()
        self._reconcile_jobs()
        self._sweep_episodes()

    # -------------------------------------------------------------- relay
    def _ensure_relay(self) -> None:
        cfg = self.cfg
        fp = cfg.fingerprint("ip", "rtsp_port", "main_path", "sub_path", "username", "password", "mode", "motion_source")
        try:
            if fp == self._relay_fp and relay_path_name(cfg.id) in self.mtx.configured_paths():
                self.using_relay = True
                return
            main = cfg.camera_url("main")
            assert main
            self.mtx.ensure_path(relay_path_name(cfg.id, "main"), main, on_demand=False)
            sub = cfg.camera_url("sub")
            if sub:
                # Sub-stream only pulled when someone watches it, unless local motion needs it 24/7.
                self.mtx.ensure_path(relay_path_name(cfg.id, "sub"), sub, on_demand=True)
            self._relay_fp = fp
            if not self.using_relay:
                log.info("Relay available again for camera %s", cfg.slug)
            self.using_relay = True
        except MediaMTXError as exc:
            if self.using_relay:
                log.warning("Relay unavailable (%s); camera %s will be read directly", exc, cfg.slug)
            self.using_relay = False
            self._relay_fp = None

    def _unregister_relay(self) -> None:
        if self._relay_fp is None:
            return
        for q in ("main", "sub"):
            try:
                self.mtx.delete_path(relay_path_name(self.cfg.id, q))
            except MediaMTXError:
                pass
        self._relay_fp = None

    def source_url(self, quality: str = "main") -> str | None:
        """URL internal consumers should read: relay if available, else the camera."""
        if quality == "sub" and not self.cfg.sub_path:
            quality = "main"
        if self.using_relay:
            return relay_url(self.cfg.id, quality)
        return self.cfg.camera_url(quality)

    # -------------------------------------------------------------- health
    def _health(self) -> None:
        now = time.monotonic()
        ready = False
        if self.using_relay:
            try:
                st = self.mtx.path_state(relay_path_name(self.cfg.id, "main")) or {}
            except MediaMTXError:
                st = {}
            ready = bool(st.get("ready"))
            rx = st.get("bytesReceived")
            if ready and isinstance(rx, int):
                if self._bytes_prev and rx >= self._bytes_prev[1]:
                    dt = now - self._bytes_prev[0]
                    if dt > 0:
                        self._bitrate_kbps = int((rx - self._bytes_prev[1]) * 8 / 1000 / dt)
                self._bytes_prev = (now, rx)
        else:
            ready = _tcp_alive(self.cfg.ip, self.cfg.rtsp_port)

        if ready:
            self._last_ready = now
            if not self.stream_info.get("codec"):
                self._probe()
            if self.status != CameraStatus.ONLINE and self.stream_info.get("codec"):
                self._set_status(CameraStatus.ONLINE, None if self.using_relay else "Sin relé MediaMTX: lectura directa")
        elif self.status in (CameraStatus.ONLINE, CameraStatus.WARNING) and now - self._last_ready > self.OFFLINE_AFTER:
            self._set_status(CameraStatus.OFFLINE, "La cámara no responde")
            self.stream_info = {}
        elif self.status in (CameraStatus.UNKNOWN, CameraStatus.OFFLINE) and now - self._last_diag > 60:
            # Not streaming: ask the camera directly so the UI can show *why*
            # (wrong password, wrong path, unreachable...) instead of a relay 404.
            self._last_diag = now
            url = self.cfg.camera_url("main")
            diag = ffmpeg.probe(url) if url else ffmpeg.StreamInfo(available=False, error="sin URL")
            detail = diag.error if not diag.available else "La cámara responde; esperando al relé"
            self._set_status(CameraStatus.OFFLINE, detail)
        self._persist_runtime()

    def _probe(self) -> None:
        url = self.source_url("main")
        info = ffmpeg.probe(url) if url else ffmpeg.StreamInfo(available=False, error="sin URL")
        if info.available:
            self.stream_info = info.to_dict()
            log.info(
                "Camera %s stream: %s %sx%s @ %sfps audio=%s",
                self.cfg.slug, info.codec, info.width, info.height, info.fps, info.audio_codec,
            )  # fmt: skip
        else:
            self.status_detail = info.error
        if self.cfg.sub_path and not self.sub_info:
            sub = ffmpeg.probe(self.source_url("sub") or "")
            if sub.available:
                self.sub_info = sub.to_dict()

    def _set_status(self, status: CameraStatus, detail: str | None) -> None:
        prev = self.status
        self.status, self.status_detail = status, detail
        if prev == status:
            return
        log.info("Camera %s %s", self.cfg.name, "connected" if status == CameraStatus.ONLINE else status.value)
        if status == CameraStatus.OFFLINE and prev in (CameraStatus.ONLINE, CameraStatus.WARNING):
            events_mod.emit(EventType.CAMERA_OFFLINE, self.cfg.id, message=detail or "Cámara desconectada")
        elif status == CameraStatus.ONLINE and prev == CameraStatus.OFFLINE:
            events_mod.emit(EventType.CAMERA_ONLINE, self.cfg.id, message="Cámara reconectada")

    def _persist_runtime(self) -> None:
        info = dict(self.stream_info)
        if self._bitrate_kbps is not None and info:
            info["bitrate_kbps"] = self._bitrate_kbps
        try:
            with session_scope() as db:
                cam = db.get(Camera, self.cfg.id)
                if cam is None:
                    return
                cam.status = self.status.value
                cam.status_detail = self.status_detail
                if self.status == CameraStatus.ONLINE:
                    cam.last_seen_at = datetime.now(UTC)
                if info:
                    cam.stream_info = info
                if self.sub_info:
                    cam.sub_stream_info = self.sub_info
        except Exception:
            log.debug("Could not persist runtime state", exc_info=True)

    # ---------------------------------------------------------------- jobs
    def _desired(self) -> dict[str, bool]:
        cfg = self.cfg
        online = self.status == CameraStatus.ONLINE and bool(self.stream_info.get("codec"))
        mode = RecordingMode(cfg.mode)
        detect = cfg.motion_source != MotionSource.OFF.value
        want = {
            "continuous": online and cfg.auto_record and (
                mode == RecordingMode.CONTINUOUS or (mode == RecordingMode.SCHEDULE and is_active(cfg.schedule_list))
            ),
            "prebuffer": online and cfg.auto_record and mode in (RecordingMode.MOTION, RecordingMode.EVENT),
            "local_motion": online and detect and cfg.motion_source in (MotionSource.LOCAL.value, MotionSource.BOTH.value)
            and mode != RecordingMode.MANUAL,
            "onvif_events": detect and cfg.onvif_enabled
            and cfg.motion_source in (MotionSource.ONVIF.value, MotionSource.BOTH.value),
        }  # fmt: skip
        return want

    def _params(self) -> StreamParams:
        return StreamParams(
            camera_id=self.cfg.id,
            slug=self.cfg.slug,
            source_url=self.source_url("main") or "",
            video_codec=self.stream_info.get("codec"),
            audio_codec=self.stream_info.get("audio_codec"),
            record_audio=self.cfg.record_audio,
        )

    def _reconcile_jobs(self) -> None:
        cfg = self.cfg
        fp = (
            cfg.fingerprint()
            + str(self.using_relay)
            + str(self.stream_info.get("codec"))
            + str(self.stream_info.get("audio_codec"))
        )
        if fp != self._jobs_fp:
            # Configuration (or stream format) changed: restart everything cleanly.
            self._stop_jobs(keep_manual=True)
            self._jobs_fp = fp
        want = self._desired()
        max_clip = 10
        try:
            with session_scope() as db:
                max_clip = int(settings_store.get(db, "recording.event_max_clip_minutes"))
        except Exception:
            log.debug("Using default max clip length", exc_info=True)

        if want["continuous"] and not self.continuous:
            self.continuous = ContinuousRecorder(self._params(), cfg.segment_minutes)
            self.continuous.start()
        elif not want["continuous"] and self.continuous:
            self._stop_job("continuous")

        if want["prebuffer"] and not self.clip_recorder:
            self.clip_recorder = EventClipRecorder(self._params(), cfg.pre_s, cfg.post_s, max_clip)
            self.clip_recorder.start()
        elif not want["prebuffer"] and self.clip_recorder:
            self._stop_job("clip_recorder")

        if want["local_motion"] and not self.motion:
            src = self.source_url("sub") or self.source_url("main")
            self.motion = LocalMotionDetector(cfg.id, src or "", cfg.sensitivity, self.on_detection)
            self.motion.start()
        elif not want["local_motion"] and self.motion:
            self._stop_job("motion")

        if want["onvif_events"] and not self.onvif_events:
            if cfg.username and cfg.password:
                self.onvif_events = OnvifEventListener(
                    cfg.id, cfg.ip, cfg.onvif_port, cfg.username, cfg.password, self.on_detection
                )
                self.onvif_events.start()
        elif not want["onvif_events"] and self.onvif_events:
            self._stop_job("onvif_events")

    def _stop_job(self, attr: str) -> None:
        job = getattr(self, attr)
        setattr(self, attr, None)
        if job is None:
            return
        job.stop()
        if not isinstance(job, OnvifEventListener):  # ONVIF thread may be blocked in a long-poll
            job.join(timeout=15)

    def _stop_jobs(self, keep_manual: bool = False) -> None:
        for attr in ("continuous", "clip_recorder", "motion", "onvif_events"):
            self._stop_job(attr)
        if not keep_manual and self.manual and self.manual.active:
            self.manual.stop()

    def _shutdown(self) -> None:
        self._stop_jobs()
        if not self.cfg.enabled:  # camera deleted/disabled: drop its relay paths too
            self._unregister_relay()
        for ep in list(self.episodes.values()):
            events_mod.update(ep.event_id, ended_at=datetime.now(UTC))
        log.info("Worker stopped for camera %s", self.cfg.slug)

    # ------------------------------------------------------------- events
    def on_detection(self, event_type: str, active: bool, source: str) -> None:
        """Called from detector threads (local motion / ONVIF)."""
        now = time.time()
        with self._lock:
            ep = self.episodes.get(event_type)
            if not active:
                if ep:
                    self._end_episode(event_type)
                return
            if ep:
                ep.last = now
            else:
                eid = events_mod.emit(
                    EventType(event_type), self.cfg.id, source=source,
                    message={"motion": "Movimiento", "person": "Persona detectada", "vehicle": "Vehículo detectado",
                             "animal": "Animal detectado"}.get(event_type, event_type),
                )  # fmt: skip
                ep = Episode(eid, event_type, now, now)
                self.episodes[event_type] = ep
                _snap_pool.submit(self._event_thumbnail, eid)
            # Feed the clip recorder according to the recording mode.
            if self.clip_recorder:
                mode = self.cfg.mode
                if mode == RecordingMode.MOTION.value or (mode == RecordingMode.EVENT.value and event_type in SMART_TYPES):
                    self.clip_recorder.trigger(ep.event_id, now)

    def _end_episode(self, event_type: str) -> None:
        ep = self.episodes.pop(event_type, None)
        if ep:
            events_mod.update(ep.event_id, ended_at=datetime.now(UTC))

    def _sweep_episodes(self) -> None:
        now = time.time()
        with self._lock:
            for t, ep in list(self.episodes.items()):
                if now - ep.last > EPISODE_TIMEOUT:
                    self._end_episode(t)

    def _event_thumbnail(self, event_id: int) -> None:
        if event_id < 0:
            return
        s = get_settings()
        rel = Path("events") / f"cam{self.cfg.id}" / f"{event_id}.jpg"
        dst = s.thumbs_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        ok, _ = ffmpeg.run_quick(ffmpeg.snapshot_cmd(self.source_url("main") or "", dst, width=640), timeout_s=15)
        if ok:
            events_mod.update(event_id, thumbnail_path=rel.as_posix())

    # ----------------------------------------------------- user actions
    def start_manual(self, max_minutes: int) -> int:
        with self._lock:
            if self.manual and self.manual.active:
                raise RuntimeError("Ya hay una grabación manual en curso")
            if not self.stream_info.get("codec"):
                raise RuntimeError("La cámara no está disponible")
            self.manual = ManualRecording(self._params(), max_minutes)
            return self.manual.start()

    def stop_manual(self) -> int | None:
        with self._lock:
            if not (self.manual and self.manual.active):
                return None
            m = self.manual
        m.stop()
        return m.recording_id

    def snapshot(self, save: bool = True) -> tuple[bytes, str | None]:
        """Full-resolution JPEG from the main stream. Optionally kept on disk."""
        s = get_settings()
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        rel = Path("snapshots") / self.cfg.slug / f"{stamp}.jpg"
        dst = (s.storage_dir / rel) if save else (s.work_dir / f"cam{self.cfg.id}" / "snapshot.jpg")
        dst.parent.mkdir(parents=True, exist_ok=True)
        ok, err = ffmpeg.run_quick(ffmpeg.snapshot_cmd(self.source_url("main") or "", dst), timeout_s=20)
        if not ok:
            raise RuntimeError(f"No se pudo capturar la imagen: {err}")
        return dst.read_bytes(), (rel.as_posix() if save else None)

    def preview(self, max_age: float = 10.0) -> bytes:
        """Small cached JPEG for dashboard cards (from the cheap sub-stream)."""
        if self._preview_cache and time.monotonic() - self._preview_cache[0] < max_age:
            return self._preview_cache[1]
        s = get_settings()
        dst = s.work_dir / f"cam{self.cfg.id}" / "preview.jpg"
        dst.parent.mkdir(parents=True, exist_ok=True)
        ok, err = ffmpeg.run_quick(ffmpeg.snapshot_cmd(self.source_url("sub") or "", dst, width=640), timeout_s=15)
        if not ok:
            raise RuntimeError(err)
        data = dst.read_bytes()
        self._preview_cache = (time.monotonic(), data)
        return data


def _tcp_alive(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
