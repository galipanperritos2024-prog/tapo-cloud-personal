"""Authentication primitives: password hashing, sessions, CSRF and stream tokens.

* Passwords: ``scrypt`` (memory-hard, in the Python stdlib — no native
  dependency to compile on Windows/Raspberry Pi).
* Sessions: random 256-bit token in an ``HttpOnly`` cookie; the database only
  stores its SHA-256, so a leaked DB cannot be replayed as a cookie.
* CSRF: synchronizer token bound to the session, sent by the SPA in the
  ``X-CSRF-Token`` header (readable cookie ``tc_csrf``). Combined with
  ``SameSite=Lax`` cookies.
* Stream tokens: short-lived HMAC tokens that the browser presents to
  MediaMTX; MediaMTX asks our ``/internal/mediamtx/auth`` endpoint to verify.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tapocloud.config import get_settings
from tapocloud.core.crypto import signing_key
from tapocloud.core.models import User, UserSession

SESSION_COOKIE = "tc_session"
CSRF_COOKIE = "tc_csrf"
CSRF_HEADER = "X-CSRF-Token"

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, maxmem=64 * 1024 * 1024)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User, ip: str | None, user_agent: str | None) -> tuple[str, str]:
    """Create a session; returns ``(cookie_token, csrf_token)``."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.add(
        UserSession(
            token_hash=_hash_token(token),
            csrf_token=csrf,
            user_id=user.id,
            expires_at=now + timedelta(hours=get_settings().session_hours),
            ip=ip,
            user_agent=(user_agent or "")[:255],
        )
    )
    user.last_login_at = now
    # Opportunistic cleanup of expired sessions.
    db.execute(delete(UserSession).where(UserSession.expires_at < now))
    return token, csrf


def get_session_by_token(db: Session, token: str | None) -> UserSession | None:
    if not token:
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _hash_token(token)))
    if sess is None or sess.expires_at < datetime.now(UTC):
        return None
    return sess


def destroy_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(UserSession).where(UserSession.token_hash == _hash_token(token)))


# --------------------------------------------------------------------------
# Stream tokens (browser -> MediaMTX)
# --------------------------------------------------------------------------
def make_stream_token(camera_id: int, ttl_seconds: int = 600) -> str:
    """Token format: ``<camera_id>.<expiry>.<hmac>`` (URL-safe, no padding)."""
    exp = int(time.time()) + ttl_seconds
    payload = f"{camera_id}.{exp}"
    sig = hmac.new(signing_key(), payload.encode(), hashlib.sha256).digest()[:18]
    return f"{payload}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def verify_stream_token(token: str, camera_id: int) -> bool:
    try:
        cam_s, exp_s, sig_s = token.split(".")
        if int(cam_s) != camera_id or int(exp_s) < time.time():
            return False
        expected = hmac.new(signing_key(), f"{cam_s}.{exp_s}".encode(), hashlib.sha256).digest()[:18]
        given = base64.urlsafe_b64decode(sig_s + "=" * (-len(sig_s) % 4))
        return hmac.compare_digest(expected, given)
    except (ValueError, TypeError):
        return False
