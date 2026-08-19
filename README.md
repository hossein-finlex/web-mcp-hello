# WebMCP Contract Portfolio

A commercial financial-lines insurance app that **Claude operates directly**
through `navigator.modelContext` — the WebMCP (Web Model Context Protocol) API.

Ask *"which contracts are expiring in the next 60 days?"* and the table filters
in front of you. Ask for a renewal and the term rolls forward in Postgres and on
screen. The assistant discovers what the page can do at runtime by reading the
tool schemas the page publishes — no DOM scraping, no selectors, no screenshots.

---

## Running it

Three processes. You need an Anthropic API key for the real assistant; without
one, everything except the model still works (see *Without a key* below).

```bash
# 1. Postgres  (port 5434 — 5432 and 5433 are already taken on this machine)
docker compose up -d

# 2. Backend
cd backend
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env          # then put your ANTHROPIC_API_KEY in it
.venv/bin/python seed.py      # 50 contracts
.venv/bin/uvicorn app.main:app --reload --port 8000

# 3. Frontend
npm install
PORT=3002 npm start           # http://localhost:3002
```

The backend seeds the database itself on first start, so `seed.py` is only
needed if you want to re-seed or change the size (`--force`, `--total 200`).

### Without a key

- `MOCK_LLM=1` in `backend/.env` swaps Claude for a scripted stub that speaks
  the identical protocol. Replies are canned; **the tool calls are real**, so
  every actuation path still works. Useful for demoing without spending tokens.
- With no backend at all, the app still loads and the **Direct tool calls**
  panel in the sidebar invokes the WebMCP tools with no model in the loop.

---

## Documentation

[**WebMCP in Practice**](docs/webmcp-in-practice.html) — what problem an in-app
assistant actually has, what WebMCP is, and how the browser, backend and model
communicate, with diagrams of the tool-call sequence and the server-to-page
handoff. Open the file in a browser.

[**CLAUDE.md**](CLAUDE.md) — orientation for working in this repository:
commands, the layering rules, and the traps already hit here.

---

## Try these

| Ask | What you should see |
|---|---|
| "Which contracts are expiring in the next 60 days?" | The table narrows, the filter bar turns purple |
| "Show me everything with Allianz." | Filters by insurer |
| "Find the Novaris D&O contract and open it." | Searches, then navigates to the detail view |
| "Renew the Lumen Digital Health cyber policy for 12 months." | Term rolls 12 months, renewal flag clears, row flashes |
| "Set up a new Cyber contract for Cortex Robotics with Markel, 3m limit." | The new-contract form opens **prefilled but not submitted** |
| "Put FL-0146's premium up to 95,000." | The contract updates in place |
| "What's the total premium by insurer?" | Aggregated in SQL, shown as a breakdown — no contracts pulled into context |
| "Which two contracts have the largest limits?" | `sort_by` + `limit` in SQL; the table reorders and shows exactly two |
| **"Renew everything expiring in the next 30 days."** | A **server** tool previews the batch. Confirm, and it commits in one transaction, then WebMCP navigates you to the result |
| **"Build me a renewal report for the next 90 days."** | Generated on the server, then `show_report` puts it on screen |
| **"Is FL-0142 priced in line with the market?"** | Benchmark data from outside the app — the page has no route to it |

The purple border around the left pane means the assistant is driving. The
**WebMCP panel** at the bottom right lists every registered tool — click one to
see the JSON Schema Claude actually receives — and logs each call as it crosses
the boundary.

Everything also works by hand: click a row, hit Edit, hit Renew. Human and agent
share the same API and the same React state, so there is no separate "agent
mode" and no way for the two to disagree.

---

## Architecture

The interesting part is that **the agent genuinely lives outside the page**,
which is how WebMCP actually works: the browser hands the agent a tool list and
marshals its tool calls back in.

```
browser (React)                backend (FastAPI)              Claude
  │  user_message + tool list        │                           │
  │─────────────────────────────────>│  messages.stream(tools=…)  │
  │                                  │──────────────────────────> │
  │          text_delta              │      streamed text         │
  │<─────────────────────────────────│<─────────────────────────── │
  │          tool_use                │   stop_reason=tool_use     │
  │<─────────────────────────────────│<─────────────────────────── │
  │                                                               │
  │  executeTool() → REST → Postgres → React state → repaint      │
  │                                                               │
  │          tool_result             │                           │
  │─────────────────────────────────>│  append, continue loop     │
  │                                  │──────────────────────────> │
  │          turn_end                │   stop_reason=end_turn     │
  │<─────────────────────────────────│<─────────────────────────── │
```

