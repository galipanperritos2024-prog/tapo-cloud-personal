"""Cameras: CRUD, connection test, live stream tokens, PTZ, recording and snapshots."""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from tapocloud.api.deps import current_user, get_db
from tapocloud.api.schemas import CameraCreate, CameraOut, CameraTest, CameraUpdate, PtzCommand, RecordAction
from tapocloud.config import get_settings
from tapocloud.core import camera_service_client as cs
from tapocloud.core import ffmpeg
from tapocloud.core.crypto import decrypt, encrypt
from tapocloud.core.models import Camera, CameraCredentials
from tapocloud.core.naming import slugify
from tapocloud.core.rtsp import build_camera_url, mask_url, relay_path_name
from tapocloud.core.security import make_stream_token
from tapocloud.onvif.device import OnvifCamera
from tapocloud.onvif.soap import OnvifError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cameras", tags=["cameras"], dependencies=[Depends(current_user)])


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _get_camera(db: Session, camera_id: int) -> Camera:
    cam = db.scalar(select(Camera).options(selectinload(Camera.credentials)).where(Camera.id == camera_id))
    if cam is None:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    return cam


def _mask_username(u: str | None) -> str | None:
    if not u:
        return None
    return u[0] + "•" * max(1, len(u) - 2) + (u[-1] if len(u) > 1 else "")


def to_out(cam: Camera) -> CameraOut:
    out = CameraOut.model_validate(cam)
    user = cam.credentials.username if cam.credentials else None
    out.username_masked = _mask_username(user)
    out.rtsp_url_masked = mask_url(build_camera_url(cam.ip, cam.rtsp_port, cam.main_stream_path, user, "x" if user else None))
    return out


def _unique_slug(db: Session, name: str, exclude_id: int | None = None) -> str:
    base = slugify(name)
    slug, n = base, 2
    while True:
        other = db.scalar(select(Camera).where(Camera.slug == slug))
        if other is None or other.id == exclude_id:
            return slug
        slug, n = f"{base}_{n}", n + 1


def _credentials(cam: Camera) -> tuple[str | None, str | None]:
    if not cam.credentials:
        return None, None
    return cam.credentials.username, decrypt(cam.credentials.password_encrypted)


def onvif_inspect(host: str, port: int, username: str, password: str) -> dict[str, Any]:
    """Connect via ONVIF and summarise what the camera offers."""
    with OnvifCamera(host, port, username, password) as cam:
        info = cam.connect()
        return info.to_dict()


async def _apply_onvif(cam: Camera, username: str, password: str) -> str | None:
    """Fill model/PTZ info from ONVIF. Returns a warning message on failure."""
    try:
        info = await run_in_threadpool(onvif_inspect, cam.ip, cam.onvif_port, username, password)
    except OnvifError as exc:
        cam.onvif_enabled = False
        cam.ptz_enabled = False
        if exc.auth:
            return "ONVIF rechazó las credenciales; PTZ y eventos ONVIF desactivados."
        return f"ONVIF no disponible ({exc}); PTZ y eventos ONVIF desactivados."
    cam.onvif_enabled = True
    cam.manufacturer = info.get("manufacturer") or cam.manufacturer
    cam.model = info.get("model") or cam.model
    cam.firmware = info.get("firmware") or cam.firmware
    ptz = info.get("ptz") or {}
    cam.ptz_caps = ptz
    cam.ptz_enabled = bool(ptz.get("pan_tilt") or ptz.get("zoom"))
    return None


# PTZ connections are reused: an ONVIF "connect" costs ~5 round trips, far too
# slow for press-and-hold controls.
_ptz_cache: dict[int, tuple[tuple, OnvifCamera]] = {}
_ptz_lock = threading.Lock()


def _ptz_client(cam_id: int, host: str, port: int, user: str, pw: str) -> OnvifCamera:
    key = (host, port, user, pw)
    with _ptz_lock:
        cached = _ptz_cache.get(cam_id)
        if cached and cached[0] == key:
            return cached[1]
        if cached:
            cached[1].close()
        client = OnvifCamera(host, port, user, pw)
        client.connect(with_streams=False)
        _ptz_cache[cam_id] = (key, client)
        return client


def _drop_ptz(cam_id: int) -> None:
    with _ptz_lock:
        cached = _ptz_cache.pop(cam_id, None)
        if cached:
            cached[1].close()


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------
@router.get("", response_model=list[CameraOut])
def list_cameras(db: Session = Depends(get_db)) -> list[CameraOut]:
    cams = db.scalars(select(Camera).options(selectinload(Camera.credentials)).order_by(Camera.id)).all()
    return [to_out(c) for c in cams]


