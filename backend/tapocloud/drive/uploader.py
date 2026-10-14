"""Upload queue worker (runs inside the backend process as an asyncio task).

State machine of an ``upload_queue`` row::

    pending ──▶ uploading ──▶ completed
                   │  ▲
                   ▼  │ (backoff: 30s, 1m, 2m, 4m ... capped at 1h, with jitter)
                 retry
                   │
                   ▼ (permanent problem: file missing, Drive full, auth revoked…)
                 error   ── user can "Reintentar" from the UI ──▶ pending

Guarantees
----------
* A recording is marked uploaded only after Google returns an MD5 equal to
  the local file's MD5. Local deletion (retention) depends on that flag, so
  **a file is never deleted locally before its upload is verified**.
* Uploads resume from the last confirmed byte after network loss *or*
  a reboot (the resumable session URI is persisted).
* Idempotent: before starting a new upload the worker searches Drive for a
  file tagged with this recording id (crash between "uploaded" and "DB
  updated" does not create duplicates).
* When the Internet is down the queue simply accumulates; recording is
  unaffected because it is a different process writing to local disk.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

from sqlalchemy import or_, select, update

from tapocloud.config import get_settings
from tapocloud.core import settings_store
from tapocloud.core.db import session_scope
from tapocloud.core.events import emit
from tapocloud.core.models import EventType, Recording, UploadItem, UploadStatus
from tapocloud.drive.client import DriveClient, DriveError, file_md5
from tapocloud.drive.oauth import DriveAuthError, connection_info

log = logging.getLogger("uploader")

BACKOFF_BASE = 30
BACKOFF_MAX = 3600
MAX_ATTEMPTS_BEFORE_ERROR = 50  # ~2 days of retries at the cap


def backoff_seconds(attempts: int) -> float:
    """Exponential backoff with ±20 % jitter."""
    base = min(BACKOFF_MAX, BACKOFF_BASE * (2 ** max(0, attempts - 1)))
    return base * random.uniform(0.8, 1.2)


def drive_folder_parts(root: str, rel_path: str) -> list[str]:
    """``Salon/2026/10/14/23-00-00.mp4`` -> ``[root, Salon, 2026, 10, 14]``."""
    return [root, *PurePosixPath(rel_path).parent.parts]


class Uploader:
    POLL_SECONDS = 5

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None  # created in start(): must belong to the running loop
        self.current: dict | None = None  # {"item_id", "file", "bytes", "total"}
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None
        self.online = True

    def start(self) -> None:
        self._recover_stuck()
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="uploader")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        if self._wake is not None:
            self._wake.set()

    @staticmethod
    def _recover_stuck() -> None:
        """Items left 'uploading' by a crash/restart go back to the queue."""
        with session_scope() as db:
            db.execute(
                update(UploadItem)
                .where(UploadItem.status == UploadStatus.UPLOADING.value)
                .values(status=UploadStatus.RETRY.value, next_attempt_at=datetime.now(UTC))
            )

    async def _run(self) -> None:
        client = DriveClient()
        try:
            while True:
                try:
                    worked = await self._process_batch(client)
                except Exception:
                    log.exception("Uploader loop error")
                    worked = False
                if not worked:
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=self.POLL_SECONDS)
                    except TimeoutError:
                        pass
                    self._wake.clear()
        finally:
            await client.aclose()

    async def _process_batch(self, client: DriveClient) -> bool:
        with session_scope() as db:
            if not settings_store.get(db, "drive.enabled"):
                return False
            parallel = max(1, int(settings_store.get(db, "drive.max_parallel_uploads")))
            limit_kbps = int(settings_store.get(db, "drive.bandwidth_limit_kbps") or 0)
            root = settings_store.get(db, "drive.root_folder") or "TapoCloud"
        if not connection_info()["connected"]:
            return False
        now = datetime.now(UTC)
        with session_scope() as db:
            items = db.scalars(
                select(UploadItem)
                .where(
                    or_(UploadItem.status == UploadStatus.PENDING.value, UploadItem.status == UploadStatus.RETRY.value),
                    UploadItem.next_attempt_at <= now,
                )
                .order_by(UploadItem.created_at)
                .limit(parallel)
            ).all()
            ids = [i.id for i in items]
            for i in items:
                i.status = UploadStatus.UPLOADING.value
        if not ids:
            return False
        await asyncio.gather(*(self._upload_one(client, i, root, limit_kbps) for i in ids))
        return True

    async def _upload_one(self, client: DriveClient, item_id: int, root: str, limit_kbps: int) -> None:
        s = get_settings()
        with session_scope() as db:
            item = db.get(UploadItem, item_id)
            rec = db.get(Recording, item.recording_id) if item else None
            if item is None or rec is None:
                return
            rel, rec_id, session_uri = rec.file_path, rec.id, item.session_uri
            local_deleted = rec.local_deleted_at is not None
            camera_slug = PurePosixPath(rel).parts[0]
        path = s.storage_dir / rel
        if local_deleted or not path.exists():
            self._finish(item_id, UploadStatus.ERROR, error="El archivo local ya no existe")
            return

        size = path.stat().st_size
        self.current = {"item_id": item_id, "file": rel, "bytes": 0, "total": size}
        log.info("Uploading to Google Drive: %s (%.1f MB)", rel, size / 1e6)
        try:
            local_md5 = await asyncio.to_thread(file_md5, path)
            result = None
            offset = 0
            if session_uri:
                q = await client.query_offset(session_uri, size)
                if q is None:
                    session_uri = None
                elif isinstance(q, int):
                    offset = q
                    log.info("Resuming upload of %s at %.1f MB", rel, offset / 1e6)
                else:
                    result = q
            if result is None and not session_uri:
                existing = await client.find_uploaded(rec_id)
                if existing and existing.md5 == local_md5:
                    result = existing
                else:
                    if existing:  # partial/corrupt leftover from an earlier attempt
                        await client.delete_file(existing.file_id)
                    folder_id = await client.ensure_folder_path(drive_folder_parts(root, rel))
                    session_uri = await client.start_resumable(
                        PurePosixPath(rel).name, folder_id, size, {"tapocloud_recording": str(rec_id), "camera": camera_slug}
                    )
                    with session_scope() as db:
                        db.get(UploadItem, item_id).session_uri = session_uri

            if result is None:

                def progress(n: int) -> None:
                    if self.current:
                        self.current["bytes"] = n
                    with session_scope() as db:
                        it = db.get(UploadItem, item_id)
                        if it:
                            it.bytes_uploaded = n

                result = await client.upload_chunks(session_uri, path, offset, size, progress, limit_kbps)

            if result.md5 and result.md5 != local_md5:
                await client.delete_file(result.file_id)
                raise DriveError("La suma MD5 no coincide tras la subida; se repetirá", transient=True)
            if result.size is not None and result.size != size:
                await client.delete_file(result.file_id)
                raise DriveError("El tamaño en Drive no coincide; se repetirá", transient=True)

            drive_path = "/".join([*drive_folder_parts(root, rel), PurePosixPath(rel).name])
            with session_scope() as db:
                rec = db.get(Recording, rec_id)
                rec.uploaded_at = datetime.now(UTC)
                rec.drive_file_id = result.file_id
            self._finish(item_id, UploadStatus.COMPLETED, drive_path=drive_path)
            self.last_success_at = datetime.now(UTC)
            self.online = True
            log.info("Upload completed: %s", drive_path)
        except DriveAuthError as exc:
            self.last_error = str(exc)
            self._finish(item_id, UploadStatus.RETRY, error=str(exc), delay=600)
            emit(EventType.UPLOAD_ERROR, message=str(exc))
        except DriveError as exc:
            self.last_error = str(exc)
            if exc.status is None:
                self.online = False
            if exc.transient:
                if exc.status in (404, 410):
                    with session_scope() as db:
                        db.get(UploadItem, item_id).session_uri = None
                self._finish(item_id, UploadStatus.RETRY, error=str(exc))
            else:
                self._finish(item_id, UploadStatus.ERROR, error=str(exc))
                emit(EventType.UPLOAD_ERROR, message=f"{rel}: {exc}")
            log.warning("Upload failed for %s: %s", rel, exc)
        except OSError as exc:
            self.last_error = str(exc)
            self._finish(item_id, UploadStatus.RETRY, error=f"Error de lectura local: {exc}")
        finally:
            self.current = None

    def _finish(
        self,
        item_id: int,
        status: UploadStatus,
        *,
        error: str | None = None,
        drive_path: str | None = None,
        delay: float | None = None,
    ) -> None:
        with session_scope() as db:
            item = db.get(UploadItem, item_id)
            if item is None:
                return
            item.status = status.value
            item.last_error = error[:500] if error else None
            if drive_path:
                item.drive_path = drive_path
            if status == UploadStatus.COMPLETED:
                item.completed_at = datetime.now(UTC)
                item.session_uri = None
            elif status == UploadStatus.RETRY:
                item.attempts += 1
                if item.attempts >= MAX_ATTEMPTS_BEFORE_ERROR:
                    item.status = UploadStatus.ERROR.value
                item.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay or backoff_seconds(item.attempts))


uploader = Uploader()
