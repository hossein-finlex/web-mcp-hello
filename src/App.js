import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ContractList from './components/ContractList';
import ContractDetail from './components/ContractDetail';
import NewContractForm from './components/NewContractForm';
import PortfolioSummary from './components/PortfolioSummary';
import BatchResult from './components/BatchResult';
import ReportView from './components/ReportView';
import AssistantChat from './components/AssistantChat';
import ToolInspector from './components/ToolInspector';
import { useWebMcpTools } from './useWebMcpTools';
import { toolResult, toolError, isNativeModelContext } from './webmcp-polyfill';
import { api, ApiError } from './api';
import './App.css';

const PRODUCTS = ['D&O', 'Cyber', 'PI', 'Crime', 'EPLI', 'W&I'];
const STATUSES = ['active', 'expiring', 'expired', 'draft'];
const GROUPABLE = ['product', 'insurer', 'status', 'broker', 'industry'];
const SORTABLE = [
  'end_date',
  'sum_insured',
  'premium',
  'deductible',
  'insured_company',
  'insurer',
  'product',
  'renewal_count',
];

const FILTER_KEYS = [
  'query',
  'product',
  'insurer',
  'broker',
  'status',
  'renewal_pending',
  'expiring_within_days',
  'min_sum_insured',
  'max_premium',
];

const EMPTY_FILTER = Object.fromEntries(FILTER_KEYS.map((k) => [k, null]));
const DEFAULT_SORT = { sort_by: 'end_date', sort_dir: 'asc' };

/** Drop nulls/blanks so the API sees only the criteria that were actually set. */
function clean(object) {
  return Object.fromEntries(
    Object.entries(object ?? {}).filter(
      ([, v]) => v !== null && v !== undefined && v !== ''
    )
  );
}

/** Compact projection for tool results — the full record is get_contract's job. */
function summariseContract(c) {
  return {
    id: c.id,
    policy_number: c.policy_number,
    product: c.product,
    insurer: c.insurer,
    insured_company: c.insured_company,
    status: c.status,
    end_date: c.end_date,
    days_to_expiry: c.days_to_expiry,
    sum_insured: c.sum_insured,
    premium: c.premium,
    renewal_pending: c.renewal_pending,
  };
}

