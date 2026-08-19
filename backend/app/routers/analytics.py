"""Aggregation endpoint — the same SQL the summarise_portfolio tool uses."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..data import queries
from ..db import get_session
from ..domain.filters import SummaryQuery

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/summary")
def summary(
    session: Annotated[Session, Depends(get_session)],
    params: Annotated[SummaryQuery, Query()],
):
    try:
        return queries.summarise(session, params.filters(), group_by=params.group_by)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
