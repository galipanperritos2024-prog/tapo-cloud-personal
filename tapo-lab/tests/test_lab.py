"""tapo-lab safety rails: disabled by default, own LAN devices only, audited, whitelisted."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TAPOCLOUD_DATA_DIR", tempfile.mkdtemp(prefix="tapolab-test-"))

import pytest  # noqa: E402

from tapocloud.config import get_settings  # noqa: E402
from tapocloud.core.db import init_db, session_scope  # noqa: E402
from tapocloud.core.models import Camera  # noqa: E402


@pytest.fixture()
def cams():
    init_db()
    with session_scope() as db:
        lan = Camera(name="LAN", slug="LAN_lab", ip="192.168.50.10")
        loop = Camera(name="Loop", slug="Loop_lab", ip="127.0.0.1")
        db.add_all([lan, loop])
        db.flush()
        ids = (lan.id, loop.id)
    yield ids
    with session_scope() as db:
        for i in ids:
            db.delete(db.get(Camera, i))


def test_disabled_by_default(cams, monkeypatch):
    from tapo_lab import discovery
    from tapo_lab.guard import LabDisabled

    monkeypatch.setattr(get_settings(), "tapo_lab_enabled", False)
    with pytest.raises(LabDisabled):
        discovery.interfaces(cams[0])


def test_only_registered_private_devices(cams, monkeypatch):
    from tapo_lab import discovery
    from tapo_lab.guard import NotOwnDevice

    monkeypatch.setattr(get_settings(), "tapo_lab_enabled", True)
    with pytest.raises(NotOwnDevice):
        discovery.interfaces(999_999)  # not registered
    with pytest.raises(NotOwnDevice):
        discovery.interfaces(cams[1])  # loopback is not a LAN camera
    audit = (get_settings().logs_dir / "tapo-lab-audit.log").read_text()
    assert '"ok": false' in audit


def test_whitelist(cams, monkeypatch):
    from tapo_lab.device import adapter

    monkeypatch.setattr(get_settings(), "tapo_lab_enabled", True)
    with pytest.raises(adapter.LabError, match="no permitida"):
        adapter.call(cams[0], "firmware.flash", "admin", "x")


def test_redaction():
    from tapo_lab.protocol import redact

    data = {"device_info": {"mac": "AA-BB-CC-DD-EE-FF", "model": "C200", "dev_id": "x", "ip": "192.168.1.5"},
            "token": "secret", "note": "seen from 8.8.8.8 and 10.0.0.2 via AA:BB:CC:DD:EE:FF"}  # fmt: skip
    out = redact(data)
    assert out["token"] == "<redacted>" and out["device_info"]["mac"] == "<redacted>"
    assert out["device_info"]["model"] == "C200" and out["device_info"]["ip"] == "192.168.1.5"
    assert out["note"] == "seen from <public-ip> and 10.0.0.2 via <mac>"
