"""
Database engine and session plumbing.

Sync SQLAlchemy on purpose: the REST handlers are sync `def`, so FastAPI runs
them in a threadpool where blocking DB calls are fine. The one async part of
the app — the agent WebSocket — never touches the database, because the tools
it drives execute in the browser and come back through these same REST routes.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Iterator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

log = logging.getLogger("app.db")

DEFAULT_URL = "postgresql+psycopg://portfolio:portfolio@localhost:5434/portfolio"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


_engine = None


def engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            database_url(),
            echo=os.environ.get("SQL_ECHO", "0") == "1",
            pool_pre_ping=True,  # a restarted container invalidates pooled conns
        )
    return _engine


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    with Session(engine()) as session:
        yield session


def wait_for_database(timeout_seconds: float = 30.0) -> bool:
    """
    Poll until Postgres accepts connections. `docker compose up` returns before
    the server is ready, so without this the first request after a cold start
    fails for no good reason.
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
    log.error("database not reachable at %s: %s", _safe_url(), last_error)
    return False


def create_tables() -> None:
    SQLModel.metadata.create_all(engine())


def database_status() -> dict:
    try:
        with engine().connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar()
        return {"connected": True, "url": _safe_url(), "server_version": version}
    except Exception as err:  # noqa: BLE001
        return {"connected": False, "url": _safe_url(), "error": str(err).split("\n")[0]}


def _safe_url() -> str:
    """The URL with the password removed, safe to show in /api/health."""
    url = database_url()
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    creds, _, host = rest.rpartition("@")
    user = creds.split(":")[0] if creds else ""
    return f"{scheme}://{user}:***@{host}"
