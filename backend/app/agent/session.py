"""
WebSocket session: transport and lifecycle only.

    browser                          backend                        Claude
      |  user_message + tool list       |                              |
      |-------------------------------->|  messages.stream(tools=...)  |
      |         tool_use                |<-----------------------------|
      |<--------------------------------|                              |
      | executeTool() -> React state    |                              |
      |         tool_result             |                              |
      |-------------------------------->|  append + continue loop      |

The turn logic lives in loop.py; routing in router.py; the browser round-trip in
bridge.py. This module owns the socket and nothing else.
"""

from __future__ import annotations

import asyncio
import itertools
import logging

from fastapi import WebSocket, WebSocketDisconnect

from .. import llm
from .. import tools as server_tools
from .bridge import ToolBridge
from .loop import run_turn
from .router import ToolRouter

log = logging.getLogger("app.agent.session")

_turn_ids = itertools.count(1)


async def agent_session(ws: WebSocket) -> None:
    await ws.accept()

    async def send_json(payload: dict) -> None:
        await ws.send_json(payload)

    bridge = ToolBridge(send_json)
    inbox: asyncio.Queue[dict] = asyncio.Queue()
    history: list[dict] = []

    await send_json(
        {
            "type": "ready",
            "model": llm.model_name(),
            "mock": llm.is_mock(),
            "credentials": llm.have_credentials(),
            # The browser shows these so it is obvious which tools run where.
            "server_tools": [
                {
                    "name": d["name"],
                    "description": d["description"],
                    "inputSchema": d["input_schema"],
                }
                for d in server_tools.definitions()
            ],
        }
    )

    async def reader() -> None:
        """
        Single owner of ws.receive(). Tool results go to the waiting future; user
        messages are queued for the turn loop. Two coroutines reading one socket
        would race, so everything funnels through here.
        """
        while True:
            message = await ws.receive_json()
            kind = message.get("type")

            if kind == "tool_result":
                bridge.resolve(
                    message["tool_use_id"],
                    {
                        "content": message.get("content", ""),
                        "is_error": bool(message.get("is_error")),
                    },
                )
            elif kind == "user_message":
                await inbox.put(message)
            elif kind == "reset":
                history.clear()
                await send_json({"type": "reset_ok"})
            else:
                log.warning("unknown message type from browser: %r", kind)

    reader_task = asyncio.create_task(reader())
    # One long-lived getter, re-created only after it yields, so a closing socket
    # never leaves a pending inbox.get() behind.
    get_task = asyncio.create_task(inbox.get())

    try:
        while True:
            done, _ = await asyncio.wait(
                {get_task, reader_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if reader_task in done:
                get_task.cancel()
                reader_task.result()  # re-raise whatever ended it
                return

            message = get_task.result()
            get_task = asyncio.create_task(inbox.get())

            text = (message.get("text") or "").strip()
            if not text:
                continue

            turn_id = f"t{next(_turn_ids)}"
            router = ToolRouter(bridge, send_json, turn_id=turn_id)
            try:
                await run_turn(
                    text=text,
                    webmcp_tools=message.get("tools") or [],
                    history=history,
                    router=router,
                    send_json=send_json,
                    turn_id=turn_id,
                )
            except WebSocketDisconnect:
                raise
            except Exception as err:  # noqa: BLE001
                log.exception("turn %s failed", turn_id)
                bridge.fail_all("The turn was aborted.")
                await send_json({"type": "error", "message": describe(err)})
                await send_json({"type": "turn_end", "stop_reason": "error"})
    except WebSocketDisconnect:
        log.info("browser disconnected")
    finally:
        reader_task.cancel()
        get_task.cancel()
        bridge.fail_all("Connection closed.")


def describe(err: Exception) -> str:
    """Turn an exception into something worth showing a person."""
    name = type(err).__name__
    if "Authentication" in name:
        return "Claude rejected the API key. Check ANTHROPIC_API_KEY in backend/.env."
    if "RateLimit" in name:
        return "Rate limited by the API — wait a moment and try again."
    if "Connection" in name:
        return "Could not reach the Claude API. Check network access."
    return f"{name}: {err}"
