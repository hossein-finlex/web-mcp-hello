import { useCallback, useEffect, useRef, useState } from 'react';
import { createAgentClient } from '../agent/agentClient';
import { executeTool } from '../webmcp-polyfill';

/**
 * AssistantChat.js
 *
 * Chat surface for the real assistant. It owns the WebSocket to the Python
 * backend and renders the turn as it arrives: streamed text, tool calls, and
 * tool results. It never touches contract state — every change reaches the app
 * through the WebMCP tools that agentClient executes.
 */

const SUGGESTIONS = [
  'Which contracts are expiring in the next 60 days?',
  'Find the Novaris D&O contract and open it.',
  'Show me everything with Allianz.',
  'Renew the Lumen Digital Health cyber policy for 12 months.',
  'Which policies have already expired?',
  'Set up a new Cyber contract for Cortex Robotics with Markel, 3m limit.',
  'Renew everything expiring in the next 30 days.',
  'Build me a renewal report for the next 90 days, grouped by insurer.',
  'Is FL-0142 priced in line with the market?',
];

let uid = 0;
const nextId = () => ++uid;

export default function AssistantChat({ onBusyChange, onResetPortfolio }) {
  const [log, setLog] = useState([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [showTrace, setShowTrace] = useState(true);
  const [conn, setConn] = useState({ state: 'connecting', info: null });

  const clientRef = useRef(null);
  const scroller = useRef(null);
  // Text deltas arrive many per second; buffer into the last assistant bubble
  // rather than pushing a new log entry per token.
  const streamingId = useRef(null);

  const append = useCallback((entry) => {
    const id = nextId();
    setLog((prev) => [...prev, { id, ...entry }]);
    return id;
  }, []);

  /* ------------------------- connection lifecycle ------------------------ */
  useEffect(() => {
    const client = createAgentClient({
      onStatus: (state, info) => setConn({ state, info }),
      onEvent: (event) => {
        switch (event.kind) {
          case 'text_delta': {
            setLog((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.id === streamingId.current) {
                return [...prev.slice(0, -1), { ...last, text: last.text + event.text }];
              }
              const id = nextId();
              streamingId.current = id;
              return [...prev, { id, role: 'assistant', kind: 'say', text: event.text }];
            });
            break;
          }
          case 'call':
            streamingId.current = null;
            append({ role: 'assistant', kind: 'call', text: event.text, tool: event.tool });
            break;
          case 'server_call':
          case 'server_done':
          case 'server_progress':
            streamingId.current = null;
            append({
              role: 'assistant',
              kind: event.kind,
              text: event.text,
              tool: event.tool,
              isError: event.isError,
            });
            break;
          case 'observation':
            append({
              role: 'assistant',
              kind: 'observation',
              text: event.text,
              tool: event.tool,
              isError: event.isError,
            });
            break;
          case 'usage':
            append({ role: 'assistant', kind: 'usage', usage: event.usage, model: event.model });
            break;
          case 'error':
            streamingId.current = null;
            append({ role: 'assistant', kind: 'error', text: event.text });
            break;
          case 'turn_end':
            streamingId.current = null;
            setBusy(false);
            break;
          default:
            break;
        }
      },
    });

    clientRef.current = client;
    client.connect().catch(() => {
      /* status callback already reported it */
    });

    return () => client.close();
  }, [append]);

  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log, showTrace]);

  /* ------------------------------- sending ------------------------------ */
  const send = useCallback(
    (text) => {
      const trimmed = text.trim();
      if (!trimmed || busy || conn.state !== 'online') return;
      setDraft('');
      append({ role: 'user', kind: 'say', text: trimmed });
      streamingId.current = null;
      setBusy(true);
      if (!clientRef.current.send(trimmed)) setBusy(false);
    },
    [append, busy, conn.state]
  );

  const clearChat = useCallback(() => {
    clientRef.current?.resetConversation();
    setLog([]);
    streamingId.current = null;
  }, []);

  const reconnect = useCallback(() => {
    clientRef.current?.connect().catch(() => {});
  }, []);

  /* Direct tool calls, no model involved — proves the actuation path works
     (and costs nothing) even with no API key configured. */
  const directCall = useCallback(
    async (name, args, label) => {
      append({ role: 'user', kind: 'say', text: `[direct] ${label}` });
      append({ role: 'assistant', kind: 'call', text: `${name}(${JSON.stringify(args)})`, tool: name });
      const result = await executeTool(name, args);
      append({
        role: 'assistant',
        kind: 'observation',
        tool: name,
        isError: !!result?.isError,
        text: result?.isError ? 'error' : 'ok — watch the view on the left',
      });
    },
    [append]
  );

  const online = conn.state === 'online';
  const mock = !!conn.info?.mock;
  const visible = showTrace
    ? log
    : log.filter((m) => m.kind === 'say' || m.kind === 'error');

  return (
    <section className="chat">
      <header className="chat__head">
        <div className="chat__title">
          <span className={`dot ${busy ? 'dot--busy' : online ? 'dot--ok' : 'dot--off'}`} />
          <strong>Assistant</strong>
          <span className="chat__hint">
            {busy
              ? 'working…'
              : online
              ? mock
                ? 'mock agent'
                : conn.info?.model ?? 'connected'
              : conn.state}
          </span>
        </div>
        <label className="toggle">
          <input
            type="checkbox"
            checked={showTrace}
            onChange={(e) => setShowTrace(e.target.checked)}
          />
          tool trace
        </label>
      </header>

      {!online && (
        <div className="connbox">
          <strong>No agent backend.</strong>
          <p>
            Start it with <code>cd backend &amp;&amp; uvicorn app.main:app --port 8000</code>. The
            app and its WebMCP tools work regardless — the direct-call buttons below prove it.
          </p>
          <button className="btn btn--sm" onClick={reconnect}>
            Retry connection
          </button>
        </div>
      )}

      {online && mock && (
        <div className="connbox connbox--warn">
          <strong>Mock agent.</strong>
          <p>
            The backend is running with <code>MOCK_LLM=1</code>, so replies come from a scripted
            stub rather than Claude. Tool calls are real. Add{' '}
            <code>ANTHROPIC_API_KEY</code> to <code>backend/.env</code> and set{' '}
            <code>MOCK_LLM=0</code> for the real thing.
          </p>
        </div>
      )}

      <div className="chat__log" ref={scroller}>
        {log.length === 0 && (
          <p className="chat__empty">
            Ask about the book, or ask for a change. The assistant discovers what this page can do
            at runtime and acts through those tools — watch the portfolio on the left.
          </p>
        )}
        {visible.map((m) => (
          <Message key={m.id} message={m} />
        ))}
      </div>

      <div className="chips">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            className="chip"
            onClick={() => send(s)}
            disabled={busy || !online}
            title={online ? undefined : 'Needs the agent backend'}
          >
            {s}
          </button>
        ))}
      </div>

      <form
        className="chat__form"
        onSubmit={(e) => {
          e.preventDefault();
          send(draft);
        }}
      >
        <input
          className="chat__input"
          value={draft}
          placeholder={
            online ? (busy ? 'Working…' : 'Ask the assistant…') : 'Agent backend offline'
          }
          onChange={(e) => setDraft(e.target.value)}
          disabled={busy || !online}
        />
        <button className="btn btn--send" type="submit" disabled={busy || !online || !draft.trim()}>
          Send
        </button>
      </form>

      <div className="chat__actions">
        <button className="btn btn--sm" onClick={clearChat} disabled={busy}>
          Clear chat
        </button>
        <button className="btn btn--sm" onClick={onResetPortfolio} disabled={busy}>
          Reset portfolio
        </button>
      </div>

      <details className="direct">
        <summary>Direct tool calls (no model, no tokens)</summary>
        <div className="direct__row">
          <button
            className="chip"
            disabled={busy}
            onClick={() =>
              directCall('search_contracts', { expiring_within_days: 60 }, 'expiring within 60 days')
            }
          >
            search: expiring ≤60d
          </button>
          <button
            className="chip"
            disabled={busy}
            onClick={() => directCall('search_contracts', { product: 'Cyber' }, 'all Cyber')}
          >
            search: Cyber
          </button>
          <button
            className="chip"
            disabled={busy}
            onClick={() =>
              directCall('navigate', { view: 'contract', contract_id: 'FL-0142' }, 'open FL-0142')
            }
          >
            navigate: FL-0142
          </button>
          <button
            className="chip"
            disabled={busy}
            onClick={() => directCall('renew_contract', { contract_id: 'FL-0148', months: 12 }, 'renew FL-0148')}
          >
            renew: FL-0148
          </button>
          <button
            className="chip"
            disabled={busy}
            onClick={() =>
              directCall(
                'prefill_new_contract_form',
                {
                  insured_company: 'Cortex Robotics GmbH',
                  product: 'Crime',
                  insurer: 'Markel',
                  sum_insured: 3000000,
                  premium: 22000,
                  deductible: 25000,
                  start_date: '2026-09-01',
                  end_date: '2027-08-31',
                },
                'prefill a new Crime contract'
              )
            }
          >
            prefill: new contract
          </button>
          <button className="chip" disabled={busy} onClick={() => directCall('search_contracts', {}, 'clear filter')}>
            search: clear
          </button>
        </div>
      </details>
    </section>
  );
}

