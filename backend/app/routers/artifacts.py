"""Batch and report records produced by server-side tools."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..data import artifacts
from ..db import get_session

router = APIRouter(prefix="/api", tags=["artifacts"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/batches")
def list_batches(session: SessionDep):
    return {"batches": [b.public() for b in artifacts.list_batches(session)]}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, session: SessionDep):
    """A batch record: what a bulk run matched, changed, and skipped."""
    try:
        return artifacts.get_batch(session, batch_id).public()
    except artifacts.ArtifactNotFound as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.get("/reports")
def list_reports(session: SessionDep):
    return {"reports": [r.public() for r in artifacts.list_reports(session)]}


@router.get("/reports/{report_id}")
def get_report(report_id: str, session: SessionDep):
    try:
        return artifacts.get_report(session, report_id).public()
    except artifacts.ArtifactNotFound as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
