"""Internal endpoints (not for browsers).

``/internal/mediamtx/auth`` — MediaMTX calls this for *every* client that
wants to read a stream (``authMethod: http``). Rules:

* Our own consumers (recorder, snapshots, motion) authenticate as user
  ``internal`` with the shared internal token.
* Browsers present a short-lived HMAC stream token (from
  ``GET /api/cameras/{id}/stream``) either as ``Authorization: Bearer`` (WHEP)
  or ``?token=`` (HLS). A token only opens the camera it was issued for.
* Publishing is never allowed: the only stream sources are our cameras.
"""

from __future__ import annotations

import hmac
import logging
import re
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from tapocloud.core.crypto import internal_token
from tapocloud.core.security import verify_stream_token

log = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", include_in_schema=False)

_PATH_RE = re.compile(r"^cam(\d+)_(main|sub)$")


class MediaMTXAuth(BaseModel):
    user: str | None = ""
    password: str | None = ""
    token: str | None = ""
    ip: str | None = ""
    action: str
    path: str | None = ""
    protocol: str | None = ""
    id: str | None = None
    query: str | None = ""


@router.post("/mediamtx/auth")
def mediamtx_auth(req: MediaMTXAuth) -> Response:
    if req.action not in ("read", "playback"):
        raise HTTPException(status_code=401)
    if req.user == "internal" and req.password and hmac.compare_digest(req.password, internal_token()):
        return Response(status_code=200)
    m = _PATH_RE.match(req.path or "")
    if not m:
        raise HTTPException(status_code=401)
    camera_id = int(m.group(1))
    candidates = [req.token or "", req.password or ""]
    candidates += parse_qs(req.query or "").get("token", [])
    if any(t and verify_stream_token(t, camera_id) for t in candidates):
        return Response(status_code=200)
    log.debug("Denied %s on %s from %s", req.protocol, req.path, req.ip)
    raise HTTPException(status_code=401)
