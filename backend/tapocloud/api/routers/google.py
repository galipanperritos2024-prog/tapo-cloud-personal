"""Google Drive: OAuth connection, status/quota and upload queue management."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from tapocloud.api.deps import current_session, current_user, get_db
from tapocloud.api.schemas import GoogleConfigIn
from tapocloud.config import get_settings
from tapocloud.core import settings_store
from tapocloud.core.models import Camera, Recording, UploadItem, UploadStatus, UserSession
from tapocloud.drive import oauth
from tapocloud.drive.client import DriveClient, DriveError
from tapocloud.drive.uploader import uploader

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/google", tags=["google"])


@router.get("/status", dependencies=[Depends(current_user)])
async def status(db: Session = Depends(get_db)) -> dict:
    cfg = oauth.client_config()
    info = oauth.connection_info()
    quota = None
    if info["connected"]:
        client = DriveClient()
        try:
            about = await client.about()
            q = about.get("storageQuota", {})
            quota = {k: int(v) for k, v in q.items() if v is not None and str(v).isdigit()}
        except (DriveError, oauth.DriveAuthError) as exc:
            info["error"] = str(exc)
        finally:
            await client.aclose()
    counts = dict(db.execute(select(UploadItem.status, func.count()).group_by(UploadItem.status)).all())
    uploaded_bytes = db.scalar(select(func.coalesce(func.sum(Recording.size_bytes), 0)).where(Recording.uploaded_at.is_not(None)))
    return {
        **info,
        "configured": cfg is not None,
        "client_id": cfg.client_id if cfg else None,
        "redirect_uri": cfg.redirect_uri if cfg else get_settings().google_redirect_uri,
        "enabled": settings_store.get(db, "drive.enabled"),
        "root_folder": settings_store.get(db, "drive.root_folder"),
        "quota": quota,
        "uploaded_bytes": int(uploaded_bytes or 0),
        "queue": {s.value: counts.get(s.value, 0) for s in UploadStatus},
        "current_upload": uploader.current,
        "last_success_at": uploader.last_success_at.isoformat() if uploader.last_success_at else None,
        "last_error": uploader.last_error,
        "online": uploader.online,
    }


@router.post("/config", dependencies=[Depends(current_user)])
def set_config(body: GoogleConfigIn, db: Session = Depends(get_db)) -> dict:
    """Store the user's own OAuth client (from their Google Cloud project)."""
    settings_store.set(db, "google.client_id", body.client_id)
    if body.client_secret:
        settings_store.set_secret(db, "google.client_secret", body.client_secret)
    if body.redirect_uri:
        settings_store.set(db, "google.redirect_uri", body.redirect_uri)
    return {"ok": True}


@router.post("/connect")
def connect(sess: UserSession = Depends(current_session)) -> dict:
    """Returns the Google consent URL; the browser navigates to it."""
    try:
        return {"auth_url": oauth.build_authorization_url(sess.id)}
    except oauth.DriveAuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/callback")
async def callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    sess: UserSession = Depends(current_session),
) -> RedirectResponse:
    """OAuth redirect target. Always lands back on the Drive page with a result flag."""
    if error or not code or not state:
        return RedirectResponse(f"/drive/?error={quote(error or 'cancelled')}", status_code=303)
    try:
        await oauth.exchange_code(code, state, sess.id)
    except oauth.DriveAuthError as exc:
        return RedirectResponse(f"/drive/?error={quote(str(exc))}", status_code=303)
    uploader.wake()
    return RedirectResponse("/drive/?connected=1", status_code=303)


@router.post("/disconnect", dependencies=[Depends(current_user)])
async def disconnect() -> dict:
    await oauth.disconnect()
    return {"ok": True}


@router.get("/queue", dependencies=[Depends(current_user)])
def queue(
    db: Session = Depends(get_db),
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    q = select(UploadItem, Recording).join(Recording, Recording.id == UploadItem.recording_id)
    if status:
        q = q.where(UploadItem.status == status)
    rows = db.execute(q.order_by(UploadItem.updated_at.desc()).limit(limit)).all()
    names = dict(db.execute(select(Camera.id, Camera.name)).all())
    return [
        {
            "id": u.id,
            "recording_id": r.id,
            "camera_name": names.get(r.camera_id),
            "file": r.file_path,
            "size_bytes": r.size_bytes,
            "status": u.status,
            "attempts": u.attempts,
            "bytes_uploaded": u.bytes_uploaded,
            "next_attempt_at": u.next_attempt_at.isoformat() if u.next_attempt_at else None,
            "last_error": u.last_error,
            "drive_path": u.drive_path,
            "completed_at": u.completed_at.isoformat() if u.completed_at else None,
        }
        for u, r in rows
    ]


@router.post("/queue/{item_id}/retry", dependencies=[Depends(current_user)])
def retry(item_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.get(UploadItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Elemento no encontrado")
    if item.status == UploadStatus.COMPLETED.value:
        raise HTTPException(status_code=409, detail="Ya está subido")
    item.status, item.attempts, item.next_attempt_at, item.last_error = UploadStatus.PENDING.value, 0, datetime.now(UTC), None
    uploader.wake()
    return {"ok": True}


@router.post("/queue/retry-all", dependencies=[Depends(current_user)])
def retry_all(db: Session = Depends(get_db)) -> dict:
    res = db.execute(
        update(UploadItem)
        .where(UploadItem.status.in_([UploadStatus.ERROR.value, UploadStatus.RETRY.value]))
        .values(status=UploadStatus.PENDING.value, attempts=0, next_attempt_at=datetime.now(UTC), last_error=None)
    )
    uploader.wake()
    return {"updated": res.rowcount}


@router.post("/queue/enqueue-missing", dependencies=[Depends(current_user)])
def enqueue_missing(db: Session = Depends(get_db)) -> dict:
    """Queue every local, complete recording that was never queued (e.g. made while Drive was off)."""
    kinds = settings_store.get(db, "drive.upload_kinds")
    queued = select(UploadItem.recording_id)
    recs = db.scalars(
        select(Recording).where(
            Recording.status == "complete",
            Recording.uploaded_at.is_(None),
            Recording.local_deleted_at.is_(None),
            Recording.kind.in_(kinds),
            Recording.id.not_in(queued),
        )
    ).all()
    for r in recs:
        db.add(UploadItem(recording_id=r.id))
    uploader.wake()
    return {"queued": len(recs)}
