"""Google OAuth 2.0 (Authorization Code + PKCE) for Google Drive.

Decisions
---------
* **Scope ``drive.file``**: the app can only see/modify files *it created*.
  Even if this machine were compromised, the token could not read the rest
  of the user's Drive. It is also a "non-sensitive" scope, so a personal
  Google Cloud project works without Google's app verification.
* The user never types their Google password into this app: they sign in on
  accounts.google.com and we receive an authorization code.
* PKCE + a random ``state`` bound to the browser session protect the
  callback against code injection / CSRF.
* Tokens are stored Fernet-encrypted (``settings`` row ``google.oauth_tokens``).
  Only the refresh token is long-lived; access tokens are refreshed on demand.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from tapocloud.config import get_settings
from tapocloud.core import settings_store
from tapocloud.core.db import session_scope

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SCOPES = ["https://www.googleapis.com/auth/drive.file", "openid", "email"]

TOKENS_KEY = "google.oauth_tokens"


class DriveAuthError(RuntimeError):
    """The user must (re)connect Google Drive."""


@dataclass
class ClientConfig:
    client_id: str
    client_secret: str | None
    redirect_uri: str


def client_config() -> ClientConfig | None:
    s = get_settings()
    with session_scope() as db:
        cid = settings_store.get(db, "google.client_id") or s.google_client_id
        secret = settings_store.get_secret(db, "google.client_secret") or s.google_client_secret
        redirect = settings_store.get(db, "google.redirect_uri") or s.google_redirect_uri
    if not cid:
        return None
    return ClientConfig(cid, secret, redirect)


# Pending authorizations: state -> (code_verifier, session_id, created)
_pending: dict[str, tuple[str, int, float]] = {}


def build_authorization_url(session_id: int) -> str:
    cfg = client_config()
    if cfg is None:
        raise DriveAuthError("Configura primero el Client ID de Google (Ajustes > Google Drive)")
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v[2] > 600]:
        _pending.pop(k, None)
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    _pending[state] = (verifier, session_id, now)
    params = {
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",  # we need a refresh token
        "prompt": "consent",  # ensures a refresh token is returned again on reconnect
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str, state: str, session_id: int) -> dict:
    pending = _pending.pop(state, None)
    if pending is None or pending[1] != session_id or time.time() - pending[2] > 600:
        raise DriveAuthError("Estado OAuth inválido o caducado. Vuelve a intentarlo.")
    cfg = client_config()
    if cfg is None:
        raise DriveAuthError("Falta la configuración del cliente OAuth")
    data = {
        "code": code,
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": pending[0],
    }
    if cfg.client_secret:
        data["client_secret"] = cfg.client_secret
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(TOKEN_URL, data=data)
    if r.status_code != 200:
        raise DriveAuthError(f"Google rechazó el código: {r.json().get('error_description', r.status_code)}")
    tok = r.json()
    if "refresh_token" not in tok:
        raise DriveAuthError("Google no devolvió refresh token; revoca el acceso en tu cuenta y reconecta.")
    email = _email_from_id_token(tok.get("id_token"))
    stored = {
        "refresh_token": tok["refresh_token"],
        "access_token": tok["access_token"],
        "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 60,
        "scope": tok.get("scope"),
        "email": email,
        "connected_at": time.time(),
    }
    _save_tokens(stored)
    log.info("Google Drive connected (%s)", email or "unknown account")
    return {"email": email}


def _email_from_id_token(id_token: str | None) -> str | None:
    """Read the e-mail claim. The token comes straight from Google's token
    endpoint over TLS, so signature verification is not required here."""
    if not id_token:
        return None
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return claims.get("email")
    except (IndexError, ValueError):
        return None


def _load_tokens() -> dict | None:
    with session_scope() as db:
        raw = settings_store.get_secret(db, TOKENS_KEY)
    return json.loads(raw) if raw else None


def _save_tokens(tokens: dict | None) -> None:
    with session_scope() as db:
        settings_store.set_secret(db, TOKENS_KEY, json.dumps(tokens) if tokens else None)


def connection_info() -> dict:
    tok = _load_tokens()
    if not tok:
        return {"connected": False}
    return {"connected": True, "email": tok.get("email"), "connected_at": tok.get("connected_at"), "scope": tok.get("scope")}


_refresh_locks: dict[int, asyncio.Lock] = {}


def _refresh_lock() -> asyncio.Lock:
    """One lock per event loop (asyncio primitives are loop-bound)."""
    loop_id = id(asyncio.get_running_loop())
    return _refresh_locks.setdefault(loop_id, asyncio.Lock())


async def get_access_token(force_refresh: bool = False) -> str:
    async with _refresh_lock():
        tok = _load_tokens()
        if not tok:
            raise DriveAuthError("Google Drive no está conectado")
        if not force_refresh and tok.get("access_token") and tok.get("expires_at", 0) > time.time():
            return tok["access_token"]
        cfg = client_config()
        if cfg is None:
            raise DriveAuthError("Falta la configuración del cliente OAuth")
        data = {"client_id": cfg.client_id, "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}
        if cfg.client_secret:
            data["client_secret"] = cfg.client_secret
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(TOKEN_URL, data=data)
        if r.status_code in (400, 401):
            err = r.json().get("error", "")
            if err in ("invalid_grant", "unauthorized_client", "invalid_client"):
                raise DriveAuthError("El acceso a Google Drive fue revocado o caducó. Reconecta la cuenta.")
        r.raise_for_status()
        new = r.json()
        tok["access_token"] = new["access_token"]
        tok["expires_at"] = time.time() + int(new.get("expires_in", 3600)) - 60
        _save_tokens(tok)
        return tok["access_token"]


async def disconnect() -> None:
    tok = _load_tokens()
    if tok:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(REVOKE_URL, data={"token": tok["refresh_token"]})
        except httpx.HTTPError:
            log.warning("Could not revoke token at Google (offline?); deleting locally anyway")
    _save_tokens(None)
    from sqlalchemy import delete

    from tapocloud.core.models import DriveFolder

    with session_scope() as db:
        db.execute(delete(DriveFolder))
