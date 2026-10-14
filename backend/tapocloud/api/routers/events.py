"""Events: list/filter, thumbnails, acknowledge."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from tapocloud.api.deps import current_user, get_db
from tapocloud.config import get_settings
from tapocloud.core.models import Camera, Event
from tapocloud.core.naming import safe_join

router = APIRouter(prefix="/api/events", tags=["events"], dependencies=[Depends(current_user)])


def event_out(ev: Event, cam_names: dict[int, str]) -> dict:
    return {
        "id": ev.id,
        "camera_id": ev.camera_id,
        "camera_name": cam_names.get(ev.camera_id) if ev.camera_id else None,
        "type": ev.type,
        "source": ev.source,
        "occurred_at": ev.occurred_at.isoformat(),
        "ended_at": ev.ended_at.isoformat() if ev.ended_at else None,
        "message": ev.message,
        "has_thumbnail": bool(ev.thumbnail_path),
        "recording_id": ev.recording_id,
        "acknowledged": ev.acknowledged,
        "details": ev.details,
    }


@router.get("")
def list_events(
    db: Session = Depends(get_db),
    camera_id: int | None = None,
    type: list[str] | None = Query(None),  # noqa: A002
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = None,
    unacknowledged: bool = False,
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    q = select(Event)
    if camera_id is not None:
        q = q.where(Event.camera_id == camera_id)
    if type:
        q = q.where(Event.type.in_(type))
    if since:
        q = q.where(Event.occurred_at >= since)
    if until:
        q = q.where(Event.occurred_at < until)
    if before_id:
        q = q.where(Event.id < before_id)
    if unacknowledged:
        q = q.where(Event.acknowledged.is_(False))
    rows = db.scalars(q.order_by(Event.id.desc()).limit(limit)).all()
    names = dict(db.execute(select(Camera.id, Camera.name)).all())
    unread = db.scalar(select(func.count()).select_from(Event).where(Event.acknowledged.is_(False))) or 0
    return {"items": [event_out(e, names) for e in rows], "unacknowledged": unread}


@router.get("/{event_id}/thumbnail")
def thumbnail(event_id: int, db: Session = Depends(get_db)) -> FileResponse:
    ev = db.get(Event, event_id)
    if ev is None or not ev.thumbnail_path:
        raise HTTPException(status_code=404, detail="Sin miniatura")
    path = safe_join(get_settings().thumbs_dir, ev.thumbnail_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Sin miniatura")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


class AckIn(BaseModel):
    ids: list[int] | None = None  # None = all


@router.post("/ack")
def acknowledge(body: AckIn, db: Session = Depends(get_db)) -> dict:
    q = update(Event).values(acknowledged=True)
    if body.ids is not None:
        q = q.where(Event.id.in_(body.ids))
    res = db.execute(q)
    return {"updated": res.rowcount}