Claude never sees the DOM. The backend holds **no tool implementations** — it
only reports what Claude wants to call. Every tool executes in the browser
against live React state.

```
docker-compose.yml            Postgres 17 on :5434
backend/
├── seed.py                   seeding CLI
└── app/
    ├── main.py               FastAPI: REST + /ws/agent
    ├── db.py                 engine, session dependency, readiness wait
    ├── models.py             SQLModel table + validated API schemas
    ├── repository.py         all SQL lives here
    ├── seed_data.py          12 curated contracts (terms relative to today)
    ├── seed_gen.py           deterministic generator for the rest
    ├── queries.py            filtering, sorting and aggregation in SQL
    ├── server_tools.py       tools that run here, not in the page
    ├── artifacts.py          batch records and reports
    ├── llm.py                Claude client + the mock provider
    └── agent_ws.py           the bridge: routes each tool call to the right side
src/
├── webmcp-polyfill.js        polyfill + agent-side bridge
├── useWebMcpTools.js         registration lifecycle hook
├── api.js                    REST client
├── App.js                    owns state; registers the seven tools
├── agent/agentClient.js      WebSocket client; executes tool calls
└── components/               ContractList · ContractDetail · NewContractForm ·
                              PortfolioSummary · BatchResult · ReportView ·
                              AssistantChat · ToolInspector
```

### Why a manual agentic loop

The Anthropic SDK's tool runner executes tools in-process. Here the tools live
in the user's browser, so `agent_ws.py` drives the
`stop_reason == "tool_use"` loop by hand and awaits each result over the
WebSocket. Parallel tool calls are executed concurrently and returned in a
single `user` message, as the API expects.

### Two tool surfaces, one tool list

Claude receives one flat list. It neither knows nor cares that some of those
tools run in the browser and some run in the backend — but the split is the
most important design decision here.

**Page tools** (WebMCP, `navigator.modelContext`) are the *page's* capabilities.
Use them when the user should watch the change happen, and for single-record
work. They execute against live React state.

**Server tools** run in the FastAPI process and never touch the browser. Use
them when driving a UI would be the wrong shape entirely:

| Server tool | Why it does not belong in the UI |
|---|---|
| `run_renewal_batch` | Renewing 14 contracts through the page is 14 round-trips through the model, any of which can stop halfway. One call, one transaction, all-or-nothing. |
| `generate_renewal_report` | Assembling a document is computation, not clicking. |
| `benchmark_rates` | Market rate data lives outside the application. No amount of UI automation would find it. |

The pattern that ties them together is **the handoff**. Server work is invisible
— so a server tool returns an artifact id, and the assistant then calls a page
tool to put it on screen:

```
run_renewal_batch(expiring_within_days=30)      ← server: previews, changes nothing
   → "4 contracts, €413,400. Shall I commit?"
run_renewal_batch(..., commit=true)             ← server: one transaction
   → batch_id: BATCH-0002
show_batch_result(batch_id="BATCH-0002")        ← page:  navigates the user there
```

Work happens off-page; the *result* still lands on-page. The chat colours the
two differently (purple = the UI moved, amber = work happened elsewhere) and the
inspector lists them under separate headings, so which side did what is never a
guess.

**Bulk changes preview by default.** `run_renewal_batch` is a dry run unless
`commit=true`. A bulk mutation should not happen because a model was 80% sure it
was wanted — the assistant shows the plan and waits.

### The page tools

| Tool | Effect on screen |
|---|---|
| `search_contracts` | Filters, sorts and limits the visible table (this is why an agent search is *visible*) |
| `summarise_portfolio` | Aggregates in SQL and opens the breakdown view |
| `get_contract` | None — returns the full record |
| `navigate` | Switches view |
| `prefill_new_contract_form` | Fills the form and stops. **The human submits.** |
| `create_contract` | Writes to Postgres, opens the new contract |
| `update_contract` | Updates the row in place |
| `renew_contract` | Rolls one term forward, clears the renewal flag |
| `show_batch_result` | Displays a server-produced batch record |
| `show_report` | Displays a server-produced report |

#### Tool surface is a cost decision

