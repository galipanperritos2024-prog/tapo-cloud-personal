"""System status, logs, runtime settings and network discovery."""

from __future__ import annotations

from collections import deque
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from tapocloud.api.deps import current_user, get_db
from tapocloud.api.schemas import DiscoveryIn
from tapocloud.config import get_settings
from tapocloud.core import camera_service_client as cs
from tapocloud.core import settings_store
from tapocloud.core.mediamtx import MediaMTXClient
from tapocloud.core.models import Camera
from tapocloud.drive.oauth import connection_info
from tapocloud.drive.uploader import uploader
from tapocloud.onvif.discovery import discover
from tapocloud.system.health import db_metrics, system_metrics

router = APIRouter(prefix="/api", tags=["system"], dependencies=[Depends(current_user)])


@router.get("/system/status")
async def system_status() -> dict:
    metrics = await run_in_threadpool(system_metrics)
    dbm = await run_in_threadpool(db_metrics)
    try:
        camsvc: dict[str, Any] = await cs.call("GET", "/status", timeout=5)
        camsvc["running"] = True
    except (cs.CameraServiceUnavailable, cs.CameraServiceError) as exc:
        camsvc = {"running": False, "error": str(exc)}
    mtx = MediaMTXClient()
    try:
        mediamtx_ok = await run_in_threadpool(mtx.healthy)
    finally:
        mtx.close()
    workers = camsvc.get("cameras", [])
    jobs = [j for w in workers for j in (w.get("jobs") or {}).values() if j]
    return {
        **metrics,
        **dbm,
        "services": {
            "backend": True,
            "camera_service": camsvc.get("running", False),
            "mediamtx": mediamtx_ok,
            "google_drive": connection_info()["connected"],
            "uploader_online": uploader.online,
        },
        "streams_active": sum(1 for w in workers if w.get("status") == "online"),
        "recordings_active": sum(
            1 for w in workers if (w.get("jobs") or {}).get("continuous", {}) and w["jobs"]["continuous"].get("running")
        )
        + sum(1 for w in workers if w.get("manual_recording")),
        "ffmpeg_processes": sum(1 for j in jobs if j.get("running") and j.get("pid")),
        "camera_workers": workers,
        "current_upload": uploader.current,
    }


@router.get("/system/logs")
def logs(file: str = Query("backend", pattern="^(backend|camera-service)$"), lines: int = Query(200, ge=10, le=2000)) -> dict:
    path = get_settings().logs_dir / f"{file}.log"
    if not path.exists():
        return {"lines": []}
    with open(path, encoding="utf-8", errors="replace") as fh:
        tail = deque(fh, maxlen=lines)
    return {"lines": [line.rstrip("\n") for line in tail]}


# -- settings ---------------------------------------------------------------
EDITABLE: dict[str, type | tuple] = {
    "drive.enabled": bool,
    "drive.root_folder": str,
    "drive.upload_kinds": list,
    "drive.max_parallel_uploads": int,
    "drive.bandwidth_limit_kbps": int,
    "storage.local_retention": str,
    "storage.rotate_when_drive_disabled": bool,
    "storage.min_free_gb": (int, float),
    "recording.default_segment_minutes": int,
    "recording.manual_max_minutes": int,
    "recording.event_max_clip_minutes": int,
    "discovery.allow_subnet_scan": bool,
    "ui.language": str,
}


def _validate_setting(key: str, value: Any) -> Any:
    expected = EDITABLE[key]
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise HTTPException(status_code=422, detail=f"{key}: tipo inválido")
    if key == "storage.local_retention" and value not in settings_store.RETENTION_CHOICES:
        raise HTTPException(status_code=422, detail=f"Retención válida: {list(settings_store.RETENTION_CHOICES)}")
    if key == "drive.upload_kinds" and not set(value) <= {"continuous", "event", "manual"}:
        raise HTTPException(status_code=422, detail="Tipos válidos: continuous, event, manual")
    if key == "drive.root_folder" and (not value.strip() or "/" in value or len(value) > 64):
        raise HTTPException(status_code=422, detail="Nombre de carpeta inválido")
    if key == "drive.max_parallel_uploads" and not 1 <= value <= 4:
        raise HTTPException(status_code=422, detail="Subidas paralelas: 1-4")
    if key == "recording.default_segment_minutes" and value not in (1, 5, 10, 15, 30, 60):
        raise HTTPException(status_code=422, detail="Segmento: 1, 5, 10, 15, 30 o 60 minutos")
    if key in ("recording.manual_max_minutes", "recording.event_max_clip_minutes") and not 1 <= value <= 240:
        raise HTTPException(status_code=422, detail="Duración: 1-240 minutos")
    if isinstance(value, int | float) and not isinstance(value, bool) and value < 0:
        raise HTTPException(status_code=422, detail=f"{key}: no puede ser negativo")
    return value


@router.get("/settings")
def get_settings_api(db: Session = Depends(get_db)) -> dict:
    values = settings_store.all_public(db)
    return {k: values.get(k) for k in EDITABLE} | {"retention_choices": list(settings_store.RETENTION_CHOICES)}


@router.patch("/settings")
async def patch_settings(body: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    unknown = set(body) - set(EDITABLE)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Ajustes desconocidos: {sorted(unknown)}")
    for k, v in body.items():
        settings_store.set(db, k, _validate_setting(k, v))
    db.commit()
    uploader.wake()
    await cs.reload()
    return get_settings_api(db)


# -- discovery ----------------------------------------------------------------
@router.post("/discovery/scan")
async def discovery_scan(body: DiscoveryIn, db: Session = Depends(get_db)) -> dict:
    allow_scan = bool(settings_store.get(db, "discovery.allow_subnet_scan"))
    devices = await run_in_threadpool(discover, body.subnet_scan and allow_scan, get_settings().scan_networks)
    known = set(db.scalars(select(Camera.ip)))
    items = []
    for d in devices:
        item = d.to_dict()
        item["already_added"] = d.ip in known
        items.append(item)
    return {"devices": items, "subnet_scan": body.subnet_scan and allow_scan}
