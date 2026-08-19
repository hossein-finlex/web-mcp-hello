"""
Typed configuration, read once from the environment.

Everything that used to call os.environ.get at point of use now reads from a
single frozen object, so there is one place to look up what is configurable and
what it defaults to.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_DB = "postgresql+psycopg://portfolio:portfolio@localhost:5434/portfolio"
DEFAULT_ORIGINS = "http://localhost:3000,http://localhost:3001,http://localhost:3002"


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "", "false", "no")


@dataclass(frozen=True)
class Settings:
    database_url: str
    model: str
    mock_llm: bool
    auto_seed: bool
    seed_total: int
    sql_echo: bool
    frontend_origins: tuple[str, ...] = field(default=())

    @property
    def has_credentials(self) -> bool:
        # Read live rather than cached: a key may be exported after import.
        return bool(
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )

    @property
    def effective_model(self) -> str:
        return "mock" if self.mock_llm else self.model


@lru_cache(maxsize=1)
def settings() -> Settings:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    return Settings(
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DB),
        model=os.environ.get("CLAUDE_MODEL", "claude-opus-5"),
        mock_llm=_flag("MOCK_LLM"),
        auto_seed=_flag("AUTO_SEED", "1"),
        seed_total=int(os.environ.get("SEED_TOTAL", "50")),
        sql_echo=_flag("SQL_ECHO"),
        frontend_origins=tuple(
            o.strip()
            for o in os.environ.get("FRONTEND_ORIGINS", DEFAULT_ORIGINS).split(",")
            if o.strip()
        ),
    )
