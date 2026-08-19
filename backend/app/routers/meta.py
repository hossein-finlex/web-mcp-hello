"""Health, the server tool catalogue, and demo reset."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from .. import llm
from .. import tools as server_tools
from ..data import artifacts, repository
from ..db import database_status, get_session
from ..domain.models import PRODUCTS
from ..settings import settings

router = APIRouter(prefix="/api", tags=["meta"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/health")
def health(session: SessionDep):
    """The frontend reads this to decide what to tell the user."""
    db = database_status()
    contracts = repository.count_contracts(session) if db["connected"] else 0
    return {
        "ok": db["connected"],
        "model": llm.model_name(),
        "mock": llm.is_mock(),
        "credentials": llm.have_credentials(),
        "database": db,
        "contracts": contracts,
        "products": list(PRODUCTS),
        "server_tools": [d["name"] for d in server_tools.definitions()],
    }


@router.get("/tools")
def list_server_tools():
    """
    The tools that execute in the backend rather than in the page.

    The browser shows these next to its own WebMCP tools so the split is visible:
    page tools move the UI, server tools do work off-page.
    """
    return {
        "server_tools": [
            {
                "name": d["name"],
                "description": d["description"],
                "inputSchema": d["input_schema"],
            }
            for d in server_tools.definitions()
        ]
    }


@router.post("/reset")
def reset(session: SessionDep):
    """Wipe the tables and re-seed the demo book."""
    count = repository.seed(session, total=settings().seed_total, force=True)
    artifacts.clear(session)
    return {"ok": True, "contracts": count}