`search_contracts` gained `sort_by` / `sort_dir` / `limit`, and
`summarise_portfolio` was added, for a specific reason. Asked *"which two
contracts have the largest sum insured?"*, the assistant originally called
`search_contracts({})`, pulled all 50 rows into context and sorted them itself —
**6,809 input tokens** and two tool calls. With sort and limit pushed into SQL
the same question costs **518 tokens** and one call, and the arithmetic is the
database's rather than the model's.

If your agent is reading a lot to answer a little, that is a missing tool, not a
prompting problem.

Tool argument names match the API and database columns exactly (snake_case
throughout), so there is no mapping layer anywhere for a bug to hide in.

`prefill_new_contract_form` is the human-in-the-loop case worth noticing: the
agent does the typing, the person keeps the decision. The system prompt tells
Claude to prefer it over `create_contract` whenever a detail was inferred.

---

## The data

50 contracts: 12 curated ones with a story in their notes, plus 38 generated.

The generator (`seed_gen.py`) is deterministic and cares about two things a
random-data script usually gets wrong:

- **Correlated figures.** Premium is a rate on the limit, with a rate band per
  product (D&O 0.35–0.75%, Cyber 0.8–1.6%, …), and deductibles scale with the
  limit. Otherwise nothing the assistant says about the book sounds credible.
- **A realistic expiry pipeline.** Terms are placed relative to *today* against
  a target status mix — roughly 10% expired, 25% expiring inside 90 days, the
  rest active, plus two drafts. So "what needs renewing?" is always a real
  question, and re-seeding in six months still produces a live-looking book
  rather than one that has entirely lapsed.

Status (`active` / `expiring` / `expired` / `draft`) is **computed from the
term**, never stored, so it cannot drift. `renewal_pending` is a separate flag a
broker sets.

All insured companies are fictional. Insurer names are real market
participants, used the way any broker demo uses them; nothing here represents a
real policy.

---

## The polyfill

`src/webmcp-polyfill.js` does two separate jobs, and the distinction matters:

**Page side (the actual polyfill).** Native `navigator.modelContext` is not
shipped everywhere yet. If it is missing, the file installs a stub implementing
the proposed surface — `registerTool`, `unregisterTool`, `provideContext` — that
logs every registration and invocation to the DevTools console. The app never
crashes, and the header badge tells you which one you got.

**Agent side (a bridge).** There is no page-facing API for "be the agent", so
the module also mirrors every registered tool and exposes `listTools()` /
`executeTool()` on top. `agentClient.js` uses that bridge and nothing else. The
mirror is maintained in both native and polyfilled browsers, so behaviour is
identical either way.

From the DevTools console:

```js
await webmcp.listTools()
await webmcp.executeTool('search_contracts', { product: 'Cyber', status: 'expiring' })
await webmcp.executeTool('renew_contract', { contract_id: 'FL-0142', months: 24 })
```

---

## The React trap worth knowing about

The obvious way to register a tool is wrong:

```js
useEffect(() => {
  const h = registerTool({ name: 'x', execute: () => doThingWith(contracts) });
  return () => h.unregister();
}, []);                       // `contracts` is frozen at mount forever
```

Re-registering on every state change is also wrong — the browser would see the
whole tool set churn constantly, and an in-flight call could be yanked out from
under the agent.

`useWebMcpTools.js` registers **once** with a stable indirection: the registered
`execute` resolves the real handler from a ref that every render refreshes. The
registration is stable; the handlers always see current state. Under React
StrictMode's double-mount you can confirm exactly seven tools are registered,
not fourteen and not zero.

---

## Notes and limits

- `SEED_TOTAL` / `seed.py --total` change the book size. Filtering, sorting and
  limiting already run in SQL (`queries.py`), so the only thing a much larger
  book needs is pagination in the list view.
- Batch records and reports live in memory (`artifacts.py`, capped at 50). They
  are job output rather than domain data; a real deployment would persist them,
  since a bulk-change record is an audit trail.
- `benchmark_rates` returns invented numbers. It stands in for a market-data
  subscription — the point is that it is data the browser has no route to.
- New contract ids come from `max(id) + 1`. Two simultaneous creates could
  collide; a database sequence is the one-line fix.
- The conversation lives in memory per WebSocket connection, so a reload starts
  a fresh chat. The portfolio itself is in Postgres and persists.
- `output_config: {effort: "medium"}` with adaptive thinking is set in
  `llm.py`; raise it to `high` if you want the assistant to plan multi-step
  work more carefully.
- Server-side refusal fallbacks are enabled. If your account or SDK version
  rejects the parameter, `llm.py` logs a warning and retries once on the plain
  path rather than failing the turn.
