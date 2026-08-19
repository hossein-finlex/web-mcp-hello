const money = (n) =>
  n == null ? '—' : `€${Number(n).toLocaleString('de-DE', { maximumFractionDigits: 0 })}`;

/**
 * BatchResult.js — where a server-side bulk run lands.
 *
 * The renewal itself happened on the server, in one transaction, without the
 * browser involved. This view exists so the outcome still arrives in front of
 * the user: the assistant calls show_batch_result and the page opens here.
 *
 * A dry run renders as a plan with nothing applied; a committed run renders as
 * an audit record of what changed.
 */
export default function BatchResult({ batch, onBack, onOpen }) {
  if (!batch) {
    return (
      <section className="panel">
        <div className="panel__head">
          <h2>No batch to show</h2>
        </div>
        <p className="empty">Ask the assistant to run a bulk renewal.</p>
        <button className="btn" onClick={onBack}>
          Back to the portfolio
        </button>
      </section>
    );
  }

  const { committed, matched, renewed, failed = [], items = [], scope, months } = batch;
  const scopeEntries = Object.entries(scope ?? {});

  return (
    <section className={`panel ${committed ? 'panel--committed' : 'panel--dryrun'}`}>
      <div className="panel__head">
        <div>
          <h2>
            {committed ? 'Bulk renewal applied' : 'Bulk renewal — preview'}{' '}
            <span className="row__policy">{batch.id}</span>
          </h2>
          <p className="detail__meta">
            Ran on the server in one transaction &middot; {months}-month terms &middot;{' '}
            {new Date(batch.created_at).toLocaleString()}
          </p>
        </div>
        <span className={`pill ${committed ? 'pill--active' : 'pill--expiring'}`}>
          {committed ? 'committed' : 'dry run'}
        </span>
      </div>

      {!committed && (
        <p className="viewnote">
          <strong>Nothing has changed yet.</strong> This is what would happen. The
          assistant has to be told to go ahead before anything is written.
        </p>
      )}

      <dl className="detail">
        <dt>Scope</dt>
        <dd>
          {scopeEntries.length === 0
            ? 'whole book'
            : scopeEntries.map(([k, v]) => (
                <span key={k} className="tag tag--product">
                  {k.replace(/_/g, ' ')}: {String(v)}
                </span>
              ))}
        </dd>
        <dt>Matched</dt>
        <dd>{matched} contracts</dd>
        <dt>{committed ? 'Renewed' : 'Would renew'}</dt>
        <dd>{committed ? renewed : matched}</dd>
        <dt>Premium affected</dt>
        <dd>{money(batch.premium_affected)}</dd>
        {failed.length > 0 && (
          <>
            <dt>Failed</dt>
            <dd className="failed">
              {failed.map((f) => (
                <div key={f.id}>
                  {f.id}: {f.error}
                </div>
              ))}
            </dd>
          </>
        )}
      </dl>

      {items.length > 0 && (
        <div className="tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>Contract</th>
                <th>Insured</th>
                <th>Product</th>
                <th>Insurer</th>
                <th className="num">Premium</th>
                <th>{committed ? 'New expiry' : 'Current expiry'}</th>
                {!committed && <th>Would become</th>}
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.id} className="row" onClick={() => onOpen(i.id)}>
                  <td>
                    <span className="row__id">{i.id}</span>
                  </td>
                  <td>
                    <span className="row__company">{i.insured_company}</span>
                  </td>
                  <td>
                    <span className="tag tag--product">{i.product}</span>
                  </td>
                  <td>{i.insurer}</td>
                  <td className="num">{money(i.premium)}</td>
                  <td>{committed ? i.new_end : i.current_end}</td>
                  {!committed && <td>{i.new_end}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="detail__actions">
        <button className="btn" onClick={onBack}>
          &larr; Portfolio
        </button>
      </div>

      <p className="panel__foot">
        This ran through <code>run_renewal_batch</code> — a server tool, not a
        WebMCP one. Renewing {matched} contracts by driving the UI would have been{' '}
        {matched} separate round-trips through the model, any of which could have
        failed halfway.
      </p>
    </section>
  );
}