function Message({ message }) {
  const { role, kind, text, usage, model, isError } = message;

  if (kind === 'say') {
    return (
      <div className={`msg msg--${role}`}>
        <span className="msg__who">{role === 'user' ? 'You' : 'Assistant'}</span>
        <p className="msg__text">{text}</p>
      </div>
    );
  }

  if (kind === 'error') {
    return (
      <div className="msg msg--error">
        <span className="msg__who">Problem</span>
        <p className="msg__text">{text}</p>
      </div>
    );
  }

  if (kind === 'usage') {
    const cached = usage?.cache_read_input_tokens ?? 0;
    return (
      <div className="trace trace--usage">
        <code className="trace__text">
          {model} · in {usage?.input_tokens ?? '?'}
          {cached ? ` (+${cached} cached)` : ''} · out {usage?.output_tokens ?? '?'}
        </code>
      </div>
    );
  }

  const LABELS = {
    call: 'page tool',
    observation: 'result',
    server_call: 'server tool',
    server_done: 'server result',
    server_progress: 'working',
  };

  return (
    <div className={`trace trace--${kind} ${isError ? 'trace--err' : ''}`}>
      <span className="trace__label">{LABELS[kind] ?? kind}</span>
      <code className="trace__text">{text}</code>
    </div>
  );
}
