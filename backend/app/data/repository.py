"""
Contract persistence. All contract SQL lives here or in queries.py.

Mutating functions take `commit: bool = True` so a bulk caller can make many
changes inside one transaction and commit once — which is what makes the renewal
batch all-or-nothing rather than partially applied.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlmodel import Session, delete, select

from ..domain.models import (
    Contract,
    ContractCreate,
    ContractUpdate,
    RenewalRequest,
)

log = logging.getLogger("app.data.repository")

PRODUCT_PREFIX = {
    "D&O": "DO",
    "Cyber": "CY",
    "PI": "PI",
    "Crime": "CR",
    "EPLI": "EP",
    "W&I": "WI",
}


class ContractNotFound(Exception):
    def __init__(self, contract_id: str):
        self.contract_id = contract_id
        super().__init__(f"No contract with id {contract_id!r}")


def list_contracts(session: Session) -> list[Contract]:
    """The whole book, ordered by expiry so urgent business is first."""
    return list(session.exec(select(Contract).order_by(Contract.end_date)).all())


def get(session: Session, contract_id: str) -> Contract:
    contract = session.get(Contract, contract_id)
    if contract is None:
        raise ContractNotFound(contract_id)
    return contract


def create(session: Session, payload: ContractCreate, commit: bool = True) -> Contract:
    if payload.end_date <= payload.start_date:
        raise ValueError("end_date must be after start_date")

    new_id = next_id(session)
    contract = Contract(
        id=new_id,
        policy_number=policy_number(payload.product, new_id, payload.end_date),
        **payload.model_dump(),
    )
    session.add(contract)
    _finish(session, contract, commit)
    return contract


def update(
    session: Session, contract_id: str, patch: ContractUpdate, commit: bool = True
) -> Contract:
    contract = get(session, contract_id)
    changes = patch.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return contract

    for key, value in changes.items():
        setattr(contract, key, value)

    if contract.end_date <= contract.start_date:
        session.rollback()
        raise ValueError("end_date must be after start_date")

    session.add(contract)
    _finish(session, contract, commit)
    return contract


def renew(
    session: Session, contract_id: str, req: RenewalRequest, commit: bool = True
) -> Contract:
    contract = get(session, contract_id)
    if contract.is_draft:
        raise ValueError("A draft cannot be renewed — issue it first")

    # The new term starts the day the old one ends, so cover is continuous even
    # when the renewal is processed late.
    new_start = contract.end_date
    new_end = add_months(new_start, req.months)

    contract.start_date = new_start
    contract.end_date = new_end
    if req.premium is not None:
        contract.premium = req.premium
    if req.sum_insured is not None:
        contract.sum_insured = req.sum_insured
    if req.notes is not None:
        contract.notes = req.notes
    contract.renewal_pending = False
    contract.renewal_count += 1
    # A renewal keeps the insurer's reference; only the term year moves.
    contract.policy_number = bump_policy_year(contract.policy_number, new_end)

    session.add(contract)
    _finish(session, contract, commit)
    return contract


def remove(session: Session, contract_id: str, commit: bool = True) -> None:
    contract = get(session, contract_id)
    session.delete(contract)
    if commit:
        session.commit()


def count_contracts(session: Session) -> int:
    from sqlalchemy import func

    return int(session.scalar(select(func.count()).select_from(Contract)) or 0)


def seed(session: Session, total: int = 50, force: bool = False) -> int:
    """
    Populate the demo book. No-op if rows exist unless `force`, so restarting the
    backend never quietly discards the user's edits.
    """
    from ..domain.models import BatchRecord, ReportRecord
    from ..seed.generator import full_book

    existing = count_contracts(session)
    if existing and not force:
        log.info("database already has %d contracts — not seeding", existing)
        return existing

    if existing:
        session.exec(delete(Contract))
        session.exec(delete(BatchRecord))
        session.exec(delete(ReportRecord))

    for row in full_book(total):
        session.add(Contract(**row))
    session.commit()

    seeded = count_contracts(session)
    log.info("seeded %d contracts", seeded)
    return seeded


# --------------------------------------------------------------------------- #

def _finish(session: Session, contract: Contract, commit: bool) -> None:
    if commit:
        session.commit()
        session.refresh(contract)
    else:
        session.flush()


def next_id(session: Session) -> str:
    """
    Next free FL-#### id, derived from the current maximum so it survives
    restarts. Two simultaneous creates could collide; a database sequence is the
    one-line fix when that matters.
    """
    highest = 0
    for cid in session.exec(select(Contract.id)).all():
        digits = "".join(ch for ch in cid if ch.isdigit())
        if digits:
            highest = max(highest, int(digits))
    return f"FL-{highest + 1:04d}"


def policy_number(product: str, contract_id: str, end: date) -> str:
    """PRODUCT-<7 digits>-<term year>, e.g. DO-8891234-26."""
    prefix = PRODUCT_PREFIX.get(product, "XX")
    seq = int("".join(ch for ch in contract_id if ch.isdigit()) or 0)
    # Deterministic spread so generated numbers read like insurer references
    # rather than an obvious counter.
    digits = (seq * 61_879 + 3_140_000) % 9_000_000 + 1_000_000
    return f"{prefix}-{digits}-{end:%y}"


def bump_policy_year(number: str, end: date) -> str:
    head, _, tail = number.rpartition("-")
    if head and tail.isdigit() and len(tail) == 2:
        return f"{head}-{end:%y}"
    return number


def add_months(start: date, months: int) -> date:
    """Add whole months, clamping to the last valid day of the target month."""
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(start.day, days_in_month(year, month)))


def days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days
