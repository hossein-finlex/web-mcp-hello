"""
Contract repository: all database access lives here.

Functions take the request's Session rather than opening their own, so a route
that does several things does them in one transaction. Nothing above this layer
knows SQL.
"""

from __future__ import annotations

import logging

from sqlmodel import Session, delete, select

from .models import Contract, ContractCreate, ContractUpdate, RenewalRequest
from .seed_data import PRODUCT_PREFIX, add_months
from .seed_gen import full_book

log = logging.getLogger("app.repo")


class ContractNotFound(Exception):
    def __init__(self, contract_id: str):
        self.contract_id = contract_id
        super().__init__(f"No contract with id {contract_id!r}")


def list_contracts(session: Session) -> list[Contract]:
    """
    The whole book, ordered by expiry so the urgent business is at the top.

    At this size the client filters in memory, which keeps the filter bar
    instant and lets one `matchesFilter` implementation serve both the UI and
    the search tool. Server-side filtering would slot in here.
    """
    return list(session.exec(select(Contract).order_by(Contract.end_date)).all())


def get_contract(session: Session, contract_id: str) -> Contract:
    contract = session.get(Contract, contract_id)
    if contract is None:
        raise ContractNotFound(contract_id)
    return contract


def create_contract(session: Session, payload: ContractCreate) -> Contract:
    if payload.end_date <= payload.start_date:
        raise ValueError("end_date must be after start_date")

    new_id = _next_id(session)
    contract = Contract(
        id=new_id,
        policy_number=_policy_number(payload.product, new_id, payload.end_date),
        **payload.model_dump(),
    )
    session.add(contract)
    session.commit()
    session.refresh(contract)
    return contract


def update_contract(session: Session, contract_id: str, patch: ContractUpdate) -> Contract:
    contract = get_contract(session, contract_id)
    changes = patch.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return contract

    for key, value in changes.items():
        setattr(contract, key, value)

    if contract.end_date <= contract.start_date:
        session.rollback()
        raise ValueError("end_date must be after start_date")

    session.add(contract)
    session.commit()
    session.refresh(contract)
    return contract


def renew_contract(
    session: Session, contract_id: str, req: RenewalRequest, commit: bool = True
) -> Contract:
    contract = get_contract(session, contract_id)
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
    contract.policy_number = _bump_policy_year(contract.policy_number, new_end)

    session.add(contract)
    # commit=False lets a bulk caller renew many contracts and commit once, so
    # the batch is all-or-nothing rather than partially applied.
    if commit:
        session.commit()
        session.refresh(contract)
    else:
        session.flush()
    return contract


def delete_contract(session: Session, contract_id: str) -> None:
    contract = get_contract(session, contract_id)
    session.delete(contract)
    session.commit()


def count_contracts(session: Session) -> int:
    return len(session.exec(select(Contract.id)).all())


def seed(session: Session, total: int = 50, force: bool = False) -> int:
    """
    Populate the demo book. No-op if rows already exist unless `force`, so
    restarting the backend never quietly discards the user's edits.
    """
    existing = count_contracts(session)
    if existing and not force:
        log.info("database already has %d contracts — not seeding", existing)
        return existing

    if existing:
        session.exec(delete(Contract))

    for row in full_book(total):
        session.add(Contract(**row))
    session.commit()

    seeded = count_contracts(session)
    log.info("seeded %d contracts", seeded)
    return seeded


# --------------------------------------------------------------------------- #

def _next_id(session: Session) -> str:
    """
    Next free FL-#### id.

    Derived from the current maximum rather than a counter, so it survives
    restarts. Two simultaneous creates could collide; a real deployment would
    use a database sequence, which is a one-line change here.
    """
    ids = session.exec(select(Contract.id)).all()
    highest = 0
    for cid in ids:
        digits = "".join(ch for ch in cid if ch.isdigit())
        if digits:
            highest = max(highest, int(digits))
    return f"FL-{highest + 1:04d}"


def _policy_number(product: str, contract_id: str, end) -> str:
    """PRODUCT-<7 digits>-<term year>, e.g. DO-8891234-26."""
    prefix = PRODUCT_PREFIX.get(product, "XX")
    seq = int("".join(ch for ch in contract_id if ch.isdigit()) or 0)
    # Deterministic spread so generated numbers read like insurer references
    # rather than an obvious counter.
    digits = (seq * 61_879 + 3_140_000) % 9_000_000 + 1_000_000
    return f"{prefix}-{digits}-{end:%y}"


def _bump_policy_year(policy_number: str, end) -> str:
    head, _, tail = policy_number.rpartition("-")
    if head and tail.isdigit() and len(tail) == 2:
        return f"{head}-{end:%y}"
    return policy_number
