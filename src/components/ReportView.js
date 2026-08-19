import { useState } from 'react';

const money = (n) =>
  typeof n === 'number'
    ? `€${n.toLocaleString('de-DE', { maximumFractionDigits: 0 })}`
    : n;

/**
 * ReportView.js — where a server-generated document lands.
 *
 * Assembling the report is computation, so it happens in the backend. WebMCP's
 * only job here is the handoff: show_report navigates the user to the finished
 * artifact rather than the assistant reciting it into the chat.
 */
/** Right-aligned columns. Matched exactly: "Insured" is a company, "Sum insured" is money. */
const NUMERIC_COLUMNS = new Set(['Contracts', 'Premium', 'Sum insured', 'Flagged', 'Days']);

export default function ReportView({ report, onBack, onOpen }) {
  const [showSource, setShowSource] = useState(false);

  if (!report) {
    return (
      <section className="panel">
        <div className="panel__head">
          <h2>No report to show</h2>
        </div>
        <p className="empty">Ask the assistant to generate a renewal report.</p>
        <button className="btn" onClick={onBack}>
          Back to the portfolio
        </button>
      </section>
    );
  }

  const { title, headline, sections = [], scope, markdown } = report;
  const scopeEntries = Object.entries(scope ?? {});

  return (
    <section className="panel">
      <div className="panel__head">
        <div>
          <h2>
            {title} <span className="row__policy">{report.id}</span>
          </h2>
          <p className="detail__meta">
            Generated on the server &middot; {new Date(report.created_at).toLocaleString()}
          </p>
        </div>
        <label className="toggle toggle--light">
          <input
            type="checkbox"
            checked={showSource}
            onChange={(e) => setShowSource(e.target.checked)}
          />
          markdown source
        </label>
      </div>

      {scopeEntries.length > 0 && (
        <p className="viewnote">
          Scope:{' '}
          {scopeEntries.map(([k, v]) => (
            <span key={k} className="tag tag--product">
              {k.replace(/_/g, ' ')}: {String(v)}
            </span>
          ))}
        </p>
      )}

      <div className="headline">
        {[
          ['Contracts', headline?.contracts],
          ['Premium at risk', money(headline?.premium_at_risk)],
          ['Sum insured', money(headline?.sum_insured)],
          ['Flagged', headline?.flagged_for_renewal],
          ['Earliest expiry', headline?.earliest_expiry],
        ].map(([label, value]) => (
          <div key={label} className="stat">
            <span className="stat__label">{label}</span>
            <span className="stat__value">{value ?? '—'}</span>
          </div>
        ))}
      </div>

      {showSource ? (
        <pre className="tool__schema tool__schema--light">{markdown}</pre>
      ) : (
        sections.map((section) => (
          <div key={section.heading} className="reportsection">
            <h3>{section.heading}</h3>
            <div className="tablewrap">
              <table className="table">
                <thead>
                  <tr>
                    {section.columns.map((c) => (
                      <th key={c} className={NUMERIC_COLUMNS.has(c) ? 'num' : undefined}>
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {section.rows.map((row, i) => (
                    <tr
                      key={`${section.heading}-${i}`}
                      className={typeof row[0] === 'string' && /^FL-/.test(row[0]) ? 'row' : undefined}
                      onClick={
                        typeof row[0] === 'string' && /^FL-/.test(row[0])
                          ? () => onOpen(row[0])
                          : undefined
                      }
                    >
                      {row.map((cell, j) => (
                        <td
                          key={j}
                          className={
                            NUMERIC_COLUMNS.has(section.columns[j]) ? 'num' : undefined
                          }
                        >
                          {typeof cell === 'number' && j > 0 && cell > 1000
                            ? money(cell)
                            : String(cell)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {section.truncated && (
              <p className="muted">
                Showing {section.rows.length} of {section.total} — the report caps the
                action list.
              </p>
            )}
          </div>
        ))
      )}

      <div className="detail__actions">
        <button className="btn" onClick={onBack}>
          &larr; Portfolio
        </button>
      </div>

      <p className="panel__foot">
        Produced by <code>generate_renewal_report</code>, a server tool. WebMCP's
        only part was <code>show_report</code> — the handoff that put it on screen.
      </p>
    </section>
  );
}
