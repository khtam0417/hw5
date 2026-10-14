// Calls to the FastAPI backend (backend/main.py). The dashboard calls it directly, at http://localhost:8000 unless
// VITE_API_URL says otherwise; the backend's CORS settings allow the dashboard's origin (http://localhost:5175).

export const API_BASE = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/+$/, '')
export const WORKING_DATABASE = 'data/campus_customs_new.db'
export const EVENTS_PAGE = 500

export type AgentName = 'Boss' | 'Inventory' | 'Accounting' | 'Facilities' | 'Customer Service'

export type Health = {
  status: string
  database: string // the working copy, or "isolated copy …" in the sandbox and the checks
  date_today: string // the shop's current date (desk.date_today), YYYY-MM-DD
  agents?: { model: string; simulated: boolean }
  run_label?: string | null // set with CAMPUS_RUN_LABEL to mark a session, e.g. a full run
}

export type Ticket = {
  id: number
  type: string
  requester: string
  subject: string
  status: string
  resolved: boolean // the backend's own answer: status is "resolved"
  active_run_id: string | null
  sku?: string | null
  size?: string | null
  qty?: number | null
  invoice_id?: number | null
  lease_id?: number | null
  notes?: string | null
  created_at?: string | null
}

export type Usage = {
  steps: number
  tool_calls: number
  total_tokens: number
  steps_left?: number
  tool_calls_left?: number
  tokens_left?: number
}

export type AgentReport = {
  summary: string
  facts?: string[]
  actions_taken?: string[]
  needs_human?: string[]
}

export type BackendRun = {
  run_id: string
  ticket_id: number
  status: 'running' | 'finished' | 'stopped' | 'failed'
  started: string
  ended?: string
  database: string
  stop_reason?: string
  report?: AgentReport | null
  usage?: Usage
}

export type StartedRun = { run_id: string; ticket_id: number; status: 'running'; status_url: string; events_url: string }

// One record of the append-only audit trail (output/audit_trail.json), with its position in the file.
export type AuditEvent = {
  index: number
  time: string
  action: string // run_started, tool, delegation_started, delegation, final_report, run_finished, run_failed, …
  run?: string
  ticket?: number | null
  agent?: string
  depth?: number
  chain?: string
  step?: number
  tool?: string
  input?: unknown
  result?: unknown
  outcome?: 'ok' | 'refused' | 'error'
  error?: string
  usage?: Usage
  database?: string
  model?: string
  trigger?: string
}

export type EventsPage = { events: AuditEvent[]; next_after: number; total_records: number }

export type ApprovalRequest = {
  id: number
  ticket_id: number | null
  kind: string // "invoice" (a vendor invoice, such as a purchase) or "rent"
  ref_id: number
  amount: number
  account: string
  due_date: string
  reason: string
  requested_by: string
  requested_at: string
  status: string // pending, paid or rejected
  decided_by: string | null
  decided_at: string | null
  decision_note: string | null
  payment_id: number | null
  paid_by: string | null
  paid_at: string | null
}

export type Cash = { account: string; balance: number; as_of: string; date_today: string; waiting_for_approval_total: number }

export type ApproveResult = {
  ok: boolean
  status: 'paid'
  approval_id: number
  ticket_id: number | null
  payment_id: number
  amount: number
  account: string
  approved_by: string
  paid_at: string
  new_balance: number
  updated: string
}

export class ApiError extends Error {
  readonly status: number // 0 when the backend couldn't be reached
  readonly detail: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

// FastAPI answers {"detail": "…"}, {"detail": {"message": "…", …}} or, for invalid input, {"detail": [{"msg": "…"}]}.
function messageOf(body: unknown, status: number): string {
  const detail = body && typeof body === 'object' && 'detail' in body ? (body as { detail: unknown }).detail : body
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    return detail.map((item) => (item && typeof item === 'object' && 'msg' in item ? String(item.msg) : String(item))).join('; ')
  }
  if (detail && typeof detail === 'object' && 'message' in detail) return String(detail.message)
  return `The backend answered with HTTP ${status}.`
}

// The run already in progress, from a 409 answer to starting a second run on the same ticket.
export function conflictRunId(detail: unknown): string | null {
  const inner = detail && typeof detail === 'object' && 'detail' in detail ? (detail as { detail: unknown }).detail : null
  return inner && typeof inner === 'object' && 'run_id' in inner && typeof inner.run_id === 'string' ? inner.run_id : null
}

async function call<T>(path: string, options: { method?: 'GET' | 'POST'; body?: unknown; timeoutMs?: number } = {}): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), options.timeoutMs ?? 20_000)
  let status = 0
  let text = ''
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method: options.method ?? 'GET',
      headers: options.body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    })
    status = response.status
    text = await response.text()
  } catch (error) {
    const timedOut = error instanceof DOMException && error.name === 'AbortError'
    throw new ApiError(0, timedOut ? `The backend at ${API_BASE} didn't answer in time.` : `Can't reach the backend at ${API_BASE}.`)
  } finally {
    window.clearTimeout(timer)
  }
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }
  if (status < 200 || status >= 300) throw new ApiError(status, messageOf(body, status), body)
  return body as T
}

export const api = {
  health: () => call<Health>('/api/health'),
  tickets: () => call<{ date_today: string; tickets: Ticket[] }>('/api/tickets'),
  // An optional note goes to the Boss with the run, for example a decision only a person can make.
  startRun: (ticketId: number, note?: string) =>
    call<StartedRun>(`/api/tickets/${ticketId}/run`, { method: 'POST', body: note ? { note } : {} }),
  run: (runId: string) => call<BackendRun>(`/api/runs/${encodeURIComponent(runId)}`),
  // Events after the given index, oldest first; with no index, the most recent page.
  events: (after: number | null) =>
    call<EventsPage>(`/api/events?limit=${EVENTS_PAGE}${after === null ? '' : `&after=${after}`}`),
  approvals: () => call<{ status: string; approval_requests: ApprovalRequest[] }>('/api/approvals?status=all'),
  approve: (id: number, approver: string) =>
    call<ApproveResult>(`/api/approvals/${id}/approve`, { method: 'POST', body: { approver, note: '' }, timeoutMs: 60_000 }),
  reject: (id: number, approver: string) =>
    call<{ ok: boolean; status: 'rejected' }>(`/api/approvals/${id}/reject`, { method: 'POST', body: { approver, note: '' }, timeoutMs: 60_000 }),
  cash: () => call<Cash>('/api/cash'),
}
