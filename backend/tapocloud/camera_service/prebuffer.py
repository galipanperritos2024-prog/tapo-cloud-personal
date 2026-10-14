"""Pre-event circular buffer and event clip assembly.

How it works::

    relay ──FFmpeg──▶ ring/chunk_0000.ts … chunk_00NN.ts   (2 s each, overwritten in a ring)
                         │
     trigger(t) ─────────┤ copy chunks ending after (t - pre) into clips/<id>/
                         │ keep copying each new chunk until (t_last + post)
                         ▼
             FFmpeg concat (-c copy) ──▶ Salon/2026/10/14/23-04-02_event.mp4

* Further detections while a clip is open *extend* it (one clip per
  activity episode, not one per frame of motion), up to a maximum length.
* Chunks are copied out of the ring as soon as they are complete, so the
  ring only needs to hold the pre-event window plus a small margin.
* MPEG-TS chunks join losslessly and audio is already AAC, so assembling a
  clip is a pure remux taking milliseconds.
"""

from __future__ import annotations

import logging
import math
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from tapocloud.camera_service import catalog
from tapocloud.camera_service.recorder import StreamParams, unique_path
from tapocloud.camera_service.supervised import CsvSegmentTail, SupervisedJob
from tapocloud.config import get_settings
from tapocloud.core import events as events_mod
from tapocloud.core import ffmpeg
from tapocloud.core.models import RecordingKind
from tapocloud.core.naming import recording_relpath

log = logging.getLogger(__name__)

CHUNK_SECONDS = 2


@dataclass
class Chunk:
    path: Path
    start_wall: float
    end_wall: float


@dataclass
class OpenClip:
    clip_id: int
    workdir: Path
    first_wall: float
    deadline: float
    event_ids: list[int] = field(default_factory=list)
    parts: list[Path] = field(default_factory=list)
    first_part_start: float | None = None  # wall time where the first copied chunk begins


