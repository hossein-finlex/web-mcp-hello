const money = (n) =>
  n == null ? '—' : `€${Number(n).toLocaleString('de-DE', { maximumFractionDigits: 0 })}`;

const COLUMNS = [
  { key: null, label: 'Contract' },
  { key: 'insured_company', label: 'Insured' },
  { key: 'product', label: 'Product' },
  { key: 'insurer', label: 'Insurer' },
  { key: 'sum_insured', label: 'Sum insured', num: true },
  { key: 'premium', label: 'Premium', num: true },
  { key: 'end_date', label: 'Term ends' },
  { key: null, label: 'Status' },
];

/**
 * ContractList.js — the portfolio table, its filter bar and its sort headers.
 *
 * Filter, sort and limit are ordinary UI state that the server applies in SQL.
 * `search_contracts` writes to exactly that state, which is why an assistant
 * search visibly narrows and reorders the table instead of only answering in
 * chat — and why the assistant never needs to page the whole book into context
 * to answer "which is largest".
 */
export default function ContractList({
  page,
  bookSize,
  filter,
  filterActive,
  sort,
  sortActive,
  limit,
  onFilterChange,
  onClearFilter,
  onToggleSort,
  onClearLimit,
  onSummarise,
  flashId,
  onOpen,
  products,
  statuses,
}) {
  const { contracts, returned, total } = page;
  const set = (key, value) => onFilterChange({ ...filter, [key]: value === '' ? null : value });

  const shown = contracts.reduce(
    (acc, c) => ({ premium: acc.premium + c.premium, sum: acc.sum + c.sum_insured }),
    { premium: 0, sum: 0 }
  );

  const arrow = (key) =>
    sort.sort_by === key ? (sort.sort_dir === 'asc' ? ' ↑' : ' ↓') : '';

  return (
    <section className="panel">
      <div className="panel__head">
        <h2>Contracts</h2>
        <span className="counter">
          {limit
            ? `top ${returned} of ${total} matching`
            : total === bookSize
            ? `${bookSize} contracts`
            : `${total} of ${bookSize} shown`}
        </span>
      </div>

      <div className={`filters ${filterActive || sortActive || limit ? 'filters--active' : ''}`}>
        <input
          className="filters__query"
          placeholder="Search company, insurer, policy number, notes…"
          value={filter.query ?? ''}
          onChange={(e) => set('query', e.target.value)}
        />
        <select value={filter.product ?? ''} onChange={(e) => set('product', e.target.value)}>
          <option value="">All products</option>
          {products.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <select value={filter.status ?? ''} onChange={(e) => set('status', e.target.value)}>
          <option value="">Any status</option>
          {statuses.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <label className="filters__check">
          <input
            type="checkbox"
            checked={filter.renewal_pending === true}
            onChange={(e) => set('renewal_pending', e.target.checked ? true : null)}
          />
          renewal pending
        </label>
        {(filterActive || sortActive || limit) && (
          <button className="btn btn--sm" onClick={onClearFilter}>
            Clear
          </button>
        )}
      </div>

      {(limit || sortActive) && (
        <p className="viewnote">
          {limit && (
            <>
              Showing the <strong>top {returned}</strong> of {total} matches
            </>
          )}
          {limit && sortActive && ' '}
          {sortActive && (
            <>
              sorted by <strong>{sort.sort_by.replace(/_/g, ' ')}</strong> (
              {sort.sort_dir})
            </>
          )}
          {limit && (
            <button className="linkbtn linkbtn--dark" onClick={onClearLimit}>
              show all matches
            </button>
          )}
        </p>
      )}

      {contracts.length === 0 ? (
        <p className="empty">
          No contracts match the current filter.{' '}
          {filterActive && (
            <button className="linkbtn linkbtn--dark" onClick={onClearFilter}>
              Clear it
            </button>
          )}
        </p>
      ) : (
        <div className="tablewrap">
          <table className="table">
            <thead>
              <tr>
                {COLUMNS.map(({ key, label, num }) => (
                  <th key={label} className={num ? 'num' : undefined}>
                    {key ? (
                      <button
                        className={`sorth ${sort.sort_by === key ? 'sorth--on' : ''}`}
                        onClick={() => onToggleSort(key)}
                        title={`Sort by ${label.toLowerCase()}`}
                      >
                        {label}
                        {arrow(key)}
                      </button>
                    ) : (
                      label
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {contracts.map((c) => (
                <tr
                  key={c.id}
                  className={`row ${flashId === c.id ? 'row--flash' : ''}`}
                  onClick={() => onOpen(c.id)}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      onOpen(c.id);
                    }
                  }}
                >
                  <td>
                    <span className="row__id">{c.id}</span>
                    <span className="row__policy">{c.policy_number}</span>
                  </td>
                  <td>
                    <span className="row__company">{c.insured_company}</span>
                    <span className="row__industry">{c.industry}</span>
                    {c.created_by_assistant && <span className="tag tag--ai">via assistant</span>}
                  </td>
                  <td>
                    <span className="tag tag--product">{c.product}</span>
                  </td>
                  <td>{c.insurer}</td>
                  <td className="num">{money(c.sum_insured)}</td>
                  <td className="num">{money(c.premium)}</td>
                  <td>
                    {c.end_date}
                    <span className="row__days">
                      {c.days_to_expiry < 0
                        ? `${Math.abs(c.days_to_expiry)}d ago`
                        : `in ${c.days_to_expiry}d`}
                    </span>
                  </td>
                  <td>
                    <span className={`pill pill--${c.status}`}>{c.status}</span>
                    {c.renewal_pending && <span className="pill pill--renewal">renewal</span>}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td colSpan={4}>Shown ({returned})</td>
                <td className="num">{money(shown.sum)}</td>
                <td className="num">{money(shown.premium)}</td>
                <td colSpan={2} />
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      <div className="panel__foot panel__foot--row">
        <span>
          Sorting, filtering and totals run in SQL — <code>search_contracts</code> and{' '}
          <code>summarise_portfolio</code> drive the same controls the buttons do.
        </span>
        <span className="groupby">
          Summarise by{' '}
          {['product', 'insurer', 'status'].map((g) => (
            <button key={g} className="linkbtn linkbtn--dark" onClick={() => onSummarise(g)}>
              {g}
            </button>
          ))}
        </span>
      </div>
    </section>
  );
}
