"""
Business operations.

Everything here is a plain function over a database Session. Nothing in this
package knows that an LLM exists, that there is an HTTP layer, or that anyone is
watching a screen. That is the point: `tools/` and `routers/` are two thin
adapters over these functions, so a capability is available to the assistant and
to a button in the UI without being written twice.

Services are synchronous on purpose. FastAPI runs sync handlers in a threadpool,
and the async tool adapters push them onto a thread with
`ToolContext.in_session`. Keeping the threading decision in the adapter means the
business logic reads like business logic.
"""

from . import market, renewals, reporting  # noqa: F401

__all__ = ["market", "renewals", "reporting"]
