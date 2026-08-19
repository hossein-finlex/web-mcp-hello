"""
Business operations that used to be reachable only through the assistant.

Bulk renewal, report generation and rate benchmarking are capabilities of the
application, not of the chat panel. These routes call the same service functions
the tools call, so a button in the UI and a sentence to the assistant do exactly
the same thing.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session

from ..data.repository import ContractNotFound
from ..db import get_session
from ..domain.filters import ContractFilter
from ..domain.models import GROUPABLE
from ..services import market, renewals, reporting

router = APIRouter(prefix="/api", tags=["operations"])

SessionDep = Annotated[Session, Depends(get_session)]


class BatchRequest(ContractFilter):
    months: int = Field(default=12, ge=1, le=60)
    commit: bool = Field(
        default=False, description="false previews; true applies the changes."
    )

    def filters(self) -> ContractFilter:
        return ContractFilter.model_validate(
            self.model_dump(exclude={"months", "commit"}, exclude_none=True)
        )


class ReportRequest(ContractFilter):
    group_by: str = "product"
    title: Optional[str] = None

    def filters(self) -> ContractFilter:
        return ContractFilter.model_validate(
            self.model_dump(exclude={"group_by", "title"}, exclude_none=True)
        )


@router.post("/renewals/batch")
def renewal_batch(payload: BatchRequest, session: SessionDep):
    """
    Preview or apply a bulk renewal.

    Previews by default, exactly like the tool: a bulk mutation needs an explicit
    `commit`.
    """
    filters = payload.filters()
    if filters.is_empty():
        raise HTTPException(
            status_code=422,
            detail="Narrow the batch with a filter such as expiring_within_days.",
        )

    plan = renewals.plan_batch(session, filters, payload.months)
    if not plan.items:
        return {"matched": 0, "renewed": 0, "note": "Nothing matched that filter."}

    if not payload.commit:
        return {
            "batch_id": renewals.record_plan(session, plan),
            "committed": False,
            "matched": plan.matched,
            "premium_affected": plan.premium_affected,
            "months": plan.months,
            "excluded_drafts": plan.excluded_drafts,
            "items": [i.model_dump() for i in plan.items],
        }

    outcome = renewals.apply_batch(session, plan)
    return outcome.model_dump()


@router.post("/reports/renewal", status_code=201)
def create_renewal_report(payload: ReportRequest, session: SessionDep):
    """Generate and store a renewal report."""
    if payload.group_by not in GROUPABLE:
        raise HTTPException(
            status_code=422, detail=f"group_by must be one of {', '.join(GROUPABLE)}"
        )
    report = reporting.build_renewal_report(
        session, payload.filters(), payload.group_by, payload.title
    )
    report_id = reporting.save(session, report)
    return {"report_id": report_id, **report.model_dump()}


@router.get("/market/benchmark")
def get_benchmark(
    session: SessionDep,
    product: Optional[str] = Query(default=None),
    contract_id: Optional[str] = Query(default=None),
):
    """Market rate band for a product, or a comparison for one contract."""
    try:
        return market.benchmark(session, product, contract_id).model_dump()
    except ContractNotFound as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except market.UnknownProduct as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
