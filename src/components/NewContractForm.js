import { useEffect, useMemo, useState } from 'react';

/**
 * NewContractForm.js
 *
 * Two ways in:
 *   - a human clicks "+ New contract" and types everything;
 *   - the assistant calls prefill_new_contract_form, which fills these fields
 *     but deliberately does NOT submit.
 *
 * That second path is the human-in-the-loop case worth showing: the agent does
 * the tedious part, the person keeps the decision. Prefilled fields are marked.
 */

const BLANK = {
  insured_company: '',
  industry: '',
  product: 'D&O',
  insurer: '',
  sum_insured: '',
  premium: '',
  deductible: '',
  start_date: '',
  end_date: '',
  broker: '',
  notes: '',
  is_draft: false,
};

const REQUIRED = [
  'insured_company',
  'product',
  'insurer',
  'sum_insured',
  'premium',
  'deductible',
  'start_date',
  'end_date',
];

const LABELS = {
  insured_company: 'Insured company',
  industry: 'Industry',
  product: 'Product',
  insurer: 'Insurer',
  sum_insured: 'Sum insured (EUR)',
  premium: 'Premium (EUR)',
  deductible: 'Deductible (EUR)',
  start_date: 'Start date',
  end_date: 'End date',
  broker: 'Broker',
};

export default function NewContractForm({ initial, products, onCancel, onCreate }) {
  const [form, setForm] = useState({ ...BLANK, ...(initial ?? {}) });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  // Which fields the assistant filled, so they can be visibly marked.
  const prefilled = useMemo(
    () =>
      new Set(
        Object.entries(initial ?? {})
          .filter(([, v]) => v !== '' && v != null)
          .map(([k]) => k)
      ),
    [initial]
  );

  useEffect(() => {
    setForm({ ...BLANK, ...(initial ?? {}) });
  }, [initial]);

  const missing = REQUIRED.filter((k) => form[k] === '' || form[k] == null);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await onCreate({
        ...form,
        sum_insured: Number(form.sum_insured),
        premium: Number(form.premium),
        deductible: Number(form.deductible),
        industry: form.industry || 'Unspecified',
        broker: form.broker || 'House account',
        created_by_assistant: prefilled.size > 0,
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="panel">
      <div className="panel__head">
        <h2>New contract</h2>
        {prefilled.size > 0 && (
          <span className="tag tag--ai">{prefilled.size} fields prefilled by the assistant</span>
        )}
      </div>

      {prefilled.size > 0 && (
        <p className="prefill-note">
          The assistant filled this in but did not create anything. Review the values and click
          Create, or edit them first.
        </p>
      )}

      <form className="form" onSubmit={submit}>
        {Object.keys(LABELS).map((key) => (
          <label key={key} className={`field ${prefilled.has(key) ? 'field--prefilled' : ''}`}>
            <span className="field__label">
              {LABELS[key]}
              {REQUIRED.includes(key) && <span className="req">*</span>}
            </span>
            {key === 'product' ? (
              <select
                value={form.product}
                onChange={(e) => setForm({ ...form, product: e.target.value })}
              >
                {products.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type={
                  key.endsWith('_date')
                    ? 'date'
                    : ['sum_insured', 'premium', 'deductible'].includes(key)
                    ? 'number'
                    : 'text'
                }
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
            checked={!!form.is_draft}
            onChange={(e) => setForm({ ...form, is_draft: e.target.checked })}
          />
          <span>Save as draft rather than a live policy</span>
        </label>

        {error && <p className="formerror">{error}</p>}

        <div className="form__actions">
          <button className="btn btn--primary" type="submit" disabled={submitting || missing.length > 0}>
            {submitting ? 'Creating…' : 'Create contract'}
          </button>
          <button className="btn" type="button" onClick={onCancel}>
            Cancel
          </button>
          {missing.length > 0 && (
            <span className="muted">
              Still needed: {missing.map((k) => LABELS[k] ?? k).join(', ')}
            </span>
          )}
        </div>
      </form>
    </section>
  );
}
