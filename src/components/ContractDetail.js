import { useEffect, useState } from 'react';

const money = (n) =>
  n == null ? '—' : `€${Number(n).toLocaleString('de-DE', { maximumFractionDigits: 0 })}`;

const EDITABLE = [
  ['insured_company', 'Insured company', 'text'],
  ['industry', 'Industry', 'text'],
  ['insurer', 'Insurer', 'text'],
  ['product', 'Product', 'select'],
  ['sum_insured', 'Sum insured (EUR)', 'number'],
  ['premium', 'Premium (EUR)', 'number'],
  ['deductible', 'Deductible (EUR)', 'number'],
  ['start_date', 'Start date', 'date'],
  ['end_date', 'End date', 'date'],
  ['broker', 'Broker', 'text'],
];

/**
 * ContractDetail.js — the editable single-contract view.
 *
 * Humans edit here; the assistant edits through update_contract / renew_contract.
 * Both paths hit the same backend and the same React state, so there is no
 * separate "agent mode" and no way for the two to disagree.
 */
export default function ContractDetail({ contract, flashId, onBack, onSave, onRenew, products }) {
  const [form, setForm] = useState(contract ?? {});
  const [editing, setEditing] = useState(false);
  const [renewing, setRenewing] = useState(false);
  const [renewal, setRenewal] = useState({ months: 12, premium: '', sum_insured: '' });

  // A tool call can change this contract while it is open. Re-sync unless the
  // user is mid-edit, in which case their typing wins.
  useEffect(() => {
    if (!editing && contract) setForm(contract);
  }, [contract, editing]);

  if (!contract) {
    return (
      <section className="panel">
        <div className="panel__head">
          <h2>Contract not found</h2>
        </div>
        <p className="empty">That contract no longer exists.</p>
        <button className="btn" onClick={onBack}>
          Back to the portfolio
        </button>
      </section>
    );
  }

  const dirty = EDITABLE.some(([key]) => String(form[key] ?? '') !== String(contract[key] ?? ''));

  const submit = async (e) => {
    e.preventDefault();
    const patch = {};
    for (const [key, , type] of EDITABLE) {
      if (String(form[key] ?? '') === String(contract[key] ?? '')) continue;
      patch[key] = type === 'number' ? Number(form[key]) : form[key];
    }
    if (Object.keys(patch).length) await onSave(contract.id, patch);
    setEditing(false);
  };

  const submitRenewal = async (e) => {
    e.preventDefault();
    await onRenew(contract.id, {
      months: Number(renewal.months) || 12,
      premium: renewal.premium === '' ? undefined : Number(renewal.premium),
      sum_insured: renewal.sum_insured === '' ? undefined : Number(renewal.sum_insured),
    });
    setRenewing(false);
    setRenewal({ months: 12, premium: '', sum_insured: '' });
  };

  return (
    <section className={`panel ${flashId === contract.id ? 'panel--flash' : ''}`}>
      <div className="panel__head">
        <div>
          <h2>{contract.insured_company}</h2>
          <p className="detail__meta">
            {contract.id} &middot; <code>{contract.policy_number}</code> &middot;{' '}
            {contract.product} &middot; {contract.insurer}
          </p>
        </div>
        <div className="panel__badges">
          <span className={`pill pill--${contract.status}`}>{contract.status}</span>
          {contract.renewal_pending && <span className="pill pill--renewal">renewal pending</span>}
          {contract.created_by_assistant && <span className="tag tag--ai">via assistant</span>}
        </div>
      </div>

      {editing ? (
        <form className="form" onSubmit={submit}>
          {EDITABLE.map(([key, label, type]) => (
            <label key={key} className="field">
              <span className="field__label">{label}</span>
              {type === 'select' ? (
                <select
                  value={form[key] ?? ''}
                  onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                >
                  {products.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type={type}
                  value={form[key] ?? ''}
                  onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                />
              )}
            </label>
          ))}
          <label className="field field--wide">
            <span className="field__label">Notes</span>
            <textarea
              rows={3}
              value={form.notes ?? ''}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
            />
          </label>
          <label className="field field--check">
            <input
              type="checkbox"
              checked={!!form.renewal_pending}
              onChange={(e) => setForm({ ...form, renewal_pending: e.target.checked })}
            />
            <span>Flagged for renewal</span>
          </label>

          <div className="form__actions">
            <button className="btn btn--primary" type="submit" disabled={!dirty}>
              Save changes
            </button>
            <button
              className="btn"
              type="button"
              onClick={() => {
                setForm(contract);
                setEditing(false);
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          <dl className="detail">
            <dt>Term</dt>
            <dd>
              {contract.start_date} &rarr; {contract.end_date}{' '}
              <span className="muted">
                (
                {contract.days_to_expiry < 0
                  ? `expired ${Math.abs(contract.days_to_expiry)} days ago`
                  : `${contract.days_to_expiry} days remaining`}
                )
              </span>
            </dd>

            <dt>Sum insured</dt>
            <dd>{money(contract.sum_insured)}</dd>

            <dt>Premium</dt>
            <dd>{money(contract.premium)} per annum</dd>

            <dt>Deductible</dt>
            <dd>{money(contract.deductible)}</dd>

            <dt>Industry</dt>
            <dd>{contract.industry}</dd>

            <dt>Broker</dt>
            <dd>{contract.broker}</dd>

            <dt>Renewals</dt>
            <dd>{contract.renewal_count}</dd>

            <dt>Notes</dt>
            <dd>{contract.notes || <span className="muted">None.</span>}</dd>
          </dl>

          {renewing ? (
            <form className="renewbox" onSubmit={submitRenewal}>
              <strong>Renew {contract.id}</strong>
              <p className="muted">
                The new term starts {contract.end_date}, so cover stays continuous.
              </p>
              <div className="renewbox__row">
                <label className="field">
                  <span className="field__label">Months</span>
                  <input
                    type="number"
                    min="1"
                    max="60"
                    value={renewal.months}
                    onChange={(e) => setRenewal({ ...renewal, months: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span className="field__label">New premium (optional)</span>
                  <input
                    type="number"
                    placeholder={String(contract.premium)}
                    value={renewal.premium}
                    onChange={(e) => setRenewal({ ...renewal, premium: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span className="field__label">New sum insured (optional)</span>
                  <input
                    type="number"
                    placeholder={String(contract.sum_insured)}
                    value={renewal.sum_insured}
                    onChange={(e) => setRenewal({ ...renewal, sum_insured: e.target.value })}
                  />
                </label>
              </div>
              <div className="form__actions">
                <button className="btn btn--primary" type="submit">
                  Confirm renewal
                </button>
                <button className="btn" type="button" onClick={() => setRenewing(false)}>
                  Cancel
                </button>
              </div>
            </form>
          ) : (
            <div className="detail__actions">
              <button className="btn" onClick={onBack}>
                &larr; Portfolio
              </button>
              <button className="btn" onClick={() => setEditing(true)}>
                Edit
              </button>
              <button
                className="btn btn--primary"
                onClick={() => setRenewing(true)}
                disabled={contract.is_draft}
                title={contract.is_draft ? 'Issue the draft before renewing it' : undefined}
              >
                Renew
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
