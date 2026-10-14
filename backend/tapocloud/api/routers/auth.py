"""Authentication: first-run setup, login/logout, password change."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from tapocloud.api.deps import client_ip, current_session, current_user, get_db
from tapocloud.api.schemas import LoginIn, PasswordChangeIn, SetupIn
from tapocloud.config import get_settings
from tapocloud.core.models import User, UserSession
from tapocloud.core.ratelimit import RateLimiter
from tapocloud.core.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    create_session,
    destroy_session,
    get_session_by_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 10 *failed* attempts per 5 minutes per IP: stops online brute force
# without ever locking out a user who types the right password.
login_limiter = RateLimiter(10, 300)

# Pre-computed hash so failed logins for unknown users take the same time.
_DUMMY_HASH = hash_password("timing-equalizer")


def _set_cookies(response: Response, token: str, csrf: str) -> None:
    s = get_settings()
    max_age = s.session_hours * 3600
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, samesite="lax", secure=s.secure_cookies, path="/")
    # Readable by JS on purpose: the SPA copies it into the X-CSRF-Token header.
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, samesite="lax", secure=s.secure_cookies, path="/")


@router.get("/status")
def status(request: Request, db: Session = Depends(get_db)) -> dict:
    users = db.scalar(select(func.count()).select_from(User)) or 0
    sess = get_session_by_token(db, request.cookies.get(SESSION_COOKIE))
    user = db.get(User, sess.user_id) if sess else None
    return {"setup_required": users == 0, "authenticated": user is not None, "username": user.username if user else None}


@router.post("/setup")
def setup(body: SetupIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    """Create the administrator account. Only possible while no user exists."""
    if (db.scalar(select(func.count()).select_from(User)) or 0) > 0:
        raise HTTPException(status_code=409, detail="La aplicación ya está configurada")
    user = User(username=body.username, password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    token, csrf = create_session(db, user, client_ip(request), request.headers.get("user-agent"))
    _set_cookies(response, token, csrf)
    return {"ok": True, "username": user.username}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    ip = client_ip(request)
    if login_limiter.blocked(ip):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos. Espera unos minutos.",
            headers={"Retry-After": str(login_limiter.retry_after(ip))},
        )
    user = db.scalar(select(User).where(User.username == body.username))
    if not verify_password(body.password, user.password_hash if user else _DUMMY_HASH) or user is None:
        login_limiter.hit(ip)
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    token, csrf = create_session(db, user, ip, request.headers.get("user-agent"))
    _set_cookies(response, token, csrf)
    return {"ok": True, "username": user.username}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db), _=Depends(current_session)) -> dict:
    destroy_session(db, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.post("/password")
def change_password(
    body: PasswordChangeIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="La contraseña actual no es correcta")
    user.password_hash = hash_password(body.new_password)
    # Invalidate every other session, then issue a fresh one for this browser.
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    token, csrf = create_session(db, user, client_ip(request), request.headers.get("user-agent"))
    _set_cookies(response, token, csrf)
    return {"ok": True}
