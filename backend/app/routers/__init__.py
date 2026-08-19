"""HTTP routers, one module per resource."""

from . import analytics, artifacts, contracts, meta, operations  # noqa: F401

# Order matters for path matching: /api/contracts/search must be registered before
# /api/contracts/{contract_id}, or "search" is swallowed as an id. Within
# contracts.py the declaration order already handles that; across routers the
# prefixes do not overlap.
ALL = (
    meta.router,
    contracts.router,
    analytics.router,
    artifacts.router,
    operations.router,
)
