"""Supervised FFmpeg job: keeps one FFmpeg process alive with exponential backoff.

Every recorder/detector derives from :class:`SupervisedJob`. Each runs in
its own thread and owns its own child process, so a camera whose stream
keeps failing only ever restarts *its* jobs.
"""

from __future__ import annotations

import logging
import threading
import time

from tapocloud.core.ffmpeg import FFmpegProcess

log = logging.getLogger(__name__)


class SupervisedJob(threading.Thread):
    #: Backoff schedule bounds (seconds).
    BACKOFF_MIN = 2.0
    BACKOFF_MAX = 60.0
    #: A process that ran at least this long is considered healthy (backoff resets).
    STABLE_AFTER = 60.0
    TICK = 1.0

    def __init__(self, name: str) -> None:
        super().__init__(name=name, daemon=True)
        self._stop_evt = threading.Event()
        self.proc: FFmpegProcess | None = None
        self.restarts = 0
        self.last_error: str | None = None
        self.started_at: float | None = None

    # -- hooks -------------------------------------------------------------
    def build_process(self) -> FFmpegProcess:  # pragma: no cover - abstract
        raise NotImplementedError

    def on_started(self) -> None:
        """Called after each (re)start."""

    def on_tick(self) -> None:
        """Called every ``TICK`` seconds while the process runs."""

    def on_exit(self, returncode: int | None) -> None:
        """Called after the process exits (normally or not)."""

    def on_shutdown(self) -> None:
        """Called once when the job is stopped for good."""

    # ---------------------------------------------------------------------
    def stop(self) -> None:
        self._stop_evt.set()

    @property
    def stopping(self) -> bool:
        return self._stop_evt.is_set()

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.running()

    def run(self) -> None:
        backoff = self.BACKOFF_MIN
        while not self._stop_evt.is_set():
            try:
                self.proc = self.build_process()
                self.proc.start()
                self.started_at = time.monotonic()
                self.on_started()
                while self.proc.running() and not self._stop_evt.is_set():
                    self.on_tick()
                    self._stop_evt.wait(self.TICK)
                if self._stop_evt.is_set():
                    self.proc.stop()
                rc = self.proc.returncode()
                self.on_exit(rc)
                if self._stop_evt.is_set():
                    break
                self.last_error = self.proc.last_error() or f"exit code {rc}"
                ran_for = time.monotonic() - (self.started_at or time.monotonic())
                if ran_for >= self.STABLE_AFTER:
                    backoff = self.BACKOFF_MIN
                log.warning("%s exited (%s); restarting in %.0fs", self.name, self.last_error, backoff)
            except Exception as exc:  # never let a job thread die silently
                self.last_error = str(exc)
                log.exception("%s crashed; restarting in %.0fs", self.name, backoff)
                if self.proc:
                    self.proc.stop(timeout_s=2)
            self.restarts += 1
            self._stop_evt.wait(backoff)
            backoff = min(backoff * 2, self.BACKOFF_MAX)
        try:
            self.on_shutdown()
        except Exception:
            log.exception("%s shutdown hook failed", self.name)


class CsvSegmentTail:
    """Incrementally reads lines appended by FFmpeg's ``-segment_list_type csv``.

    Each line is ``filename,start,end`` and is written when that segment is
    *closed* — i.e. the file is complete and safe to move/upload.
    """

    def __init__(self, path) -> None:
        self.path = path
        self._offset = 0
        self._partial = ""

    def reset(self) -> None:
        self._offset, self._partial = 0, ""
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def read_new(self) -> list[tuple[str, float, float]]:
        try:
            with open(self.path, encoding="utf-8", errors="replace") as fh:
                fh.seek(self._offset)
                data = fh.read()
                self._offset = fh.tell()
        except FileNotFoundError:
            return []
        data = self._partial + data
        lines = data.split("\n")
        self._partial = lines.pop()  # incomplete last line (no newline yet)
        out = []
        for line in lines:
            parts = line.strip().rsplit(",", 2)
            if len(parts) != 3:
                continue
            try:
                out.append((parts[0], float(parts[1]), float(parts[2])))
            except ValueError:
                continue
        return out
