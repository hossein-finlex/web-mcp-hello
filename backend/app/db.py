"""
Engine and session plumbing.

Sync SQLAlchemy on purpose: the REST handlers are sync `def`, so FastAPI runs them
in a threadpool where blocking DB calls are fine. Server tools push their DB work
onto a thread with asyncio.to_thread for the same reason.
"""

from __future__ import annotations

import logging
import time
from typing import Iterator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from .settings import settings

log = logging.getLogger("app.db")

_engine = None


def engine():
    global _engine
    if _engine is None:
        cfg = settings()
        _engine = create_engine(
            cfg.database_url,
            echo=cfg.sql_echo,
            pool_pre_ping=True,  # a restarted container invalidates pooled conns
        )
    return _engine


def reset_engine() -> None:
    """Drop the cached engine. Used by tests to point at a different database."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    with Session(engine()) as session:
        yield session


def session_scope() -> Session:
    """A session for code outside the request cycle (server tools, scripts)."""
    return Session(engine())


def wait_for_database(timeout_seconds: float = 30.0) -> bool:
    """
    Poll until the database accepts connections. `docker compose up` returns
    before the server is ready, so without this the first request after a cold
    start fails for no good reason.
    """
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            with engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            if attempt > 1:
                log.info("database ready after %d attempts", attempt)
            return True
        except Exception as err:  # noqa: BLE001
            last_error = err
            time.sleep(0.75)
    log.error("database not reachable at %s: %s", safe_url(), last_error)
    return False


def create_tables() -> None:
    # Import for side effect: model classes must be defined before create_all.
    from .domain import models  # noqa: F401

    SQLModel.metadata.create_all(engine())


def database_status() -> dict:
    try:
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            version = None
            try:
                version = conn.execute(text("SHOW server_version")).scalar()
            except Exception:  # noqa: BLE001
                pass  # not Postgres (e.g. SQLite under test)
        return {"connected": True, "url": safe_url(), "server_version": version}
    except Exception as err:  # noqa: BLE001
        return {
            "connected": False,
            "url": safe_url(),
            "error": str(err).split("\n")[0],
        }


def safe_url() -> str:
    """The URL with the password removed, safe to show in /api/health."""
    url = settings().database_url
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    creds, _, host = rest.rpartition("@")
    user = creds.split(":")[0] if creds else ""
    return f"{scheme}://{user}:***@{host}"
