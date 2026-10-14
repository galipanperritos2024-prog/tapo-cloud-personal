"""Continuous (24/7 and scheduled) recording and manual recording.

Continuous recording flow::

    relay RTSP ──FFmpeg -c copy──▶ <storage>/.incoming/cam<id>/2026-10-14_23-05-00.mp4
                                     │ (segment closed, CSV line appended)
                                     ▼
                     <storage>/Salon/2026/10/14/23-05-00.mp4  (atomic rename)
                                     ▼
                         recordings row + upload_queue row

Segments are first written to ``.incoming`` (same filesystem, so the final
move is an atomic rename) because FFmpeg's segment muxer cannot create the
date directories by itself. Anything left in ``.incoming`` after a crash or
power loss is recovered on the next start: fragmented MP4 keeps it playable.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tapocloud.camera_service import catalog
from tapocloud.camera_service.supervised import CsvSegmentTail, SupervisedJob
from tapocloud.config import get_settings
from tapocloud.core import ffmpeg
from tapocloud.core.events import emit
from tapocloud.core.models import EventType, RecordingKind, RecordingStatus
from tapocloud.core.naming import recording_relpath

log = logging.getLogger(__name__)

_INCOMING_FMT = "%Y-%m-%d_%H-%M-%S"


@dataclass(frozen=True)
class StreamParams:
    """What a recorder needs to know about the camera (immutable snapshot)."""

    camera_id: int
    slug: str
    source_url: str  # relay URL (preferred) or direct camera URL
    video_codec: str | None
    audio_codec: str | None
    record_audio: bool


def unique_path(path: Path) -> Path:
    """Avoid overwriting when two files start in the same second."""
    if not path.exists():
        return path
    for i in range(1, 100):
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
    raise FileExistsError(path)


def local_to_utc(dt_local: datetime) -> datetime:
    return dt_local.astimezone().astimezone(UTC)


class ContinuousRecorder(SupervisedJob):
    def __init__(self, params: StreamParams, segment_minutes: int) -> None:
        super().__init__(name=f"rec-cam{params.camera_id}")
        s = get_settings()
        self.params = params
        self.segment_seconds = max(60, int(segment_minutes) * 60)
        self.incoming = s.storage_dir / ".incoming" / f"cam{params.camera_id}"
        self.incoming.mkdir(parents=True, exist_ok=True)
        work = s.work_dir / f"cam{params.camera_id}"
        work.mkdir(parents=True, exist_ok=True)
        self.tail = CsvSegmentTail(work / "continuous.csv")
        self.segments_done = 0
        self._recovered = False

    # -- SupervisedJob hooks ----------------------------------------------
    def build_process(self) -> ffmpeg.FFmpegProcess:
        if not self._recovered:
            self.recover_orphans()
            self._recovered = True
        self.tail.reset()
        p = self.params
        cmd = ffmpeg.continuous_segment_cmd(
            p.source_url,
            self.incoming / f"{_INCOMING_FMT}.mp4",
            self.tail.path,
            self.segment_seconds,
            p.video_codec,
            p.audio_codec,
            p.record_audio,
        )
        return ffmpeg.FFmpegProcess(self.name, cmd)

    def on_started(self) -> None:
        log.info("Recording started (camera %s, %d min segments)", self.params.slug, self.segment_seconds // 60)
        emit(EventType.RECORDING_STARTED, self.params.camera_id, message="Grabación continua iniciada")

    def on_tick(self) -> None:
        for fname, start, end in self.tail.read_new():
            self._finalize(self.incoming / fname, end - start)

    def on_exit(self, returncode: int | None) -> None:
        self.on_tick()
        # The segment being written when FFmpeg died has no CSV line.
        self.recover_orphans()

    def on_shutdown(self) -> None:
        log.info("Recording stopped (camera %s)", self.params.slug)
        emit(EventType.RECORDING_FINISHED, self.params.camera_id, message="Grabación continua detenida")

    # ---------------------------------------------------------------------
    def recover_orphans(self) -> None:
        for f in sorted(self.incoming.glob("*.mp4")):
            try:
                start = datetime.strptime(f.stem[:19], _INCOMING_FMT)
                duration = max(0.0, f.stat().st_mtime - start.timestamp())
            except (ValueError, IndexError, OSError):
                duration = 0.0
            self._finalize(f, duration)

    def _finalize(self, src: Path, duration: float) -> None:
        if not src.exists():
            return
        try:
            start_local = datetime.strptime(src.stem[:19], _INCOMING_FMT).astimezone()
        except ValueError:
            start_local = datetime.fromtimestamp(src.stat().st_mtime).astimezone() - timedelta(seconds=duration)
        if src.stat().st_size < 4096 or duration < 1:
            log.debug("Discarding empty segment %s", src.name)
            src.unlink(missing_ok=True)
            return
        s = get_settings()
        rel = recording_relpath(self.params.slug, start_local)
        dest = unique_path(s.storage_dir / rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(src, dest)
        except OSError as exc:
            log.error("Cannot move segment %s: %s", src.name, exc)
            emit(EventType.STORAGE_ERROR, self.params.camera_id, message=f"No se pudo guardar el segmento: {exc}")
            return
        rel_str = dest.relative_to(s.storage_dir).as_posix()
        start_utc = local_to_utc(start_local)
        catalog.register_recording(
            self.params.camera_id,
            RecordingKind.CONTINUOUS.value,
            rel_str,
            start_utc,
            start_utc + timedelta(seconds=duration),
        )
        self.segments_done += 1
        log.info("Segment saved: %s (%.0fs)", rel_str, duration)


class ManualRecording:
    """One manual recording (button in the UI). Writes directly to its final path."""

    def __init__(self, params: StreamParams, max_minutes: int) -> None:
        s = get_settings()
        self.params = params
        self.started_local = datetime.now().astimezone()
        rel = recording_relpath(params.slug, self.started_local, suffix="manual")
        self.path = unique_path(s.storage_dir / rel)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rel = self.path.relative_to(s.storage_dir).as_posix()
        self.max_seconds = max(10, max_minutes * 60)
        cmd = ffmpeg.single_file_cmd(
            params.source_url, self.path, self.max_seconds, params.video_codec, params.audio_codec, params.record_audio
        )
        self.proc = ffmpeg.FFmpegProcess(f"manual-cam{params.camera_id}", cmd)
        self.recording_id: int | None = None
        self._watch: threading.Thread | None = None
        self._done = threading.Event()

    def start(self) -> int:
        self.proc.start()
        self.recording_id = catalog.register_recording(
            self.params.camera_id,
            RecordingKind.MANUAL.value,
            self.rel,
            local_to_utc(self.started_local),
            None,
            status=RecordingStatus.RECORDING,
        )
        self._watch = threading.Thread(target=self._wait_exit, name=f"manual-watch-{self.params.camera_id}", daemon=True)
        self._watch.start()
        emit(
            EventType.RECORDING_STARTED,
            self.params.camera_id,
            message="Grabación manual iniciada",
            recording_id=self.recording_id,
        )
        return self.recording_id

    def _wait_exit(self) -> None:
        assert self.proc.proc
        self.proc.proc.wait()
        self._finish()

    def _finish(self) -> None:
        if self._done.is_set():
            return
        self._done.set()
        if self.recording_id is not None:
            catalog.complete_recording(self.recording_id, datetime.now(UTC))
        emit(
            EventType.RECORDING_FINISHED,
            self.params.camera_id,
            message="Grabación manual finalizada",
            recording_id=self.recording_id,
        )

    def stop(self) -> None:
        self.proc.stop()
        if self._watch:
            self._watch.join(timeout=10)
        self._finish()

    @property
    def active(self) -> bool:
        return not self._done.is_set()

    @property
    def elapsed_seconds(self) -> float:
        return (datetime.now().astimezone() - self.started_local).total_seconds()


def disk_free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1024**3
