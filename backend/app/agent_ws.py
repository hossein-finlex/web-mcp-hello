"""
The WebMCP bridge.

This is the piece that makes the demo faithful to how WebMCP actually works.
A real agent does not live inside the page — the browser hands it the page's
tool list and marshals its tool calls back in. That is exactly the shape here:

    browser                          backend                        Claude
      |  user_message + tool list       |                              |
      |-------------------------------->|  messages.stream(tools=...)  |
      |                                 |----------------------------->|
      |         text_delta              |        text deltas           |
      |<--------------------------------|<-----------------------------|
      |         tool_use                |     stop_reason=tool_use     |
      |<--------------------------------|<-----------------------------|
      | executeTool() -> React state    |                              |
      | mutates, screen repaints        |                              |
      |         tool_result             |                              |
      |-------------------------------->|  append + continue loop      |
      |                                 |----------------------------->|
      |         turn_end                |      stop_reason=end_turn    |
      |<--------------------------------|<-----------------------------|

Every tool actually executes in the browser against live React state. The
backend never touches the DOM and holds no tool implementations.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect

from . import llm, server_tools

log = logging.getLogger("agent.ws")

TOOL_TIMEOUT_SECONDS = 60
MAX_TOOL_ROUNDS = 12  # guards against a pathological tool loop


class ToolBridge:
    """Sends a tool call to the browser and awaits the browser's result."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._waiters: dict[str, asyncio.Future] = {}

    async def call(self, tool_use_id: str, name: str, payload: dict) -> dict:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._waiters[tool_use_id] = future

        await self._ws.send_json(
            {"type": "tool_use", "id": tool_use_id, "name": name, "input": payload}
        )
        try:
            return await asyncio.wait_for(future, timeout=TOOL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
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


async def agent_session(ws: WebSocket) -> None:
    await ws.accept()

    bridge = ToolBridge(ws)
    inbox: asyncio.Queue[dict] = asyncio.Queue()
    history: list[dict] = []

    await ws.send_json(
        {
            "type": "ready",
            "model": "mock" if llm.is_mock() else llm.MODEL,
            "mock": llm.is_mock(),
            "credentials": llm.have_credentials(),
            # The browser shows these so it is obvious which tools run where.
            "server_tools": [
                {"name": d["name"], "description": d["description"], "inputSchema": d["input_schema"]}
                for d in server_tools.definitions()
            ],
        }
    )

    async def reader() -> None:
        """
        Single owner of ws.receive(). Tool results are routed to the waiting
        future; user messages are queued for the turn loop. Two coroutines
        reading one WebSocket would race, so everything funnels through here.
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
                await ws.send_json({"type": "reset_ok"})
            else:
                log.warning("unknown message type from browser: %r", kind)

    reader_task = asyncio.create_task(reader())

    # One long-lived getter, re-created only after it yields, so a closing
    # socket never leaves a pending inbox.get() task behind.
    get_task = asyncio.create_task(inbox.get())
    try:
        while True:
            done, _ = await asyncio.wait(
                {get_task, reader_task}, return_when=asyncio.FIRST_COMPLETED
            )
            # If the reader finished, the socket is gone (or errored) — let the
            # exception surface rather than waiting on a queue nobody feeds.
            if reader_task in done:
                get_task.cancel()
                reader_task.result()
                return

            message = get_task.result()
            get_task = asyncio.create_task(inbox.get())
            try:
                await run_turn(ws, bridge, history, message)
            except WebSocketDisconnect:
                raise
            except Exception as err:  # noqa: BLE001
                log.exception("turn failed")
                bridge.fail_all("The turn was aborted.")
                await ws.send_json({"type": "error", "message": _describe(err)})
                await ws.send_json({"type": "turn_end", "stop_reason": "error"})
    except WebSocketDisconnect:
        log.info("browser disconnected")
    finally:
        reader_task.cancel()
        get_task.cancel()
        bridge.fail_all("Connection closed.")


async def run_turn(
    ws: WebSocket, bridge: ToolBridge, history: list[dict], message: dict
) -> None:
    """One user turn: stream, execute tools in the browser, repeat until done."""
    text = (message.get("text") or "").strip()
    if not text:
        return

    # The browser sends its live tool list with every message. That is not
    # redundant — the registered tools change as the user navigates, which is
    # precisely the dynamic capability discovery WebMCP exists to provide.
    webmcp_tools = message.get("tools") or []

    # One flat tool list for the model — it does not know or care that some of
    # these run in the browser and some run here. The split matters to us, not
    # to Claude: page tools are the ones whose effect the user should watch,
    # server tools are the ones that would be absurd to drive through a UI.
    api_tools = llm.webmcp_tools_to_api(webmcp_tools) + server_tools.definitions()

    history.append({"role": "user", "content": text})

    async def on_text(chunk: str) -> None:
        await ws.send_json({"type": "text_delta", "text": chunk})

    stop_reason = "error"
    for _round in range(MAX_TOOL_ROUNDS):
        await ws.send_json({"type": "turn_status", "status": "thinking"})

        result = await llm.stream_turn(history, api_tools, on_text)
        content = result["content"]
        stop_reason = result["stop_reason"]

        history.append({"role": "assistant", "content": content})

        if result.get("usage"):
            await ws.send_json({"type": "usage", "usage": result["usage"], "model": result.get("model")})

        if stop_reason == "refusal":
            await ws.send_json(
                {
                    "type": "error",
                    "message": "The model declined this request"
                    + (
                        f" ({result['stop_details'].get('category')})"
                        if result.get("stop_details")
                        else ""
                    )
                    + ".",
                }
            )
            break

        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if stop_reason != "tool_use" or not tool_uses:
            break

        # Parallel tool use: Claude may ask for several at once. Run them
        # concurrently — each routed to whichever side owns it — then return
        # every result in a single user message; splitting them across messages
        # teaches the model to stop batching.
        results = await asyncio.gather(
            *(_dispatch(ws, bridge, tu) for tu in tool_uses)
        )

        history.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": _as_text(res.get("content")),
                        **({"is_error": True} if res.get("is_error") else {}),
                    }
                    for tu, res in zip(tool_uses, results)
                ],
            }
        )
    else:
        await ws.send_json(
            {
                "type": "error",
                "message": f"Stopped after {MAX_TOOL_ROUNDS} rounds of tool calls.",
            }
        )

    await ws.send_json({"type": "turn_end", "stop_reason": stop_reason})


async def _dispatch(ws: WebSocket, bridge: ToolBridge, tool_use: dict) -> dict:
    """Route one tool call to the browser or to this process."""
    name = tool_use["name"]
    args = tool_use.get("input") or {}

    if not server_tools.is_server_tool(name):
        return await bridge.call(tool_use["id"], name, args)

    async def progress(text: str) -> None:
        await ws.send_json(
            {"type": "server_tool_progress", "name": name, "message": text}
        )

    await ws.send_json(
        {"type": "server_tool", "phase": "start", "name": name, "input": args}
    )
    payload = await server_tools.execute(name, args, progress)
    is_error = isinstance(payload, dict) and "error" in payload
    await ws.send_json(
        {
            "type": "server_tool",
            "phase": "done",
            "name": name,
            "is_error": is_error,
            "summary": _summarise_server_result(name, payload),
        }
    )
    return {"content": json.dumps(payload), "is_error": is_error}


def _summarise_server_result(name: str, payload: dict) -> str:
    if not isinstance(payload, dict):
        return "done"
    if "error" in payload:
        return str(payload["error"])
    if name == "run_renewal_batch":
        if payload.get("committed"):
            return f"{payload.get('renewed', 0)} renewed · {payload.get('batch_id')}"
        return f"dry run · {payload.get('matched', 0)} would renew · {payload.get('batch_id')}"
    if name == "generate_renewal_report":
        head = payload.get("headline", {})
        return f"{payload.get('report_id')} · {head.get('contracts', '?')} contracts"
    if name == "benchmark_rates":
        verdict = (payload.get("comparison") or {}).get("verdict")
        return f"{payload.get('product')} benchmark{f' · {verdict}' if verdict else ''}"
    return "done"


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content or "(no output)"
    return json.dumps(content) if content is not None else "(no output)"


def _describe(err: Exception) -> str:
    name = type(err).__name__
    if "Authentication" in name:
        return (
            "Claude rejected the API key. Check ANTHROPIC_API_KEY in backend/.env."
        )
    if "RateLimit" in name:
        return "Rate limited by the API — wait a moment and try again."
    if "Connection" in name or "APIConnection" in name:
        return "Could not reach the Claude API. Check network access."
    return f"{name}: {err}"
