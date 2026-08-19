"""Rate benchmarks, exposed to the assistant. Logic lives in services.market."""

from __future__ import annotations

import asyncio
from typing import Optional

from pydantic import BaseModel, Field

from ..domain.models import Product
from ..services import market
from .registry import ToolContext, server_tool

# Retained so tests and the reporting docs can point at the fixture.
FIXTURE = market.FIXTURE


class BenchmarkInput(BaseModel):
    product: Optional[Product] = Field(
        default=None, description="Omit if you pass contract_id — it is inferred."
    )
    contract_id: Optional[str] = Field(
        default=None,
        description="Compare this contract's rate on line against the band.",
    )


@server_tool()
async def benchmark_rates(args: BenchmarkInput, ctx: ToolContext) -> dict:
    """
    Look up current market rate benchmarks for a product, optionally comparing one
    contract's rate on line against the band. Pass contract_id on its own and the
    product is inferred. This data comes from outside the application — the page
    has no route to it.
    """
    try:
        # Resolve first so the progress line names the product, not the id.
        product = await ctx.in_session(
            market.product_for, args.product, args.contract_id
        )
    except market.ContractNotFound:
        return {"error": f"No contract with id {args.contract_id!r}."}

    await ctx.say(f"Querying market benchmarks for {product}…")
    await asyncio.sleep(0.3)  # stands in for an external call

    try:
        result = await ctx.in_session(market.benchmark, args.product, args.contract_id)
    except market.ContractNotFound:
        return {"error": f"No contract with id {args.contract_id!r}."}
    except market.UnknownProduct as err:
        return {"error": str(err)}

    return result.model_dump()