class EventClipRecorder(SupervisedJob):
    def __init__(self, params: StreamParams, pre_seconds: int, post_seconds: int, max_clip_minutes: int = 10) -> None:
        super().__init__(name=f"ring-cam{params.camera_id}")
        s = get_settings()
        self.params = params
        self.pre = max(0, min(30, int(pre_seconds)))
        self.post = max(10, min(120, int(post_seconds)))
        self.max_clip = max(60, max_clip_minutes * 60)
        base = s.work_dir / f"cam{params.camera_id}"
        self.ring_dir = base / "ring"
        self.clips_dir = base / "clips"
        for d in (self.ring_dir, self.clips_dir):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
        # Ring must hold the pre-event window + margin for keyframe-aligned chunks.
        self.wrap = max(6, math.ceil(self.pre / CHUNK_SECONDS) * 2 + 4)
        self.tail = CsvSegmentTail(base / "ring.csv")
        self.chunks: deque[Chunk] = deque(maxlen=self.wrap)
        self.clip: OpenClip | None = None
        self._lock = threading.Lock()
        self._clip_seq = 0
        self.clips_done = 0
        # Wall time where the previous clip ended: a new clip's pre-roll never
        # re-includes footage already saved (continuous activity => back-to-back clips).
        self._last_clip_end = 0.0

    # -- SupervisedJob hooks ----------------------------------------------
    def build_process(self) -> ffmpeg.FFmpegProcess:
        self.tail.reset()
        self.chunks.clear()
        p = self.params
        cmd = ffmpeg.ring_buffer_cmd(
            p.source_url, self.ring_dir, self.tail.path, CHUNK_SECONDS, self.wrap, p.video_codec, p.audio_codec, p.record_audio
        )
        return ffmpeg.FFmpegProcess(self.name, cmd)

    def on_started(self) -> None:
        log.info("Pre-event buffer running (camera %s, pre=%ss post=%ss)", self.params.slug, self.pre, self.post)

    def on_tick(self) -> None:
        now = time.time()
        for fname, start, end in self.tail.read_new():
            dur = max(0.1, end - start)
            chunk = Chunk(self.ring_dir / fname, now - dur, now)
            with self._lock:
                self.chunks.append(chunk)
                if self.clip:
                    self._copy_into_clip(chunk)
        with self._lock:
            if self.clip and (now >= self.clip.deadline or now - self.clip.first_wall >= self.max_clip):
                self._close_clip()

    def on_exit(self, returncode: int | None) -> None:
        with self._lock:
            if self.clip:
                self._close_clip()

    def on_shutdown(self) -> None:
        with self._lock:
            if self.clip:
                self._close_clip()

    # -- public API (called from detector threads) ---------------------------
    def trigger(self, event_id: int | None, at: float | None = None) -> None:
        """Start a clip, or extend the open one, for a detection at time ``at``."""
        at = at or time.time()
        with self._lock:
            if self.clip is None:
                self._clip_seq += 1
                workdir = self.clips_dir / f"clip{self._clip_seq}"
                workdir.mkdir(parents=True, exist_ok=True)
                self.clip = OpenClip(self._clip_seq, workdir, first_wall=at - self.pre, deadline=at + self.post)
                for ch in self.chunks:
                    if ch.end_wall > at - self.pre and ch.start_wall >= self._last_clip_end - 0.5:
                        self._copy_into_clip(ch)
                log.info("Event clip opened (camera %s)", self.params.slug)
            else:
                self.clip.deadline = max(self.clip.deadline, at + self.post)
            if event_id is not None and event_id >= 0:
                self.clip.event_ids.append(event_id)

    @property
    def clip_open(self) -> bool:
        return self.clip is not None

    # -- internals -----------------------------------------------------------
    def _copy_into_clip(self, chunk: Chunk) -> None:
        assert self.clip
        dst = self.clip.workdir / f"part{len(self.clip.parts):05d}.ts"
        try:
            shutil.copyfile(chunk.path, dst)
        except OSError as exc:
            log.warning("Could not copy ring chunk %s: %s", chunk.path.name, exc)
            return
        if not self.clip.parts:
            self.clip.first_part_start = chunk.start_wall
        self.clip.parts.append(dst)

    def _close_clip(self) -> None:
        clip, self.clip = self.clip, None
        if clip is None:
            return
        if self.chunks:
            self._last_clip_end = self.chunks[-1].end_wall
        # Assemble in the background: never block the tail loop (or other cameras).
        threading.Thread(target=self._assemble, args=(clip,), name=f"clip-{self.params.camera_id}", daemon=True).start()

    def _assemble(self, clip: OpenClip) -> None:
        try:
            if not clip.parts:
                log.warning("Event clip without video (camera %s)", self.params.slug)
                return
            s = get_settings()
            start_wall = clip.first_part_start or clip.first_wall
            start_local = datetime.fromtimestamp(start_wall).astimezone()
            rel = recording_relpath(self.params.slug, start_local, suffix="event")
            dest = unique_path(s.storage_dir / rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            list_file = clip.workdir / "list.txt"
            list_file.write_text("".join(f"file '{p.as_posix()}'\n" for p in clip.parts), encoding="utf-8")
            ok, err = ffmpeg.run_quick(ffmpeg.concat_cmd(list_file, dest, self.params.video_codec), timeout_s=120)
            if not ok:
                log.error("Event clip assembly failed (camera %s): %s", self.params.slug, err)
                return
            duration = len(clip.parts) * CHUNK_SECONDS
            start_utc = datetime.fromtimestamp(start_wall, UTC)
            end_utc = datetime.fromtimestamp(start_wall + duration, UTC)
            rec_id = catalog.register_recording(
                self.params.camera_id, RecordingKind.EVENT.value, dest.relative_to(s.storage_dir).as_posix(), start_utc, end_utc
            )
            for eid in set(clip.event_ids):
                events_mod.update(eid, recording_id=rec_id)
            self.clips_done += 1
            log.info("Event clip saved: %s (~%ds)", dest.name, duration)
        finally:
            shutil.rmtree(clip.workdir, ignore_errors=True)
