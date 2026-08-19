/**
 * REST client for the Python backend.
 *
 * The backend is the source of truth for contract data. The browser mirrors it
 * in React state; every mutation — human click or assistant tool call — goes
 * through these functions.
 *
 * Field names are snake_case end to end (API, React state, and WebMCP tool
 * schemas alike). One naming convention everywhere means no translation layer
 * and nowhere for a mapping bug to hide.
 */

const BASE = process.env.REACT_APP_API_BASE || 'http://localhost:8000';

export const AGENT_WS_URL = `${BASE.replace(/^http/, 'ws')}/ws/agent`;

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
  } catch (cause) {
    throw new ApiError(
      `Cannot reach the backend at ${BASE}. Is it running? (cd backend && uvicorn app.main:app --port 8000)`,
      0
    );
  }

  if (response.status === 204) return null;

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    throw new ApiError(describeError(body, response.status), response.status);
  }
  return body;
}

/** FastAPI returns either {detail: "msg"} or {detail: [validation errors]}. */
function describeError(body, status) {
  const detail = body?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => {
        const field = (e.loc ?? []).filter((p) => p !== 'body').join('.');
        return field ? `${field}: ${e.msg}` : e.msg;
      })
      .join('; ');
  }
  return `Request failed with status ${status}`;
}

/** Build a query string, dropping empty values so the URL stays readable. */
function qs(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const out = search.toString();
  return out ? `?${out}` : '';
}

export const api = {
  health: () => request('/api/health'),

  listContracts: () => request('/api/contracts').then((r) => r.contracts),

  serverTools: () => request('/api/tools').then((r) => r.server_tools),

  /**
   * Filter, sort and limit server-side. Returns { contracts, returned, total },
   * where `total` counts matches before `limit` — so "top 3 of 14" is sayable.
   */
  searchContracts: (params) => request(`/api/contracts/search${qs(params)}`),

  /** Aggregate totals per group, computed in SQL. */
  summary: (params) => request(`/api/summary${qs(params)}`),

  getContract: (id) => request(`/api/contracts/${encodeURIComponent(id)}`),

  createContract: (payload) =>
    request('/api/contracts', { method: 'POST', body: JSON.stringify(payload) }),

  updateContract: (id, patch) =>
    request(`/api/contracts/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  renewContract: (id, payload) =>
    request(`/api/contracts/${encodeURIComponent(id)}/renew`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  getBatch: (id) => request(`/api/batches/${encodeURIComponent(id)}`),

  getReport: (id) => request(`/api/reports/${encodeURIComponent(id)}`),

  reset: () => request('/api/reset', { method: 'POST' }),
};
