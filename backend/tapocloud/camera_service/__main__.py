"""Entry point of the camera service.

Exposes a tiny *internal* HTTP API (bound to 127.0.0.1 by default and
protected by the shared internal token) that the backend uses to forward user
actions: manual record, snapshot, preview, reload.
"""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from tapocloud.camera_service.supervisor import Supervisor
from tapocloud.config import get_settings
from tapocloud.core import settings_store
from tapocloud.core.crypto import internal_token
from tapocloud.core.db import init_db, session_scope
from tapocloud.logging_setup import setup_logging

log = logging.getLogger("camera_service")
supervisor: Supervisor | None = None


def require_internal(x_internal_token: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_internal_token, internal_token()):
        raise HTTPException(status_code=401, detail="unauthorized")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global supervisor
    init_db()
    supervisor = Supervisor()
    supervisor.start()
    log.info("Camera service started")
    yield
    supervisor.stop()
    supervisor.join(timeout=30)
    log.info("Camera service stopped")


app = FastAPI(
    title="Tapo Cloud camera-service (internal)",
    lifespan=lifespan,
    dependencies=[Depends(require_internal)],
    docs_url=None,
    redoc_url=None,
)


def _worker(camera_id: int):
    w = supervisor.get(camera_id) if supervisor else None
    if w is None:
        raise HTTPException(status_code=404, detail="Cámara no gestionada por el servicio")
    return w


class RecordRequest(BaseModel):
    action: str  # "start" | "stop"
    max_minutes: int | None = None


@app.get("/status")
def status() -> dict:
    return supervisor.status() if supervisor else {}


@app.post("/reload")
def reload() -> dict:
    if supervisor:
        supervisor.reload()
    return {"ok": True}


@app.post("/cameras/{camera_id}/record")
async def record(camera_id: int, req: RecordRequest) -> dict:
    w = _worker(camera_id)
    if req.action == "start":
        max_minutes = req.max_minutes
        if not max_minutes:
            with session_scope() as db:
                max_minutes = int(settings_store.get(db, "recording.manual_max_minutes"))
        try:
            rec_id = await run_in_threadpool(w.start_manual, max_minutes)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"recording_id": rec_id, "recording": True}
    rec_id = await run_in_threadpool(w.stop_manual)
    return {"recording_id": rec_id, "recording": False}


@app.post("/cameras/{camera_id}/snapshot")
async def snapshot(camera_id: int, save: bool = True) -> Response:
    w = _worker(camera_id)
    try:
        data, rel = await run_in_threadpool(w.snapshot, save)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=data, media_type="image/jpeg", headers={"X-Snapshot-Path": rel or ""})


@app.get("/cameras/{camera_id}/preview")
async def preview(camera_id: int) -> Response:
    w = _worker(camera_id)
    try:
        data = await run_in_threadpool(w.preview)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def main() -> None:
    s = get_settings()
    setup_logging(s.log_level, s.logs_dir, "camera-service")
    uvicorn.run(app, host=s.camera_service_host, port=s.camera_service_port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
