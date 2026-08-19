"""
The model side of the bridge.

`stream_turn` issues one Claude request and returns a normalised result:

    {"content": [<plain dict blocks>], "stop_reason": "tool_use" | "end_turn" | ...}

Blocks are plain dicts (not SDK objects) so the conversation history is pure
JSON — it can be logged, replayed, and fed back to the API unchanged, and the
mock provider below can produce the exact same shape.

Note there is no tool *execution* here. The tools live in the user's browser, so
this module only reports what Claude wants to call; `agent_ws.py` marshals the
call across the WebSocket. That is also why we use the manual agentic loop
rather than the SDK's tool runner — the runner expects to execute tools in-process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import date
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("agent.llm")

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5")
MAX_TOKENS = 8_000

SYSTEM_TEMPLATE = """\
You are the assistant embedded in Portfolio, a commercial financial-lines \
insurance application used by insurance brokers. You operate the application \
directly through the tools it registers with the browser via WebMCP.

Today is {today}. All amounts are EUR. The book is D&O, Cyber, PI \
(Vermögensschaden-Haftpflicht), Crime, EPLI and W&I.

How to work here:

- The user is watching the screen while you act. Your tool calls visibly change \
what they see: searching filters the table, navigating switches the view, an \
edit updates the row in place. Act rather than describing what could be done.
- Never invent or guess a contract id. Call search_contracts first and use the \
ids it returns.
- Make the tools do the work. For superlatives — largest, smallest, soonest to \
expire, most expensive — use search_contracts with sort_by and a small limit. \
For totals and rankings — how much premium, how many contracts, which insurer \
has the most — use summarise_portfolio. Both run in SQL. Do not pull the whole \
book back and add it up yourself: it is slower, it costs far more, and your \
arithmetic can be wrong where the database's cannot.
- When the user asks about a specific contract, navigate to it so they can see \
it rather than reciting its fields.
- To create a contract: if any detail is missing or ambiguous, call \
prefill_new_contract_form so the user can review and submit it themselves. Use \
create_contract only when the user clearly wants it created immediately.
- Renewals extend the term from the current expiry date. Do not change premium \
or sum insured during a renewal unless the user asked you to.
- Contract status is derived from the term: expired, expiring (within 90 days), \
active, or draft. `renewal_pending` is a separate flag a broker sets.
- Keep replies to one or two sentences. The result is already on screen, so do \
not read it back. Mention figures only when they are the point of the answer.
- If a tool returns an error, say plainly what failed and what would fix it.

Two kinds of tool are available to you, and choosing the right one matters:

- **Page tools** (search_contracts, navigate, update_contract, renew_contract, \
the form tools) run inside the user's browser. Their effect is visible \
immediately. Use them for anything the user should watch happen, and for \
single-record work.
- **Server tools** (run_renewal_batch, generate_renewal_report, \
benchmark_rates) run in the backend. Use them when driving the UI would be the \
wrong shape: bulk changes across many contracts, assembling a document, or \
data that lives outside the application. Renewing twelve contracts one page \
call at a time is slow and can stop half-finished; run_renewal_batch does it in \
one transaction.

Server work is invisible to the user, so always finish the handoff: when a \
server tool returns a batch_id or report_id, call show_batch_result or \
show_report so the result appears on screen. Never just describe it.

