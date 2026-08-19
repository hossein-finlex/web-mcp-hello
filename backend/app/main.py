"""
FastAPI app: REST for the contract portfolio + a WebSocket agent bridge.

    docker compose up -d                                   # Postgres
    cd backend && uvicorn app.main:app --reload --port 8000
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env before anything reads the environment.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import Depends, FastAPI, HTTPException, WebSocket  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from sqlmodel import Session  # noqa: E402

from . import llm, queries, repository as repo, server_tools  # noqa: E402
from .artifacts import batches, reports  # noqa: E402
from .agent_ws import agent_session  # noqa: E402
from .db import (  # noqa: E402
    create_tables,
    database_status,
    get_session,
    wait_for_database,
)
from .models import (  # noqa: E402
    PRODUCTS,
    ContractCreate,
    ContractUpdate,
    RenewalRequest,
)
from typing import Optional  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s  %(message)s"
)
log = logging.getLogger("app")

SEED_TOTAL = int(os.environ.get("SEED_TOTAL", "50"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    if wait_for_database():
        create_tables()
        if os.environ.get("AUTO_SEED", "1") == "1":
            from .db import engine

            with Session(engine()) as session:
                repo.seed(session, total=SEED_TOTAL)
    else:
        log.error("Starting without a database. Run: docker compose up -d")

    if llm.is_mock():
        log.warning("MOCK_LLM is on — the agent is a scripted stub, not Claude.")
    elif not llm.have_credentials():
        log.warning(
            "No ANTHROPIC_API_KEY found. Set it in backend/.env, or set MOCK_LLM=1."
        )
    else:
        log.info("Agent will use %s", llm.MODEL)

    yield


app = FastAPI(title="Portfolio API", version="2.0.0", lifespan=lifespan)

origins = [
    o.strip()
    for o in os.environ.get(
        "FRONTEND_ORIGINS",
        "http://localhost:3000,http://localhost:3001,http://localhost:3002",
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health(session: Session = Depends(get_session)):
    """The frontend reads this to decide what to tell the user."""
    db = database_status()
    contracts = repo.count_contracts(session) if db["connected"] else 0
    return {
        "ok": db["connected"],
        "model": "mock" if llm.is_mock() else llm.MODEL,
        "mock": llm.is_mock(),
        "credentials": llm.have_credentials(),
        "database": db,
        "contracts": contracts,
        "products": list(PRODUCTS),
        "server_tools": [d["name"] for d in server_tools.definitions()],
    }


@app.get("/api/tools")
def list_server_tools():
    """
    The tools that execute in this process rather than in the page.

    The browser shows these next to its own WebMCP tools so the split is
    visible: page tools move the UI, server tools do work off-page.
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


@app.get("/api/contracts")
def list_contracts(session: Session = Depends(get_session)):
    return {"contracts": [c.public() for c in repo.list_contracts(session)]}


@app.get("/api/contracts/search")
def search_contracts(
    query: Optional[str] = None,
    product: Optional[str] = None,
    insurer: Optional[str] = None,
    broker: Optional[str] = None,
    status: Optional[str] = None,
    renewal_pending: Optional[bool] = None,
    expiring_within_days: Optional[int] = None,
    min_sum_insured: Optional[int] = None,
    max_premium: Optional[int] = None,
    sort_by: str = "end_date",
    sort_dir: str = "asc",
    limit: Optional[int] = None,
    session: Session = Depends(get_session),
):
    """
    Filter, sort and limit in SQL.

    `total` is the number of matches before `limit`, so a caller that asked for
    the top 3 still knows how many there were.
    """
    try:
        rows, total = queries.search(
            session,
            query=query,
            product=product,
            insurer=insurer,
            broker=broker,
            status=status,
            renewal_pending=renewal_pending,
            expiring_within_days=expiring_within_days,
            min_sum_insured=min_sum_insured,
            max_premium=max_premium,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=limit,
        )
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err

    return {
        "contracts": [c.public() for c in rows],
        "returned": len(rows),
        "total": total,
        "sort": {"by": sort_by, "dir": sort_dir},
    }


@app.get("/api/contracts/{contract_id}")
def get_contract(contract_id: str, session: Session = Depends(get_session)):
    try:
        return repo.get_contract(session, contract_id).public()
    except repo.ContractNotFound as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@app.post("/api/contracts", status_code=201)
def create_contract(payload: ContractCreate, session: Session = Depends(get_session)):
    try:
        return repo.create_contract(session, payload).public()
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@app.patch("/api/contracts/{contract_id}")
def update_contract(
    contract_id: str, patch: ContractUpdate, session: Session = Depends(get_session)
):
    try:
        return repo.update_contract(session, contract_id, patch).public()
    except repo.ContractNotFound as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@app.post("/api/contracts/{contract_id}/renew")
def renew_contract(
    contract_id: str, req: RenewalRequest, session: Session = Depends(get_session)
):
    try:
        return repo.renew_contract(session, contract_id, req).public()
    except repo.ContractNotFound as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@app.delete("/api/contracts/{contract_id}", status_code=204)
def delete_contract(contract_id: str, session: Session = Depends(get_session)):
    try:
        repo.delete_contract(session, contract_id)
    except repo.ContractNotFound as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@app.get("/api/summary")
def summary(
    group_by: str = "product",
    query: Optional[str] = None,
    product: Optional[str] = None,
    insurer: Optional[str] = None,
    broker: Optional[str] = None,
    status: Optional[str] = None,
    renewal_pending: Optional[bool] = None,
    expiring_within_days: Optional[int] = None,
    min_sum_insured: Optional[int] = None,
    max_premium: Optional[int] = None,
    session: Session = Depends(get_session),
):
    """Aggregate totals per group, computed in SQL."""
    try:
        return queries.summarise(
            session,
            group_by=group_by,
            query=query,
            product=product,
            insurer=insurer,
            broker=broker,
            status=status,
            renewal_pending=renewal_pending,
            expiring_within_days=expiring_within_days,
            min_sum_insured=min_sum_insured,
            max_premium=max_premium,
        )
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str):
    """A batch record: what a bulk run matched, changed, and skipped."""
    record = batches.get(batch_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No batch {batch_id!r}")
    return record


@app.get("/api/batches")
def list_batches():
    return {"batches": batches.list()}


@app.get("/api/reports/{report_id}")
def get_report(report_id: str):
    record = reports.get(report_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No report {report_id!r}")
    return record


@app.get("/api/reports")
def list_reports():
    return {"reports": reports.list()}


@app.post("/api/reset")
def reset(session: Session = Depends(get_session)):
    """Wipe the table and re-seed the demo book."""
    count = repo.seed(session, total=SEED_TOTAL, force=True)
    batches.clear()
    reports.clear()
    return {"ok": True, "contracts": count}


@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket):
    await agent_session(ws)
