"""
Tool routing: browser or backend.

Claude receives one flat tool list and cannot tell the difference. This module is
where the difference is decided — page tools go over the bridge to the browser,
server tools execute in this process.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .. import tools as server_tools
from ..db import session_scope
from .bridge import ToolBridge

log = logging.getLogger("app.agent.router")


class ToolRouter:
    def __init__(self, bridge: ToolBridge, send_json, turn_id: str = "-") -> None:
        self._bridge = bridge
        self._send = send_json
        self._turn_id = turn_id

    async def dispatch(self, tool_use: dict) -> dict[str, Any]:
        name = tool_use["name"]
        args = tool_use.get("input") or {}

        if not server_tools.is_server_tool(name):
            return await self._bridge.call(tool_use["id"], name, args)
        return await self._run_server_tool(name, args)

    async def _run_server_tool(self, name: str, args: dict) -> dict[str, Any]:
        async def progress(text: str) -> None:
            await self._send(
                {"type": "server_tool_progress", "name": name, "message": text}
            )

        ctx = server_tools.ToolContext(
            progress=progress, session_factory=session_scope, turn_id=self._turn_id
        )

        await self._send(
            {"type": "server_tool", "phase": "start", "name": name, "input": args}
        )
        payload = await server_tools.execute(name, args, ctx)
        is_error = isinstance(payload, dict) and "error" in payload

        log.info(
            "server tool %s %s [turn=%s]",
            name,
            "failed" if is_error else "ok",
            self._turn_id,
        )
        await self._send(
            {
                "type": "server_tool",
                "phase": "done",
                "name": name,
                "is_error": is_error,
                "summary": summarise(name, payload),
            }
        )
        return {"content": json.dumps(payload), "is_error": is_error}


def summarise(name: str, payload: Any) -> str:
    """A one-line description of a server tool's outcome, for the chat trace."""
    if not isinstance(payload, dict):
        return "done"
    if "error" in payload:
        return str(payload["error"])
    if name == "run_renewal_batch":
        if payload.get("committed"):
            return f"{payload.get('renewed', 0)} renewed · {payload.get('batch_id')}"
        return (
            f"dry run · {payload.get('matched', 0)} would renew · "
            f"{payload.get('batch_id')}"
        )
    if name == "generate_renewal_report":
        head = payload.get("headline", {})
        return f"{payload.get('report_id')} · {head.get('contracts', '?')} contracts"
    if name == "benchmark_rates":
        verdict = (payload.get("comparison") or {}).get("verdict")
        return f"{payload.get('product')} benchmark" + (f" · {verdict}" if verdict else "")
    return "done"
