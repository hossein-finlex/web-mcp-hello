#!/usr/bin/env python
"""
Seed the portfolio database.

    .venv/bin/python seed.py             # seed only if empty
    .venv/bin/python seed.py --force     # wipe and re-seed
    .venv/bin/python seed.py --total 200 # a bigger book
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlmodel import Session

from app.data import repository
from app.db import create_tables, database_status, engine, wait_for_database
from app.settings import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="wipe existing rows first")
    parser.add_argument(
        "--total", type=int, default=settings().seed_total, help="how many contracts"
    )
    args = parser.parse_args()

    if not wait_for_database(timeout_seconds=20):
        status = database_status()
        print(f"\nCannot reach the database at {status['url']}.", file=sys.stderr)
        print("Start it with:  docker compose up -d\n", file=sys.stderr)
        return 1

    create_tables()
    with Session(engine()) as session:
        before = repository.count_contracts(session)
        count = repository.seed(session, total=args.total, force=args.force)

    if before and not args.force:
        print(f"Database already had {before} contracts — left untouched.")
        print("Use --force to wipe and re-seed.")
    else:
        print(f"Seeded {count} contracts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
