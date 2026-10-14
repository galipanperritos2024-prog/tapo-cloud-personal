"""Runtime settings editable from the UI, persisted in the ``settings`` table.

Every key has a typed default in :data:`DEFAULTS`, so a fresh install works
without any configuration. Secret values use :func:`get_secret` /
:func:`set_secret` and are stored Fernet-encrypted.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tapocloud.core.crypto import decrypt, encrypt
from tapocloud.core.models import Setting

# Local retention choices (hours). 0 = delete as soon as the upload is verified.
RETENTION_CHOICES = {"until_uploaded": 0, "24h": 24, "3d": 72, "7d": 168, "30d": 720}

DEFAULTS: dict[str, Any] = {
    # Drive
    "drive.enabled": True,
    "drive.root_folder": "TapoCloud",
    "drive.upload_kinds": ["continuous", "event", "manual"],
    "drive.max_parallel_uploads": 1,
    "drive.bandwidth_limit_kbps": 0,  # 0 = unlimited
    # Storage
    "storage.local_retention": "7d",
    # When Drive is disabled, should old local files be rotated anyway?
    "storage.rotate_when_drive_disabled": False,
    "storage.min_free_gb": 5.0,
    # Recording defaults for new cameras
    "recording.default_segment_minutes": 5,
    "recording.manual_max_minutes": 30,
    "recording.event_max_clip_minutes": 10,
    # Network discovery
    "discovery.allow_subnet_scan": True,
    # UI
    "ui.language": "es",
}

SECRET_KEYS = {"google.oauth_tokens", "google.client_secret"}


def get(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key, default)
    if row.encrypted:
        raise KeyError(f"{key} is a secret; use get_secret()")
    return row.value


def set(db: Session, key: str, value: Any) -> None:  # noqa: A001 - mirrors dict API
    if key in SECRET_KEYS:
        raise KeyError(f"{key} is a secret; use set_secret()")
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, encrypted=False))
    else:
        row.value = value


def all_public(db: Session) -> dict[str, Any]:
    values = dict(DEFAULTS)
    for row in db.scalars(select(Setting).where(Setting.encrypted.is_(False))):
        values[row.key] = row.value
    return values


def get_secret(db: Session, key: str) -> str | None:
    row = db.get(Setting, key)
    if row is None or row.value is None:
        return None
    return decrypt(row.value)


def set_secret(db: Session, key: str, value: str | None) -> None:
    row = db.get(Setting, key)
    enc = encrypt(value) if value is not None else None
    if row is None:
        db.add(Setting(key=key, value=enc, encrypted=True))
    else:
        row.value, row.encrypted = enc, True


def has_secret(db: Session, key: str) -> bool:
    row = db.get(Setting, key)
    return row is not None and row.value is not None
