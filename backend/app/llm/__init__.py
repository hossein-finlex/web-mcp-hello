"""
The model side of the bridge.

`stream_turn` issues one request and returns a normalised result:

    {"content": [<plain dict blocks>], "stop_reason": "tool_use" | "end_turn", ...}

Which provider answers depends on configuration; callers cannot tell.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..settings import settings
from .adapters import webmcp_tools_to_api  # noqa: F401
from .prompt import system_prompt  # noqa: F401


async def stream_turn(
    messages: list[dict],
    tools: list[dict],
    on_text: Callable[[str], Awaitable[None]],
) -> dict[str, Any]:
    if settings().mock_llm:
        from . import mock

        return await mock.stream_turn(messages, tools, on_text)

    from . import claude

    return await claude.stream_turn(messages, tools, on_text)


def is_mock() -> bool:
    return settings().mock_llm


def have_credentials() -> bool:
    return settings().has_credentials


def model_name() -> str:
    return settings().effective_model


__all__ = [
    "stream_turn",
    "is_mock",
    "have_credentials",
    "model_name",
    "system_prompt",
    "webmcp_tools_to_api",
]