@router.post("/test")
async def test_camera(body: CameraTest) -> dict:
    """Try the connection *without saving*: RTSP main/sub + ONVIF."""
    main_url = build_camera_url(body.ip, body.rtsp_port, body.main_stream_path, body.username, body.password)
    main = await run_in_threadpool(ffmpeg.probe, main_url, 12)
    result: dict[str, Any] = {"main": main.to_dict(), "sub": None, "onvif": None}
    if body.sub_stream_path and main.available:
        sub_url = build_camera_url(body.ip, body.rtsp_port, body.sub_stream_path, body.username, body.password)
        result["sub"] = (await run_in_threadpool(ffmpeg.probe, sub_url, 12)).to_dict()
    if body.onvif:
        try:
            info = await run_in_threadpool(onvif_inspect, body.ip, body.onvif_port, body.username, body.password)
            result["onvif"] = {
                "ok": True,
                "manufacturer": info.get("manufacturer"),
                "model": info.get("model"),
                "firmware": info.get("firmware"),
                "ptz": info.get("ptz"),
                "events": bool(info.get("events_xaddr")),
                "profiles": [
                    {k: p[k] for k in ("token", "name", "encoding", "width", "height", "fps")} for p in info.get("profiles", [])
                ],
            }
        except OnvifError as exc:
            result["onvif"] = {"ok": False, "error": str(exc), "auth": exc.auth}
    result["ok"] = main.available
    return result


@router.post("", response_model=CameraOut, status_code=201)
async def create_camera(body: CameraCreate, response: Response, db: Session = Depends(get_db)) -> CameraOut:
    data = body.model_dump(exclude={"username", "password"})
    data["recording_mode"] = body.recording_mode.value
    data["motion_source"] = body.motion_source.value
    cam = Camera(**data, slug=_unique_slug(db, body.name))
    cam.credentials = CameraCredentials(username=body.username, password_encrypted=encrypt(body.password))
    if body.onvif_enabled:
        warning = await _apply_onvif(cam, body.username, body.password)
        if warning:
            response.headers["X-Warning"] = warning.encode("ascii", "ignore").decode()
    db.add(cam)
    db.flush()
    log.info("Camera added: %s (%s)", cam.name, cam.ip)
    db.commit()
    await cs.reload()
    return to_out(cam)


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: int, db: Session = Depends(get_db)) -> CameraOut:
    return to_out(_get_camera(db, camera_id))


