"""Database engine and session management.

SQLite is the default (zero-install). Two processes (backend and
camera-service) share the file, so we enable WAL mode and a busy timeout:
readers never block writers and short write transactions simply queue.
Switching to PostgreSQL only requires ``TAPOCLOUD_DATABASE_URL``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from tapocloud.config import get_settings


class Base(DeclarativeBase):
    pass


def _sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - trivial
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.close()


@lru_cache
def get_engine() -> Engine:
    url = get_settings().db_url
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
        event.listen(engine, "connect", _sqlite_pragmas)
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)
    return engine


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def init_db() -> None:
    """Create tables that do not exist yet.

    For the MVP we rely on ``create_all``; schema migrations (Alembic) are
    planned before the first release that changes an existing table.
    """
    from tapocloud.core import models  # noqa: F401  (register models)

    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background workers: commit or roll back."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
