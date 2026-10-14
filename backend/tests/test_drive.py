"""Upload pipeline against a fake Google Drive (httpx.MockTransport).

Covers: folder tree creation, chunked resumable upload, resuming after a
network failure mid-upload, MD5 verification, and the retention guarantee.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tapocloud.config import get_settings
from tapocloud.core.db import init_db, session_scope
from tapocloud.core.models import Camera, Recording, UploadItem, UploadStatus


class FakeDrive:
    """Just enough of Drive v3 for the uploader."""

    def __init__(self, fail_on_chunk: int | None = None) -> None:
        self.folders: dict[str, tuple[str, str]] = {}  # id -> (name, parent)
        self.files: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.chunk_puts = 0
        self.fail_on_chunk = fail_on_chunk

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/drive/v3/files" in url and request.method == "GET" and "q=" in url:
            q = request.url.params["q"]
            if "appProperties" in q:
                rid = re.search(r"value='(\d+)'", q).group(1)
                hits = [f for f in self.files.values() if f["appProperties"].get("tapocloud_recording") == rid]
                return httpx.Response(200, json={"files": hits})
            name = re.search(r"name = '([^']+)'", q).group(1)
            parent = re.search(r"'([^']+)' in parents", q).group(1)
            hits = [{"id": fid} for fid, (n, p) in self.folders.items() if n == name and p == parent]
            return httpx.Response(200, json={"files": hits})
        if re.search(r"/drive/v3/files/[^/?]+", url) and request.method == "GET":
            fid = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"id": fid, "trashed": False}) if fid in self.folders else httpx.Response(404)
        if url.startswith("https://www.googleapis.com/drive/v3/files") and request.method == "POST":
            meta = json.loads(request.content)
            fid = f"folder{len(self.folders) + 1}"
            self.folders[fid] = (meta["name"], meta["parents"][0])
            return httpx.Response(200, json={"id": fid})
        if "uploadType=resumable" in url and request.method == "POST":
            sid = f"s{len(self.sessions) + 1}"
            self.sessions[sid] = {
                "meta": json.loads(request.content),
                "size": int(request.headers["X-Upload-Content-Length"]),
                "data": bytearray(),
            }
            return httpx.Response(200, headers={"Location": f"https://upload.fake/{sid}"})
        if url.startswith("https://upload.fake/") and request.method == "PUT":
            sess = self.sessions[url.rsplit("/", 1)[-1]]
            rng = request.headers["Content-Range"]
            if rng.startswith("bytes */"):  # status query
                n = len(sess["data"])
                return httpx.Response(308, headers={"Range": f"bytes=0-{n - 1}"} if n else {})
            self.chunk_puts += 1
            if self.fail_on_chunk == self.chunk_puts:
                raise httpx.ConnectError("network down")
            start = int(rng.split(" ")[1].split("-")[0])
            assert start == len(sess["data"]), "client must resume at the confirmed offset"
            sess["data"] += request.content
            if len(sess["data"]) < sess["size"]:
                return httpx.Response(308, headers={"Range": f"bytes=0-{len(sess['data']) - 1}"})
            fid = f"file{len(self.files) + 1}"
            self.files[fid] = {
                "id": fid,
                "md5Checksum": hashlib.md5(sess["data"]).hexdigest(),
                "size": str(len(sess["data"])),
                "appProperties": sess["meta"]["appProperties"],
                "name": sess["meta"]["name"],
                "parents": sess["meta"]["parents"],
            }
            return httpx.Response(200, json=self.files[fid])
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(f"unexpected request {request.method} {url}")


@pytest.fixture()
def recording(tmp_path):
    init_db()
    s = get_settings()
    rel = "Patio/2026/10/14/23-00-00.mp4"
    path = s.storage_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(range(256)) * 2600)  # ~650 KB -> 3 chunks of 256 KiB
    with session_scope() as db:
        cam = Camera(name="Patio", slug="Patio_t", ip="192.168.1.9")
        db.add(cam)
        db.flush()
        rec = Recording(camera_id=cam.id, kind="continuous", status="complete", file_path=rel,
                        started_at=datetime.now(UTC) - timedelta(minutes=5), ended_at=datetime.now(UTC), size_bytes=path.stat().st_size)  # fmt: skip
        db.add(rec)
        db.flush()
        item = UploadItem(recording_id=rec.id)
        db.add(item)
        db.flush()
        ids = (rec.id, item.id)
        cam_id = cam.id
    yield ids
    with session_scope() as db:
        db.delete(db.get(Recording, ids[0]))  # cascades to upload_queue
        db.delete(db.get(Camera, cam_id))


async def _upload(monkeypatch, fake: FakeDrive, item_id: int):
    from tapocloud.drive import client as drive_client
    from tapocloud.drive import uploader as up

    async def token(force_refresh: bool = False) -> str:
        return "test-token"

    monkeypatch.setattr(drive_client, "get_access_token", token)
    monkeypatch.setattr(drive_client, "CHUNK_SIZE", 256 * 1024)
    client = drive_client.DriveClient()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    u = up.Uploader()
    await u._upload_one(client, item_id, "TapoCloud", 0)
    await client.aclose()


def test_upload_resumes_after_network_failure_and_verifies_md5(monkeypatch, recording):
    rec_id, item_id = recording
    fake = FakeDrive(fail_on_chunk=2)  # connection drops on the 2nd chunk

    asyncio.run(_upload(monkeypatch, fake, item_id))
    with session_scope() as db:
        item = db.get(UploadItem, item_id)
        assert item.status == UploadStatus.RETRY.value  # transient -> retry later
        assert item.session_uri  # resumable session kept
        assert db.get(Recording, rec_id).uploaded_at is None  # not deletable yet
        item.next_attempt_at = datetime.now(UTC)

    fake.fail_on_chunk = None
    asyncio.run(_upload(monkeypatch, fake, item_id))  # resumes at the confirmed offset (asserted in FakeDrive)

    with session_scope() as db:
        item = db.get(UploadItem, item_id)
        rec = db.get(Recording, rec_id)
        assert item.status == UploadStatus.COMPLETED.value, item.last_error
        assert item.drive_path == "TapoCloud/Patio/2026/10/14/23-00-00.mp4"
        assert rec.uploaded_at is not None and rec.drive_file_id
    # Folder tree TapoCloud/Patio/2026/10/14 created exactly once.
    names = sorted(n for n, _ in fake.folders.values())
    assert names == sorted(["TapoCloud", "Patio", "2026", "10", "14"])
    local = (get_settings().storage_dir / "Patio/2026/10/14/23-00-00.mp4").read_bytes()
    assert next(iter(fake.files.values()))["md5Checksum"] == hashlib.md5(local).hexdigest()


def test_upload_is_idempotent(monkeypatch, recording):
    """A file already in Drive for this recording (crash after upload) is not re-uploaded."""
    rec_id, item_id = recording
    fake = FakeDrive()
    data = (get_settings().storage_dir / "Patio/2026/10/14/23-00-00.mp4").read_bytes()
    fake.files["existing"] = {"id": "existing", "md5Checksum": hashlib.md5(data).hexdigest(), "size": str(len(data)),
                              "appProperties": {"tapocloud_recording": str(rec_id)}}  # fmt: skip
    asyncio.run(_upload(monkeypatch, fake, item_id))
    assert fake.chunk_puts == 0
    with session_scope() as db:
        assert db.get(UploadItem, item_id).status == UploadStatus.COMPLETED.value
        assert db.get(Recording, rec_id).drive_file_id == "existing"
