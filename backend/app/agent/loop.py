"""
The agentic loop: one user turn.

Call the model, and while it keeps naming tools, execute them and hand the results
back. That is the entire mechanism; everything else in this package is transport.

The SDK's tool runner cannot be used here because it executes tools in-process —
half of ours execute in the user's browser, over a WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from .. import llm
from .. import tools as server_tools
from .router import ToolRouter

log = logging.getLogger("app.agent.loop")

MAX_TOOL_ROUNDS = 12  # guards against a pathological tool loop


async def run_turn(
    *,
    text: str,
    webmcp_tools: list[dict],
    history: list[dict],
    router: ToolRouter,
    send_json: Callable[[dict], Awaitable[None]],
    turn_id: str = "-",
) -> str:
    """
    Run one turn to completion. Mutates `history`. Returns the final stop reason.
    """
    # The browser sends its live tool list with every message. That is not
    # redundant: the registered set changes as the user navigates, which is the
    # dynamic capability discovery WebMCP exists to provide.
    api_tools = llm.webmcp_tools_to_api(webmcp_tools) + server_tools.definitions()

    history.append({"role": "user", "content": text})

    async def on_text(chunk: str) -> None:
        await send_json({"type": "text_delta", "text": chunk})

    stop_reason = "error"

    for round_index in range(MAX_TOOL_ROUNDS):
        await send_json({"type": "turn_status", "status": "thinking"})

        result = await llm.stream_turn(history, api_tools, on_text)
        content = result["content"]
        stop_reason = result["stop_reason"]
        history.append({"role": "assistant", "content": content})

        if result.get("usage"):
            await send_json(
                {
                    "type": "usage",
                    "usage": result["usage"],
                    "model": result.get("model"),
                }
            )

        if stop_reason == "refusal":
            category = (result.get("stop_details") or {}).get("category")
            await send_json(
                {
                    "type": "error",
                    "message": "The model declined this request"
                    + (f" ({category})" if category else "")
                    + ".",
                }
            )
            break

        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if stop_reason != "tool_use" or not tool_uses:
            break

        log.info(
            "turn=%s round=%d tools=%s",
            turn_id,
            round_index + 1,
            [t["name"] for t in tool_uses],
        )

        # Parallel tool use: Claude may ask for several at once. Run them
        # concurrently — each routed to whichever side owns it — then return every
        # result in a single user message; splitting them across messages teaches
        # the model to stop batching.
        results = await asyncio.gather(*(router.dispatch(t) for t in tool_uses))

        history.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": use["id"],
                        "content": as_text(res.get("content")),
                        **({"is_error": True} if res.get("is_error") else {}),
                    }
                    for use, res in zip(tool_uses, results)
                ],
            }
        )
    else:
        await send_json(
            {
                "type": "error",
                "message": f"Stopped after {MAX_TOOL_ROUNDS} rounds of tool calls.",
            }
        )

    await send_json({"type": "turn_end", "stop_reason": stop_reason})
    return stop_reason


def as_text(content: Any) -> str:
    if isinstance(content, str):
        return content or "(no output)"
    return json.dumps(content) if content is not None else "(no output)"
