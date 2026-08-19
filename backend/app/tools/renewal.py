"""Bulk renewal, exposed to the assistant. The logic lives in services.renewals."""

from __future__ import annotations

from pydantic import Field

from ..domain.filters import ContractFilter
from ..services import renewals
from .registry import ToolContext, server_tool


class RenewalBatchInput(ContractFilter):
    """The filter fields plus the batch's own two parameters."""

    months: int = Field(default=12, ge=1, le=60, description="Length of each new term.")
    commit: bool = Field(
        default=False,
        description="false (default) previews; true applies the changes.",
    )

    def filters(self) -> ContractFilter:
        return ContractFilter.model_validate(
            self.model_dump(exclude={"months", "commit"}, exclude_none=True)
        )


@server_tool()
async def run_renewal_batch(args: RenewalBatchInput, ctx: ToolContext) -> dict:
    """
    Renew every contract matching a filter in one atomic transaction. Use this
    instead of calling renew_contract repeatedly — it is one round-trip, it cannot
    stop half-finished, and it produces an auditable batch record. Runs as a DRY
    RUN by default and changes nothing: show the user the preview, get their
    agreement, then call again with commit=true. Afterwards call
    show_batch_result with the returned batch_id so the user can see what
    happened.
    """
    filters = args.filters()
    if filters.is_empty():
        return {
            "error": "Refusing to run against the whole book. Narrow it with a "
            "filter such as expiring_within_days or product."
        }

    await ctx.say("Selecting contracts…")
    plan = await ctx.in_session(renewals.plan_batch, filters, args.months)

    if not plan.items:
        return {"matched": 0, "renewed": 0, "note": "Nothing matched that filter."}

    if not args.commit:
        batch_id = await ctx.in_session(renewals.record_plan, plan)
        await ctx.say(f"Dry run: {plan.matched} contracts would be renewed.")
        return {
            "batch_id": batch_id,
            "committed": False,
            "matched": plan.matched,
            "premium_affected": plan.premium_affected,
            "months": plan.months,
            "preview": [i.model_dump() for i in plan.items[:10]],
            "note": "Nothing has changed. Call again with commit=true to apply, "
            "after the user has confirmed.",
        }

    await ctx.say(f"Renewing {plan.matched} contracts in one transaction…")
    outcome = await ctx.in_session(renewals.apply_batch, plan)
    await ctx.say(
        f"Committed. {len(outcome.renewed)} renewed, {len(outcome.failed)} failed."
    )
    return {
        "batch_id": outcome.batch_id,
        "committed": True,
        "matched": outcome.matched,
        "renewed": len(outcome.renewed),
        "failed": outcome.failed,
        "premium_affected": outcome.premium_affected,
        "note": "Call show_batch_result with this batch_id to put it on screen.",
    }
