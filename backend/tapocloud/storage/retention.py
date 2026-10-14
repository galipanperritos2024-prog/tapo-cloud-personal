"""Local retention policy.

Rules, in order:

1. If Drive is enabled, a recording is deletable **only** when
   ``uploaded_at`` is set (upload verified by MD5) — never before.
   ``until_uploaded`` deletes right after verification; ``24h``/``3d``/
   ``7d``/``30d`` keep a local copy for that long after the recording ended.
2. If Drive is disabled, nothing is deleted unless the user explicitly
   enabled ``storage.rotate_when_drive_disabled`` (local-only rotating mode).
3. Recordings whose kind is not uploaded (excluded in ``drive.upload_kinds``)
   follow rule 2.
4. Thumbnails are removed together with the video; the DB row is kept (with
   ``local_deleted_at``) so the timeline still links to the Drive copy.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from tapocloud.config import get_settings
from tapocloud.core import settings_store
from tapocloud.core.db import session_scope
from tapocloud.core.models import Recording, RecordingStatus
from tapocloud.core.naming import safe_join
from tapocloud.core.settings_store import RETENTION_CHOICES

log = logging.getLogger("retention")


def is_deletable(
    rec: Recording,
    *,
    drive_enabled: bool,
    upload_kinds: list[str],
    retention_hours: int,
    rotate_without_drive: bool,
    now: datetime,
) -> bool:
    """Pure decision function (unit-tested)."""
    if rec.local_deleted_at is not None or rec.status == RecordingStatus.RECORDING.value:
        return False
    reference = rec.ended_at or rec.started_at
    expired = now - reference >= timedelta(hours=retention_hours)
    if drive_enabled and rec.kind in upload_kinds:
        if rec.uploaded_at is None:
            return False  # golden rule: never delete before a verified upload
        return retention_hours == 0 or expired
    # Not covered by Drive: only rotate if the user asked for it.
    return rotate_without_drive and retention_hours > 0 and expired


def run_retention_once() -> int:
    s = get_settings()
    now = datetime.now(UTC)
    with session_scope() as db:
        drive_enabled = bool(settings_store.get(db, "drive.enabled"))
        upload_kinds = list(settings_store.get(db, "drive.upload_kinds"))
        policy = settings_store.get(db, "storage.local_retention")
        rotate = bool(settings_store.get(db, "storage.rotate_when_drive_disabled"))
    hours = RETENTION_CHOICES.get(policy, 168)
    deleted = 0
    with session_scope() as db:
        candidates = db.scalars(
            select(Recording)
            .where(Recording.local_deleted_at.is_(None), Recording.status != RecordingStatus.RECORDING.value)
            .limit(5000)
        ).all()
        for rec in candidates:
            if not is_deletable(
                rec,
                drive_enabled=drive_enabled,
                upload_kinds=upload_kinds,
                retention_hours=hours,
                rotate_without_drive=rotate,
                now=now,
            ):
                continue
            try:
                safe_join(s.storage_dir, rec.file_path).unlink(missing_ok=True)
                if rec.thumbnail_path:
                    safe_join(s.thumbs_dir, rec.thumbnail_path).unlink(missing_ok=True)
            except (OSError, ValueError) as exc:
                log.warning("Could not delete %s: %s", rec.file_path, exc)
                continue
            rec.local_deleted_at = now
            deleted += 1
    if deleted:
        log.info("Retention: deleted %d local recordings (policy %s)", deleted, policy)
        _prune_empty_dirs(s.storage_dir)
    return deleted


def _prune_empty_dirs(root) -> None:
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if d.name.startswith("."):
            continue
        try:
            d.rmdir()  # only succeeds when empty
        except OSError:
            pass


async def retention_loop(interval_s: int = 300) -> None:
    while True:
        try:
            await asyncio.to_thread(run_retention_once)
        except Exception:
            log.exception("Retention run failed")
        await asyncio.sleep(interval_s)
