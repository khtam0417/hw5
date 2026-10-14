// The dashboard's connection to the backend: one polling loop for tickets, audit events, runs, payment requests and
// cash, plus the actions a person takes (start a run, approve or reject a payment request).
//
// Polling: every second while any run is going, every 5 seconds otherwise (15 when the tab is hidden). Audit events
// are fetched after the last index seen, so each arrives once, and are stored by index, so a repeated page can't
// duplicate one. Actions refresh right away.
import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'
import { api, ApiError, conflictRunId, EVENTS_PAGE, type ApprovalRequest, type ApproveResult, type AuditEvent, type BackendRun, type Cash, type Health, type Ticket } from './api.ts'
import { money, requestTitle, round2 } from './format.ts'
import { buildRuns, type LocalRun, type RunIndex } from './runs.ts'

const ACTIVE_MS = 1000
const IDLE_MS = 5000
const HIDDEN_MS = 15000

export type Connection = 'connecting' | 'online' | 'offline'
// The last change to the balance: a payment approved here, or a change seen when refreshing (approve.py, a reset).
export type CashDelta = { amount: number; key: number; cause: 'payment' | 'change' }
export type Outcome = { ok: boolean; message: string; runId?: string; ignored?: boolean }

type State = {
  connection: Connection
  connectionError: string | null
  refreshError: string | null
  health: Health | null
  tickets: Ticket[] | null
  approvals: ApprovalRequest[] | null
  cash: Cash | null
  cashDelta: CashDelta | null
  events: ReadonlyMap<number, AuditEvent>
  backendRuns: Readonly<Record<string, BackendRun>>
  localRuns: Readonly<Record<string, LocalRun>>
  missingRuns: Readonly<Record<string, boolean>>
  starting: Readonly<Record<number, boolean>>
  deciding: number | null
  clock: number
}

type Action =
  | { type: 'health'; health: Health }
  | { type: 'backend-changed' }
  | { type: 'synced'; at: number }
  | { type: 'offline'; message: string }
  | { type: 'refresh-error'; message: string }
  | { type: 'snapshot'; tickets: Ticket[]; approvals: ApprovalRequest[]; cash: Cash; at: number }
  | { type: 'events'; events: AuditEvent[] }
  | { type: 'events-reset' }
  | { type: 'backend-run'; run: BackendRun }
  | { type: 'run-missing'; id: string }
  | { type: 'local-run'; run: LocalRun }
  | { type: 'starting'; ticketId: number; on: boolean }
  | { type: 'deciding'; id: number | null }
  | { type: 'paid'; result: ApproveResult; at: number }

