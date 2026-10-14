"""Event detectors.

Level 1 — :class:`OnvifEventListener`: the camera's own detection (motion,
and person/vehicle/pet on recent Tapo firmwares) through ONVIF PullPoint.
Cheapest option: zero video decoding on our side.

Level 2 — :class:`LocalMotionDetector`: our own analysis. FFmpeg decodes the
*sub-stream* to 160x90 grayscale at 4 fps and pipes raw frames to numpy,
which compares each frame against a running-average background.
No OpenCV needed (one less heavy dependency); the ``on_detection`` hook is
where an AI classifier (persons/vehicles/animals) can be plugged in later.
Face recognition is intentionally not implemented.

Both report through the same callback: ``callback(event_type, active, source)``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import numpy as np

from tapocloud.camera_service.supervised import SupervisedJob
from tapocloud.core import ffmpeg
from tapocloud.onvif.device import OnvifCamera, classify_notification
from tapocloud.onvif.soap import OnvifError

log = logging.getLogger(__name__)

DetectionCallback = Callable[[str, bool, str], None]


def sensitivity_to_area(sensitivity: int) -> float:
    """Map UI sensitivity 1..100 to the fraction of changed pixels that counts as motion.

    1 -> ~9.6 % of the image, 50 -> ~1.4 %, 100 -> 0.2 % (exponential scale
    so the slider feels linear to humans).
    """
    s = max(1, min(100, sensitivity))
    return 0.1 * (0.02 ** (s / 100))


class MotionAnalyzer:
    """Pure frame-differencing logic (no I/O, unit-testable)."""

    PIXEL_THRESHOLD = 25  # grey-level difference that counts as "changed"
    GLOBAL_CHANGE = 0.65  # > 65 % changed = IR switch / lights: ignore & re-learn
    CONFIRM_FRAMES = 2  # consecutive frames over threshold before triggering

    def __init__(self, width: int, height: int, sensitivity: int, alpha: float = 0.05) -> None:
        self.w, self.h = width, height
        self.area = sensitivity_to_area(sensitivity)
        self.alpha = alpha
        self.bg: np.ndarray | None = None
        self.hits = 0

    def feed(self, frame_bytes: bytes) -> tuple[bool, float]:
        """Return ``(motion, changed_ratio)`` for one raw gray frame."""
        frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(self.h, self.w).astype(np.float32)
        if self.bg is None:
            self.bg = frame
            return False, 0.0
        diff = np.abs(frame - self.bg)
        ratio = float((diff > self.PIXEL_THRESHOLD).mean())
        if ratio > self.GLOBAL_CHANGE:
            self.bg = frame  # sudden global change: adopt as new background
            self.hits = 0
            return False, ratio
        self.bg = (1 - self.alpha) * self.bg + self.alpha * frame
        self.hits = self.hits + 1 if ratio >= self.area else 0
        return self.hits >= self.CONFIRM_FRAMES, ratio


class LocalMotionDetector(SupervisedJob):
    WIDTH, HEIGHT, FPS = 160, 90, 4
    QUIET_SECONDS = 8  # no motion for this long -> episode ended

    def __init__(self, camera_id: int, source_url: str, sensitivity: int, callback: DetectionCallback) -> None:
        super().__init__(name=f"motion-cam{camera_id}")
        self.source_url = source_url
        self.sensitivity = sensitivity
        self.callback = callback
        self.analyzer = MotionAnalyzer(self.WIDTH, self.HEIGHT, sensitivity)
        self.active = False
        self.last_motion = 0.0
        self.last_ratio = 0.0
        self._last_continue = 0.0
        self._reader: threading.Thread | None = None

    def build_process(self) -> ffmpeg.FFmpegProcess:
        self.analyzer = MotionAnalyzer(self.WIDTH, self.HEIGHT, self.sensitivity)
        cmd = ffmpeg.motion_frames_cmd(self.source_url, self.WIDTH, self.HEIGHT, self.FPS)
        return ffmpeg.FFmpegProcess(self.name, cmd, stdout_pipe=True)

    def on_started(self) -> None:
        self._reader = threading.Thread(target=self._read_frames, name=f"{self.name}-frames", daemon=True)
        self._reader.start()

    def _read_frames(self) -> None:
        proc = self.proc.proc if self.proc else None
        if not proc or not proc.stdout:
            return
        size = self.WIDTH * self.HEIGHT
        while not self.stopping:
            buf = proc.stdout.read(size)
            if not buf or len(buf) < size:
                break
            motion, ratio = self.analyzer.feed(buf)
            self.last_ratio = ratio
            if motion:
                self.last_motion = time.time()
                if not self.active:
                    self.active = True
                    self._emit(True)
                else:
                    self._emit_continue()

    def _emit_continue(self) -> None:
        # Re-assert every 3 s while motion continues (extends open clips).
        if time.time() - self._last_continue > 3:
            self._last_continue = time.time()
            self._emit(True)

    def _emit(self, active: bool) -> None:
        try:
            self.callback("motion", active, "local")
        except Exception:
            log.exception("Motion callback failed")

    def on_tick(self) -> None:
        if self.active and time.time() - self.last_motion > self.QUIET_SECONDS:
            self.active = False
            self._emit(False)

    def on_exit(self, returncode: int | None) -> None:
        if self.active:
            self.active = False
            self._emit(False)


class OnvifEventListener(threading.Thread):
    """Long-polls the camera's PullPoint subscription and forwards detections."""

    def __init__(self, camera_id: int, host: str, port: int, username: str, password: str, callback: DetectionCallback) -> None:
        super().__init__(name=f"onvif-ev-cam{camera_id}", daemon=True)
        self.host, self.port, self.username, self.password = host, port, username, password
        self.callback = callback
        self._stop_evt = threading.Event()
        self.connected = False
        self.last_error: str | None = None
        self.supported = True

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        backoff = 5.0
        while not self._stop_evt.is_set():
            cam = OnvifCamera(self.host, self.port, self.username, self.password)
            address = None
            try:
                cam.connect(with_streams=False)
                if not cam.info.events_xaddr:
                    self.supported = False
                    self.last_error = "La cámara no ofrece eventos ONVIF"
                    log.info("Camera %s has no ONVIF events service", self.host)
                    return
                address = cam.create_pullpoint()
                self.connected, backoff = True, 5.0
                renewed = time.monotonic()
                log.info("ONVIF events subscribed (%s)", self.host)
                while not self._stop_evt.is_set():
                    for n in cam.pull_messages(address, timeout_s=10):
                        kind = classify_notification(n)
                        if kind:
                            self.callback(kind[0], kind[1], "onvif")
                    if time.monotonic() - renewed > 240:
                        cam.renew(address)
                        renewed = time.monotonic()
            except OnvifError as exc:
                self.last_error = str(exc)
                log.warning("ONVIF events error (%s): %s; retry in %.0fs", self.host, exc, backoff)
            except Exception as exc:
                self.last_error = str(exc)
                log.exception("ONVIF event listener crashed (%s)", self.host)
            finally:
                self.connected = False
                if address:
                    cam.unsubscribe(address)
                cam.close()
            self._stop_evt.wait(backoff)
            backoff = min(backoff * 2, 300)
