#!/usr/bin/env python
"""
Seed the portfolio database.

    cd backend
    .venv/bin/python seed.py             # seed only if empty
    .venv/bin/python seed.py --force     # wipe and re-seed
    .venv/bin/python seed.py --total 200 # a bigger book
"""

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from sqlmodel import Session  # noqa: E402

from app import repository as repo  # noqa: E402
from app.db import create_tables, database_status, engine, wait_for_database  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="wipe existing rows first")
    parser.add_argument("--total", type=int, default=50, help="how many contracts")
    args = parser.parse_args()

    if not wait_for_database(timeout_seconds=20):
        status = database_status()
        print(f"\nCannot reach the database at {status['url']}.", file=sys.stderr)
        print("Start it with:  docker compose up -d\n", file=sys.stderr)
        return 1

    create_tables()
    with Session(engine()) as session:
        before = repo.count_contracts(session)
        count = repo.seed(session, total=args.total, force=args.force)

    if before and not args.force:
        print(f"Database already had {before} contracts — left untouched.")
        print("Use --force to wipe and re-seed.")
    else:
        print(f"Seeded {count} contracts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
