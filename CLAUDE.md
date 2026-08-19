# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A React + FastAPI app that Claude operates directly through **WebMCP**
(`navigator.modelContext`): a commercial financial-lines insurance contract
portfolio. The demo's point is that the assistant discovers the page's
capabilities at runtime and acts through them, so the UI visibly changes while
the user watches — no DOM scraping.

## Running it

Three processes, on **non-default ports**. 5432 and 5433 were already taken on
the original machine, as were 3000 and 3001.

```bash
docker compose up -d                                    # Postgres on :5434

cd backend
uv venv .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
cp .env.example .env                                    # add ANTHROPIC_API_KEY
.venv/bin/uvicorn app.main:app --reload --port 8000

npm install && PORT=3002 npm start                      # from the repo root
```

The backend seeds the database itself on first start; `backend/seed.py` is only
for re-seeding (`--force`, `--total 200`).

`MOCK_LLM=1` in `backend/.env` swaps Claude for a scripted stub that speaks the
identical protocol — replies are canned but **tool calls are real**, so every
actuation path works with no key and no tokens. Use it for any work that isn't
about model behaviour.

## Tests

```bash
cd backend
.venv/bin/python -m pytest -q                                  # all 108
.venv/bin/python -m pytest tests/test_services.py -q           # one file
.venv/bin/python -m pytest tests/test_agent_loop.py -k refusal # one test
```

Tests run against **SQLite in a temp file**, not the Postgres container — fast
and no docker needed. `conftest.py` sets `DATABASE_URL` before `settings()` is
cached. Anything genuinely Postgres-specific must be verified against the running
stack, not here.

Two suites carry most of the value:

- `test_services.py` — business logic with no `ToolContext`, prompt or
  WebSocket. If a change makes these need the assistant, the layering broke.
- `test_agent_loop.py` — the agentic loop against a fake model and a fake
  browser. Asserts the protocol: `tool_use_id` round-trips, parallel calls
  batched into **one** user message, `is_error` preserved, refusal handling, the
  `MAX_TOOL_ROUNDS` guard. Change the loop against these rather than by hand in a
  browser.

The frontend has no tests. `CI=true npx react-scripts build` is the check (it
treats warnings as errors).

## Architecture

The thing worth understanding before editing anything: **there are two tool
surfaces behind one flat tool list, and the model cannot tell them apart.**

```
browser (React)                backend (FastAPI)              Claude
  │  user_message + tool list        │                            │
  │─────────────────────────────────>│  messages.stream(tools=…)  │
  │          tool_use                │<─────────────────────────  │
  │<─────────────────────────────────│                            │
  │  executeTool() → React state → REST → Postgres → repaint      │
  │          tool_result             │                            │
  │─────────────────────────────────>│  append, loop              │
```

- **Page tools** live in the browser (`src/App.js`, 10 of them) and are
  registered with `navigator.modelContext`. Use them when the user should watch
  the change: navigation, form prefill, filter/sort state, showing an artifact.
- **Server tools** live in `backend/app/tools/` (3 of them) and execute in the
  FastAPI process. Use them when driving a UI would be the wrong shape: bulk
  changes, document generation, external data.

**The backend never registers page tools.** The browser sends its live tool list
with *every* message (`src/agent/agentClient.js` → `tools: listTools()`); the
backend appends its own definitions and forwards the lot. Routing is by absence:
`agent/router.py` treats anything not in the server registry as the browser's. So
a tool registered when a view mounts becomes callable on the next message — that
dynamic discovery is the reason the list is re-sent, not redundancy.

**Server work ends with a page tool.** A server tool returns an artifact id and
the assistant then calls `show_batch_result` / `show_report` to put it on screen.
Preserve that handoff when adding server tools, or the work is invisible.

### Backend layering

```
api (routers/) → assistant (llm/, tools/, agent/) → services/ → data/ → domain/ → config
```

Dependencies point one way. There is one deliberate exception: `db.py` imports
`domain.models` so `create_all` sees the tables.