run_renewal_batch previews by default and changes nothing. Show the user what \
it would do, wait for them to agree, then call it again with commit=true. Do \
not commit a bulk change on your own initiative.\
"""


def system_prompt() -> str:
    # Rounded to the day on purpose: a timestamp here would change on every
    # request and silently invalidate the prompt cache.
    return SYSTEM_TEMPLATE.format(today=date.today().isoformat())


def webmcp_tools_to_api(tools: list[dict]) -> list[dict]:
    """
    Adapt WebMCP tool descriptors to Messages API tool definitions.

    WebMCP publishes `inputSchema` (camelCase, JSON Schema); the API wants
    `input_schema`. Doing the translation here keeps the browser speaking pure
    WebMCP and confines the provider-specific shape to this module.
    """
    adapted = []
    for tool in tools:
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        if "type" not in schema:
            schema = {**schema, "type": "object"}
        schema.setdefault("properties", {})
        adapted.append(
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": schema,
            }
        )
    return adapted


def is_mock() -> bool:
    return os.environ.get("MOCK_LLM", "0") not in ("0", "", "false", "False")


def have_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


# --------------------------------------------------------------------------- #
# Real provider                                                               #
# --------------------------------------------------------------------------- #

_client = None


def _get_client():
    global _client
    if _client is None:
        from anthropic import AsyncAnthropic

        # Zero-arg constructor: resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
        # / an `ant auth login` profile from the environment.
        _client = AsyncAnthropic()
    return _client


async def _stream_real(
    messages: list[dict],
    tools: list[dict],
    on_text: Callable[[str], Awaitable[None]],
) -> dict:
    client = _get_client()

    kwargs: dict[str, Any] = dict(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": system_prompt(),
                # The system prompt and tool list are stable across a session,
                # so caching the prefix pays off from the second turn onward.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=messages,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
    )
    if tools:
        kwargs["tools"] = tools

    if not _fallbacks_available(MODEL):
        return await _run_stream(client.messages.stream, kwargs, on_text)

    # Server-side refusal fallback: if a policy classifier declines, the API
    # re-runs the same request on a fallback model within the same call.
    beta_kwargs = dict(
        kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
    )

    try:
        return await _run_stream(client.beta.messages.stream, beta_kwargs, on_text)
    except Exception as err:  # noqa: BLE001
        if not _is_unsupported_param_error(err):
            raise
        # Remember the rejection: without this every future request pays a 400
        # before its real call, doubling latency for the life of the process.
        _FALLBACKS_REJECTED.add(MODEL)
        log.warning("%s rejected `fallbacks`; disabling it for this process", MODEL)
        return await _run_stream(client.messages.stream, kwargs, on_text)


async def _run_stream(stream_fn, kwargs: dict, on_text) -> dict:
    async with stream_fn(**kwargs) as stream:
        async for event in stream:
            if (
                event.type == "content_block_delta"
                and getattr(event.delta, "type", None) == "text_delta"
            ):
                await on_text(event.delta.text)
        final = await stream.get_final_message()

    return {
        # mode="json" keeps thinking signatures and tool inputs intact while
        # guaranteeing the history stays JSON-serialisable.
        "content": [block.model_dump(mode="json", exclude_none=True) for block in final.content],
        "stop_reason": final.stop_reason,
        "stop_details": (
            final.stop_details.model_dump(mode="json") if getattr(final, "stop_details", None) else None
        ),
        "usage": final.usage.model_dump(mode="json") if final.usage else None,
        "model": final.model,
    }


# Models that accept the server-side `fallbacks` parameter. Sonnet 5 does not
# ("'claude-sonnet-5' does not support the `fallbacks` parameter"), so sending it
# there costs a wasted 400 on every call.
_FALLBACK_MODELS = ("claude-opus-5", "claude-opus-4-8", "claude-fable-5", "claude-mythos-5")

# Filled in at runtime if a model rejects the parameter despite the list above.
_FALLBACKS_REJECTED: set[str] = set()


def _fallbacks_available(model: str) -> bool:
    return model not in _FALLBACKS_REJECTED and model.startswith(_FALLBACK_MODELS)


def _is_unsupported_param_error(err: Exception) -> bool:
    status = getattr(err, "status_code", None) or getattr(err, "status", None)
    if status != 400:
        return False
    text = str(err).lower()
    return any(k in text for k in ("fallback", "beta", "unexpected keyword", "unrecognized"))


# --------------------------------------------------------------------------- #
# Mock provider                                                               #
# --------------------------------------------------------------------------- #
#
# Exercises the entire pipeline — WebSocket transport, tool_use round-trip to
# the browser, React state mutation, tool_result marshalling — without an API
# key or a single token spent. It is a scripted stub, not a language model: it
# keyword-matches and emits real tool_use blocks.

_MOCK_ID = iter(range(1, 10_000))

# Word-boundary anchored so "expiring" is not read as the PI product.
_MOCK_PRODUCTS = [
    (r"\bd&o\b|\bdirectors?\b", "D&O"),
    (r"\bcyber\b", "Cyber"),
    (r"\bcrime\b|\bfidelity\b", "Crime"),
    (r"\bepli\b|\bemployment\b", "EPLI"),
    (r"\bw&i\b|\bwarrant(y|ies)\b", "W&I"),
    (r"\bpi\b|\bprofessional indemnity\b", "PI"),
]


async def _stream_mock(
    messages: list[dict],
    tools: list[dict],
    on_text: Callable[[str], Awaitable[None]],
) -> dict:
    tool_names = {t["name"] for t in tools}
    last_user = _last_user_text(messages).lower()
    # Only this turn's tool results count. Scanning the whole history would make
    # every turn after the first look like it had already run its tool.
    results = _mock_tool_results(messages[_current_turn_start(messages):])

    async def say(text: str) -> dict:
        for chunk in re.findall(r"\S+\s*", text):
            await on_text(chunk)
            await asyncio.sleep(0.02)
        return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}

    def call(name: str, payload: dict) -> dict:
        return {
            "content": [
                {
                    "type": "tool_use",
                    "id": f"mocktool_{next(_MOCK_ID)}",
                    "name": name,
                    "input": payload,
                }
            ],
            "stop_reason": "tool_use",
        }

    # Second pass: a tool already ran this turn, so report and stop.
    if results:
        last = results[-1]
        if last["payload"].get("error"):
            return await say(f"[mock] {last['name']} failed: {last['payload']['error']}")
        if last["name"] == "search_contracts":
            n = len(last["payload"].get("contracts", []))
            return await say(
                f"[mock] Found {n} matching contract{'' if n == 1 else 's'} — "
                "the table on the left is filtered to them."
            )
        if last["name"] == "renew_contract":
            c = last["payload"].get("contract", {})
            return await say(
                f"[mock] Renewed {c.get('insured_company', 'the contract')} through "
                f"{c.get('end_date', 'the new expiry')}."
            )
        if last["name"] == "prefill_new_contract_form":
            return await say(
                "[mock] I filled in the new-contract form — review it and hit Create."
            )
        return await say("[mock] Done — see the updated view on the left.")

    # First pass: pick a tool from the utterance.
    if "renew" in last_user and "renew_contract" in tool_names:
        target = _mock_find_id(last_user, messages)
        if target:
            return call("renew_contract", {"contract_id": target, "months": 12})

    if any(w in last_user for w in ("new contract", "create", "add a contract")) and (
        "prefill_new_contract_form" in tool_names
    ):
        return call(
            "prefill_new_contract_form",
            {
                "insured_company": "Mock Industries GmbH",
                "product": "Cyber",
                "insurer": "Chubb",
                "sum_insured": 5_000_000,
                "premium": 40_000,
                "deductible": 50_000,
                "start_date": "2026-09-01",
                "end_date": "2027-08-31",
            },
        )

    if "search_contracts" in tool_names:
        query: dict[str, Any] = {}

        for pattern, product in _MOCK_PRODUCTS:
            if re.search(pattern, last_user):
                query["product"] = product
                break

        # "in the next 60 days" / "within 30 days"
        window = re.search(r"(\d+)\s*days", last_user)
        if window and "expir" in last_user:
            query["expiring_within_days"] = int(window.group(1))
        elif "expired" in last_user or "lapsed" in last_user:
            query["status"] = "expired"
        elif "expir" in last_user:
            query["status"] = "expiring"

        if re.search(r"\brenewal\b.*\bpending\b|\bpending\b.*\brenewal\b", last_user):
            query["renewal_pending"] = True

        insurer = re.search(
            r"\b(allianz|axa|hdi|zurich|chubb|markel|aig|ergo|vov|hiscox)\b", last_user
        )
        if insurer:
            query["insurer"] = insurer.group(1).title()

        if not query:
            query["query"] = _mock_freetext(last_user)
        return call("search_contracts", query)

    return await say("[mock] No tools are registered, so there is nothing I can do.")


def _current_turn_start(messages: list[dict]) -> int:
    """
    Index of the user utterance that began the current turn. A real user message
    carries plain string content; tool_result messages carry a block list.
    """
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg["role"] == "user" and isinstance(msg["content"], str):
            return i
    return 0


def _last_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg["role"] != "user":
            continue
        content = msg["content"]
        if isinstance(content, str):
            return content
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block["text"]
    return ""


def _mock_tool_results(messages: list[dict]) -> list[dict]:
    """Pair tool_use blocks with the tool_result blocks that answered them."""
    names: dict[str, str] = {}
    out = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, str):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                names[block["id"]] = block["name"]
            elif block.get("type") == "tool_result":
                raw = block.get("content")
                if isinstance(raw, list):
                    raw = next(
                        (b.get("text") for b in raw if isinstance(b, dict) and b.get("text")),
                        "",
                    )
                try:
                    payload = json.loads(raw) if isinstance(raw, str) else {}
                except json.JSONDecodeError:
                    payload = {}
                out.append(
                    {
                        "name": names.get(block.get("tool_use_id"), "?"),
                        "payload": payload if isinstance(payload, dict) else {},
                    }
                )
    return out


def _mock_find_id(text: str, messages: list[dict]) -> Optional[str]:
    match = re.search(r"fl-?0?(\d{3,4})", text)
    if match:
        return f"FL-{int(match.group(1)):04d}"
    for result in reversed(_mock_tool_results(messages)):
        contracts = result["payload"].get("contracts") or []
        if contracts:
            return contracts[0].get("id")
    return None


def _mock_freetext(text: str) -> str:
    stop = {
        "find", "show", "me", "the", "a", "an", "for", "please", "contract",
        "contracts", "on", "of", "in", "my", "our", "all", "list", "which",
        "what", "is", "are", "and", "get", "look", "up",
    }
    words = [w for w in re.findall(r"[a-zA-Z&]{2,}", text) if w.lower() not in stop]
    return " ".join(words[:3])


# --------------------------------------------------------------------------- #

async def stream_turn(
    messages: list[dict],
    tools: list[dict],
    on_text: Callable[[str], Awaitable[None]],
) -> dict:
    if is_mock():
        return await _stream_mock(messages, tools, on_text)
    return await _stream_real(messages, tools, on_text)
