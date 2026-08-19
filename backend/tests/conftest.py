"""
Test fixtures.

Tests run against SQLite in a temp file rather than the Postgres container: they
stay fast and need no docker. The queries are ORM-level, so they translate — the
one Postgres-specific call (`SHOW server_version`) is already guarded. Anything
that must be verified against Postgres itself belongs in the live checks, not here.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

# Environment must be set before settings() is first called and cached.
os.environ.setdefault("MOCK_LLM", "1")
os.environ.setdefault("AUTO_SEED", "0")


@pytest.fixture(scope="session", autouse=True)
def database(tmp_path_factory):
    from app import db
    from app.settings import settings

    path = tmp_path_factory.mktemp("db") / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    settings.cache_clear()
    db.reset_engine()
    db.create_tables()
    yield
    db.reset_engine()


@pytest.fixture
def session(database):
    """A clean database for every test."""
    from sqlmodel import delete

    from app.db import session_scope
    from app.domain.models import BatchRecord, Contract, ReportRecord

    with session_scope() as s:
        s.exec(delete(Contract))
        s.exec(delete(BatchRecord))
        s.exec(delete(ReportRecord))
        s.commit()
        yield s


@pytest.fixture
def book(session):
    """A small, fully deterministic book with one contract per status."""
    from app.domain.models import Contract

    today = date.today()
    rows = [
        # id, product, insurer, company, industry, sum, premium, end offset, flags
        ("FL-0001", "D&O", "Chubb", "Alpha GmbH", "Software", 10_000_000, 40_000, -30, {}),
        ("FL-0002", "Cyber", "AXA XL", "Beta AG", "MedTech", 5_000_000, 60_000, 10, {"renewal_pending": True}),
        ("FL-0003", "Cyber", "Allianz", "Gamma GmbH", "Retail", 2_000_000, 20_000, 45, {}),
        ("FL-0004", "PI", "HDI", "Delta KG", "Logistics", 3_000_000, 18_000, 200, {}),
        ("FL-0005", "D&O", "Allianz", "Epsilon SE", "Utilities", 20_000_000, 130_000, 300, {}),
        ("FL-0006", "Crime", "Markel", "Zeta GmbH", "Chemicals", 5_000_000, 27_000, 60, {"is_draft": True}),
    ]
    for cid, product, insurer, company, industry, total, premium, offset, flags in rows:
        end = today + timedelta(days=offset)
        session.add(
            Contract(
                id=cid,
                policy_number=f"XX-{cid[-4:]}000-{end:%y}",
                product=product,
                insurer=insurer,
                insured_company=company,
                industry=industry,
                sum_insured=total,
                premium=premium,
                deductible=total // 100,
                start_date=end - timedelta(days=365),
                end_date=end,
                broker="T. Test",
                notes=f"{product} cover for {company}.",
                **flags,
            )
        )
    session.commit()
    return session
