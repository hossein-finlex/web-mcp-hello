"""Contract writes: term arithmetic, renewal semantics, and atomicity."""

from datetime import date

import pytest

from app.data import repository
from app.domain.models import ContractCreate, ContractUpdate, RenewalRequest


@pytest.mark.parametrize(
    "start, months, expected",
    [
        (date(2026, 9, 30), 12, date(2027, 9, 30)),
        (date(2026, 12, 31), 12, date(2027, 12, 31)),
        (date(2026, 1, 31), 1, date(2026, 2, 28)),   # clamps to a short month
        (date(2028, 2, 29), 12, date(2029, 2, 28)),  # leap day, non-leap target
        (date(2026, 8, 19), 24, date(2028, 8, 19)),
    ],
)
def test_add_months_clamps_to_valid_days(start, months, expected):
    assert repository.add_months(start, months) == expected


def test_renewal_starts_where_the_old_term_ended(book):
    before = repository.get(book, "FL-0002")
    old_end = before.end_date

    after = repository.renew(book, "FL-0002", RenewalRequest(months=12))

    assert after.start_date == old_end, "cover must be continuous"
    assert after.end_date == repository.add_months(old_end, 12)


def test_renewal_clears_the_flag_and_counts(book):
    before = repository.get(book, "FL-0002")
    assert before.renewal_pending is True
    count = before.renewal_count

    after = repository.renew(book, "FL-0002", RenewalRequest(months=12))
    assert after.renewal_pending is False
    assert after.renewal_count == count + 1


def test_renewal_leaves_pricing_alone_unless_asked(book):
    before = repository.get(book, "FL-0003")
    premium, limit = before.premium, before.sum_insured

    after = repository.renew(book, "FL-0003", RenewalRequest(months=12))
    assert (after.premium, after.sum_insured) == (premium, limit)

    repriced = repository.renew(
        book, "FL-0003", RenewalRequest(months=12, premium=99_000)
    )
    assert repriced.premium == 99_000


def test_renewal_keeps_the_insurer_reference_and_moves_the_year(book):
    before = repository.get(book, "FL-0003")
    head = before.policy_number.rsplit("-", 1)[0]

    after = repository.renew(book, "FL-0003", RenewalRequest(months=12))
    assert after.policy_number.startswith(head), "reference must survive a renewal"
    assert after.policy_number.endswith(f"{after.end_date:%y}")


def test_a_draft_cannot_be_renewed(book):
    with pytest.raises(ValueError, match="draft"):
        repository.renew(book, "FL-0006", RenewalRequest(months=12))


def test_missing_contract_raises(book):
    with pytest.raises(repository.ContractNotFound):
        repository.get(book, "FL-9999")
    with pytest.raises(repository.ContractNotFound):
        repository.renew(book, "FL-9999", RenewalRequest())


def test_deferred_commit_lets_a_caller_roll_the_whole_batch_back(book):
    """
    This is what makes run_renewal_batch all-or-nothing. Without commit=False the
    first renewal would already be durable when a later one failed.
    """
    original = {c.id: c.end_date for c in repository.list_contracts(book)}

    repository.renew(book, "FL-0002", RenewalRequest(months=12), commit=False)
    repository.renew(book, "FL-0003", RenewalRequest(months=12), commit=False)
    book.rollback()

    after = {c.id: c.end_date for c in repository.list_contracts(book)}
    assert after == original, "rollback must undo every renewal in the transaction"


def test_create_rejects_an_inverted_term(book):
    with pytest.raises(ValueError, match="end_date"):
        repository.create(
            book,
            ContractCreate(
                insured_company="Bad Dates GmbH",
                product="Cyber",
                insurer="Markel",
                sum_insured=1_000_000,
                premium=9_000,
                deductible=10_000,
                start_date=date(2027, 1, 1),
                end_date=date(2026, 1, 1),
            ),
        )


def test_new_ids_continue_from_the_maximum(book):
    created = repository.create(
        book,
        ContractCreate(
            insured_company="Next GmbH",
            product="Cyber",
            insurer="Markel",
            sum_insured=1_000_000,
            premium=9_000,
            deductible=10_000,
            start_date=date(2026, 1, 1),
            end_date=date(2027, 1, 1),
        ),
    )
    assert created.id == "FL-0007"
    assert created.policy_number.startswith("CY-")


def test_update_only_touches_supplied_fields(book):
    before = repository.get(book, "FL-0004")
    after = repository.update(book, "FL-0004", ContractUpdate(premium=25_000))
    assert after.premium == 25_000
    assert after.insured_company == before.insured_company
    assert after.end_date == before.end_date
