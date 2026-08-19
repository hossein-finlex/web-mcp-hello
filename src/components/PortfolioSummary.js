const money = (n) =>
  n == null ? '—' : `€${Number(n).toLocaleString('de-DE', { maximumFractionDigits: 0 })}`;

/**
 * PortfolioSummary.js — the aggregate view.
 *
 * This exists so the assistant has somewhere to *show* an answer it computed
 * without loading the book. `summarise_portfolio` returns a handful of numbers
 * from SQL and lands the user here, rather than reciting fifty rows into chat.
 */
export default function PortfolioSummary({ summary, groupable, onGroupBy, onBack }) {
  if (!summary) {
    return (
      <section className="panel">
        <div className="panel__head">
          <h2>No summary yet</h2>
        </div>
        <p className="empty">Ask the assistant for a breakdown, or pick one below.</p>
        <div className="detail__actions">
          {groupable.map((g) => (
            <button key={g} className="btn btn--sm" onClick={() => onGroupBy(g)}>
              by {g}
            </button>
          ))}
        </div>
      </section>
    );
  }

  const { group_by: groupBy, groups, totals, scope } = summary;
  const max = Math.max(1, ...groups.map((g) => g.total_premium));
  const scopeEntries = Object.entries(scope ?? {});

  return (
    <section className="panel">
      <div className="panel__head">
        <div>
          <h2>Premium by {groupBy}</h2>
          <p className="detail__meta">
            {totals.contracts} contracts &middot; {money(totals.total_premium)} premium &middot;{' '}
            {money(totals.total_sum_insured)} limits
            {totals.renewal_pending > 0 && ` · ${totals.renewal_pending} flagged for renewal`}
          </p>
        </div>
        <span className="counter">computed in SQL</span>
      </div>

      {scopeEntries.length > 0 && (
        <p className="viewnote">
          Scoped to{' '}
          {scopeEntries.map(([k, v]) => (
            <span key={k} className="tag tag--product">
              {k.replace(/_/g, ' ')}: {String(v)}
            </span>
          ))}
        </p>
      )}

      <div className="tablewrap">
        <table className="table">
          <thead>
            <tr>
              <th>{groupBy}</th>
              <th className="num">Contracts</th>
              <th className="num">Premium</th>
              <th>Share of premium</th>
              <th className="num">Sum insured</th>
              <th className="num">Renewal</th>
              <th>Earliest expiry</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.key}>
                <td>
                  <span className="row__company">{g.key}</span>
                </td>
                <td className="num">{g.contracts}</td>
                <td className="num">{money(g.total_premium)}</td>
                <td>
                  <span className="bar">
                    <span
                      className="bar__fill"
                      style={{ width: `${Math.round((g.total_premium / max) * 100)}%` }}
                    />
                  </span>
                </td>
                <td className="num">{money(g.total_sum_insured)}</td>
                <td className="num">{g.renewal_pending || '—'}</td>
                <td>{g.earliest_expiry ?? '—'}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td>Total</td>
              <td className="num">{totals.contracts}</td>
              <td className="num">{money(totals.total_premium)}</td>
              <td />
              <td className="num">{money(totals.total_sum_insured)}</td>
              <td className="num">{totals.renewal_pending || '—'}</td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>

      <div className="detail__actions">
        <button className="btn" onClick={onBack}>
          &larr; Portfolio
        </button>
        {groupable
          .filter((g) => g !== groupBy)
          .map((g) => (
            <button key={g} className="btn btn--sm" onClick={() => onGroupBy(g)}>
              by {g}
            </button>
          ))}
      </div>
    </section>
  );
}
