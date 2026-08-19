"""
The agentic loop, against a fake model and a fake browser.

This is the test that makes the loop safe to change: no network, no WebSocket, no
API key. It asserts the protocol — that tool_use blocks come back as tool_result
blocks with matching ids, that parallel calls are batched into one user message,
and that the round guard holds.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent import loop


class FakeModel:
    """Returns scripted turns; records the tools and messages it was given."""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls: list[dict] = []

    async def stream_turn(self, messages, tools, on_text):
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        turn = self.script.pop(0) if self.script else {
            "content": [{"type": "text", "text": "done"}],
            "stop_reason": "end_turn",
        }
        for block in turn["content"]:
            if block.get("type") == "text":
                await on_text(block["text"])
        return turn


class FakeRouter:
    """Stands in for the browser and the server tools."""

    def __init__(self, results: dict[str, Any] | None = None, error: set[str] = frozenset()):
        self.results = results or {}
        self.error = error
        self.dispatched: list[str] = []

    async def dispatch(self, tool_use: dict) -> dict:
        name = tool_use["name"]
        self.dispatched.append(name)
        payload = self.results.get(name, {"ok": True})
        return {"content": json.dumps(payload), "is_error": name in self.error}


def text_turn(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def tool_turn(*calls: tuple[str, dict]) -> dict:
    return {
        "content": [
            {"type": "tool_use", "id": f"tu_{i}", "name": name, "input": args}
            for i, (name, args) in enumerate(calls)
        ],
        "stop_reason": "tool_use",
    }


@pytest.fixture
def sent():
    """Collects everything the loop would push to the browser."""
    messages: list[dict] = []

    async def send_json(payload: dict) -> None:
        messages.append(payload)

    send_json.messages = messages  # type: ignore[attr-defined]
    return send_json


async def run(model, router, sent, monkeypatch, text="hello", tools=None, history=None):
    monkeypatch.setattr(loop.llm, "stream_turn", model.stream_turn)
    history = history if history is not None else []
    stop = await loop.run_turn(
        text=text,
        webmcp_tools=tools if tools is not None else [{"name": "navigate", "description": "d", "inputSchema": {}}],
        history=history,
        router=router,
        send_json=sent,
    )
    return stop, history


# --------------------------------------------------------------------------- #

async def test_a_turn_with_no_tools_streams_text_and_ends(sent, monkeypatch):
    model = FakeModel([text_turn("Eleven contracts are expiring.")])
    router = FakeRouter()

    stop, history = await run(model, router, sent, monkeypatch)

    assert stop == "end_turn"
    assert router.dispatched == []
    deltas = [m["text"] for m in sent.messages if m["type"] == "text_delta"]
    assert "".join(deltas) == "Eleven contracts are expiring."
    assert sent.messages[-1] == {"type": "turn_end", "stop_reason": "end_turn"}


async def test_tool_use_is_dispatched_and_the_result_is_fed_back(sent, monkeypatch):
    model = FakeModel(
        [tool_turn(("search_contracts", {"product": "Cyber"})), text_turn("Two found.")]
    )
    router = FakeRouter({"search_contracts": {"count": 2}})

    stop, history = await run(model, router, sent, monkeypatch)

    assert stop == "end_turn"
    assert router.dispatched == ["search_contracts"]

    # The second model call must carry the tool_result, keyed to the tool_use id.
    second = model.calls[1]["messages"]
    result_block = second[-1]["content"][0]
    assert second[-1]["role"] == "user"
    assert result_block["type"] == "tool_result"
    assert result_block["tool_use_id"] == "tu_0"
    assert json.loads(result_block["content"]) == {"count": 2}


async def test_parallel_tool_calls_return_in_one_user_message(sent, monkeypatch):
    """
    Splitting results across messages teaches the model to stop batching, so this
    is a protocol requirement rather than a style preference.
    """
    model = FakeModel(
        [
            tool_turn(("search_contracts", {}), ("get_contract", {"contract_id": "FL-0001"})),
            text_turn("Both done."),
        ]
    )
    router = FakeRouter()

    await run(model, router, sent, monkeypatch)

    assert sorted(router.dispatched) == ["get_contract", "search_contracts"]
    follow_up = model.calls[1]["messages"][-1]
    assert follow_up["role"] == "user"
    assert len(follow_up["content"]) == 2
    assert [b["tool_use_id"] for b in follow_up["content"]] == ["tu_0", "tu_1"]


async def test_a_failed_tool_is_marked_is_error_not_dropped(sent, monkeypatch):
    model = FakeModel([tool_turn(("renew_contract", {"contract_id": "FL-9999"})), text_turn("Sorry.")])
    router = FakeRouter({"renew_contract": {"error": "no such contract"}}, error={"renew_contract"})

    await run(model, router, sent, monkeypatch)

    block = model.calls[1]["messages"][-1]["content"][0]
    assert block["is_error"] is True
    assert "no such contract" in block["content"]


async def test_server_tool_definitions_are_appended_to_the_page_tools(sent, monkeypatch):
    model = FakeModel([text_turn("hi")])
    await run(model, FakeRouter(), sent, monkeypatch)

    names = [t["name"] for t in model.calls[0]["tools"]]
    assert "navigate" in names, "the page's tool list must be forwarded"
    assert "run_renewal_batch" in names, "server tools must be merged in"


async def test_the_page_tool_list_is_re_read_every_turn(sent, monkeypatch):
    """Dynamic capability discovery: a tool registered later must become visible."""
    model = FakeModel([text_turn("one"), text_turn("two")])
    history: list[dict] = []

    await run(model, FakeRouter(), sent, monkeypatch, tools=[
        {"name": "navigate", "description": "d", "inputSchema": {}}
    ], history=history)
    await run(model, FakeRouter(), sent, monkeypatch, tools=[
        {"name": "navigate", "description": "d", "inputSchema": {}},
        {"name": "show_report", "description": "d", "inputSchema": {}},
    ], history=history)

    first = {t["name"] for t in model.calls[0]["tools"]}
    second = {t["name"] for t in model.calls[1]["tools"]}
    assert "show_report" not in first
    assert "show_report" in second


async def test_refusal_is_surfaced_and_stops_the_turn(sent, monkeypatch):
    model = FakeModel(
        [
            {
                "content": [],
                "stop_reason": "refusal",
                "stop_details": {"category": "cyber"},
            }
        ]
    )
    stop, _ = await run(model, FakeRouter(), sent, monkeypatch)

    assert stop == "refusal"
    errors = [m for m in sent.messages if m["type"] == "error"]
    assert errors and "declined" in errors[0]["message"]
    assert "cyber" in errors[0]["message"]


async def test_the_round_guard_stops_a_runaway_loop(sent, monkeypatch):
    # A model that always wants another tool.
    model = FakeModel([tool_turn(("navigate", {}))] * (loop.MAX_TOOL_ROUNDS + 5))
    router = FakeRouter()

    stop, _ = await run(model, router, sent, monkeypatch)

    assert len(router.dispatched) == loop.MAX_TOOL_ROUNDS
    errors = [m for m in sent.messages if m["type"] == "error"]
    assert errors and "rounds of tool calls" in errors[0]["message"]
    assert sent.messages[-1]["type"] == "turn_end"


async def test_history_accumulates_across_turns(sent, monkeypatch):
    model = FakeModel([text_turn("first"), text_turn("second")])
    history: list[dict] = []

    await run(model, FakeRouter(), sent, monkeypatch, text="one", history=history)
    await run(model, FakeRouter(), sent, monkeypatch, text="two", history=history)

    roles = [m["role"] for m in history]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert history[0]["content"] == "one"
    assert history[2]["content"] == "two"


async def test_usage_is_reported_when_the_provider_supplies_it(sent, monkeypatch):
    turn = text_turn("hi")
    turn["usage"] = {"input_tokens": 91, "output_tokens": 12}
    turn["model"] = "claude-test"

    await run(FakeModel([turn]), FakeRouter(), sent, monkeypatch)

    usage = [m for m in sent.messages if m["type"] == "usage"]
    assert usage and usage[0]["usage"]["input_tokens"] == 91
