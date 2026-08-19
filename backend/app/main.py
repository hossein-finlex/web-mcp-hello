"""
Application factory.

    docker compose up -d                                   # Postgres
    cd backend && uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from . import llm, routers
from .agent import agent_session
from .data import repository
from .db import create_tables, session_scope, wait_for_database
from .settings import settings

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s  %(message)s"
)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    cfg = settings()

    if wait_for_database():
        create_tables()
        if cfg.auto_seed:
            with session_scope() as session:
                repository.seed(session, total=cfg.seed_total)
    else:
        log.error("Starting without a database. Run: docker compose up -d")

    if llm.is_mock():
        log.warning("MOCK_LLM is on — the agent is a scripted stub, not Claude.")
    elif not llm.have_credentials():
        log.warning("No ANTHROPIC_API_KEY found. Set it in backend/.env, or MOCK_LLM=1.")
    else:
        log.info("Agent will use %s", cfg.model)

    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Portfolio API", version="3.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings().frontend_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in routers.ALL:
        app.include_router(router)

    @app.websocket("/ws/agent")
    async def ws_agent(ws: WebSocket):  # pragma: no cover - transport
        await agent_session(ws)

    return app


app = create_app()
