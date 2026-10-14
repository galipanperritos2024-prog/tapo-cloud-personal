"""RTSP URL helpers.

Tapo cameras expose::

    rtsp://<user>:<pass>@<ip>:554/stream1   main stream (up to 2K/4MP, H.264/H.265)
    rtsp://<user>:<pass>@<ip>:554/stream2   sub stream  (360p/720p)

The user/password are the *camera account* created in the Tapo app
(Advanced settings > Camera account), not the TP-Link cloud login.
Credentials are URL-encoded and these URLs are only ever built in memory
and handed to MediaMTX/FFmpeg — never logged (see ``logging_setup.redact``).
"""

from __future__ import annotations

from urllib.parse import quote

from tapocloud.config import get_settings
from tapocloud.core.crypto import internal_token


def build_camera_url(ip: str, port: int, path: str, username: str | None, password: str | None) -> str:
    host = f"[{ip}]" if ":" in ip else ip
    auth = ""
    if username:
        auth = quote(username, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        auth += "@"
    return f"rtsp://{auth}{host}:{port}/{path.lstrip('/')}"


def mask_url(url: str) -> str:
    """For display: ``rtsp://admin:***@192.168.1.20:554/stream1``."""
    from tapocloud.logging_setup import redact

    return redact(url)


def relay_path_name(camera_id: int, quality: str = "main") -> str:
    """Name of the MediaMTX path that relays a camera stream."""
    return f"cam{camera_id}_{quality}"


def relay_url(camera_id: int, quality: str = "main") -> str:
    """Internal RTSP URL of the relayed stream (authenticated with the internal token).

    All internal consumers (recorder, snapshots, motion detector) read from the
    relay so the camera only ever serves *one* connection per stream.
    """
    base = get_settings().mediamtx_rtsp_url.rstrip("/")
    scheme, rest = base.split("://", 1)
    return f"{scheme}://internal:{quote(internal_token(), safe='')}@{rest}/{relay_path_name(camera_id, quality)}"