@router.patch("/{camera_id}", response_model=CameraOut)
async def update_camera(camera_id: int, body: CameraUpdate, db: Session = Depends(get_db)) -> CameraOut:
    cam = _get_camera(db, camera_id)
    changes = body.model_dump(exclude_unset=True)
    username, password = changes.pop("username", None), changes.pop("password", None)
    for key, value in changes.items():
        if value is None and key not in ("sub_stream_path",):
            continue
        if hasattr(value, "value"):
            value = value.value
        setattr(cam, key, value)
    if changes.get("name"):
        # New recordings go to the folder of the new name; existing files stay where they are.
        cam.slug = _unique_slug(db, changes["name"], cam.id)
    if username or password:
        cur_user, cur_pw = _credentials(cam)
        new_user, new_pw = username or cur_user, password or cur_pw
        if not new_user or not new_pw:
            raise HTTPException(status_code=422, detail="Usuario y contraseña requeridos")
        if cam.credentials:
            cam.credentials.username = new_user
            cam.credentials.password_encrypted = encrypt(new_pw)
        else:
            cam.credentials = CameraCredentials(username=new_user, password_encrypted=encrypt(new_pw))
        _drop_ptz(cam.id)
    if changes.get("onvif_enabled") or ((username or password or "ip" in changes) and cam.onvif_enabled):
        u, p = _credentials(cam)
        if u and p:
            await _apply_onvif(cam, u, p)
    db.commit()
    await cs.reload()
    return to_out(cam)


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(camera_id: int, db: Session = Depends(get_db)) -> Response:
    """Removes the camera and its events. Its recordings are kept (and still uploaded)."""
    cam = _get_camera(db, camera_id)
    db.delete(cam)
    db.commit()
    _drop_ptz(camera_id)
    await cs.reload()
    log.info("Camera deleted: %s", camera_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------
# Live view
# --------------------------------------------------------------------------
@router.get("/{camera_id}/stream")
def stream_info(camera_id: int, request: Request, quality: str = "sub", db: Session = Depends(get_db)) -> dict:
    """URLs + short-lived token for the browser player (WebRTC/WHEP, HLS fallback)."""
    cam = _get_camera(db, camera_id)
    if quality not in ("main", "sub"):
        raise HTTPException(status_code=422, detail="quality debe ser main o sub")
    if quality == "sub" and not cam.sub_stream_path:
        quality = "main"
    s = get_settings()
    host = request.url.hostname or "localhost"
    path = relay_path_name(cam.id, quality)
    webrtc = s.public_webrtc_url.format(host=host).rstrip("/")
    hls = s.public_hls_url.format(host=host).rstrip("/")
    return {
        "quality": quality,
        "path": path,
        "whep_url": f"{webrtc}/{path}/whep",
        "hls_url": f"{hls}/{path}/index.m3u8",
        "token": make_stream_token(cam.id, ttl_seconds=600),
        "expires_in": 600,
    }


@router.get("/{camera_id}/preview.jpg")
async def preview(camera_id: int, db: Session = Depends(get_db)) -> Response:
    _get_camera(db, camera_id)
    try:
        r = await cs.call("GET", f"/cameras/{camera_id}/preview", raw=True, timeout=20)
    except cs.CameraServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except cs.CameraServiceError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return Response(content=r.content, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=5"})


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------
@router.post("/{camera_id}/record")
async def record(camera_id: int, body: RecordAction, db: Session = Depends(get_db)) -> dict:
    _get_camera(db, camera_id)
    try:
        return await cs.call("POST", f"/cameras/{camera_id}/record", json=body.model_dump())
    except cs.CameraServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except cs.CameraServiceError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.post("/{camera_id}/snapshot")
async def snapshot(camera_id: int, db: Session = Depends(get_db)) -> Response:
    cam = _get_camera(db, camera_id)
    try:
        r = await cs.call("POST", f"/cameras/{camera_id}/snapshot", raw=True, timeout=30)
    except cs.CameraServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except cs.CameraServiceError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    fname = r.headers.get("X-Snapshot-Path", "").rsplit("/", 1)[-1] or f"{cam.slug}.jpg"
    return Response(
        content=r.content, media_type="image/jpeg", headers={"Content-Disposition": f'inline; filename="{cam.slug}_{fname}"'}
    )


@router.post("/{camera_id}/ptz")
async def ptz(camera_id: int, cmd: PtzCommand, db: Session = Depends(get_db)) -> dict:
    cam = _get_camera(db, camera_id)
    if not cam.ptz_enabled:
        raise HTTPException(status_code=409, detail="Esta cámara no tiene PTZ (o ONVIF está desactivado)")
    user, pw = _credentials(cam)

    def run() -> dict:
        client = _ptz_client(cam.id, cam.ip, cam.onvif_port, user or "", pw or "")
        if cmd.action == "move":
            client.continuous_move(cmd.pan, cmd.tilt, cmd.zoom if cam.ptz_caps.get("zoom") else 0, cmd.duration)
        elif cmd.action == "stop":
            client.stop()
        elif cmd.action == "goto_preset":
            if not cmd.preset:
                raise HTTPException(status_code=422, detail="preset requerido")
            client.goto_preset(cmd.preset)
        elif cmd.action == "set_preset":
            return {"ok": True, "preset": client.set_preset(cmd.preset_name or "Preset", cmd.preset)}
        return {"ok": True}

    try:
        return await run_in_threadpool(run)
    except OnvifError as exc:
        _drop_ptz(cam.id)
        raise HTTPException(status_code=502, detail=f"PTZ: {exc}") from exc


@router.get("/{camera_id}/ptz/presets")
async def ptz_presets(camera_id: int, db: Session = Depends(get_db)) -> list[dict]:
    cam = _get_camera(db, camera_id)
    if not cam.ptz_enabled:
        return []
    user, pw = _credentials(cam)
    try:
        return await run_in_threadpool(lambda: _ptz_client(cam.id, cam.ip, cam.onvif_port, user or "", pw or "").get_presets())
    except OnvifError as exc:
        _drop_ptz(cam.id)
        raise HTTPException(status_code=502, detail=f"PTZ: {exc}") from exc


@router.post("/{camera_id}/onvif/refresh", response_model=CameraOut)
async def onvif_refresh(camera_id: int, db: Session = Depends(get_db)) -> CameraOut:
    cam = _get_camera(db, camera_id)
    user, pw = _credentials(cam)
    if not (user and pw):
        raise HTTPException(status_code=409, detail="La cámara no tiene credenciales")
    warning = await _apply_onvif(cam, user, pw)
    db.commit()
    if warning:
        raise HTTPException(status_code=502, detail=warning)
    _drop_ptz(cam.id)
    await cs.reload()
    return to_out(cam)
