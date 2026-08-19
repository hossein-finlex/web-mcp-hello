"""
Renewal operations, single and bulk.

The bulk case is the reason this module exists. It is not "renew, fourteen
times": it selects a cohort, projects the new terms, and applies them in one
transaction that either lands completely or not at all — plus it writes an audit
record. That composition is business logic, and it used to live inside a tool.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Session

from ..data import artifacts, queries, repository
from ..domain.filters import ContractFilter
from ..domain.models import Contract, RenewalRequest

log = logging.getLogger("app.services.renewals")


class PlannedRenewal(BaseModel):
    id: str
    insured_company: str
    product: str
    insurer: str
    premium: int
    current_end: str
    new_end: str


class AppliedRenewal(BaseModel):
    id: str
    insured_company: str
    product: str
    insurer: str
    premium: int
    new_end: str
    renewal_count: int


class RenewalPlan(BaseModel):
    """What a bulk renewal would do. Produced without writing anything."""

    months: int
    scope: dict
    items: list[PlannedRenewal]
    premium_affected: int
    excluded_drafts: list[str]

    @property
    def matched(self) -> int:
        return len(self.items)


class BatchOutcome(BaseModel):
    batch_id: str
    committed: bool
    matched: int
    renewed: list[AppliedRenewal]
    failed: list[dict]
    premium_affected: int
    months: int


def is_renewable(contract: Contract) -> tuple[bool, Optional[str]]:
    """
    The single definition of whether a contract can be renewed.

    Callers decide how to react: the single-contract path raises so a broker gets
    told, while the bulk path excludes and reports the exclusion. Same rule, one
    place, two appropriate responses.
    """
    if contract.is_draft:
        return False, "A draft cannot be renewed — issue it first"
    return True, None


def plan_batch(
    session: Session, filters: ContractFilter, months: int = 12
) -> RenewalPlan:
    """Select the cohort and project the new terms. Writes nothing."""
    rows, _ = queries.search(session, filters, sort_by="end_date", sort_dir="asc")

    eligible: list[Contract] = []
    excluded: list[str] = []
    for contract in rows:
        allowed, _ = is_renewable(contract)
        if allowed:
            eligible.append(contract)
        else:
            excluded.append(contract.id)

    items = [
        PlannedRenewal(
            id=c.id,
            insured_company=c.insured_company,
            product=c.product,
            insurer=c.insurer,
            premium=c.premium,
            current_end=c.end_date.isoformat(),
            new_end=repository.add_months(c.end_date, months).isoformat(),
        )
        for c in eligible
    ]
    return RenewalPlan(
        months=months,
        scope=filters.active(),
        items=items,
        premium_affected=sum(c.premium for c in eligible),
        excluded_drafts=excluded,
    )


def record_plan(session: Session, plan: RenewalPlan) -> str:
    """Persist a dry run as an auditable batch record. Returns the batch id."""
    return artifacts.save_batch(
        session,
        kind="renewal",
        committed=False,
        months=plan.months,
        scope=plan.scope,
        matched=plan.matched,
        renewed=0,
        failed=[],
        premium_affected=plan.premium_affected,
        items=[i.model_dump() for i in plan.items],
    ).id


def apply_batch(session: Session, plan: RenewalPlan) -> BatchOutcome:
    """
    Apply a plan in one transaction.

    All-or-nothing: if any single renewal fails the whole batch is rolled back,
    because a partially renewed book is worse than an untouched one — nobody
    knows where it stopped. The audit record is written either way.
    """
    renewed: list[AppliedRenewal] = []
    failed: list[dict] = []

    for item in plan.items:
        try:
            contract = repository.renew(
                session, item.id, RenewalRequest(months=plan.months), commit=False
            )
            renewed.append(
                AppliedRenewal(
                    id=contract.id,
                    insured_company=contract.insured_company,
                    product=contract.product,
                    insurer=contract.insurer,
                    premium=contract.premium,
                    new_end=contract.end_date.isoformat(),
                    renewal_count=contract.renewal_count,
                )
            )
        except Exception as err:  # noqa: BLE001
            failed.append({"id": item.id, "error": str(err)})

    if failed:
        session.rollback()
        renewed = []
        log.warning("renewal batch rolled back: %d failures", len(failed))
    else:
        session.commit()

    batch_id = artifacts.save_batch(
        session,
        kind="renewal",
        committed=True,
        months=plan.months,
        scope=plan.scope,
        matched=plan.matched,
        renewed=len(renewed),
        failed=failed,
        premium_affected=plan.premium_affected,
        items=[r.model_dump() for r in renewed],
    ).id

    return BatchOutcome(
        batch_id=batch_id,
        committed=True,
        matched=plan.matched,
        renewed=renewed,
        failed=failed,
        premium_affected=plan.premium_affected,
        months=plan.months,
    )
