"""API tests: auth flow, CSRF, validation, cameras CRUD, settings, MediaMTX auth hook."""

from __future__ import annotations

from tapocloud.core.crypto import internal_token
from tapocloud.core.security import make_stream_token


def test_requires_auth(client):
    assert client.get("/api/cameras").status_code == 401
    assert client.get("/api/health").json()["ok"] is True


def test_setup_login_and_csrf(auth_client):
    c = auth_client
    status = c.get("/api/auth/status").json()
    assert status == {"setup_required": False, "authenticated": True, "username": "admin"}
    # Setup cannot run twice.
    assert c.post("/api/auth/setup", json={"username": "evil", "password": "0123456789ab"}).status_code == 409
    # Mutations without the CSRF header are rejected.
    token = c.headers.pop("X-CSRF-Token")
    assert c.patch("/api/settings", json={"drive.enabled": False}).status_code == 403
    c.headers["X-CSRF-Token"] = token
    assert c.patch("/api/settings", json={"drive.enabled": True}).status_code == 200


def test_login_wrong_password(client, auth_client):
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


CAM = {
    "name": "Salón",
    "ip": "192.168.1.20",
    "username": "camuser",
    "password": "camera-secret-pw",
    "onvif_enabled": False,
    "recording_mode": "continuous",
    "segment_minutes": 5,
}


def test_camera_crud_never_leaks_password(auth_client):
    c = auth_client
    r = c.post("/api/cameras", json=CAM)
    assert r.status_code == 201, r.text
    cam = r.json()
    assert cam["slug"] == "Salon"
    assert "camera-secret-pw" not in r.text
    assert cam["username_masked"] == "c•••••r"
    assert cam["rtsp_url_masked"].startswith("rtsp://camuser:***@192.168.1.20:554/stream1")

    listed = c.get("/api/cameras")
    assert "camera-secret-pw" not in listed.text and len(listed.json()) >= 1

    r = c.patch(
        f"/api/cameras/{cam['id']}",
        json={"recording_mode": "schedule", "schedule": [{"days": [5, 6], "start": "00:00", "end": "24:00"}]},
    )
    assert r.status_code == 200 and r.json()["recording_mode"] == "schedule"

    # Second camera with same name gets a unique slug.
    assert c.post("/api/cameras", json={**CAM, "ip": "192.168.1.21"}).json()["slug"] == "Salon_2"

    s = c.get(f"/api/cameras/{cam['id']}/stream?quality=sub").json()
    assert s["whep_url"].endswith(f"/cam{cam['id']}_sub/whep") and s["token"]

    assert c.delete(f"/api/cameras/{cam['id']}").status_code == 204
    assert c.get(f"/api/cameras/{cam['id']}").status_code == 404


def test_camera_validation(auth_client):
    c = auth_client
    assert c.post("/api/cameras", json={**CAM, "ip": "8.8.8.8"}).status_code == 422  # public IP refused
    assert c.post("/api/cameras", json={**CAM, "main_stream_path": "../../etc"}).status_code == 422
    assert c.post("/api/cameras", json={**CAM, "segment_minutes": 7}).status_code == 422
    assert c.post("/api/cameras", json={**CAM, "pre_event_seconds": 99}).status_code == 422
    bad_sched = {**CAM, "schedule": [{"days": [9], "start": "22:00", "end": "08:00"}]}
    assert c.post("/api/cameras", json=bad_sched).status_code == 422


def test_settings_validation(auth_client):
    c = auth_client
    assert c.patch("/api/settings", json={"storage.local_retention": "forever"}).status_code == 422
    assert c.patch("/api/settings", json={"unknown.key": 1}).status_code == 422
    r = c.patch("/api/settings", json={"storage.local_retention": "3d"})
    assert r.status_code == 200 and r.json()["storage.local_retention"] == "3d"


def test_record_without_camera_service(auth_client):
    c = auth_client
    cam = c.post("/api/cameras", json={**CAM, "ip": "192.168.1.40"}).json()
    r = c.post(f"/api/cameras/{cam['id']}/record", json={"action": "start"})
    assert r.status_code == 503  # service down -> clear error, not a crash


def test_mediamtx_auth_hook(client):
    url = "/internal/mediamtx/auth"
    base = {"ip": "192.168.1.50", "protocol": "webrtc", "query": ""}
    # internal consumer
    assert (
        client.post(
            url, json={**base, "action": "read", "path": "cam1_main", "user": "internal", "password": internal_token()}
        ).status_code
        == 200
    )
    # browser with a valid token for that camera (bearer or query)
    tok = make_stream_token(1)
    assert client.post(url, json={**base, "action": "read", "path": "cam1_sub", "token": tok}).status_code == 200
    assert client.post(url, json={**base, "action": "read", "path": "cam1_sub", "query": f"token={tok}"}).status_code == 200
    # token for camera 1 does not open camera 2
    assert client.post(url, json={**base, "action": "read", "path": "cam2_sub", "token": tok}).status_code == 401
    # publishing is never allowed, even internally
    assert (
        client.post(
            url, json={**base, "action": "publish", "path": "cam1_main", "user": "internal", "password": internal_token()}
        ).status_code
        == 401
    )
    assert client.post(url, json={**base, "action": "read", "path": "cam1_main"}).status_code == 401


def test_login_rate_limit_counts_only_failures(client, auth_client):
    from tapocloud.api.routers.auth import login_limiter

    login_limiter._hits.clear()
    client.cookies.clear()
    for _ in range(12):  # successful logins never lock the user out
        assert client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse-battery"}).status_code == 200
    for _ in range(10):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse-battery"}).status_code == 429
    login_limiter._hits.clear()


def test_deleting_camera_keeps_recordings(auth_client):
    from datetime import UTC, datetime

    from tapocloud.core.db import session_scope
    from tapocloud.core.models import Recording, UploadItem

    c = auth_client
    cam = c.post("/api/cameras", json={**CAM, "ip": "192.168.1.77", "name": "Temporal"}).json()
    with session_scope() as db:
        rec = Recording(
            camera_id=cam["id"], kind="continuous", status="complete", file_path="Temporal/x.mp4", started_at=datetime.now(UTC)
        )
        db.add(rec)
        db.flush()
        db.add(UploadItem(recording_id=rec.id))
        rec_id = rec.id
    assert c.delete(f"/api/cameras/{cam['id']}").status_code == 204
    with session_scope() as db:
        rec = db.get(Recording, rec_id)
        assert rec is not None and rec.camera_id is None
        assert db.query(UploadItem).filter_by(recording_id=rec_id).count() == 1
