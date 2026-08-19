"""
Server-side tools.

Importing this package registers every tool. The registry's read-side functions
are re-exported so callers never need to know which module a tool lives in.
"""

from .registry import (  # noqa: F401
    ServerTool,
    ToolContext,
    definitions,
    execute,
    get,
    is_server_tool,
    names,
    server_tool,
)

# Import for side effect: each module registers its tools on import.
from . import market, renewal, reports  # noqa: F401,E402

__all__ = [
    "ServerTool",
    "ToolContext",
    "definitions",
    "execute",
    "get",
    "is_server_tool",
    "names",
    "server_tool",
]
