"""Test fixtures: every test session gets an isolated, throw-away data directory."""

from __future__ import annotations

import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="tapocloud-test-")
os.environ["TAPOCLOUD_DATA_DIR"] = _tmp
os.environ.setdefault("TAPOCLOUD_LOG_LEVEL", "WARNING")
# Unreachable on purpose: API tests must not depend on the other services.
os.environ["TAPOCLOUD_CAMERA_SERVICE_PORT"] = "1"
os.environ["TAPOCLOUD_MEDIAMTX_API_URL"] = "http://127.0.0.1:1"


@pytest.fixture(scope="session")
def data_dir() -> str:
    return _tmp


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from tapocloud.api.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_client(client):
    """A client logged in as admin, with the CSRF header pre-set."""
    from tapocloud.core.security import CSRF_COOKIE

    status = client.get("/api/auth/status").json()
    if status["setup_required"]:
        r = client.post("/api/auth/setup", json={"username": "admin", "password": "correct-horse-battery"})
    else:
        r = client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse-battery"})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = client.cookies.get(CSRF_COOKIE)
    return client