const INITIAL: State = {
  connection: 'connecting',
  connectionError: null,
  refreshError: null,
  health: null,
  tickets: null,
  approvals: null,
  cash: null,
  cashDelta: null,
  events: new Map(),
  backendRuns: {},
  localRuns: {},
  missingRuns: {},
  starting: {},
  deciding: null,
  clock: Date.now(),
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'health':
      return { ...state, health: action.health }
    case 'backend-changed': // another backend or database on the same address: start over
      return { ...INITIAL, connection: state.connection, starting: state.starting, clock: state.clock }
    case 'synced':
      return { ...state, connection: 'online', connectionError: null, refreshError: null, clock: action.at }
    case 'offline':
      return { ...state, connection: 'offline', connectionError: action.message }
    case 'refresh-error':
      return { ...state, connection: 'online', refreshError: action.message }
    case 'snapshot': {
      const previous = state.cash
      const changed = previous !== null && previous.account === action.cash.account && Math.abs(previous.balance - action.cash.balance) > 0.004
      return {
        ...state,
        tickets: action.tickets,
        approvals: action.approvals,
        cash: action.cash,
        cashDelta: changed ? { amount: round2(action.cash.balance - previous.balance), key: action.at, cause: 'change' } : state.cashDelta,
      }
    }
    case 'events': {
      let events: Map<number, AuditEvent> | null = null
      for (const event of action.events) {
        if (state.events.has(event.index) || events?.has(event.index)) continue // never twice
        events ??= new Map(state.events)
        events.set(event.index, event)
      }
      return events ? { ...state, events } : state
    }
    case 'events-reset':
      return { ...state, events: new Map(), backendRuns: {}, localRuns: {}, missingRuns: {} }
    case 'backend-run':
      return { ...state, backendRuns: { ...state.backendRuns, [action.run.run_id]: action.run } }
    case 'run-missing': {
      const localRuns = { ...state.localRuns }
      delete localRuns[action.id]
      return { ...state, localRuns, missingRuns: { ...state.missingRuns, [action.id]: true } }
    }
    case 'local-run':
      return { ...state, localRuns: { ...state.localRuns, [action.run.id]: action.run } }
    case 'starting':
      return { ...state, starting: { ...state.starting, [action.ticketId]: action.on } }
    case 'deciding':
      return { ...state, deciding: action.id }
    case 'paid': {
      if (!state.cash) return state
      const amount = round2(action.result.new_balance - state.cash.balance)
      return {
        ...state,
        cash: { ...state.cash, balance: action.result.new_balance },
        cashDelta: amount ? { amount, key: action.at, cause: 'payment' } : state.cashDelta,
      }
    }
  }
}

const backendKey = (health: Health): string => `${health.database}|${health.agents?.model ?? ''}`

