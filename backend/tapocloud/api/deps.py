"""FastAPI dependencies: DB session, authentication and CSRF enforcement."""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from tapocloud.core.db import get_session
from tapocloud.core.models import User, UserSession
from tapocloud.core.security import CSRF_HEADER, SESSION_COOKIE, get_session_by_token

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_db(db: Session = Depends(get_session)) -> Session:
    return db


def current_session(request: Request, db: Session = Depends(get_db)) -> UserSession:
    sess = get_session_by_token(db, request.cookies.get(SESSION_COOKIE))
    if sess is None:
        raise HTTPException(status_code=401, detail="No autenticado")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent, sess.csrf_token):
            raise HTTPException(status_code=403, detail="Token CSRF inválido")
    return sess


def current_user(sess: UserSession = Depends(current_session), db: Session = Depends(get_db)) -> User:
    user = db.get(User, sess.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="No autenticado")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
