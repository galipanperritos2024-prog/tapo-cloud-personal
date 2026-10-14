"""Helper to record events from any process (backend, camera-service, workers)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from tapocloud.core.db import session_scope
from tapocloud.core.models import Event, EventType, utcnow

log = logging.getLogger("events")


def emit(
    event_type: EventType,
    camera_id: int | None = None,
    *,
    message: str | None = None,
    source: str = "system",
    occurred_at: datetime | None = None,
    thumbnail_path: str | None = None,
    recording_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    """Persist an event and return its id. Never raises (events are best effort)."""
    try:
        with session_scope() as db:
            ev = Event(
                camera_id=camera_id,
                type=event_type.value,
                source=source,
                occurred_at=occurred_at or utcnow(),
                thumbnail_path=thumbnail_path,
                recording_id=recording_id,
                message=message,
                details=details or {},
            )
            db.add(ev)
            db.flush()
            log.info("%s camera=%s %s", event_type.value, camera_id, message or "")
            return ev.id
    except Exception:  # pragma: no cover - DB locked/unavailable
        log.exception("Could not store event %s", event_type.value)
        return -1


def update(event_id: int, **fields: Any) -> None:
    if event_id < 0:
        return
    try:
        with session_scope() as db:
            ev = db.get(Event, event_id)
            if ev:
                for k, v in fields.items():
                    setattr(ev, k, v)
    except Exception:  # pragma: no cover
        log.exception("Could not update event %s", event_id)
