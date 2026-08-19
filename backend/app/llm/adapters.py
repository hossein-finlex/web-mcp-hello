"""
Shape translation between WebMCP and the Messages API.

WebMCP publishes `inputSchema` (camelCase JSON Schema); the API wants
`input_schema`. Confining the translation here keeps the browser speaking pure
WebMCP and the provider-specific shape out of everything else.
"""

from __future__ import annotations

from typing import Any


def webmcp_tools_to_api(tools: list[dict]) -> list[dict[str, Any]]:
    adapted = []
    for tool in tools:
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        schema = {**schema}
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        adapted.append(
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": schema,
            }
        )
    return adapted


def blocks_to_json(content: list[Any]) -> list[dict]:
    """
    Normalise SDK content blocks to plain dicts.

    Keeping history as pure JSON means it can be logged, replayed, and fed back to
    the API unchanged — and lets the mock provider produce the identical shape.
    mode="json" preserves thinking signatures and tool inputs.
    """
    return [b.model_dump(mode="json", exclude_none=True) for b in content]
