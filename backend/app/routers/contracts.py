"""Contract endpoints. Paths and payloads are unchanged from before the refactor."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..data import queries, repository
from ..db import get_session
from ..domain.filters import ContractQuery
from ..domain.models import ContractCreate, ContractUpdate, RenewalRequest

router = APIRouter(prefix="/api/contracts", tags=["contracts"])

SessionDep = Annotated[Session, Depends(get_session)]
# The query model binds straight to query parameters — one definition serves the
# REST layer, the SQL layer and the tool schemas.
QueryDep = Annotated[ContractQuery, Query()]


@router.get("")
def list_contracts(session: SessionDep):
    return {"contracts": [c.public() for c in repository.list_contracts(session)]}


@router.get("/search")
def search_contracts(session: SessionDep, params: QueryDep):
    """
    Filter, sort and limit in SQL.

    `total` is the number of matches before `limit`, so a caller that asked for the
    top 3 still knows how many there were.
    """
    try:
        rows, total = queries.search(
            session,
            params.filters(),
            sort_by=params.sort_by,
            sort_dir=params.sort_dir,
            limit=params.limit,
        )
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err

    return {
        "contracts": [c.public() for c in rows],
        "returned": len(rows),
        "total": total,
        "sort": {"by": params.sort_by, "dir": params.sort_dir},
    }


@router.get("/{contract_id}")
def get_contract(contract_id: str, session: SessionDep):
    try:
        return repository.get(session, contract_id).public()
    except repository.ContractNotFound as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post("", status_code=201)
def create_contract(payload: ContractCreate, session: SessionDep):
    try:
        return repository.create(session, payload).public()
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.patch("/{contract_id}")
def update_contract(contract_id: str, patch: ContractUpdate, session: SessionDep):
    try:
        return repository.update(session, contract_id, patch).public()
    except repository.ContractNotFound as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.post("/{contract_id}/renew")
def renew_contract(contract_id: str, req: RenewalRequest, session: SessionDep):
    try:
        return repository.renew(session, contract_id, req).public()
    except repository.ContractNotFound as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.delete("/{contract_id}", status_code=204)
def delete_contract(contract_id: str, session: SessionDep):
    try:
        repository.remove(session, contract_id)
    except repository.ContractNotFound as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
