"""Recordings: list, timeline, playback/download, thumbnails, deletion."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tapocloud.api.deps import current_user, get_db
from tapocloud.config import get_settings
from tapocloud.core.models import Camera, Event, Recording, UploadItem
from tapocloud.core.naming import safe_join

router = APIRouter(prefix="/api/recordings", tags=["recordings"], dependencies=[Depends(current_user)])


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """UTC bounds of a *local* calendar day (what the user means by 'el 14')."""
    start_local = datetime.combine(day, time.min).astimezone()
    end_local = datetime.combine(day + timedelta(days=1), time.min).astimezone()
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def recording_out(r: Recording, names: dict[int, str], upload: UploadItem | None = None) -> dict:
    return {
        "id": r.id,
        "camera_id": r.camera_id,
        "camera_name": names.get(r.camera_id),
        "kind": r.kind,
        "status": r.status,
        "started_at": r.started_at.isoformat(),
        "ended_at": r.ended_at.isoformat() if r.ended_at else None,
        "duration_seconds": r.duration_seconds,
        "file_name": r.file_path.rsplit("/", 1)[-1],
        "file_path": r.file_path,
        "size_bytes": r.size_bytes,
        "has_thumbnail": bool(r.thumbnail_path),
        "local_available": r.local_deleted_at is None,
        "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
        "drive_file_id": r.drive_file_id,
        "drive_url": f"https://drive.google.com/file/d/{r.drive_file_id}/view" if r.drive_file_id else None,
        "upload_status": upload.status if upload else None,
    }


@router.get("")
def list_recordings(
    db: Session = Depends(get_db),
    camera_id: int | None = None,
    day: date | None = None,
    kind: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict:
    q = select(Recording)
    if camera_id is not None:
        q = q.where(Recording.camera_id == camera_id)
    if day:
        a, b = local_day_bounds(day)
        q = q.where(Recording.started_at >= a, Recording.started_at < b)
    if kind:
        q = q.where(Recording.kind == kind)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Recording.started_at.desc()).limit(limit).offset(offset)).all()
    uploads = {u.recording_id: u for u in db.scalars(select(UploadItem).where(UploadItem.recording_id.in_([r.id for r in rows])))}
    names = dict(db.execute(select(Camera.id, Camera.name)).all())
    return {"total": total, "items": [recording_out(r, names, uploads.get(r.id)) for r in rows]}


@router.get("/timeline")
def timeline(camera_id: int, day: date, db: Session = Depends(get_db)) -> dict:
    """Recordings and events of one camera for one local day (for the timeline bar)."""
    a, b = local_day_bounds(day)
    recs = db.scalars(
        select(Recording)
        .where(
            Recording.camera_id == camera_id,
            Recording.started_at < b,
            func.coalesce(Recording.ended_at, Recording.started_at) >= a,
        )
        .order_by(Recording.started_at)
    ).all()
    evs = db.scalars(
        select(Event)
        .where(Event.camera_id == camera_id, Event.occurred_at >= a, Event.occurred_at < b)
        .order_by(Event.occurred_at)
    ).all()
    return {
        "day": day.isoformat(),
        "start": a.isoformat(),
        "end": b.isoformat(),
        "recordings": [
            {
                "id": r.id,
                "kind": r.kind,
                "start": r.started_at.isoformat(),
                "end": (r.ended_at or r.started_at).isoformat(),
                "local": r.local_deleted_at is None,
                "drive": bool(r.uploaded_at),
            }
            for r in recs
        ],
        "events": [{"id": e.id, "type": e.type, "at": e.occurred_at.isoformat(), "recording_id": e.recording_id} for e in evs],
    }


@router.get("/days")
def days_with_recordings(camera_id: int | None = None, db: Session = Depends(get_db)) -> list[str]:
    """Distinct local days that have recordings (for the date picker)."""
    q = select(Recording.started_at)
    if camera_id is not None:
        q = q.where(Recording.camera_id == camera_id)
    days = {r.astimezone().date().isoformat() for r in db.scalars(q.order_by(Recording.started_at.desc()).limit(20000))}
    return sorted(days, reverse=True)


def _get(db: Session, rec_id: int) -> Recording:
    rec = db.get(Recording, rec_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Grabación no encontrada")
    return rec


@router.get("/{rec_id}")
def get_recording(rec_id: int, db: Session = Depends(get_db)) -> dict:
    rec = _get(db, rec_id)
    names = dict(db.execute(select(Camera.id, Camera.name)).all())
    up = db.scalar(select(UploadItem).where(UploadItem.recording_id == rec.id))
    events = db.scalars(select(Event).where(Event.recording_id == rec.id)).all()
    out = recording_out(rec, names, up)
    out["events"] = [{"id": e.id, "type": e.type, "at": e.occurred_at.isoformat()} for e in events]
    return out


@router.get("/{rec_id}/file")
def recording_file(rec_id: int, download: bool = False, db: Session = Depends(get_db)) -> FileResponse:
    """Streams the MP4 (HTTP Range supported, so the browser can seek)."""
    rec = _get(db, rec_id)
    path = safe_join(get_settings().storage_dir, rec.file_path)
    if rec.local_deleted_at or not path.exists():
        raise HTTPException(status_code=410, detail="El archivo ya no está en local (consúltalo en Google Drive)")
    names = dict(db.execute(select(Camera.id, Camera.slug)).all())
    fname = f"{names.get(rec.camera_id, 'cam')}_{rec.started_at.astimezone().strftime('%Y-%m-%d_%H-%M-%S')}.mp4"
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=fname if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/{rec_id}/thumbnail")
def recording_thumbnail(rec_id: int, db: Session = Depends(get_db)) -> FileResponse:
    rec = _get(db, rec_id)
    if not rec.thumbnail_path:
        raise HTTPException(status_code=404, detail="Sin miniatura")
    path = safe_join(get_settings().thumbs_dir, rec.thumbnail_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Sin miniatura")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/{rec_id}", status_code=204)
def delete_recording(rec_id: int, db: Session = Depends(get_db)) -> Response:
    """Delete the *local* file. The Drive copy (if any) is left untouched."""
    rec = _get(db, rec_id)
    if rec.status == "recording":
        raise HTTPException(status_code=409, detail="La grabación sigue en curso")
    s = get_settings()
    safe_join(s.storage_dir, rec.file_path).unlink(missing_ok=True)
    if rec.thumbnail_path:
        safe_join(s.thumbs_dir, rec.thumbnail_path).unlink(missing_ok=True)
    if rec.uploaded_at is None:
        # Nothing left to upload: drop the queue entry and the row itself.
        db.delete(rec)
    else:
        rec.local_deleted_at = datetime.now(UTC)
    return Response(status_code=204)
