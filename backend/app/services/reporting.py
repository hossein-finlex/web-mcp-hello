"""
Report assembly.

Headline figures, a grouped breakdown, a dated action list, and a markdown
rendering. All computation over the book — no LLM, no HTTP, no screen.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel
from sqlmodel import Session

from ..data import artifacts, queries
from ..domain.filters import ContractFilter

ACTION_LIST_CAP = 25
DEFAULT_TITLE = "Renewal pipeline"
DEFAULT_WINDOW_DAYS = 90


class Headline(BaseModel):
    contracts: int
    premium_at_risk: int
    sum_insured: int
    flagged_for_renewal: int
    earliest_expiry: Optional[str] = None


class Section(BaseModel):
    heading: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False
    total: Optional[int] = None


class Report(BaseModel):
    title: str
    group_by: str
    scope: dict
    headline: Headline
    sections: list[Section]
    markdown: str


def build_renewal_report(
    session: Session,
    filters: ContractFilter,
    group_by: str = "product",
    title: Optional[str] = None,
) -> Report:
    """
    Assemble a renewal report.

    An empty filter is widened to the next 90 days rather than reporting on the
    whole book, because "the renewal pipeline" implies a horizon.
    """
    if filters.is_empty():
        filters = ContractFilter(expiring_within_days=DEFAULT_WINDOW_DAYS)

    summary = queries.summarise(session, filters, group_by=group_by)
    rows, total = queries.search(
        session, filters, sort_by="end_date", sort_dir="asc", limit=ACTION_LIST_CAP
    )
    contracts = [c.public() for c in rows]

    headline = Headline(
        contracts=summary["totals"]["contracts"],
        premium_at_risk=summary["totals"]["total_premium"],
        sum_insured=summary["totals"]["total_sum_insured"],
        flagged_for_renewal=summary["totals"]["renewal_pending"],
        earliest_expiry=contracts[0]["end_date"] if contracts else None,
    )

    sections = [
        Section(
            heading=f"By {group_by}",
            columns=["Group", "Contracts", "Premium", "Sum insured", "Flagged"],
            rows=[
                [
                    g["key"],
                    g["contracts"],
                    g["total_premium"],
                    g["total_sum_insured"],
                    g["renewal_pending"],
                ]
                for g in summary["groups"]
            ],
        ),
        Section(
            heading="Action list (soonest first)",
            columns=[
                "Contract", "Insured", "Product", "Insurer", "Expires", "Days", "Premium"
            ],
            rows=[
                [
                    c["id"], c["insured_company"], c["product"], c["insurer"],
                    c["end_date"], c["days_to_expiry"], c["premium"],
                ]
                for c in contracts
            ],
            truncated=total > len(contracts),
            total=total,
        ),
    ]

    resolved_title = title or DEFAULT_TITLE
    return Report(
        title=resolved_title,
        group_by=group_by,
        scope=filters.active(),
        headline=headline,
        sections=sections,
        markdown=render_markdown(resolved_title, filters.active(), headline, sections),
    )


def save(session: Session, report: Report) -> str:
    """Persist a report and return its id."""
    return artifacts.save_report(
        session,
        title=report.title,
        scope=report.scope,
        group_by=report.group_by,
        headline=report.headline.model_dump(),
        sections=[s.model_dump() for s in report.sections],
        markdown=report.markdown,
    ).id


def render_markdown(
    title: str, scope: dict, headline: Headline, sections: list[Section]
) -> str:
    described = ", ".join(f"{k}={v}" for k, v in scope.items()) or "whole book"
    lines = [
        f"# {title}", "", f"Scope: {described}", "",
        f"- Contracts: {headline.contracts}",
        f"- Premium at risk: EUR {headline.premium_at_risk:,}",
        f"- Sum insured: EUR {headline.sum_insured:,}",
        f"- Flagged for renewal: {headline.flagged_for_renewal}",
        f"- Earliest expiry: {headline.earliest_expiry}",
    ]
    for section in sections:
        lines += [
            "", f"## {section.heading}", "",
            "| " + " | ".join(section.columns) + " |",
            "|" + "|".join(["---"] * len(section.columns)) + "|",
        ]
        lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in section.rows]
    return "\n".join(lines)
