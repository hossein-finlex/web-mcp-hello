"""
Domain models.

`Contract` is the table. The `*Create` / `*Update` schemas are the validated API
boundary — SQLModel skips validation on `table=True` classes, so that is where
enums and bounds live. Batch and report records are tables too now: a bulk change
is an audit trail and should outlive a process restart.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal, Optional

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

Product = Literal["D&O", "Cyber", "PI", "Crime", "EPLI", "W&I"]
PRODUCTS: tuple[str, ...] = ("D&O", "Cyber", "PI", "Crime", "EPLI", "W&I")

# Derived, never stored. Priority order is draft > expired > expiring > active.
Status = Literal["draft", "expired", "expiring", "active"]
STATUSES: tuple[str, ...] = ("active", "expiring", "expired", "draft")

GROUPABLE: tuple[str, ...] = ("product", "insurer", "status", "broker", "industry")
SORTABLE: tuple[str, ...] = (
    "end_date",
    "sum_insured",
    "premium",
    "deductible",
    "insured_company",
    "insurer",
    "product",
    "renewal_count",
)

EXPIRING_WINDOW_DAYS = 90


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Contract(SQLModel, table=True):
    __tablename__ = "contracts"

    id: str = Field(primary_key=True)
    policy_number: str = Field(index=True)
    product: str = Field(index=True)
    insurer: str = Field(index=True)
    insured_company: str = Field(index=True)
    industry: str = ""
    sum_insured: int = 0
    premium: int = 0
    deductible: int = 0
    currency: str = "EUR"
    start_date: date
    end_date: date = Field(index=True)
    broker: str = ""
    notes: str = ""
    is_draft: bool = Field(default=False, index=True)
    renewal_pending: bool = Field(default=False, index=True)
    renewal_count: int = 0
    created_by_assistant: bool = False

    @property
    def status(self) -> str:
        """Computed from the term, so it can never drift out of sync."""
        if self.is_draft:
            return "draft"
        today = date.today()
        if self.end_date < today:
            return "expired"
        if (self.end_date - today).days <= EXPIRING_WINDOW_DAYS:
            return "expiring"
        return "active"

    @property
    def days_to_expiry(self) -> int:
        return (self.end_date - date.today()).days

    def public(self) -> dict[str, Any]:
        """Wire shape: stored columns plus the derived fields."""
        data = self.model_dump(mode="json")
        data["status"] = self.status
        data["days_to_expiry"] = self.days_to_expiry
        return data

    def summary(self) -> dict[str, Any]:
        """Compact projection for tool results and lists."""
        return {
            "id": self.id,
            "policy_number": self.policy_number,
            "product": self.product,
            "insurer": self.insurer,
            "insured_company": self.insured_company,
            "status": self.status,
            "end_date": self.end_date.isoformat(),
            "days_to_expiry": self.days_to_expiry,
            "sum_insured": self.sum_insured,
            "premium": self.premium,
            "renewal_pending": self.renewal_pending,
        }


class ContractCreate(SQLModel):
    insured_company: str = Field(min_length=1)
    product: Product
    insurer: str = Field(min_length=1)
    sum_insured: int = Field(gt=0)
    premium: int = Field(gt=0)
    deductible: int = Field(ge=0)
    start_date: date
    end_date: date
    industry: str = "Unspecified"
    broker: str = "House account"
    notes: str = ""
    is_draft: bool = False
    created_by_assistant: bool = False


class ContractUpdate(SQLModel):
    """Every field optional — this is a PATCH."""

    insured_company: Optional[str] = Field(default=None, min_length=1)
    product: Optional[Product] = None
    insurer: Optional[str] = Field(default=None, min_length=1)
    sum_insured: Optional[int] = Field(default=None, gt=0)
    premium: Optional[int] = Field(default=None, gt=0)
    deductible: Optional[int] = Field(default=None, ge=0)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    industry: Optional[str] = None
    broker: Optional[str] = None
    notes: Optional[str] = None
    is_draft: Optional[bool] = None
    renewal_pending: Optional[bool] = None


class RenewalRequest(SQLModel):
    months: int = Field(default=12, ge=1, le=60)
    premium: Optional[int] = Field(default=None, gt=0)
    sum_insured: Optional[int] = Field(default=None, gt=0)
    notes: Optional[str] = None


# --------------------------------------------------------------------------- #
# Artifacts: output of server-side jobs. Persisted (P5) because a record of who
# changed what, in bulk, is an audit trail rather than scratch state.
# --------------------------------------------------------------------------- #

class BatchRecord(SQLModel, table=True):
    __tablename__ = "batches"

    id: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=_utcnow)
    kind: str = "renewal"
    committed: bool = False
    months: int = 12
    matched: int = 0
    renewed: int = 0
    premium_affected: int = 0
    scope: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    items: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    failed: list[Any] = Field(default_factory=list, sa_column=Column(JSON))

    def public(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["created_at"] = self.created_at.isoformat()
        return data


class ReportRecord(SQLModel, table=True):
    __tablename__ = "reports"

    id: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=_utcnow)
    title: str = ""
    group_by: str = "product"
    markdown: str = ""
    scope: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    headline: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    sections: list[Any] = Field(default_factory=list, sa_column=Column(JSON))

    def public(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["created_at"] = self.created_at.isoformat()
        return data
