"""Backend -> camera-service internal HTTP client."""

from __future__ import annotations

from typing import Any

import httpx

from tapocloud.config import get_settings
from tapocloud.core.crypto import internal_token


class CameraServiceUnavailable(RuntimeError):
    pass


class CameraServiceError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def call(method: str, path: str, *, timeout: float = 30, raw: bool = False, **kw: Any) -> Any:
    url = f"{get_settings().camera_service_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request(method, url, headers={"X-Internal-Token": internal_token()}, **kw)
    except httpx.HTTPError as exc:
        raise CameraServiceUnavailable("El servicio de cámaras no está en ejecución") from exc
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise CameraServiceError(r.status_code, str(detail))
    return r if raw else r.json()


async def reload() -> None:
    """Ask the camera service to pick up configuration changes now (best effort)."""
    try:
        await call("POST", "/reload", timeout=3)
    except (CameraServiceUnavailable, CameraServiceError):
        pass
