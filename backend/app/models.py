"""
Models for the commercial financial-lines portfolio.

`Contract` is the SQLModel table. The `*Create` / `*Update` schemas are the
validated API boundary — that is where `Literal` enums and bounds live, because
SQLModel skips validation on `table=True` classes. The column itself is a plain
string, so adding a product later does not need a migration.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from sqlmodel import Field, SQLModel

Product = Literal["D&O", "Cyber", "PI", "Crime", "EPLI", "W&I"]
PRODUCTS: tuple[str, ...] = ("D&O", "Cyber", "PI", "Crime", "EPLI", "W&I")

# Derived, never stored. Priority order is draft > expired > expiring > active.
Status = Literal["draft", "expired", "expiring", "active"]

EXPIRING_WINDOW_DAYS = 90


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

    def public(self) -> dict:
        """Wire shape: the stored columns plus the derived fields."""
        data = self.model_dump(mode="json")
        data["status"] = self.status
        data["days_to_expiry"] = self.days_to_expiry
        return data


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
