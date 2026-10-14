"""Google Drive API v3 client (REST over httpx).

Uses the official Drive REST API directly instead of
``google-api-python-client``: we need only four operations and full control
over *resumable uploads* (chunking, resuming after a reboot, bandwidth
limiting), which the generated client makes awkward.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import select

from tapocloud.core.db import session_scope
from tapocloud.core.models import DriveFolder
from tapocloud.drive.oauth import get_access_token

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
CHUNK_SIZE = 8 * 1024 * 1024  # must be a multiple of 256 KiB


class DriveError(RuntimeError):
    def __init__(self, message: str, *, transient: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.status = status


@dataclass
class UploadResult:
    file_id: str
    md5: str | None
    size: int | None


def _q_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def file_md5(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class DriveClient:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15))

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _request(self, method: str, url: str, *, retry_auth: bool = True, **kw) -> httpx.Response:
        token = await get_access_token()
        headers = {**kw.pop("headers", {}), "Authorization": f"Bearer {token}"}
        try:
            r = await self.http.request(method, url, headers=headers, **kw)
        except httpx.TransportError as exc:
            raise DriveError(f"Sin conexión con Google ({exc.__class__.__name__})", transient=True) from exc
        if r.status_code == 401 and retry_auth:
            await get_access_token(force_refresh=True)
            return await self._request(method, url, retry_auth=False, headers=headers, **kw)
        return r

    @staticmethod
    def _raise_for(r: httpx.Response, what: str) -> None:
        if r.status_code < 400:
            return
        transient = r.status_code in (408, 429) or r.status_code >= 500
        if r.status_code == 403 and any(k in r.text for k in ("rateLimitExceeded", "userRateLimitExceeded")):
            transient = True
        if r.status_code == 403 and "storageQuotaExceeded" in r.text:
            raise DriveError("Google Drive está lleno", transient=False, status=403)
        raise DriveError(f"{what}: HTTP {r.status_code} {r.text[:200]}", transient=transient, status=r.status_code)

    # -- account -----------------------------------------------------------
    async def about(self) -> dict:
        r = await self._request("GET", f"{API}/about", params={"fields": "user(emailAddress,displayName),storageQuota"})
        self._raise_for(r, "about")
        return r.json()

    # -- folders -----------------------------------------------------------
    async def ensure_folder_path(self, parts: list[str]) -> str:
        """Create (or find) ``parts[0]/parts[1]/...``; returns the deepest folder id.

        IDs are cached in ``drive_folders`` so a typical upload costs zero
        folder lookups.
        """
        parent = "root"
        path = ""
        for name in parts:
            path = f"{path}/{name}" if path else name
            with session_scope() as db:
                cached = db.get(DriveFolder, path)
                folder_id = cached.folder_id if cached else None
            if folder_id and await self._folder_exists(folder_id):
                parent = folder_id
                continue
            folder_id = await self._find_child_folder(parent, name) or await self._create_folder(parent, name)
            with session_scope() as db:
                db.merge(DriveFolder(path=path, folder_id=folder_id))
            parent = folder_id
        return parent

    async def _folder_exists(self, folder_id: str) -> bool:
        r = await self._request("GET", f"{API}/files/{folder_id}", params={"fields": "id,trashed"})
        if r.status_code == 404:
            return False
        self._raise_for(r, "get folder")
        return not r.json().get("trashed", False)

    async def _find_child_folder(self, parent: str, name: str) -> str | None:
        q = f"name = '{_q_escape(name)}' and mimeType = '{FOLDER_MIME}' and '{parent}' in parents and trashed = false"
        r = await self._request("GET", f"{API}/files", params={"q": q, "fields": "files(id)", "spaces": "drive"})
        self._raise_for(r, "find folder")
        files = r.json().get("files", [])
        return files[0]["id"] if files else None

    async def _create_folder(self, parent: str, name: str) -> str:
        meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent]}
        r = await self._request("POST", f"{API}/files", json=meta, params={"fields": "id"})
        self._raise_for(r, "create folder")
        log.info("Drive folder created: %s", name)
        return r.json()["id"]

    async def find_uploaded(self, recording_id: int) -> UploadResult | None:
        """Idempotency: find a file we already uploaded for this recording."""
        q = f"appProperties has {{ key='tapocloud_recording' and value='{recording_id}' }} and trashed = false"
        r = await self._request("GET", f"{API}/files", params={"q": q, "fields": "files(id,md5Checksum,size)"})
        self._raise_for(r, "search")
        files = r.json().get("files", [])
        if not files:
            return None
        f = files[0]
        return UploadResult(f["id"], f.get("md5Checksum"), int(f["size"]) if f.get("size") else None)

    async def delete_file(self, file_id: str) -> None:
        r = await self._request("DELETE", f"{API}/files/{file_id}")
        if r.status_code not in (204, 404):
            self._raise_for(r, "delete")

    # -- resumable upload --------------------------------------------------
    async def start_resumable(self, name: str, parent_id: str, size: int, app_props: dict[str, str]) -> str:
        meta = {"name": name, "parents": [parent_id], "mimeType": "video/mp4", "appProperties": app_props}
        r = await self._request(
            "POST",
            UPLOAD_API,
            params={"uploadType": "resumable", "fields": "id,md5Checksum,size"},
            json=meta,
            headers={"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
        )
        self._raise_for(r, "start upload")
        return r.headers["Location"]

    async def query_offset(self, session_uri: str, size: int) -> int | UploadResult | None:
        """Ask Google how much of an interrupted upload it has.

        Returns the next byte offset, an :class:`UploadResult` if it had
        actually finished, or ``None`` if the session expired (start over).
        """
        r = await self._request("PUT", session_uri, headers={"Content-Range": f"bytes */{size}", "Content-Length": "0"})
        if r.status_code in (200, 201):
            j = r.json()
            return UploadResult(j["id"], j.get("md5Checksum"), int(j["size"]) if j.get("size") else None)
        if r.status_code == 308:
            rng = r.headers.get("Range")
            return int(rng.split("-")[1]) + 1 if rng else 0
        if r.status_code in (404, 410):
            return None
        self._raise_for(r, "query upload")
        return None

    async def upload_chunks(
        self,
        session_uri: str,
        path: Path,
        offset: int,
        size: int,
        progress: Callable[[int], None] | None = None,
        limit_kbps: int = 0,
    ) -> UploadResult:
        loop = asyncio.get_running_loop()
        with open(path, "rb") as fh:
            while True:
                fh.seek(offset)
                chunk = await loop.run_in_executor(None, fh.read, CHUNK_SIZE)
                end = offset + len(chunk) - 1
                t0 = loop.time()
                r = await self._request(
                    "PUT", session_uri, content=chunk, headers={"Content-Range": f"bytes {offset}-{end}/{size}"}
                )
                if r.status_code in (200, 201):
                    j = r.json()
                    if progress:
                        progress(size)
                    return UploadResult(j["id"], j.get("md5Checksum"), int(j["size"]) if j.get("size") else None)
                if r.status_code == 308:
                    rng = r.headers.get("Range")
                    offset = int(rng.split("-")[1]) + 1 if rng else 0
                    if progress:
                        progress(offset)
                    if limit_kbps > 0:  # crude but effective bandwidth cap
                        min_dt = len(chunk) * 8 / 1000 / limit_kbps
                        await asyncio.sleep(max(0.0, min_dt - (loop.time() - t0)))
                    continue
                if r.status_code in (404, 410):
                    raise DriveError("Sesión de subida caducada", transient=True, status=r.status_code)
                self._raise_for(r, "upload chunk")
                raise DriveError(f"Respuesta inesperada de Google: HTTP {r.status_code}", transient=True, status=r.status_code)


async def list_recent_folders() -> list[str]:
    with session_scope() as db:
        return [f.path for f in db.scalars(select(DriveFolder).order_by(DriveFolder.path.desc()).limit(50))]