**`services/` owns business operations** — plain *synchronous* functions over a
`Session`, importing nothing from `llm/`, `tools/`, `agent/` or `fastapi`.
`tools/` and `routers/` are two thin adapters over them, so the assistant and a
button in the UI cannot disagree. When adding a capability, put the logic in
`services/` and add both adapters; don't put logic in a tool.

Services are sync so FastAPI can call them directly in its threadpool. The async
tool adapters bridge via `ToolContext.in_session(fn, *args)`, which is the only
place `asyncio.to_thread` appears — keep it out of business logic.

### Two things defined exactly once

- **`domain/filters.py::ContractFilter`** is the filter contract. The REST layer
  binds query parameters to it, `data/queries.py` consumes it, and tool JSON
  Schemas are *generated* from it via `tool_properties()`. It used to be written
  out six times. Add a criterion here and it appears everywhere; don't hand-write
  it into a tool schema.
- **`services/renewals.py::is_renewable`** is the "can this be renewed" rule. The
  single-contract path raises; the bulk path excludes and reports which ids it
  excluded. Same rule, two appropriate reactions.

### Adding a server tool

One decorated function. Name comes from the function, schema from the typed input
model, description from the docstring:

```python
@server_tool()
async def my_tool(args: MyInput, ctx: ToolContext) -> dict:
    """Description the model reads to decide whether this tool fits."""
    await ctx.say("Working…")
    return (await ctx.in_session(services.my_op, args.thing)).model_dump()
```

Register it by importing the module in `app/tools/__init__.py`. Tool descriptions
are prompt engineering — they are the only text the model reads when choosing.

## Conventions

- **snake_case end to end** — Postgres columns, Pydantic fields, REST payloads,
  WebMCP tool arguments, React state. No mapping layer anywhere, deliberately.
- **`status` is derived, never stored** (`Contract.status`, and
  `queries.status_expression()` for the SQL equivalent). `renewal_pending` is a
  separate broker-set flag.
- Tools return MCP shapes: `toolResult(data)` / `toolError(msg)` on the frontend;
  a plain dict with an `"error"` key marks failure on the backend.
- The system prompt (`llm/prompt.py`) carries *agent guidance*, not business
  invariants. Rules the code must guarantee belong in code.

## Frontend gotcha worth knowing

`src/useWebMcpTools.js` registers tools **once** with a late-bound handler ref.
The obvious `useEffect(..., [])` freezes state at mount; re-registering on every
change churns the registry and can yank a tool out from under an in-flight call.
Under StrictMode's double-mount you can verify correctness by counting: exactly
10 page tools registered, not 20 and not 0.

## Traps already hit here

Each of these cost real debugging time. They are not hypothetical.

- **FastAPI 0.115 expands a Pydantic query model only when it is the *only* query
  parameter.** Declare a sibling (`sort_by: str = "end_date"`) and the whole model
  silently becomes one required parameter named after its argument — every request
  then 422s. Sort/limit therefore live inside `ContractQuery`, not in the route
  signature.
- **Import `select` from `sqlmodel`, not `sqlalchemy`.** With SQLAlchemy's,
  `session.exec()` returns `Row` objects instead of model instances.
- **Use `session.scalar()` for `select(func.count())`** — `session.exec(...).one()`
  gives a `Row`, not an int.
- **Literal route paths must be declared before parameterised ones.**
  `/api/contracts/search` before `/api/contracts/{contract_id}`, or "search" is
  read as a contract id.
- **`claude-sonnet-5` rejects the server-side `fallbacks` parameter.**
  `llm/claude.py` gates it by model and caches a runtime rejection; without that
  every call paid a wasted 400 first.
- **`repository.renew(..., commit=False)`** is what makes the bulk batch atomic.
  Committing per contract would leave a half-renewed book on failure.

## Not committed

`backend/.env` holds the API key and is gitignored. `backend/.env.example`
documents the variables with placeholders.
