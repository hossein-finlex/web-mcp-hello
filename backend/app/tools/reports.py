"""Report generation, exposed to the assistant. Logic lives in services.reporting."""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from ..domain.filters import ContractFilter
from ..domain.models import GROUPABLE
from ..services import reporting
from .registry import ToolContext, server_tool


class ReportInput(ContractFilter):
    group_by: str = Field(
        default="product", description=f"One of: {', '.join(GROUPABLE)}."
    )
    title: Optional[str] = Field(default=None, description="Report heading.")

    def filters(self) -> ContractFilter:
        return ContractFilter.model_validate(
            self.model_dump(exclude={"group_by", "title"}, exclude_none=True)
        )


@server_tool()
async def generate_renewal_report(args: ReportInput, ctx: ToolContext) -> dict:
    """
    Build a renewal report for a slice of the book: headline figures, a breakdown,
    and a dated action list. Assembling this is computation, not clicking — it
    does not belong in the UI. Returns a report_id; call show_report with it to
    display the result.
    """
    if args.group_by not in GROUPABLE:
        return {"error": f"group_by must be one of {', '.join(GROUPABLE)}."}

    await ctx.say("Querying the book…")
    report = await ctx.in_session(
        reporting.build_renewal_report, args.filters(), args.group_by, args.title
    )

    await ctx.say("Assembling the report…")
    report_id = await ctx.in_session(reporting.save, report)
    await ctx.say(f"Report {report_id} ready.")

    return {
        "report_id": report_id,
        "title": report.title,
        "headline": report.headline.model_dump(),
        "note": "Call show_report with this report_id to put it on screen.",
    }
