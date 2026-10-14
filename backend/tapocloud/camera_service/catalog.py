"""Registering finished recordings: DB row + thumbnail + upload queue entry.

This is the hand-off point of the pipeline::

    FFmpeg segment closed -> register_recording() -> upload_queue (pending)
                                                  -> backend uploader picks it up
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from tapocloud.config import get_settings
from tapocloud.core import ffmpeg, settings_store
from tapocloud.core.db import session_scope
from tapocloud.core.models import Recording, RecordingStatus, UploadItem, UploadStatus

log = logging.getLogger(__name__)

# Thumbnails are low priority: one shared small pool for all cameras.
_thumb_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thumbs")
_lock = threading.Lock()


def _thumb_relpath(rec_rel: str) -> str:
    return str(Path(rec_rel).with_suffix(".jpg"))


def make_thumbnail(recording_id: int, video_rel: str, offset_s: float = 1.0) -> None:
    s = get_settings()
    src = s.storage_dir / video_rel
    rel = _thumb_relpath(video_rel)
    dst = s.thumbs_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    ok, err = ffmpeg.run_quick(ffmpeg.thumbnail_cmd(src, dst, offset_s=offset_s))
    if not ok:
        # Very short clips: retry from the first frame.
        ok, err = ffmpeg.run_quick(ffmpeg.thumbnail_cmd(src, dst, offset_s=0))
    if not ok:
        log.debug("Thumbnail failed for %s: %s", video_rel, err)
        return
    with session_scope() as db:
        rec = db.get(Recording, recording_id)
        if rec:
            rec.thumbnail_path = rel


def register_recording(
    camera_id: int,
    kind: str,
    rel_path: str,
    started_at: datetime,
    ended_at: datetime | None,
    status: RecordingStatus = RecordingStatus.COMPLETE,
) -> int:
    """Insert a recording and, when complete, enqueue it for upload. Returns its id."""
    s = get_settings()
    full = s.storage_dir / rel_path
    size = full.stat().st_size if full.exists() else None
    with _lock, session_scope() as db:
        rec = Recording(
            camera_id=camera_id,
            kind=kind,
            status=status.value,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=(ended_at - started_at).total_seconds() if ended_at else None,
            file_path=rel_path,
            size_bytes=size,
        )
        db.add(rec)
        db.flush()
        rec_id = rec.id
        if status == RecordingStatus.COMPLETE:
            _maybe_enqueue(db, rec)
    if status == RecordingStatus.COMPLETE:
        _thumb_pool.submit(make_thumbnail, rec_id, rel_path)
    return rec_id


def complete_recording(recording_id: int, ended_at: datetime) -> None:
    """Mark a recording started with status=recording (manual) as complete."""
    s = get_settings()
    with session_scope() as db:
        rec = db.get(Recording, recording_id)
        if not rec:
            return
        full = s.storage_dir / rec.file_path
        if not full.exists() or full.stat().st_size < 1024:
            rec.status = RecordingStatus.FAILED.value
            return
        rec.status = RecordingStatus.COMPLETE.value
        rec.ended_at = ended_at
        rec.duration_seconds = (ended_at - rec.started_at).total_seconds()
        rec.size_bytes = full.stat().st_size
        _maybe_enqueue(db, rec)
        rel = rec.file_path
    _thumb_pool.submit(make_thumbnail, recording_id, rel)


def _maybe_enqueue(db, rec: Recording) -> None:
    if not settings_store.get(db, "drive.enabled"):
        return
    if rec.kind not in settings_store.get(db, "drive.upload_kinds"):
        return
    db.add(UploadItem(recording_id=rec.id, status=UploadStatus.PENDING.value))