export function useDesk() {
  const [state, dispatch] = useReducer(reducer, INITIAL)
  const cursor = useRef<number | null>(null) // the index of the last audit record seen
  const watching = useRef(new Set<string>()) // runs in progress, polled every second
  const backend = useRef<string | null>(null)
  const busy = useRef(new Set<string>()) // actions in flight, so a second click does nothing
  const loop = useRef({ alive: false, running: false, again: false, offline: false, ticks: 0, timer: 0, kick: () => {} })

  const syncEvents = useCallback(async () => {
    for (let page = 0; page < 50; page += 1) {
      const after = cursor.current
      const result = await api.events(after)
      if (after !== null && result.total_records <= after) {
        // A different, shorter audit file (for example a restarted sandbox): read it from the start.
        cursor.current = null
        dispatch({ type: 'events-reset' })
        continue
      }
      if (result.events.length) dispatch({ type: 'events', events: result.events })
      cursor.current = result.next_after
      if (after === null || result.events.length < EVENTS_PAGE) return
    }
  }, [])

  const sync = useCallback(
    async (full: boolean) => {
      if (full) {
        const health = await api.health()
        const key = backendKey(health)
        if (backend.current !== null && backend.current !== key) {
          cursor.current = null
          watching.current.clear()
          dispatch({ type: 'backend-changed' })
        }
        backend.current = key
        dispatch({ type: 'health', health })
      }
      await syncEvents()
      let ended = false
      for (const id of [...watching.current]) {
        try {
          const run = await api.run(id)
          dispatch({ type: 'backend-run', run })
          if (run.status !== 'running') {
            watching.current.delete(id)
            ended = true
          }
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 404) throw error
          watching.current.delete(id) // started by another backend process, or before this one restarted
          dispatch({ type: 'run-missing', id })
        }
      }
      const [tickets, approvals, cash] = await Promise.all([api.tickets(), api.approvals(), api.cash()])
      for (const ticket of tickets.tickets) if (ticket.active_run_id) watching.current.add(ticket.active_run_id)
      dispatch({ type: 'snapshot', tickets: tickets.tickets, approvals: approvals.approval_requests, cash, at: Date.now() })
      if (ended) loop.current.again = true // fetch the finished run's last records right away
    },
    [syncEvents],
  )

  useEffect(() => {
    const control = loop.current
    control.alive = true
    const tick = async () => {
      if (!control.alive) return
      if (control.running) {
        control.again = true
        return
      }
      control.running = true
      window.clearTimeout(control.timer)
      control.ticks += 1
      const active = watching.current.size > 0
      try {
        await sync(!active || control.offline || backend.current === null || control.ticks % 5 === 0)
        control.offline = false
        dispatch({ type: 'synced', at: Date.now() })
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        if (error instanceof ApiError && error.status === 0) {
          control.offline = true
          dispatch({ type: 'offline', message })
        } else {
          dispatch({ type: 'refresh-error', message })
        }
      } finally {
        control.running = false
        if (control.alive) {
          const delay = control.again ? 60 : watching.current.size > 0 ? ACTIVE_MS : document.hidden ? HIDDEN_MS : IDLE_MS
          control.again = false
          control.timer = window.setTimeout(() => void tick(), delay)
        }
      }
    }
    control.kick = () => void tick()
    void tick()
    const onVisibility = () => {
      if (!document.hidden) control.kick()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      control.alive = false
      window.clearTimeout(control.timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [sync])

  const startRun = useCallback(async (ticketId: number, note?: string): Promise<Outcome> => {
    const key = `run:${ticketId}`
    if (busy.current.has(key)) return { ok: false, message: '', ignored: true }
    busy.current.add(key)
    dispatch({ type: 'starting', ticketId, on: true })
    try {
      const started = await api.startRun(ticketId, note)
      dispatch({ type: 'local-run', run: { id: started.run_id, ticketId, startedAt: new Date().toISOString() } })
      watching.current.add(started.run_id)
      return { ok: true, message: `The agent team started on ticket ${ticketId}.`, runId: started.run_id }
    } catch (error) {
      const runId = error instanceof ApiError ? conflictRunId(error.detail) : null
      if (runId) watching.current.add(runId) // that ticket already has a run: follow it
      return { ok: false, message: error instanceof Error ? error.message : String(error), runId: runId ?? undefined }
    } finally {
      busy.current.delete(key)
      dispatch({ type: 'starting', ticketId, on: false })
      loop.current.kick()
    }
  }, [])

  const decide = useCallback(async (request: ApprovalRequest, decision: 'approve' | 'reject', approver: string): Promise<Outcome> => {
    if (busy.current.has('decision')) return { ok: false, message: '', ignored: true } // one payment decision at a time
    busy.current.add('decision')
    dispatch({ type: 'deciding', id: request.id })
    try {
      if (decision === 'approve') {
        const result = await api.approve(request.id, approver)
        dispatch({ type: 'paid', result, at: Date.now() })
        return {
          ok: true,
          message: `Paid ${money(result.amount)}: ${requestTitle(request).toLowerCase()} (payment #${result.payment_id}). Checking is now ${money(result.new_balance)}.`,
        }
      }
      await api.reject(request.id, approver)
      return { ok: true, message: `Rejected request #${request.id}. Nothing was paid.` }
    } catch (error) {
      return { ok: false, message: error instanceof Error ? error.message : String(error) }
    } finally {
      busy.current.delete('decision')
      dispatch({ type: 'deciding', id: null })
      loop.current.kick() // refresh the balance, the requests and the tickets
    }
  }, [])

  const refresh = useCallback(() => loop.current.kick(), [])

  const activeRunIds = useMemo(
    () => new Set((state.tickets ?? []).flatMap((ticket) => (ticket.active_run_id ? [ticket.active_run_id] : []))),
    [state.tickets],
  )
  const runs: RunIndex = useMemo(
    () =>
      buildRuns({
        events: state.events,
        database: state.health?.database ?? null,
        backendRuns: state.backendRuns,
        localRuns: state.localRuns,
        missingRuns: state.missingRuns,
        activeRunIds,
        clock: state.clock,
      }),
    [state.events, state.health, state.backendRuns, state.localRuns, state.missingRuns, activeRunIds, state.clock],
  )

  return { ...state, runs, startRun, decide, refresh }
}
