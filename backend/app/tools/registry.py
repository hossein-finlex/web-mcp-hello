"""
Server-side tool registry.

Adding a tool used to mean three coordinated edits — a DEFINITIONS entry, a
HANDLERS entry, and a hand-written JSON Schema that could drift from both. Now a
tool is one decorated function:

    @server_tool()
    async def benchmark_rates(args: BenchmarkInput, ctx: ToolContext) -> dict:
        ...

The name comes from the function, the schema is generated from the argument
model, and the arguments arrive validated and typed instead of as a dict you
poke at with .get().
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, get_type_hints

from pydantic import BaseModel, ValidationError
from sqlmodel import Session

from ..domain.filters import flatten_optional_schema

log = logging.getLogger("app.tools")

Progress = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ToolContext:
    """
    Everything a server tool is allowed to reach for.

    Passing one object rather than threading `progress` (and later a session, a
    request id, …) through every signature means adding a capability does not
    touch every tool.
    """

    progress: Progress
    session_factory: Callable[[], Session]
    turn_id: str = "-"

    async def say(self, message: str) -> None:
        """Report progress to whoever is watching. Never fails the tool."""
        try:
            await self.progress(message)
        except Exception:  # noqa: BLE001
            log.debug("progress dropped: %s", message)

    async def in_session(self, fn, *args, **kwargs):
        """
        Run a synchronous service function on a worker thread with its own
        session.

        Services are sync so the HTTP layer can call them directly; tools are
        async. This is the one place that reconciles the two, which keeps
        `asyncio.to_thread` out of the business logic.
        """
        import asyncio

        def call():
            with self.session_factory() as session:
                return fn(session, *args, **kwargs)

        return await asyncio.to_thread(call)


@dataclass(frozen=True)
class ServerTool:
    name: str
    description: str
    input_model: Optional[type[BaseModel]]
    handler: Callable[..., Awaitable[dict]]

    def definition(self) -> dict[str, Any]:
        """The Messages API tool definition, generated from the input model."""
        if self.input_model is None:
            schema: dict[str, Any] = {"type": "object", "properties": {}}
        else:
            schema = flatten_optional_schema(self.input_model.model_json_schema())
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }

    async def run(self, raw_args: dict, ctx: ToolContext) -> dict:
        if self.input_model is None:
            return await self.handler(ctx)
        try:
            args = self.input_model.model_validate(raw_args or {})
        except ValidationError as err:
            # Hand the model a usable correction rather than a stack trace.
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}"
                for e in err.errors()
            )
            return {"error": f"Invalid arguments for {self.name} — {problems}"}
        return await self.handler(args, ctx)


_REGISTRY: dict[str, ServerTool] = {}


def server_tool(
    *, name: Optional[str] = None, description: Optional[str] = None
) -> Callable:
    """
    Register one server-side tool.

    The description defaults to the function's docstring, so the text the model
    reads lives with the code it describes.
    """

    def decorate(fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
        tool_name = name or fn.__name__
        text = description or inspect.cleandoc(fn.__doc__ or "")
        if not text:
            raise ValueError(f"{tool_name} needs a description or a docstring")

        input_model = _first_model_argument(fn)

        if tool_name in _REGISTRY:
            raise ValueError(f"server tool {tool_name!r} is already registered")

        _REGISTRY[tool_name] = ServerTool(
            name=tool_name, description=text, input_model=input_model, handler=fn
        )
        return fn

    return decorate


def _first_model_argument(fn: Callable) -> Optional[type[BaseModel]]:
    """The annotation of the first parameter, if it is a Pydantic model."""
    hints = get_type_hints(fn)
    for param in inspect.signature(fn).parameters.values():
        annotation = hints.get(param.name)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
        break
    return None


# --------------------------------------------------------------------------- #
# Read-side API — the agent router talks to these three functions only.
# --------------------------------------------------------------------------- #

def definitions() -> list[dict[str, Any]]:
    return [tool.definition() for tool in _REGISTRY.values()]


def names() -> frozenset[str]:
    return frozenset(_REGISTRY)


def is_server_tool(name: str) -> bool:
    return name in _REGISTRY


def get(name: str) -> Optional[ServerTool]:
    return _REGISTRY.get(name)


async def execute(name: str, raw_args: dict, ctx: ToolContext) -> dict:
    tool = _REGISTRY.get(name)
    if tool is None:
        return {"error": f"Unknown server tool {name!r}"}
    try:
        return await tool.run(raw_args, ctx)
    except Exception as err:  # noqa: BLE001
        log.exception("server tool %s failed [turn=%s]", name, ctx.turn_id)
        return {"error": f"{type(err).__name__}: {err}"}
