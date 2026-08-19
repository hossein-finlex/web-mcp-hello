"""
Business operations, tested with no assistant in sight.

That these pass without a ToolContext, a prompt, or a WebSocket is the point of
the services layer: the logic is the application's, not the chat panel's.
"""

from __future__ import annotations

import pytest

from app.data import artifacts, repository
from app.domain.filters import ContractFilter
from app.services import market, renewals, reporting


# --------------------------------------------------------------------------- #
# renewals
# --------------------------------------------------------------------------- #

def test_plan_projects_new_terms_without_writing(book):
    before = {c.id: c.end_date for c in repository.list_contracts(book)}

    plan = renewals.plan_batch(book, ContractFilter(expiring_within_days=60), months=12)

    assert plan.matched == 2
    assert {i.id for i in plan.items} == {"FL-0002", "FL-0003"}
    assert plan.premium_affected == 60_000 + 20_000

    for item in plan.items:
        assert item.new_end > item.current_end

    book.expire_all()
    after = {c.id: c.end_date for c in repository.list_contracts(book)}
    assert after == before, "planning must not write"


def test_plan_excludes_drafts_and_says_which(book):
    """
    The rule lives in `is_renewable`. The bulk path excludes rather than raising —
    and now reports the exclusion instead of hiding it.
    """
    plan = renewals.plan_batch(book, ContractFilter(product="Crime"))
    assert plan.matched == 0
    assert plan.excluded_drafts == ["FL-0006"]


def test_is_renewable_is_the_single_definition(book):
    draft = repository.get(book, "FL-0006")
    live = repository.get(book, "FL-0002")

    assert renewals.is_renewable(live) == (True, None)
    allowed, reason = renewals.is_renewable(draft)
    assert allowed is False and "draft" in reason


def test_apply_renews_every_planned_contract(book):
    plan = renewals.plan_batch(book, ContractFilter(expiring_within_days=60))
    outcome = renewals.apply_batch(book, plan)

    assert outcome.committed is True
    assert len(outcome.renewed) == 2
    assert outcome.failed == []

    book.expire_all()
    for renewed in outcome.renewed:
        contract = repository.get(book, renewed.id)
        assert contract.renewal_pending is False
        assert contract.end_date.isoformat() == renewed.new_end


def test_apply_writes_an_audit_record(book):
    plan = renewals.plan_batch(book, ContractFilter(expiring_within_days=60))
    outcome = renewals.apply_batch(book, plan)

    record = artifacts.get_batch(book, outcome.batch_id)
    assert record.committed is True
    assert record.matched == 2
    assert record.scope == {"expiring_within_days": 60}
    assert len(record.items) == 2


def test_record_plan_stores_a_dry_run(book):
    plan = renewals.plan_batch(book, ContractFilter(expiring_within_days=60))
    batch_id = renewals.record_plan(book, plan)

    record = artifacts.get_batch(book, batch_id)
    assert record.committed is False
    assert record.renewed == 0
    assert record.matched == 2


def test_apply_is_all_or_nothing(book, monkeypatch):
    """
    A partially renewed book is worse than an untouched one, so one failure must
    undo the rest.
    """
    plan = renewals.plan_batch(book, ContractFilter(expiring_within_days=60))
    assert plan.matched == 2

    original = {c.id: c.end_date for c in repository.list_contracts(book)}
    real_renew = repository.renew
    calls = {"n": 0}

    def flaky(session, contract_id, req, commit=True):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("insurer declined")
        return real_renew(session, contract_id, req, commit=commit)

    monkeypatch.setattr(renewals.repository, "renew", flaky)
    outcome = renewals.apply_batch(book, plan)

    assert outcome.renewed == []
    assert len(outcome.failed) == 1
    assert "declined" in outcome.failed[0]["error"]

    book.expire_all()
    after = {c.id: c.end_date for c in repository.list_contracts(book)}
    assert after == original, "the successful renewal must have been rolled back"

    # The attempt is still auditable.
    record = artifacts.get_batch(book, outcome.batch_id)
    assert record.renewed == 0
    assert len(record.failed) == 1


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

def test_report_headline_matches_the_filtered_book(book):
    report = reporting.build_renewal_report(
        book, ContractFilter(expiring_within_days=60), group_by="insurer"
    )
    assert report.headline.contracts == 2
    assert report.headline.premium_at_risk == 80_000
    assert report.headline.flagged_for_renewal == 1
    assert report.headline.earliest_expiry is not None


def test_report_has_a_breakdown_and_an_action_list(book):
    report = reporting.build_renewal_report(book, ContractFilter(), group_by="product")
    headings = [s.heading for s in report.sections]
    assert headings == ["By product", "Action list (soonest first)"]
    assert report.sections[1].total == report.headline.contracts


def test_empty_filter_is_widened_to_a_horizon(book):
    """"The renewal pipeline" implies a window, not the entire book."""
    report = reporting.build_renewal_report(book, ContractFilter())
    assert report.scope == {"expiring_within_days": reporting.DEFAULT_WINDOW_DAYS}


def test_report_markdown_is_renderable(book):
    report = reporting.build_renewal_report(book, ContractFilter(expiring_within_days=60))
    md = report.markdown
    assert md.startswith(f"# {report.title}")
    assert "## By product" in md
    assert md.count("|") > 10, "tables should be rendered as pipe tables"


def test_report_can_be_saved_and_reread(book):
    report = reporting.build_renewal_report(book, ContractFilter(expiring_within_days=60))
    report_id = reporting.save(book, report)

    stored = artifacts.get_report(book, report_id)
    assert stored.title == report.title
    assert stored.headline["contracts"] == report.headline.contracts
    assert len(stored.sections) == 2


# --------------------------------------------------------------------------- #
# market
# --------------------------------------------------------------------------- #

def test_benchmark_by_product_needs_no_contract(book):
    result = market.benchmark(book, product="Cyber")
    assert result.product == "Cyber"
    assert result.benchmark.rate_low < result.benchmark.rate_high
    assert result.comparison is None


def test_benchmark_infers_the_product_from_a_contract(book):
    result = market.benchmark(book, contract_id="FL-0001")
    assert result.product == "D&O"
    assert result.comparison.contract_id == "FL-0001"


@pytest.mark.parametrize(
    "premium, limit, expected",
    [
        (10_000, 10_000_000, "below the benchmark band"),   # 0.10%
        (50_000, 10_000_000, "within the benchmark band"),  # 0.50%
        (900_000, 10_000_000, "above the benchmark band"),  # 9.00%
    ],
)
def test_rate_on_line_verdicts(book, premium, limit, expected):
    from app.domain.models import ContractUpdate

    repository.update(
        book, "FL-0001", ContractUpdate(premium=premium, sum_insured=limit)
    )
    result = market.benchmark(book, contract_id="FL-0001")
    assert result.comparison.verdict == expected


def test_unknown_product_raises_a_typed_error(book):
    with pytest.raises(market.UnknownProduct):
        market.benchmark(book, product="Aviation")


def test_product_for_resolves_without_a_full_benchmark(book):
    assert market.product_for(book, contract_id="FL-0002") == "Cyber"
    assert market.product_for(book, product="PI") == "PI"
    assert market.product_for(book) is None
