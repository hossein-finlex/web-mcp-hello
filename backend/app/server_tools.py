"""
Server-side tools — the other half of the story.

WebMCP tools are the *page's* capabilities: navigate, filter, edit the record
on screen. They are the right shape when the user should watch the change
happen. They are the wrong shape for work that is not UI-shaped at all:

  * **Bulk operations.** Renewing 14 contracts through the page would be 14
    round-trips through the model, each one a chance to stop halfway. Here it
    is one call, one transaction, all-or-nothing.
  * **Document generation.** Assembling a report is computation, not clicking.
  * **Data the page does not have.** Market benchmark rates come from
    elsewhere; no amount of UI automation would find them.

These execute in this process. They never touch the browser. The pattern that
makes them useful is the handoff: a server tool does the work and returns an
artifact id, then the assistant calls a WebMCP tool — show_batch_result,
show_report — to put the result in front of the user. Work happens off-page;
the *result* still lands on-page.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any, Awaitable, Callable, Optional

from sqlmodel import Session

from . import queries
from .artifacts import batches, reports
from .db import engine
from .models import PRODUCTS, RenewalRequest
from .repository import renew_contract

log = logging.getLogger("app.server_tools")

Progress = Callable[[str], Awaitable[None]]

_FILTER_PROPERTIES = {
    "query": {"type": "string", "description": "Free-text match."},
    "product": {"type": "string", "enum": list(PRODUCTS)},
    "insurer": {"type": "string", "description": "Substring match."},
    "broker": {"type": "string", "description": "Substring match."},
    "status": {"type": "string", "enum": ["active", "expiring", "expired", "draft"]},
    "renewal_pending": {"type": "boolean"},
    "expiring_within_days": {"type": "integer"},
    "min_sum_insured": {"type": "integer"},
    "max_premium": {"type": "integer"},
}

_FILTER_KEYS = tuple(_FILTER_PROPERTIES)


def _filters_from(args: dict) -> dict:
    return {k: args[k] for k in _FILTER_KEYS if args.get(k) not in (None, "")}


# --------------------------------------------------------------------------- #
# 1. Bulk renewal                                                             #
# --------------------------------------------------------------------------- #

async def run_renewal_batch(args: dict, progress: Progress) -> dict:
    months = int(args.get("months") or 12)
    commit = bool(args.get("commit"))
    filters = _filters_from(args)

    if not filters:
        return {
            "error": "Refusing to run against the whole book. Narrow it with a "
            "filter such as expiring_within_days or product."
        }

    await progress("Selecting contracts…")
    rows = await asyncio.to_thread(_select_renewable, filters)

    if not rows:
        return {"matched": 0, "renewed": 0, "note": "Nothing matched that filter."}

    plan = [
        {
            "id": r["id"],
            "insured_company": r["insured_company"],
            "product": r["product"],
            "insurer": r["insurer"],
            "premium": r["premium"],
            "current_end": r["end_date"],
            "new_end": (date.fromisoformat(r["end_date"]) + timedelta(days=months * 30)).isoformat(),
        }
        for r in rows
    ]
    premium = sum(r["premium"] for r in rows)

    if not commit:
        # Preview by default. A bulk mutation is exactly the kind of thing that
        # should not happen because a model was 80% sure it was wanted.
        record = batches.put(
            {
                "kind": "renewal",
                "committed": False,
                "months": months,
                "scope": filters,
                "matched": len(rows),
                "renewed": 0,
                "failed": [],
                "premium_affected": premium,
                "items": plan,
            }
        )
        await progress(f"Dry run: {len(rows)} contracts would be renewed.")
        return {
            "batch_id": record["id"],
            "committed": False,
            "matched": len(rows),
            "premium_affected": premium,
            "months": months,
            "preview": plan[:10],
            "note": "Nothing has changed. Call again with commit=true to apply, "
            "after the user has confirmed.",
        }

    await progress(f"Renewing {len(rows)} contracts in one transaction…")
    renewed, failed = await asyncio.to_thread(
        _renew_all, [r["id"] for r in rows], months
    )

    record = batches.put(
        {
            "kind": "renewal",
            "committed": True,
            "months": months,
            "scope": filters,
            "matched": len(rows),
            "renewed": len(renewed),
            "failed": failed,
            "premium_affected": premium,
            "items": renewed,
        }
    )
    await progress(f"Committed. {len(renewed)} renewed, {len(failed)} failed.")
    return {
        "batch_id": record["id"],
        "committed": True,
        "matched": len(rows),
        "renewed": len(renewed),
        "failed": failed,
        "premium_affected": premium,
        "note": "Call show_batch_result with this batch_id to put it on screen.",
    }


def _select_renewable(filters: dict) -> list[dict]:
    with Session(engine()) as session:
        rows, _ = queries.search(session, sort_by="end_date", sort_dir="asc", **filters)
        return [c.public() for c in rows if not c.is_draft]


def _renew_all(ids: list[str], months: int) -> tuple[list[dict], list[dict]]:
    """
    One session, one commit. Either the whole batch lands or none of it does —
    which is the actual reason this is a server tool and not fourteen trips
    through the page.
    """
    renewed: list[dict] = []
    failed: list[dict] = []

    with Session(engine()) as session:
        for contract_id in ids:
            try:
                contract = renew_contract(
                    session, contract_id, RenewalRequest(months=months), commit=False
                )
                renewed.append(
                    {
                        "id": contract.id,
                        "insured_company": contract.insured_company,
                        "product": contract.product,
                        "insurer": contract.insurer,
                        "premium": contract.premium,
                        "new_end": contract.end_date.isoformat(),
                        "renewal_count": contract.renewal_count,
                    }
                )
            except Exception as err:  # noqa: BLE001
                failed.append({"id": contract_id, "error": str(err)})

        if failed:
            # All-or-nothing: a partially renewed book is worse than an
            # untouched one, because nobody knows where it stopped.
            session.rollback()
            return [], failed

        session.commit()

    return renewed, failed


# --------------------------------------------------------------------------- #
# 2. Report generation                                                        #
# --------------------------------------------------------------------------- #

async def generate_renewal_report(args: dict, progress: Progress) -> dict:
    filters = _filters_from(args)
    if not filters:
        filters = {"expiring_within_days": 90}
    group_by = args.get("group_by") or "product"
    title = args.get("title") or "Renewal pipeline"

    await progress("Querying the book…")
    data = await asyncio.to_thread(_report_data, filters, group_by)

    await progress("Assembling the report…")
    await asyncio.sleep(0.2)

    record = reports.put(
        {
            "title": title,
            "scope": filters,
            "group_by": group_by,
            "headline": data["headline"],
            "sections": data["sections"],
            "markdown": _report_markdown(title, filters, data),
        }
    )
    await progress(f"Report {record['id']} ready.")

    return {
        "report_id": record["id"],
        "title": title,
        "headline": data["headline"],
        "note": "Call show_report with this report_id to put it on screen.",
    }


def _report_data(filters: dict, group_by: str) -> dict:
    with Session(engine()) as session:
        summary = queries.summarise(session, group_by=group_by, **filters)
        rows, total = queries.search(
            session, sort_by="end_date", sort_dir="asc", limit=25, **filters
        )
        contracts = [c.public() for c in rows]

    return {
        "headline": {
            "contracts": summary["totals"]["contracts"],
            "premium_at_risk": summary["totals"]["total_premium"],
            "sum_insured": summary["totals"]["total_sum_insured"],
            "flagged_for_renewal": summary["totals"]["renewal_pending"],
            "earliest_expiry": contracts[0]["end_date"] if contracts else None,
        },
        "sections": [
            {
                "heading": f"By {group_by}",
                "columns": ["Group", "Contracts", "Premium", "Sum insured", "Flagged"],
                "rows": [
                    [
                        g["key"],
                        g["contracts"],
                        g["total_premium"],
                        g["total_sum_insured"],
                        g["renewal_pending"],
                    ]
                    for g in summary["groups"]
                ],
            },
            {
                "heading": "Action list (soonest first)",
                "columns": ["Contract", "Insured", "Product", "Insurer", "Expires", "Days", "Premium"],
                "rows": [
                    [
                        c["id"],
                        c["insured_company"],
                        c["product"],
                        c["insurer"],
                        c["end_date"],
                        c["days_to_expiry"],
                        c["premium"],
                    ]
                    for c in contracts
                ],
                "truncated": total > len(contracts),
                "total": total,
            },
        ],
    }


def _report_markdown(title: str, filters: dict, data: dict) -> str:
    h = data["headline"]
    scope = ", ".join(f"{k}={v}" for k, v in filters.items()) or "whole book"
    lines = [
        f"# {title}",
        "",
        f"Scope: {scope}",
        "",
        f"- Contracts: {h['contracts']}",
        f"- Premium at risk: EUR {h['premium_at_risk']:,}",
        f"- Sum insured: EUR {h['sum_insured']:,}",
        f"- Flagged for renewal: {h['flagged_for_renewal']}",
        f"- Earliest expiry: {h['earliest_expiry']}",
    ]
    for section in data["sections"]:
        lines += ["", f"## {section['heading']}", "", "| " + " | ".join(section["columns"]) + " |",
                  "|" + "|".join(["---"] * len(section["columns"])) + "|"]
        for row in section["rows"]:
            lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 3. External data the page cannot reach                                      #
# --------------------------------------------------------------------------- #

# Stand-in for a market data subscription. The numbers are invented; the point
# is that this is information the browser has no route to, so no amount of UI
# automation would ever produce it.
_BENCHMARKS = {
    "D&O":   {"rate_low": 0.0035, "rate_high": 0.0075, "trend": "softening", "commentary": "Capacity returning; renewals broadly flat to -10%."},
    "Cyber": {"rate_low": 0.0080, "rate_high": 0.0160, "trend": "flat",      "commentary": "Rate reductions have stalled; insurers holding on ransomware sublimits."},
    "PI":    {"rate_low": 0.0050, "rate_high": 0.0110, "trend": "hardening", "commentary": "Construction and valuation risks seeing +5-15%."},
    "Crime": {"rate_low": 0.0040, "rate_high": 0.0080, "trend": "flat",      "commentary": "Stable; social engineering sublimits under scrutiny."},
    "EPLI":  {"rate_low": 0.0080, "rate_high": 0.0150, "trend": "hardening", "commentary": "Claims frequency up; expect +10%."},
    "W&I":   {"rate_low": 0.0070, "rate_high": 0.0140, "trend": "softening", "commentary": "Competitive; deal flow down, capacity plentiful."},
}


async def benchmark_rates(args: dict, progress: Progress) -> dict:
    contract_id = args.get("contract_id")
    product = args.get("product")

    # If a contract was named, look its product up rather than making the
    # caller fetch the contract first just to tell us something we can read.
    contract = None
    if contract_id:
        contract = await asyncio.to_thread(_load_contract, contract_id)
        if contract is None:
            return {"error": f"No contract with id {contract_id!r}."}
        product = product or contract["product"]

    if product not in _BENCHMARKS:
        return {
            "error": f"No benchmark for product {product!r}. Pass one of "
            f"{', '.join(_BENCHMARKS)}, or a contract_id to infer it."
        }

    await progress(f"Querying market benchmarks for {product}…")
    await asyncio.sleep(0.3)  # stands in for an external call
    bench = _BENCHMARKS[product]

    comparison = None
    if contract:
        rate = contract["premium"] / contract["sum_insured"]
        if rate < bench["rate_low"]:
            verdict = "below the benchmark band"
        elif rate > bench["rate_high"]:
            verdict = "above the benchmark band"
        else:
            verdict = "within the benchmark band"
        comparison = {
            "contract_id": contract["id"],
            "product": contract["product"],
            "insured_company": contract["insured_company"],
            "rate_on_line": round(rate, 5),
            "benchmark_low": bench["rate_low"],
            "benchmark_high": bench["rate_high"],
            "verdict": verdict,
        }

    return {"product": product, "benchmark": bench, "comparison": comparison,
            "source": "Demo market data (synthetic)"}


def _load_contract(contract_id: str) -> Optional[dict]:
    from .repository import ContractNotFound, get_contract

    with Session(engine()) as session:
        try:
            return get_contract(session, contract_id).public()
        except ContractNotFound:
            return None


# --------------------------------------------------------------------------- #
# Registry                                                                    #
# --------------------------------------------------------------------------- #

DEFINITIONS: list[dict] = [
    {
        "name": "run_renewal_batch",
        "description": (
            "Renew every contract matching a filter in one atomic transaction. "
            "Use this instead of calling renew_contract repeatedly — it is one "
            "round-trip, it cannot stop half-finished, and it produces an "
            "auditable batch record. Runs as a DRY RUN by default and changes "
            "nothing: show the user the preview, get their agreement, then call "
            "again with commit=true. Afterwards call show_batch_result with the "
            "returned batch_id so the user can see what happened."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_FILTER_PROPERTIES,
                "months": {"type": "integer", "description": "Length of each new term. Defaults to 12."},
                "commit": {
                    "type": "boolean",
                    "description": "false (default) previews; true applies the changes.",
                },
            },
        },
    },
    {
        "name": "generate_renewal_report",
        "description": (
            "Build a renewal report for a slice of the book: headline figures, a "
            "breakdown, and a dated action list. Assembling this is computation, "
            "not clicking — it does not belong in the UI. Returns a report_id; "
            "call show_report with it to display the result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_FILTER_PROPERTIES,
                "group_by": {
                    "type": "string",
                    "enum": ["product", "insurer", "status", "broker", "industry"],
                },
                "title": {"type": "string"},
            },
        },
    },
    {
        "name": "benchmark_rates",
        "description": (
            "Look up current market rate benchmarks for a product, optionally "
            "comparing one contract's rate on line against the band. Pass "
            "contract_id on its own and the product is inferred. This data "
            "comes from outside the application — the page has no route to it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {
                    "type": "string",
                    "enum": list(PRODUCTS),
                    "description": "Omit if you pass contract_id — it is inferred.",
                },
                "contract_id": {
                    "type": "string",
                    "description": "Compare this contract's rate on line against the band.",
                },
            },
        },
    },
]

HANDLERS: dict[str, Callable[[dict, Progress], Awaitable[dict]]] = {
    "run_renewal_batch": run_renewal_batch,
    "generate_renewal_report": generate_renewal_report,
    "benchmark_rates": benchmark_rates,
}

NAMES = frozenset(HANDLERS)


def definitions() -> list[dict]:
    return [dict(d) for d in DEFINITIONS]


def is_server_tool(name: str) -> bool:
    return name in NAMES


async def execute(name: str, args: dict, progress: Progress) -> dict:
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"Unknown server tool {name!r}"}
    try:
        return await handler(args or {}, progress)
    except Exception as err:  # noqa: BLE001
        log.exception("server tool %s failed", name)
        return {"error": f"{type(err).__name__}: {err}"}
