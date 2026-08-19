import { useEffect, useState } from 'react';
import { subscribeToRegistry, subscribeToTraffic } from '../webmcp-polyfill';
import { api } from '../api';

/**
 * ToolInspector.js
 *
 * A live window onto the protocol: which tools the page currently publishes,
 * and every call that crosses the boundary. Useful proof that the assistant is
 * going through WebMCP and not touching the DOM.
 */
export default function ToolInspector() {
  const [tools, setTools] = useState([]);
  const [serverTools, setServerTools] = useState([]);
  const [traffic, setTraffic] = useState([]);
  const [open, setOpen] = useState(null);

  useEffect(() => subscribeToRegistry(setTools), []);

  // Server tools are not registered with navigator.modelContext — they run in
  // the backend. Listing them here makes the two halves visible side by side.
  useEffect(() => {
    let alive = true;
    api.serverTools().then((t) => alive && setServerTools(t)).catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  useEffect(
    () =>
      subscribeToTraffic((event) =>
        setTraffic((prev) => [...prev.slice(-40), { ...event, key: `${event.id}-${event.phase}` }])
      ),
    []
  );

  return (
    <section className="inspector">
      <header className="inspector__head">
        <strong>Page tools</strong>
        <span className="inspector__count">
          {tools.length} registered · run in this tab
        </span>
      </header>

      <ul className="tools">
        {tools.map((t) => (
          <li key={t.name} className="tool">
            <button
              className="tool__name"
              onClick={() => setOpen(open === t.name ? null : t.name)}
            >
              <span className="tool__caret">{open === t.name ? '-' : '+'}</span>
              {t.name}
            </button>
            {open === t.name && (
              <div className="tool__body">
                <p className="tool__desc">{t.description}</p>
                <pre className="tool__schema">
                  {JSON.stringify(t.inputSchema, null, 2)}
                </pre>
              </div>
            )}
          </li>
        ))}
      </ul>

      {serverTools.length > 0 && (
        <>
          <header className="inspector__head inspector__head--sub">
            <strong>Server tools</strong>
            <span className="inspector__count">
              {serverTools.length} · run in the backend
            </span>
          </header>
          <ul className="tools">
            {serverTools.map((t) => (
              <li key={t.name} className="tool">
                <button
                  className="tool__name tool__name--server"
                  onClick={() => setOpen(open === t.name ? null : t.name)}
                >
                  <span className="tool__caret">{open === t.name ? '-' : '+'}</span>
                  {t.name}
                </button>
                {open === t.name && (
                  <div className="tool__body">
                    <p className="tool__desc">{t.description}</p>
                    <pre className="tool__schema">
                      {JSON.stringify(t.inputSchema, null, 2)}
                    </pre>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      <header className="inspector__head inspector__head--sub">
        <strong>Traffic</strong>
        {traffic.length > 0 && (
          <button className="linkbtn" onClick={() => setTraffic([])}>
            clear
          </button>
        )}
      </header>

      <ul className="traffic">
        {traffic.length === 0 && (
          <li className="traffic__empty">No tool calls yet.</li>
        )}
        {traffic
          .slice()
          .reverse()
          .map((e) => (
            <li key={e.key} className={`traffic__row traffic__row--${e.phase}`}>
              <span className="traffic__phase">
                {e.phase === 'call' ? '->' : e.phase === 'result' ? '<-' : '!!'}
              </span>
              <span className="traffic__name">{e.name}</span>
              <span className="traffic__args">
                {e.phase === 'call'
                  ? JSON.stringify(e.args)
                  : e.phase === 'error'
                  ? e.message
                  : e.result?.isError
                  ? 'error'
                  : 'ok'}
              </span>
            </li>
          ))}
      </ul>
    </section>
  );
}
