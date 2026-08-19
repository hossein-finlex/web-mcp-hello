"""
The browser half of the tool bridge.

A real WebMCP agent lives outside the page; the browser marshals its tool calls
in. This class is that marshalling: send a tool_use, await the matching
tool_result, and make sure nothing is left waiting forever.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("app.agent.bridge")

TOOL_TIMEOUT_SECONDS = 60


class ToolBridge:
    def __init__(self, send_json) -> None:
        self._send = send_json
        self._waiters: dict[str, asyncio.Future] = {}

    async def call(self, tool_use_id: str, name: str, payload: dict) -> dict[str, Any]:
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[tool_use_id] = future

        await self._send(
            {"type": "tool_use", "id": tool_use_id, "name": name, "input": payload}
        )
        try:
            return await asyncio.wait_for(future, timeout=TOOL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            log.warning("browser did not answer %s within %ss", name, TOOL_TIMEOUT_SECONDS)
            return {
                "is_error": True,
                "content": f"The browser did not respond to {name} within "
                f"{TOOL_TIMEOUT_SECONDS}s.",
            }
        finally:
            self._waiters.pop(tool_use_id, None)

    def resolve(self, tool_use_id: str, result: dict) -> None:
        future = self._waiters.get(tool_use_id)
        if future and not future.done():
            future.set_result(result)

    def fail_all(self, reason: str) -> None:
        for future in self._waiters.values():
            if not future.done():
                future.set_result({"is_error": True, "content": reason})
        self._waiters.clear()

    @property
    def pending(self) -> int:
        return len(self._waiters)
