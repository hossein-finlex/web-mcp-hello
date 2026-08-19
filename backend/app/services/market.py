"""
Rate benchmarking against market bands.

The bands come from a fixture rather than code, so refreshing them is editing a
data file. In a real deployment this module would call a market-data provider;
nothing above it would change.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Session

from ..data import repository
from ..data.repository import ContractNotFound
from ..domain.models import PRODUCTS

FIXTURE = Path(__file__).parent.parent / "tools" / "data" / "benchmarks.json"


# Re-exported so an adapter over this service depends only on this module.
__all__ = ["Band", "Benchmark", "Comparison", "ContractNotFound", "UnknownProduct",
           "bands", "benchmark", "product_for", "source", "FIXTURE"]


class UnknownProduct(Exception):
    def __init__(self, product: Optional[str]):
        self.product = product
        super().__init__(
            f"No benchmark for product {product!r}. Pass one of "
            f"{', '.join(PRODUCTS)}, or a contract_id to infer it."
        )


class Band(BaseModel):
    rate_low: float
    rate_high: float
    trend: str
    commentary: str


class Comparison(BaseModel):
    contract_id: str
    product: str
    insured_company: str
    rate_on_line: float
    benchmark_low: float
    benchmark_high: float
    verdict: str


class Benchmark(BaseModel):
    product: str
    benchmark: Band
    comparison: Optional[Comparison] = None
    source: str


@lru_cache(maxsize=1)
def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def bands() -> dict[str, dict]:
    return _fixture()["bands"]


def source() -> str:
    return _fixture()["_source"]


def product_for(
    session: Session,
    product: Optional[str] = None,
    contract_id: Optional[str] = None,
) -> Optional[str]:
    """Which product a benchmark request is about, reading the contract if needed."""
    if product:
        return product
    if contract_id:
        return repository.get(session, contract_id).product  # raises ContractNotFound
    return None


def benchmark(
    session: Session,
    product: Optional[str] = None,
    contract_id: Optional[str] = None,
) -> Benchmark:
    """
    Look up a band, optionally comparing one contract's rate on line against it.

    Given a contract_id the product is read from the contract rather than demanded
    from the caller — asking for both cost the assistant two extra round-trips
    before it was fixed.
    """
    contract = None
    if contract_id:
        contract = repository.get(session, contract_id)  # raises ContractNotFound
        product = product or contract.product

    if product not in bands():
        raise UnknownProduct(product)

    band = Band(**bands()[product])

    comparison = None
    if contract:
        rate = contract.premium / contract.sum_insured
        if rate < band.rate_low:
            verdict = "below the benchmark band"
        elif rate > band.rate_high:
            verdict = "above the benchmark band"
        else:
            verdict = "within the benchmark band"
        comparison = Comparison(
            contract_id=contract.id,
            product=contract.product,
            insured_company=contract.insured_company,
            rate_on_line=round(rate, 5),
            benchmark_low=band.rate_low,
            benchmark_high=band.rate_high,
            verdict=verdict,
        )

    return Benchmark(
        product=product, benchmark=band, comparison=comparison, source=source()
    )