export default function App() {
  /* ------------------------------- state ------------------------------- */
  // What the table shows now. The server decides — filter, sort and limit are
  // all applied in SQL, so there is one source of truth rather than a copy of
  // the filtering logic living in the browser too.
  const [list, setList] = useState({ contracts: [], returned: 0, total: 0 });
  const [bookSize, setBookSize] = useState(0);

  const [filter, setFilter] = useState(EMPTY_FILTER);
  const [sort, setSort] = useState(DEFAULT_SORT);
  const [limit, setLimit] = useState(null);

  const [route, setRoute] = useState({ view: 'portfolio', contractId: null });
  const [selected, setSelected] = useState(null);
  const [summary, setSummary] = useState(null);
  const [draft, setDraft] = useState(null);
  // Artifacts produced by server-side tools, fetched by id for display.
  const [batch, setBatch] = useState(null);
  const [report, setReport] = useState(null);

  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState(null);
  const [agentBusy, setAgentBusy] = useState(false);

  const [flashId, setFlashId] = useState(null);
  const flashTimer = useRef(null);
  const flash = useCallback((id) => {
    setFlashId(id);
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlashId(null), 2000);
  }, []);
  useEffect(() => () => clearTimeout(flashTimer.current), []);

  /* ------------------------------ fetching ----------------------------- */
  const query = useMemo(
    () => ({ ...clean(filter), ...sort, ...(limit ? { limit } : {}) }),
    [filter, sort, limit]
  );

  const refresh = useCallback(
    async (override) => {
      const params = override ?? query;
      const [page, health] = await Promise.all([
        api.searchContracts(params),
        api.health(),
      ]);
      setList(page);
      setBookSize(health.contracts);
      return page;
    },
    [query]
  );

  // Re-run whenever the criteria change — whether a human moved a dropdown or
  // a tool call set them.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [page, health] = await Promise.all([
          api.searchContracts(query),
          api.health(),
        ]);
        if (!alive) return;
        setList(page);
        setBookSize(health.contracts);
      } catch (err) {
        if (alive) setBanner({ kind: 'error', text: err.message });
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [query]);

  // The open contract is fetched by id, so it stays visible even when the
  // current filter would exclude it.
  useEffect(() => {
    if (route.view !== 'contract' || !route.contractId) {
      setSelected(null);
      return undefined;
    }
    let alive = true;
    api
      .getContract(route.contractId)
      .then((c) => alive && setSelected(c))
      .catch(() => alive && setSelected(null));
    return () => {
      alive = false;
    };
  }, [route.view, route.contractId, list]);

  const filterActive = useMemo(
    () => Object.keys(clean(filter)).length > 0,
    [filter]
  );
  const sortActive =
    sort.sort_by !== DEFAULT_SORT.sort_by || sort.sort_dir !== DEFAULT_SORT.sort_dir;

  /* =====================================================================
   *  WebMCP TOOLS
   *
   *  Note what search_contracts and summarise_portfolio have in common: both
   *  push the work into SQL and hand back only the answer. An assistant asking
   *  "which two are largest?" gets two rows, not fifty — the tool surface is
   *  where you control how much context a question costs.
   * ===================================================================== */
  useWebMcpTools([
    {
      name: 'search_contracts',
      description:
        'Search the portfolio and filter, sort and limit the on-screen table to ' +
        'the result. Runs in SQL, so use sort_by + limit for superlatives ' +
        '("largest", "soonest", "most expensive") instead of listing everything ' +
        'and comparing yourself. Returns summaries plus the total number of ' +
        'matches; call get_contract for one full record. Call with no arguments ' +
        'to clear the filter and show the whole book.',
      inputSchema: {
        type: 'object',
        properties: {
          query: {
            type: 'string',
            description:
              'Free text. Every whitespace-separated term must appear in one of: ' +
              'contract id, policy number, insured company, insurer, product, ' +
              'industry, broker, notes.',
          },
          product: { type: 'string', enum: PRODUCTS },
          insurer: { type: 'string', description: 'Substring match, e.g. "Allianz".' },
          broker: { type: 'string', description: 'Substring match.' },
          status: {
            type: 'string',
            enum: STATUSES,
            description:
              'Derived from the term: expired, expiring (ends within 90 days), ' +
              'active, or draft.',
          },
          renewal_pending: {
            type: 'boolean',
            description: 'Only contracts a broker has flagged for renewal.',
          },
          expiring_within_days: {
            type: 'integer',
            description: 'In force today and ending within this many days.',
          },
          min_sum_insured: { type: 'integer', description: 'EUR.' },
          max_premium: { type: 'integer', description: 'EUR.' },
          sort_by: {
            type: 'string',
            enum: SORTABLE,
            description: 'Defaults to end_date (soonest expiry first).',
          },
          sort_dir: { type: 'string', enum: ['asc', 'desc'] },
          limit: {
            type: 'integer',
            description:
              'Return only the first N rows after sorting. Use 1-5 for ' +
              '"the largest" or "the next to expire" — the table shows exactly ' +
              'those rows too.',
          },
        },
      },
      execute: async (args = {}) => {
        const nextFilter = { ...EMPTY_FILTER };
        for (const key of FILTER_KEYS) {
          if (args[key] !== undefined && args[key] !== null && args[key] !== '') {
            nextFilter[key] = args[key];
          }
        }
        const nextSort = {
          sort_by: args.sort_by ?? DEFAULT_SORT.sort_by,
          sort_dir: args.sort_dir ?? (args.sort_by ? 'desc' : DEFAULT_SORT.sort_dir),
        };
        const nextLimit = args.limit ?? null;

        try {
          const page = await refresh({
            ...clean(nextFilter),
            ...nextSort,
            ...(nextLimit ? { limit: nextLimit } : {}),
          });
          setFilter(nextFilter);
          setSort(nextSort);
          setLimit(nextLimit);
          setSummary(null);
          setRoute({ view: 'portfolio', contractId: null });

          return toolResult({
            returned: page.returned,
            total_matching: page.total,
            sort: page.sort,
            contracts: page.contracts.map(summariseContract),
          });
        } catch (err) {
          return toolError(err.message);
        }
      },
    },

    {
      name: 'summarise_portfolio',
      description:
        'Aggregate the book in SQL and show the breakdown on screen. Use this ' +
        'for every "how much / how many / which X has the most" question — ' +
        'totals by product, insurer, status, broker or industry — instead of ' +
        'listing contracts and adding them up. Accepts the same filters as ' +
        'search_contracts, so you can summarise a subset.',
      inputSchema: {
        type: 'object',
        properties: {
          group_by: {
            type: 'string',
            enum: GROUPABLE,
            description: 'Defaults to product.',
          },
          query: { type: 'string' },
          product: { type: 'string', enum: PRODUCTS },
          insurer: { type: 'string' },
          broker: { type: 'string' },
          status: { type: 'string', enum: STATUSES },
          renewal_pending: { type: 'boolean' },
          expiring_within_days: { type: 'integer' },
          min_sum_insured: { type: 'integer' },
          max_premium: { type: 'integer' },
        },
      },
      execute: async (args = {}) => {
        const { group_by: groupBy = 'product', ...rest } = args;
        const scope = {};
        for (const key of FILTER_KEYS) {
          if (rest[key] !== undefined && rest[key] !== null && rest[key] !== '') {
            scope[key] = rest[key];
          }
        }
        try {
          const result = await api.summary({ group_by: groupBy, ...scope });
          setSummary({ ...result, scope });
          setRoute({ view: 'summary', contractId: null });
          return toolResult(result);
        } catch (err) {
          return toolError(err.message);
        }
      },
    },

    {
      name: 'show_batch_result',
      description:
        'Put the result of a bulk run on screen. Call this straight after ' +
        'run_renewal_batch — the work happened on the server, so this is how ' +
        'the user actually gets to see what it did.',
      inputSchema: {
        type: 'object',
        properties: {
          batch_id: { type: 'string', description: 'e.g. "BATCH-0001".' },
        },
        required: ['batch_id'],
      },
      execute: async ({ batch_id: id }) => {
        try {
          const record = await api.getBatch(id);
          setBatch(record);
          setRoute({ view: 'batch', contractId: null });
          // The book changed underneath us if the batch committed.
          if (record.committed) await refresh();
          return toolResult({
            ok: true,
            showing: id,
            committed: record.committed,
            renewed: record.renewed,
            matched: record.matched,
          });
        } catch (err) {
          return toolError(err.message);
        }
      },
    },

    {
      name: 'show_report',
      description:
        'Put a generated report on screen. Call this straight after ' +
        'generate_renewal_report so the user can read what was produced ' +
        'instead of having it recited into the chat.',
      inputSchema: {
        type: 'object',
        properties: {
          report_id: { type: 'string', description: 'e.g. "RPT-0001".' },
        },
        required: ['report_id'],
      },
      execute: async ({ report_id: id }) => {
        try {
          const record = await api.getReport(id);
          setReport(record);
          setRoute({ view: 'report', contractId: null });
          return toolResult({ ok: true, showing: id, title: record.title });
        } catch (err) {
          return toolError(err.message);
        }
      },
    },

    {
      name: 'get_contract',
      description:
        'Read one contract in full, including notes, deductible, broker and ' +
        'renewal history. Does not change the view — use navigate for that.',
      inputSchema: {
        type: 'object',
        properties: {
          contract_id: { type: 'string', description: 'e.g. "FL-0142".' },
        },
        required: ['contract_id'],
      },
      execute: async ({ contract_id: id }) => {
        try {
          return toolResult({ contract: await api.getContract(id) });
        } catch (err) {
          return toolError(
            err instanceof ApiError && err.status === 404
              ? `No contract with id "${id}". Use search_contracts to find valid ids.`
              : err.message
          );
        }
      },
    },

    {
      name: 'navigate',
      description:
        'Switch the view. "portfolio" is the contract table, "contract" opens ' +
        'one contract (needs contract_id), "new" opens the blank new-contract ' +
        'form, "summary" shows the last aggregate breakdown.',
      inputSchema: {
        type: 'object',
        properties: {
          view: {
            type: 'string',
            enum: ['portfolio', 'contract', 'new', 'summary', 'batch', 'report'],
          },
          contract_id: {
            type: 'string',
            description: 'Required when view is "contract".',
          },
        },
        required: ['view'],
      },
      execute: async ({ view, contract_id: id }) => {
        if (view === 'contract') {
          try {
            const contract = await api.getContract(id);
            setRoute({ view: 'contract', contractId: id });
            setSelected(contract);
            flash(id);
            return toolResult({ ok: true, view, contract: summariseContract(contract) });
          } catch (err) {
            return toolError(
              err instanceof ApiError && err.status === 404
                ? `No contract with id "${id}". Use search_contracts to find valid ids.`
                : err.message
            );
          }
        }
        if (view === 'new') {
          setDraft((prev) => prev ?? {});
          setRoute({ view: 'new', contractId: null });
          return toolResult({ ok: true, view });
        }
        if (view === 'summary') {
          if (!summary) {
            return toolError(
              'No summary has been produced yet. Call summarise_portfolio first.'
            );
          }
          setRoute({ view: 'summary', contractId: null });
          return toolResult({ ok: true, view, group_by: summary.group_by });
        }
        if (view === 'batch' || view === 'report') {
          const artifact = view === 'batch' ? batch : report;
          if (!artifact) {
            return toolError(
              `Nothing to show. Run ${
                view === 'batch' ? 'run_renewal_batch' : 'generate_renewal_report'
              } first, then call show_${view === 'batch' ? 'batch_result' : 'report'}.`
            );
          }
          setRoute({ view, contractId: null });
          return toolResult({ ok: true, view, id: artifact.id });
        }
        setRoute({ view: 'portfolio', contractId: null });
        return toolResult({ ok: true, view, visible: list.returned, total: list.total });
      },
    },

    {
      name: 'prefill_new_contract_form',
      description:
        'Open the new-contract form and fill in the fields you have, WITHOUT ' +
        'submitting. The user reviews and clicks Create. Prefer this over ' +
        'create_contract whenever a detail is missing or you inferred a value.',
      inputSchema: {
        type: 'object',
        properties: {
          insured_company: { type: 'string' },
          product: { type: 'string', enum: PRODUCTS },
          insurer: { type: 'string' },
          industry: { type: 'string' },
          sum_insured: { type: 'integer', description: 'EUR.' },
          premium: { type: 'integer', description: 'EUR, annual.' },
          deductible: { type: 'integer', description: 'EUR.' },
          start_date: { type: 'string', description: 'ISO date, e.g. "2026-09-01".' },
          end_date: { type: 'string', description: 'ISO date.' },
          broker: { type: 'string' },
          notes: { type: 'string' },
        },
      },
      execute: async (fields) => {
        const prefilled = clean(fields);
        setDraft(prefilled);
        setRoute({ view: 'new', contractId: null });
        return toolResult({
          ok: true,
          prefilled,
          note: 'The form is filled in but nothing has been created yet. The user must click Create.',
        });
      },
    },

    {
      name: 'create_contract',
      description:
        'Create a contract immediately. Only use this when the user clearly ' +
        'wants it created now and you have every required field; otherwise use ' +
        'prefill_new_contract_form.',
      inputSchema: {
        type: 'object',
        properties: {
          insured_company: { type: 'string' },
          product: { type: 'string', enum: PRODUCTS },
          insurer: { type: 'string' },
          sum_insured: { type: 'integer', description: 'EUR.' },
          premium: { type: 'integer', description: 'EUR, annual.' },
          deductible: { type: 'integer', description: 'EUR.' },
          start_date: { type: 'string', description: 'ISO date.' },
          end_date: { type: 'string', description: 'ISO date, after start_date.' },
          industry: { type: 'string' },
          broker: { type: 'string' },
          notes: { type: 'string' },
          is_draft: {
            type: 'boolean',
            description: 'Create as a draft rather than a live policy.',
          },
        },
        required: [
          'insured_company',
          'product',
          'insurer',
          'sum_insured',
          'premium',
          'deductible',
          'start_date',
          'end_date',
        ],
      },
      execute: async (payload) => {
        try {
          const created = await api.createContract({
            ...payload,
            created_by_assistant: true,
          });
          setFilter(EMPTY_FILTER);
          setSort(DEFAULT_SORT);
          setLimit(null);
          setDraft(null);
          setSelected(created);
          setRoute({ view: 'contract', contractId: created.id });
          flash(created.id);
          return toolResult({ ok: true, contract: created });
        } catch (err) {
          return toolError(err.message);
        }
      },
    },

    {
      name: 'update_contract',
      description:
        'Change fields on an existing contract. Only send the fields you are ' +
        'changing. The row updates on screen in place.',
      inputSchema: {
        type: 'object',
        properties: {
          contract_id: { type: 'string' },
          insured_company: { type: 'string' },
          product: { type: 'string', enum: PRODUCTS },
          insurer: { type: 'string' },
          sum_insured: { type: 'integer' },
          premium: { type: 'integer' },
          deductible: { type: 'integer' },
          start_date: { type: 'string', description: 'ISO date.' },
          end_date: { type: 'string', description: 'ISO date.' },
          industry: { type: 'string' },
          broker: { type: 'string' },
          notes: { type: 'string' },
          renewal_pending: { type: 'boolean' },
          is_draft: { type: 'boolean' },
        },
        required: ['contract_id'],
      },
      execute: async ({ contract_id: id, ...patch }) => {
        try {
          const updated = await api.updateContract(id, patch);
          await refresh();
          setSelected((prev) => (prev?.id === id ? updated : prev));
          flash(id);
          return toolResult({
            ok: true,
            contract: updated,
            changed: Object.keys(patch),
          });
        } catch (err) {
          return toolError(
            err instanceof ApiError && err.status === 404
              ? `No contract with id "${id}".`
              : err.message
          );
        }
      },
    },

    {
      name: 'renew_contract',
      description:
        'Renew a contract: the new term starts the day the old one ends, so ' +
        'cover stays continuous. Leave premium and sum_insured alone unless the ' +
        'user asked to change them. Clears the renewal_pending flag.',
      inputSchema: {
        type: 'object',
        properties: {
          contract_id: { type: 'string' },
          months: {
            type: 'integer',
            description: 'Length of the new term. Defaults to 12.',
          },
          premium: {
            type: 'integer',
            description: 'New annual premium in EUR, if repriced.',
          },
          sum_insured: { type: 'integer', description: 'New limit in EUR, if changed.' },
          notes: { type: 'string' },
        },
        required: ['contract_id'],
      },
      execute: async ({ contract_id: id, months, premium, sum_insured, notes }) => {
        try {
          const before = await api.getContract(id).catch(() => null);
          const renewed = await api.renewContract(id, {
            months: months ?? 12,
            premium,
            sum_insured,
            notes,
          });
          await refresh();
          setSelected(renewed);
          setRoute({ view: 'contract', contractId: id });
          flash(id);
          return toolResult({
            ok: true,
            contract: renewed,
            previous_term: before ? `${before.start_date}..${before.end_date}` : null,
            new_term: `${renewed.start_date}..${renewed.end_date}`,
          });
        } catch (err) {
          return toolError(
            err instanceof ApiError && err.status === 404
              ? `No contract with id "${id}".`
              : err.message
          );
        }
      },
    },
  ]);

  /* ---------------------- human-driven equivalents ---------------------- */
  const openContract = useCallback((id) => setRoute({ view: 'contract', contractId: id }), []);
  const openPortfolio = useCallback(() => setRoute({ view: 'portfolio', contractId: null }), []);
  const openNew = useCallback(() => {
    setDraft({});
    setRoute({ view: 'new', contractId: null });
  }, []);

  const clearFilter = useCallback(() => {
    setFilter(EMPTY_FILTER);
    setSort(DEFAULT_SORT);
    setLimit(null);
  }, []);

  /** Clicking a column header: same state the search tool writes to. */
  const toggleSort = useCallback((column) => {
    if (!SORTABLE.includes(column)) return;
    setSort((prev) =>
      prev.sort_by === column
        ? { sort_by: column, sort_dir: prev.sort_dir === 'asc' ? 'desc' : 'asc' }
        : { sort_by: column, sort_dir: column === 'insured_company' ? 'asc' : 'desc' }
    );
    setLimit(null);
  }, []);

  const showSummary = useCallback(
    async (groupBy) => {
      try {
        const result = await api.summary({ group_by: groupBy, ...clean(filter) });
        setSummary({ ...result, scope: clean(filter) });
        setRoute({ view: 'summary', contractId: null });
      } catch (err) {
        setBanner({ kind: 'error', text: err.message });
      }
    },
    [filter]
  );

  const saveContract = useCallback(
    async (id, patch) => {
      try {
        const updated = await api.updateContract(id, patch);
        await refresh();
        setSelected(updated);
        flash(id);
        setBanner({ kind: 'ok', text: `${id} saved.` });
      } catch (err) {
        setBanner({ kind: 'error', text: err.message });
      }
    },
    [refresh, flash]
  );

  const renewContract = useCallback(
    async (id, payload) => {
      try {
        const renewed = await api.renewContract(id, payload);
        await refresh();
        setSelected(renewed);
        flash(id);
        setBanner({ kind: 'ok', text: `${id} renewed through ${renewed.end_date}.` });
      } catch (err) {
        setBanner({ kind: 'error', text: err.message });
      }
    },
    [refresh, flash]
  );

  const createContract = useCallback(
    async (payload) => {
      try {
        const created = await api.createContract(payload);
        setDraft(null);
        setFilter(EMPTY_FILTER);
        setSort(DEFAULT_SORT);
        setLimit(null);
        setSelected(created);
        setRoute({ view: 'contract', contractId: created.id });
        flash(created.id);
        setBanner({ kind: 'ok', text: `${created.id} created.` });
      } catch (err) {
        setBanner({ kind: 'error', text: err.message });
        throw err;
      }
    },
    [flash]
  );

  const resetDemo = useCallback(async () => {
    try {
      await api.reset();
      setFilter(EMPTY_FILTER);
      setSort(DEFAULT_SORT);
      setLimit(null);
      setSummary(null);
      setDraft(null);
      setBatch(null);
      setReport(null);
      setRoute({ view: 'portfolio', contractId: null });
      await refresh({ ...DEFAULT_SORT });
      setBanner({ kind: 'ok', text: 'Portfolio reset to the seeded book.' });
    } catch (err) {
      setBanner({ kind: 'error', text: err.message });
    }
  }, [refresh]);

  useEffect(() => {
    if (!banner) return undefined;
    const t = setTimeout(() => setBanner(null), 4000);
    return () => clearTimeout(t);
  }, [banner]);

  /* ------------------------------- render ------------------------------ */
  return (
    <div className={`app ${agentBusy ? 'app--agent-driving' : ''}`}>
      <main className="stage">
        <header className="stage__head">
          <div>
            <h1>
              Portfolio <span className="stage__demo">demo data</span>
            </h1>
            <p className="stage__sub">
              Commercial financial lines &mdash; an app an AI assistant can operate.
            </p>
          </div>
          <span className={`badge ${isNativeModelContext() ? 'badge--live' : 'badge--poly'}`}>
            {isNativeModelContext()
              ? 'native navigator.modelContext'
              : 'navigator.modelContext (polyfilled)'}
          </span>
        </header>

        <nav className="crumbs">
          <button
            className={route.view === 'portfolio' ? 'crumb crumb--on' : 'crumb'}
            onClick={openPortfolio}
          >
            Portfolio
          </button>
          <span className="crumb__sep">/</span>
          {route.view === 'contract' && selected ? (
            <span className="crumb crumb--on">
              {selected.id} &middot; {selected.insured_company}
            </span>
          ) : route.view === 'new' ? (
            <span className="crumb crumb--on">New contract</span>
          ) : route.view === 'summary' ? (
            <span className="crumb crumb--on">Summary by {summary?.group_by}</span>
          ) : route.view === 'batch' ? (
            <span className="crumb crumb--on">Batch {batch?.id}</span>
          ) : route.view === 'report' ? (
            <span className="crumb crumb--on">{report?.title ?? 'Report'}</span>
          ) : (
            <span className="crumb crumb--off">no contract open</span>
          )}
          <span className="crumbs__spacer" />
          <button className="btn btn--sm" onClick={openNew}>
            + New contract
          </button>
        </nav>

        {banner && <div className={`banner banner--${banner.kind}`}>{banner.text}</div>}

        {loading ? (
          <section className="panel">
            <p className="empty">Loading the portfolio&hellip;</p>
          </section>
        ) : route.view === 'contract' ? (
          <ContractDetail
            contract={selected}
            flashId={flashId}
            onBack={openPortfolio}
            onSave={saveContract}
            onRenew={renewContract}
            products={PRODUCTS}
          />
        ) : route.view === 'new' ? (
          <NewContractForm
            initial={draft}
            products={PRODUCTS}
            onCancel={openPortfolio}
            onCreate={createContract}
          />
        ) : route.view === 'batch' ? (
          <BatchResult batch={batch} onBack={openPortfolio} onOpen={openContract} />
        ) : route.view === 'report' ? (
          <ReportView report={report} onBack={openPortfolio} onOpen={openContract} />
        ) : route.view === 'summary' ? (
          <PortfolioSummary
            summary={summary}
            groupable={GROUPABLE}
            onGroupBy={showSummary}
            onBack={openPortfolio}
          />
        ) : (
          <ContractList
            page={list}
            bookSize={bookSize}
            filter={filter}
            filterActive={filterActive}
            sort={sort}
            sortActive={sortActive}
            limit={limit}
            onFilterChange={setFilter}
            onClearFilter={clearFilter}
            onToggleSort={toggleSort}
            onClearLimit={() => setLimit(null)}
            onSummarise={showSummary}
            flashId={flashId}
            onOpen={openContract}
            products={PRODUCTS}
            statuses={STATUSES}
          />
        )}
      </main>

      <aside className="sidebar">
        <AssistantChat onBusyChange={setAgentBusy} onResetPortfolio={resetDemo} />
        <ToolInspector />
      </aside>
    </div>
  );
}
