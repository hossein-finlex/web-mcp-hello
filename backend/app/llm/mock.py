"""
The mock provider.

A scripted stub that speaks the exact same contract as the real client, so the
whole pipeline — WebSocket transport, tool_use round-trip to the browser, React
state mutation, tool_result marshalling — can be exercised with no API key and no
tokens spent. It keyword-matches and emits real tool_use blocks; it is not a
language model and does not pretend to be.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable, Optional



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


async def stream_turn(
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
