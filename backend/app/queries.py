"""
Filtering and aggregation in SQL.

This exists so the assistant can ask a precise question — "premium by insurer
for expiring D&O" — instead of pulling the whole book into its context and doing
arithmetic on it. That is cheaper, faster, and less likely to be wrong.

`status` is derived from the term rather than stored, so it appears here as a
CASE expression that both the filters and the GROUP BY reuse.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import case, func, or_
from sqlmodel import Session, select

from .models import EXPIRING_WINDOW_DAYS, Contract

GROUPABLE = ("product", "insurer", "status", "broker", "industry")

SORTABLE = (
    "end_date",
    "sum_insured",
    "premium",
    "deductible",
    "insured_company",
    "insurer",
    "product",
    "renewal_count",
)


def status_expression():
    """The stored-column equivalent of Contract.status."""
    today = date.today()
    return case(
        (Contract.is_draft.is_(True), "draft"),
        (Contract.end_date < today, "expired"),
        (
            Contract.end_date <= today + timedelta(days=EXPIRING_WINDOW_DAYS),
            "expiring",
        ),
        else_="active",
    )


def build_filters(
    query: Optional[str] = None,
    product: Optional[str] = None,
    insurer: Optional[str] = None,
    status: Optional[str] = None,
    renewal_pending: Optional[bool] = None,
    expiring_within_days: Optional[int] = None,
    min_sum_insured: Optional[int] = None,
    max_premium: Optional[int] = None,
    broker: Optional[str] = None,
) -> list:
    """All supplied criteria must match (AND), mirroring the UI filter bar."""
    today = date.today()
    clauses = []

    if product:
        clauses.append(Contract.product == product)
    if insurer:
        clauses.append(Contract.insurer.ilike(f"%{insurer}%"))
    if broker:
        clauses.append(Contract.broker.ilike(f"%{broker}%"))
    if renewal_pending is not None:
        clauses.append(Contract.renewal_pending.is_(renewal_pending))
    if min_sum_insured is not None:
        clauses.append(Contract.sum_insured >= min_sum_insured)
    if max_premium is not None:
        clauses.append(Contract.premium <= max_premium)
    if status:
        clauses.append(status_expression() == status)
    if expiring_within_days is not None:
        clauses.append(Contract.is_draft.is_(False))
        clauses.append(Contract.end_date >= today)
        clauses.append(Contract.end_date <= today + timedelta(days=expiring_within_days))
    if query:
        # Every whitespace-separated term must appear in some searchable column.
        for term in query.split():
            like = f"%{term}%"
            clauses.append(
                or_(
                    Contract.id.ilike(like),
                    Contract.policy_number.ilike(like),
                    Contract.insured_company.ilike(like),
                    Contract.insurer.ilike(like),
                    Contract.product.ilike(like),
                    Contract.industry.ilike(like),
                    Contract.broker.ilike(like),
                    Contract.notes.ilike(like),
                )
            )
    return clauses


def search(
    session: Session,
    *,
    sort_by: str = "end_date",
    sort_dir: str = "asc",
    limit: Optional[int] = None,
    **filters,
) -> tuple[list[Contract], int]:
    """Return (page, total_matching). `total` is the count before `limit`."""
    if sort_by not in SORTABLE:
        raise ValueError(f"sort_by must be one of {', '.join(SORTABLE)}")
    if sort_dir not in ("asc", "desc"):
        raise ValueError("sort_dir must be 'asc' or 'desc'")

    clauses = build_filters(**filters)

    # session.scalar() rather than session.exec().one(): the latter yields a
    # SQLAlchemy Row, not the integer.
    total = session.scalar(select(func.count()).select_from(Contract).where(*clauses)) or 0

    column = getattr(Contract, sort_by)
    order = column.desc() if sort_dir == "desc" else column.asc()
    statement = select(Contract).where(*clauses).order_by(order, Contract.id)
    if limit:
        statement = statement.limit(limit)

    return list(session.exec(statement).all()), int(total)


def summarise(session: Session, group_by: str = "product", **filters) -> dict:
    """
    Aggregate the book, optionally over a filtered subset.

    Returns totals per group plus an overall row, so a question like "which
    insurer has the most D&O premium" is answered by a handful of numbers
    rather than by shipping every contract to the model.
    """
    if group_by not in GROUPABLE:
        raise ValueError(f"group_by must be one of {', '.join(GROUPABLE)}")

    clauses = build_filters(**filters)
    key = status_expression() if group_by == "status" else getattr(Contract, group_by)

    rows = session.exec(
        select(
            key.label("key"),
            func.count().label("contracts"),
            func.coalesce(func.sum(Contract.sum_insured), 0).label("total_sum_insured"),
            func.coalesce(func.sum(Contract.premium), 0).label("total_premium"),
            func.coalesce(func.sum(
                case((Contract.renewal_pending.is_(True), 1), else_=0)
            ), 0).label("renewal_pending"),
            func.min(Contract.end_date).label("earliest_expiry"),
        )
        .where(*clauses)
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
            "earliest_expiry": r.earliest_expiry.isoformat() if r.earliest_expiry else None,
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
