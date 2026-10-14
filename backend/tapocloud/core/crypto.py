"""Secret management.

* Camera passwords and OAuth tokens are encrypted with **Fernet**
  (AES-128-CBC + HMAC-SHA256, from ``cryptography``): authenticated
  encryption, so tampered ciphertexts are rejected, not silently decrypted.
* The master key is resolved in this order:
    1. ``TAPOCLOUD_MASTER_KEY`` environment variable (Docker secrets, NAS);
    2. the OS keyring (Windows Credential Manager / macOS Keychain / Secret
       Service) when the optional ``keyring`` package is installed and
       ``TAPOCLOUD_USE_KEYRING=1``;
    3. ``<data_dir>/secret.key``, created with owner-only permissions.
* Losing the key means re-entering camera passwords and reconnecting Drive;
  recordings are not encrypted and remain readable.
"""

from __future__ import annotations

import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from tapocloud.config import get_settings

log = logging.getLogger(__name__)

_KEYRING_SERVICE = "TapoCloudPersonal"


class SecretError(RuntimeError):
    """Raised when a secret cannot be decrypted (wrong/rotated master key)."""


def _write_private(path: Path, data: str) -> None:
    """Write a file readable only by the current user."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(data)
    # On Windows, files under %LOCALAPPDATA% inherit a per-user ACL already.


def _load_or_create_file_secret(path: Path, factory) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = factory()
    _write_private(path, value)
    log.info("Generated new secret file %s", path.name)
    return value


def _keyring_key() -> str | None:
    if os.environ.get("TAPOCLOUD_USE_KEYRING") != "1":
        return None
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        log.warning("TAPOCLOUD_USE_KEYRING=1 but 'keyring' is not installed; using key file")
        return None
    key = keyring.get_password(_KEYRING_SERVICE, "master_key")
    if not key:
        key = Fernet.generate_key().decode()
        keyring.set_password(_KEYRING_SERVICE, "master_key", key)
    return key


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.master_key or _keyring_key()
    if not key:
        key = _load_or_create_file_secret(settings.data_dir / "secret.key", lambda: Fernet.generate_key().decode())
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretError("Cannot decrypt secret: master key changed or data corrupted") from exc


@lru_cache
def internal_token() -> str:
    """Shared secret used between our own processes and MediaMTX."""
    settings = get_settings()
    if settings.internal_token:
        return settings.internal_token
    return _load_or_create_file_secret(settings.data_dir / "internal.token", lambda: secrets.token_urlsafe(32))


@lru_cache
def signing_key() -> bytes:
    """HMAC key for short-lived stream tokens (derived, never stored separately)."""
    import hashlib

    return hashlib.sha256(b"tapocloud-stream-v1:" + internal_token().encode()).digest()
