"""Filtering, sorting and aggregation — the SQL the assistant depends on."""

import pytest

from app.data import queries
from app.domain.filters import ContractFilter


def test_status_is_derived_from_the_term(book):
    by_id = {c.id: c.status for c in queries.search(book, ContractFilter())[0]}
    assert by_id["FL-0001"] == "expired"
    assert by_id["FL-0002"] == "expiring"
    assert by_id["FL-0004"] == "active"
    assert by_id["FL-0006"] == "draft"  # draft beats its dates


@pytest.mark.parametrize(
    "filters, expected",
    [
        (dict(product="Cyber"), {"FL-0002", "FL-0003"}),
        (dict(status="expired"), {"FL-0001"}),
        (dict(status="draft"), {"FL-0006"}),
        (dict(insurer="allianz"), {"FL-0003", "FL-0005"}),  # case-insensitive substring
        (dict(renewal_pending=True), {"FL-0002"}),
        (dict(min_sum_insured=10_000_000), {"FL-0001", "FL-0005"}),
        (dict(max_premium=20_000), {"FL-0003", "FL-0004"}),
        (dict(product="Cyber", insurer="Allianz"), {"FL-0003"}),
    ],
)
def test_filters_select_the_right_rows(book, filters, expected):
    rows, total = queries.search(book, ContractFilter(**filters))
    assert {c.id for c in rows} == expected
    assert total == len(expected)


def test_expiring_within_days_excludes_expired_and_drafts(book):
    rows, _ = queries.search(book, ContractFilter(expiring_within_days=60))
    ids = {c.id for c in rows}
    assert ids == {"FL-0002", "FL-0003"}
    assert "FL-0001" not in ids, "already expired"
    assert "FL-0006" not in ids, "draft has not incepted"


def test_free_text_requires_every_term(book):
    one, _ = queries.search(book, ContractFilter(query="Alpha"))
    assert {c.id for c in one} == {"FL-0001"}

    # Both terms must match somewhere on the same contract.
    both, _ = queries.search(book, ContractFilter(query="Cyber MedTech"))
    assert {c.id for c in both} == {"FL-0002"}

    neither, _ = queries.search(book, ContractFilter(query="Cyber Utilities"))
    assert neither == []


def test_free_text_searches_notes(book):
    rows, _ = queries.search(book, ContractFilter(query="cover for Gamma"))
    assert {c.id for c in rows} == {"FL-0003"}


def test_sort_and_limit(book):
    rows, total = queries.search(
        book, ContractFilter(), sort_by="sum_insured", sort_dir="desc", limit=2
    )
    assert [c.id for c in rows] == ["FL-0005", "FL-0001"]
    assert total == 6, "total counts matches before the limit"


def test_sort_rejects_arbitrary_columns(book):
    with pytest.raises(ValueError):
        queries.search(book, ContractFilter(), sort_by="notes; DROP TABLE contracts")
    with pytest.raises(ValueError):
        queries.search(book, ContractFilter(), sort_dir="sideways")


def test_summarise_totals_match_the_rows(book):
    result = queries.summarise(book, ContractFilter(), group_by="product")
    assert result["totals"]["contracts"] == 6
    assert result["totals"]["total_premium"] == 40_000 + 60_000 + 20_000 + 18_000 + 130_000 + 27_000
    assert result["totals"]["renewal_pending"] == 1

    cyber = next(g for g in result["groups"] if g["key"] == "Cyber")
    assert cyber["contracts"] == 2
    assert cyber["total_premium"] == 80_000


def test_summarise_respects_the_filter(book):
    result = queries.summarise(book, ContractFilter(product="D&O"), group_by="insurer")
    assert result["totals"]["contracts"] == 2
    assert {g["key"] for g in result["groups"]} == {"Chubb", "Allianz"}


def test_summarise_groups_by_derived_status(book):
    result = queries.summarise(book, ContractFilter(), group_by="status")
    counts = {g["key"]: g["contracts"] for g in result["groups"]}
    assert counts == {"expired": 1, "expiring": 2, "active": 2, "draft": 1}


def test_summarise_rejects_unknown_grouping(book):
    with pytest.raises(ValueError):
        queries.summarise(book, ContractFilter(), group_by="notes")
