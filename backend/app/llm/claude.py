"""
The real provider: one streamed request to the Messages API.

There is no tool *execution* here. Tools live in the user's browser or in the
tools package, so this module only reports what Claude wants to call — which is
also why the manual agentic loop is used rather than the SDK's tool runner.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from ..settings import settings
from .adapters import blocks_to_json
from .prompt import system_prompt

log = logging.getLogger("app.llm.claude")

MAX_TOKENS = 8_000
EFFORT = "medium"

# Models that accept the server-side `fallbacks` parameter. Sonnet 5 does not
# ("'claude-sonnet-5' does not support the `fallbacks` parameter"), and sending it
# anyway costs a wasted 400 on every single call.
FALLBACK_MODELS = (
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-fable-5",
    "claude-mythos-5",
)
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Populated at runtime if a model rejects the parameter despite the list above.
_rejected_fallbacks: set[str] = set()

_client = None


def _get_client():
    global _client
    if _client is None:
        from anthropic import AsyncAnthropic

        # Zero-arg constructor: resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
        # / an `ant auth login` profile from the environment.
        _client = AsyncAnthropic()
    return _client


def fallbacks_available(model: str) -> bool:
    return model not in _rejected_fallbacks and model.startswith(FALLBACK_MODELS)


async def stream_turn(
    messages: list[dict],
    tools: list[dict],
    on_text: Callable[[str], Awaitable[None]],
) -> dict[str, Any]:
    client = _get_client()
    model = settings().model

    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": system_prompt(),
                # System prompt and tool list are stable across a session, so
                # caching the prefix pays off from the second turn onward.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=messages,
        thinking={"type": "adaptive"},
        output_config={"effort": EFFORT},
    )
    if tools:
        kwargs["tools"] = tools

    if not fallbacks_available(model):
        return await _run(client.messages.stream, kwargs, on_text)

    beta_kwargs = dict(kwargs, betas=[FALLBACK_BETA], fallbacks="default")
    try:
        return await _run(client.beta.messages.stream, beta_kwargs, on_text)
    except Exception as err:  # noqa: BLE001
        if not _is_unsupported_param(err):
            raise
        # Remember the rejection: without this every future request pays a 400
        # before its real call, doubling latency for the life of the process.
        _rejected_fallbacks.add(model)
        log.warning("%s rejected `fallbacks`; disabling it for this process", model)
        return await _run(client.messages.stream, kwargs, on_text)


async def _run(stream_fn, kwargs: dict, on_text) -> dict[str, Any]:
    async with stream_fn(**kwargs) as stream:
        async for event in stream:
            if (
                event.type == "content_block_delta"
                and getattr(event.delta, "type", None) == "text_delta"
            ):
                await on_text(event.delta.text)
        final = await stream.get_final_message()

    stop_details = getattr(final, "stop_details", None)
    return {
        "content": blocks_to_json(final.content),
        "stop_reason": final.stop_reason,
        "stop_details": stop_details.model_dump(mode="json") if stop_details else None,
        "usage": final.usage.model_dump(mode="json") if final.usage else None,
        "model": final.model,
    }


def _is_unsupported_param(err: Exception) -> bool:
    status = getattr(err, "status_code", None) or getattr(err, "status", None)
    if status != 400:
        return False
    text = str(err).lower()
    return any(k in text for k in ("fallback", "beta", "unexpected keyword", "unrecognized"))
