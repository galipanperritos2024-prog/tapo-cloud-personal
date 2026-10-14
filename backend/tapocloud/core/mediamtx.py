"""Client for the MediaMTX control API (v3).

MediaMTX is the streaming relay: it keeps *one* RTSP connection per camera
stream and re-serves it as WebRTC (sub-second latency in the browser),
LL-HLS (fallback) and RTSP (for our internal consumers).

Paths are configured at runtime through the API, so camera passwords are
held in memory only and never written into ``mediamtx.yml``. The
camera-service re-registers every path on startup (MediaMTX restarts are
therefore harmless).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from tapocloud.config import get_settings

log = logging.getLogger(__name__)


class MediaMTXError(RuntimeError):
    pass


class MediaMTXClient:
    def __init__(self, base_url: str | None = None, timeout: float = 5.0) -> None:
        self.base = (base_url or get_settings().mediamtx_api_url).rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    # -- low level ---------------------------------------------------------
    def _req(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            return self._client.request(method, f"{self.base}{path}", **kw)
        except httpx.HTTPError as exc:
            raise MediaMTXError(f"MediaMTX unreachable: {exc.__class__.__name__}") from exc

    def healthy(self) -> bool:
        try:
            return self._req("GET", "/v3/paths/list?itemsPerPage=1").status_code == 200
        except MediaMTXError:
            return False

    # -- path configuration -----------------------------------------------
    def ensure_path(self, name: str, source_url: str, on_demand: bool) -> None:
        """Create or replace a relay path pulling from ``source_url``."""
        conf = {
            "source": source_url,
            "rtspTransport": "tcp",
            "sourceOnDemand": on_demand,
            "sourceOnDemandStartTimeout": "15s",
            "sourceOnDemandCloseAfter": "20s",
        }
        r = self._req("POST", f"/v3/config/paths/replace/{name}", json=conf)
        if r.status_code == 404:  # older MediaMTX without "replace": add instead
            r = self._req("POST", f"/v3/config/paths/add/{name}", json=conf)
        if r.status_code >= 400:
            raise MediaMTXError(f"Cannot configure path {name}: HTTP {r.status_code} {r.text[:200]}")

    def delete_path(self, name: str) -> None:
        r = self._req("DELETE", f"/v3/config/paths/delete/{name}")
        if r.status_code not in (200, 404):
            raise MediaMTXError(f"Cannot delete path {name}: HTTP {r.status_code}")

    def configured_paths(self) -> list[str]:
        r = self._req("GET", "/v3/config/paths/list?itemsPerPage=1000")
        if r.status_code != 200:
            return []
        return [item["name"] for item in r.json().get("items", [])]

    # -- runtime state -------------------------------------------------------
    def path_state(self, name: str) -> dict[str, Any] | None:
        """Runtime info: ``ready``, ``tracks``, ``bytesReceived``, ``readers``..."""
        r = self._req("GET", f"/v3/paths/get/{name}")
        if r.status_code != 200:
            return None
        return r.json()

    def list_states(self) -> list[dict[str, Any]]:
        r = self._req("GET", "/v3/paths/list?itemsPerPage=1000")
        return r.json().get("items", []) if r.status_code == 200 else []
