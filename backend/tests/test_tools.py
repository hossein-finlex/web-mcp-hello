"""Server tools: the registry contract, and the behaviour that matters."""

import json
from pathlib import Path

import pytest

from app import tools as server_tools
from app.db import session_scope
from app.domain.filters import ContractFilter


class RecordingContext(server_tools.ToolContext):
    """ToolContext is frozen, so the recorder holds the list beside it."""


@pytest.fixture
def ctx(book):
    """A ToolContext that records progress instead of sending it over a socket."""
    messages: list[str] = []

    async def progress(text: str) -> None:
        messages.append(text)

    context = server_tools.ToolContext(progress=progress, session_factory=session_scope)
    return _Ctx(context, messages)


class _Ctx:
    """Thin proxy so tests can read `ctx.messages` off a frozen context."""

    def __init__(self, context, messages):
        self._context = context
        self.messages = messages

    def __getattr__(self, item):
        return getattr(self._context, item)


# --------------------------------------------------------------------------- #
# Registry contract
# --------------------------------------------------------------------------- #

def test_every_tool_is_registered_once_with_a_usable_definition():
    definitions = server_tools.definitions()
    names = [d["name"] for d in definitions]

    assert sorted(names) == [
        "benchmark_rates",
        "generate_renewal_report",
        "run_renewal_batch",
    ]
    assert len(names) == len(set(names)), "a name was registered twice"

    for d in definitions:
        assert d["description"].strip(), f"{d['name']} has no description"
        assert d["input_schema"]["type"] == "object"
        assert "properties" in d["input_schema"]
        # No Pydantic bookkeeping should reach the model.
        assert "$defs" not in json.dumps(d["input_schema"])
        assert "anyOf" not in json.dumps(d["input_schema"])


def test_filter_tools_expose_the_whole_filter_contract():
    """A new filter field must appear in the tools automatically, not by hand."""
    filter_fields = set(ContractFilter.model_fields)
    for name in ("run_renewal_batch", "generate_renewal_report"):
        tool = server_tools.get(name)
        props = set(tool.definition()["input_schema"]["properties"])
        assert filter_fields <= props, f"{name} is missing {filter_fields - props}"


async def test_invalid_arguments_come_back_as_a_correctable_error(ctx):
    result = await server_tools.execute(
        "run_renewal_batch", {"product": "Aviation"}, ctx
    )
    assert "error" in result
    assert "product" in result["error"], "the model needs to know which field"


async def test_unknown_tool_is_reported_not_raised(ctx):
    result = await server_tools.execute("nonexistent_tool", {}, ctx)
    assert "error" in result


# --------------------------------------------------------------------------- #
# run_renewal_batch
# --------------------------------------------------------------------------- #

async def test_batch_refuses_to_run_against_the_whole_book(ctx):
    result = await server_tools.execute("run_renewal_batch", {}, ctx)
    assert "error" in result
    assert "whole book" in result["error"]


async def test_batch_dry_run_changes_nothing(ctx, book):
    from app.data import repository

    before = {c.id: c.end_date for c in repository.list_contracts(book)}

    result = await server_tools.execute(
        "run_renewal_batch", {"expiring_within_days": 60}, ctx
    )

    assert result["committed"] is False
    assert result["matched"] == 2
    assert result["batch_id"].startswith("BATCH-")
    assert "preview" in result

    book.expire_all()
    after = {c.id: c.end_date for c in repository.list_contracts(book)}
    assert after == before, "a dry run must not write"


async def test_batch_commit_renews_and_records(ctx, book):
    from app.data import artifacts, repository

    result = await server_tools.execute(
        "run_renewal_batch", {"expiring_within_days": 60, "commit": True}, ctx
    )

    assert result["committed"] is True
    assert result["renewed"] == 2
    assert result["failed"] == []

    book.expire_all()
    renewed = repository.get(book, "FL-0002")
    assert renewed.renewal_pending is False

    record = artifacts.get_batch(book, result["batch_id"])
    assert record.committed is True
    assert record.matched == 2
    assert len(record.items) == 2


async def test_batch_excludes_drafts(ctx, book):
    result = await server_tools.execute(
        "run_renewal_batch", {"product": "Crime"}, ctx
    )
    # FL-0006 is the only Crime contract and it is a draft.
    assert result["matched"] == 0


async def test_batch_reports_progress(ctx):
    await server_tools.execute("run_renewal_batch", {"expiring_within_days": 60}, ctx)
    assert any("Selecting" in m for m in ctx.messages)
    assert any("Dry run" in m for m in ctx.messages)


# --------------------------------------------------------------------------- #
# generate_renewal_report
# --------------------------------------------------------------------------- #

async def test_report_is_persisted_and_headline_matches_the_book(ctx, book):
    from app.data import artifacts

    result = await server_tools.execute(
        "generate_renewal_report", {"expiring_within_days": 60, "group_by": "insurer"}, ctx
    )

    assert result["report_id"].startswith("RPT-")
    assert result["headline"]["contracts"] == 2

    record = artifacts.get_report(book, result["report_id"])
    assert record.group_by == "insurer"
    assert record.markdown.startswith("# ")
    assert len(record.sections) == 2


async def test_report_rejects_unknown_grouping(ctx):
    result = await server_tools.execute(
        "generate_renewal_report", {"group_by": "notes"}, ctx
    )
    assert "error" in result


# --------------------------------------------------------------------------- #
# benchmark_rates
# --------------------------------------------------------------------------- #

async def test_benchmark_infers_the_product_from_a_contract(ctx):
    """
    The regression that mattered: requiring `product` alongside `contract_id` cost
    two extra round-trips while the model went and fetched the contract.
    """
    result = await server_tools.execute("benchmark_rates", {"contract_id": "FL-0001"}, ctx)

    assert "error" not in result
    assert result["product"] == "D&O"
    assert result["comparison"]["contract_id"] == "FL-0001"
    assert result["comparison"]["verdict"].endswith("benchmark band")


async def test_benchmark_without_either_argument_explains_itself(ctx):
    result = await server_tools.execute("benchmark_rates", {}, ctx)
    assert "error" in result
    assert "contract_id" in result["error"]


async def test_benchmark_rejects_a_missing_contract(ctx):
    result = await server_tools.execute("benchmark_rates", {"contract_id": "FL-9999"}, ctx)
    assert "error" in result


def test_benchmark_fixture_covers_every_product():
    from app.domain.models import PRODUCTS
    from app.tools.market import FIXTURE

    bands = json.loads(Path(FIXTURE).read_text())["bands"]
    assert set(bands) == set(PRODUCTS)
    for product, band in bands.items():
        assert band["rate_low"] < band["rate_high"], product
