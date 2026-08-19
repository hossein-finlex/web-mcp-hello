"""
The contract filter — defined exactly once.

Before this existed the same nine criteria were written out in the FastAPI route
signature, in the SQL builder's kwargs, in two hand-written JSON Schemas for the
tools, and again in the frontend. Adding one field meant six edits and there was
nothing to catch a mismatch.

Now: this model is the contract. The REST layer binds query parameters to it,
the SQL layer consumes it, and tool schemas are *generated* from it.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import GROUPABLE, PRODUCTS, SORTABLE, STATUSES, Product, Status


class ContractFilter(BaseModel):
    """All supplied criteria must match (AND), mirroring the UI filter bar."""

    model_config = ConfigDict(extra="forbid")

    query: Optional[str] = Field(
        default=None,
        description=(
            "Free text. Every whitespace-separated term must appear in one of: "
            "contract id, policy number, insured company, insurer, product, "
            "industry, broker, notes."
        ),
    )
    product: Optional[Product] = Field(default=None, description="Exact product match.")
    insurer: Optional[str] = Field(
        default=None, description='Substring match, e.g. "Allianz".'
    )
    broker: Optional[str] = Field(default=None, description="Substring match.")
    status: Optional[Status] = Field(
        default=None,
        description=(
            "Derived from the term: expired, expiring (ends within 90 days), "
            "active, or draft."
        ),
    )
    renewal_pending: Optional[bool] = Field(
        default=None, description="Only contracts a broker has flagged for renewal."
    )
    expiring_within_days: Optional[int] = Field(
        default=None,
        ge=0,
        le=3650,
        description="In force today and ending within this many days.",
    )
    min_sum_insured: Optional[int] = Field(default=None, ge=0, description="EUR.")
    max_premium: Optional[int] = Field(default=None, ge=0, description="EUR.")

    def active(self) -> dict[str, Any]:
        """Only the criteria that were actually supplied."""
        return self.model_dump(exclude_none=True)

    def is_empty(self) -> bool:
        return not self.active()

    @classmethod
    def tool_properties(cls) -> dict[str, Any]:
        """
        The filter fields as flat JSON Schema properties, for embedding in a
        tool's inputSchema.

        Pydantic renders `Optional[X]` as `anyOf: [X, null]`, which is correct
        but noisy for a model reading a tool definition. This flattens it back
        to the plain shape and drops Pydantic's bookkeeping.
        """
        return flatten_optional_schema(cls.model_json_schema())["properties"]


def flatten_optional_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Unwrap `anyOf: [T, null]` into T, and strip titles and defaults."""
    props: dict[str, Any] = {}
    for name, spec in (schema.get("properties") or {}).items():
        cleaned = dict(spec)
        variants = cleaned.pop("anyOf", None)
        if variants:
            concrete = next(
                (v for v in variants if v.get("type") != "null"), {"type": "string"}
            )
            description = cleaned.get("description")
            cleaned = dict(concrete)
            if description:
                cleaned["description"] = description
        cleaned.pop("title", None)
        cleaned.pop("default", None)
        props[name] = cleaned
    out = {"type": "object", "properties": props}
    if schema.get("required"):
        out["required"] = schema["required"]
    return out


class ContractQuery(ContractFilter):
    """
    The filter plus the presentation controls, as one query model.

    FastAPI 0.115 only expands a Pydantic model into individual query parameters
    when it is the *only* query parameter on the route — declare a sibling like
    `sort_by: str = "end_date"` and the model silently becomes one required
    parameter named after its argument. So sort and limit live here rather than in
    the route signature. It reads better anyway: one object describes the request.
    """

    sort_by: str = Field(
        default="end_date", description=f"One of: {', '.join(SORTABLE)}."
    )
    sort_dir: str = Field(default="asc", description='"asc" or "desc".')
    limit: Optional[int] = Field(
        default=None, ge=1, le=500, description="Return only the first N rows."
    )

    @field_validator("sort_by")
    @classmethod
    def _known_sort_column(cls, value: str) -> str:
        if value not in SORTABLE:
            raise ValueError(f"must be one of: {', '.join(SORTABLE)}")
        return value

    @field_validator("sort_dir")
    @classmethod
    def _known_direction(cls, value: str) -> str:
        if value not in ("asc", "desc"):
            raise ValueError('must be "asc" or "desc"')
        return value

    def filters(self) -> ContractFilter:
        return ContractFilter.model_validate(
            self.model_dump(exclude={"sort_by", "sort_dir", "limit"}, exclude_none=True)
        )


class SummaryQuery(ContractFilter):
    """The filter plus the grouping key, for the aggregation endpoint."""

    group_by: str = Field(
        default="product", description=f"One of: {', '.join(GROUPABLE)}."
    )

    @field_validator("group_by")
    @classmethod
    def _known_grouping(cls, value: str) -> str:
        if value not in GROUPABLE:
            raise ValueError(f"must be one of: {', '.join(GROUPABLE)}")
        return value

    def filters(self) -> ContractFilter:
        return ContractFilter.model_validate(
            self.model_dump(exclude={"group_by"}, exclude_none=True)
        )


# Re-exported so tool modules building schemas do not import from two places.
__all__ = [
    "ContractFilter",
    "ContractQuery",
    "SummaryQuery",
    "flatten_optional_schema",
    "PRODUCTS",
    "STATUSES",
]
