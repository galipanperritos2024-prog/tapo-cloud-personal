"""Safety rails for every EXPERIMENTAL operation.

* :func:`resolve_own_camera` — the target must be a camera already registered
  in Tapo Cloud Personal (i.e. one the user added with its own credentials)
  and must have a private/link-local IP address.
* :func:`audit` — append-only JSON-lines log of every lab call (without secrets).
* :func:`experimental` — decorator that refuses to run when the lab is
  disabled and audits the call.
"""

from __future__ import annotations

import functools
import ipaddress
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from tapo_lab import is_enabled
from tapocloud.config import get_settings
from tapocloud.core.db import session_scope
from tapocloud.core.models import Camera

log = logging.getLogger("tapo_lab")


class LabDisabled(RuntimeError):
    pass


class NotOwnDevice(PermissionError):
    pass


def resolve_own_camera(camera_id: int) -> tuple[str, str]:
    """Return ``(ip, name)`` of a registered camera on a private network."""
    with session_scope() as db:
        cam = db.get(Camera, camera_id)
        if cam is None:
            raise NotOwnDevice("La cámara no está registrada en Tapo Cloud Personal")
        ip, name = cam.ip, cam.name
    addr = ipaddress.ip_address(ip)
    if not (addr.is_private or addr.is_link_local) or addr.is_loopback:
        raise NotOwnDevice("tapo-lab solo opera sobre dispositivos de la red local")
    return ip, name


def audit(action: str, camera_id: int | None, ok: bool, detail: str = "") -> None:
    path = get_settings().logs_dir / "tapo-lab-audit.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "action": action,
        "camera_id": camera_id,
        "ok": ok,
        "detail": detail[:300],
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def experimental(action: str) -> Callable:
    """Decorator for lab operations whose first argument is ``camera_id``."""

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(camera_id: int, *args: Any, **kwargs: Any) -> Any:
            if not is_enabled():
                raise LabDisabled("tapo-lab está desactivado (TAPOCLOUD_TAPO_LAB_ENABLED=false)")
            try:
                resolve_own_camera(camera_id)  # rejected attempts are audited too
                result = fn(camera_id, *args, **kwargs)
            except Exception as exc:
                audit(action, camera_id, False, f"{exc.__class__.__name__}: {exc}")
                raise
            audit(action, camera_id, True)
            return result

        wrapper.experimental = True  # type: ignore[attr-defined]
        return wrapper

    return deco
