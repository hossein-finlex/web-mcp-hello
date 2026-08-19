"""
Filtering, sorting and aggregation in SQL.

This exists so the assistant can ask a precise question — "premium by insurer for
expiring D&O" — instead of pulling the whole book into its context and doing
arithmetic on it.

Every entry point takes a `ContractFilter`, so the criteria cannot drift from
what the API and the tool schemas advertise.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import case, func, or_
from sqlmodel import Session, select

from ..domain.filters import ContractFilter
from ..domain.models import EXPIRING_WINDOW_DAYS, GROUPABLE, SORTABLE, Contract

SEARCHABLE_COLUMNS = (
    Contract.id,
    Contract.policy_number,
    Contract.insured_company,
    Contract.insurer,
    Contract.product,
    Contract.industry,
    Contract.broker,
    Contract.notes,
)


def status_expression():
    """The stored-column equivalent of Contract.status."""
    today = date.today()
    return case(
        (Contract.is_draft.is_(True), "draft"),
        (Contract.end_date < today, "expired"),
        (Contract.end_date <= today + timedelta(days=EXPIRING_WINDOW_DAYS), "expiring"),
        else_="active",
    )


def where_clauses(filters: ContractFilter) -> list:
    """Translate the filter model into SQL predicates (AND)."""
    today = date.today()
    clauses = []

    if filters.product:
        clauses.append(Contract.product == filters.product)
    if filters.insurer:
        clauses.append(Contract.insurer.ilike(f"%{filters.insurer}%"))
    if filters.broker:
        clauses.append(Contract.broker.ilike(f"%{filters.broker}%"))
    if filters.renewal_pending is not None:
        clauses.append(Contract.renewal_pending.is_(filters.renewal_pending))
    if filters.min_sum_insured is not None:
        clauses.append(Contract.sum_insured >= filters.min_sum_insured)
    if filters.max_premium is not None:
        clauses.append(Contract.premium <= filters.max_premium)
    if filters.status:
        clauses.append(status_expression() == filters.status)
    if filters.expiring_within_days is not None:
        clauses.append(Contract.is_draft.is_(False))
        clauses.append(Contract.end_date >= today)
        clauses.append(
            Contract.end_date <= today + timedelta(days=filters.expiring_within_days)
        )
    if filters.query:
        # Every whitespace-separated term must appear in some searchable column.
        for term in filters.query.split():
            like = f"%{term}%"
            clauses.append(or_(*(col.ilike(like) for col in SEARCHABLE_COLUMNS)))
    return clauses


def count(session: Session, filters: ContractFilter) -> int:
    total = session.scalar(
        select(func.count()).select_from(Contract).where(*where_clauses(filters))
    )
    return int(total or 0)


def search(
    session: Session,
    filters: ContractFilter,
    *,
    sort_by: str = "end_date",
    sort_dir: str = "asc",
    limit: Optional[int] = None,
) -> tuple[list[Contract], int]:
    """Return (page, total_matching). `total` is the count before `limit`."""
    if sort_by not in SORTABLE:
        raise ValueError(f"sort_by must be one of {', '.join(SORTABLE)}")
    if sort_dir not in ("asc", "desc"):
        raise ValueError("sort_dir must be 'asc' or 'desc'")

    clauses = where_clauses(filters)
    total = count(session, filters)

    column = getattr(Contract, sort_by)
    order = column.desc() if sort_dir == "desc" else column.asc()
    statement = select(Contract).where(*clauses).order_by(order, Contract.id)
    if limit:
        statement = statement.limit(limit)

    return list(session.exec(statement).all()), total


def summarise(
    session: Session, filters: ContractFilter, group_by: str = "product"
) -> dict[str, Any]:
    """
    Aggregate the book, optionally over a filtered subset.

    Answers "which insurer has the most D&O premium" with a handful of numbers
    rather than by shipping every contract to the model.
    """
    if group_by not in GROUPABLE:
        raise ValueError(f"group_by must be one of {', '.join(GROUPABLE)}")

    key = status_expression() if group_by == "status" else getattr(Contract, group_by)

    rows = session.exec(
        select(
            key.label("key"),
            func.count().label("contracts"),
            func.coalesce(func.sum(Contract.sum_insured), 0).label("total_sum_insured"),
            func.coalesce(func.sum(Contract.premium), 0).label("total_premium"),
            func.coalesce(
                func.sum(case((Contract.renewal_pending.is_(True), 1), else_=0)), 0
            ).label("renewal_pending"),
            func.min(Contract.end_date).label("earliest_expiry"),
        )
        .where(*where_clauses(filters))
        .group_by(key)
        .order_by(func.sum(Contract.premium).desc())
    ).all()

    groups = [
        {
            "key": r.key,
            "contracts": int(r.contracts),
            "total_sum_insured": int(r.total_sum_insured),
            "total_premium": int(r.total_premium),
            "renewal_pending": int(r.renewal_pending),
            "earliest_expiry": _iso(r.earliest_expiry),
        }
        for r in rows
    ]

    return {
        "group_by": group_by,
        "groups": groups,
        "totals": {
            "contracts": sum(g["contracts"] for g in groups),
            "total_sum_insured": sum(g["total_sum_insured"] for g in groups),
            "total_premium": sum(g["total_premium"] for g in groups),
            "renewal_pending": sum(g["renewal_pending"] for g in groups),
        },
    }


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
