// Agent runs, rebuilt from the audit trail's records. Records are keyed by their index in the append-only file, so
// each appears once, and grouped by run id, so one ticket's activity never mixes with another's.
import type { AgentName, AgentReport, AuditEvent, BackendRun, Usage } from './api.ts'
import { AGENTS, isAgentName } from './agents.ts'
import { isSimulatedModel, rec, shorten, text, toReport, toolHighlight } from './format.ts'

export type RunStatus = 'running' | 'finished' | 'stopped' | 'failed' | 'interrupted'

export const RUN_LABELS: Record<RunStatus, string> = {
  running: 'Agents working',
  finished: 'Run finished',
  stopped: 'Run stopped',
  failed: 'Run failed',
  interrupted: 'Run interrupted',
}
export type LocalRun = { id: string; ticketId: number; startedAt: string } // started here; its first record may not be in yet

export type RunView = {
  id: string
  ticketId: number
  number: number // 1, 2, … among this ticket's runs
  order: number
  events: AuditEvent[] // oldest first
  startedAt: string
  model: string | null
  trigger: string | null
  simulated: boolean
  status: RunStatus
  stopReason: string | null
  report: AgentReport | null
  usage: Usage | null
}

export type RunIndex = { byId: Record<string, RunView>; byTicket: Record<number, RunView[]> }

// A run nobody is tracking and that hasn't finished: treat it as still going while records keep arriving.
const QUIET_RUN_MS = 10 * 60 * 1000

type BuildInput = {
  events: ReadonlyMap<number, AuditEvent>
  database: string | null
  backendRuns: Readonly<Record<string, BackendRun>>
  localRuns: Readonly<Record<string, LocalRun>>
  missingRuns: Readonly<Record<string, boolean>>
  activeRunIds: ReadonlySet<string>
  clock: number
}

export function buildRuns(input: BuildInput): RunIndex {
  const ordered = [...input.events.values()].sort((a, b) => a.index - b.index)
  let resetAt = -1
  const groups = new Map<string, AuditEvent[]>()
  for (const event of ordered) {
    if (event.action === 'database_reset' && (!input.database || event.database === input.database)) resetAt = event.index
    if (!event.run) continue
    const group = groups.get(event.run)
    if (group) group.push(event)
    else groups.set(event.run, [event])
  }

  const runs: RunView[] = []
  for (const id of new Set([...groups.keys(), ...Object.keys(input.localRuns)])) {
    const events = groups.get(id) ?? []
    const start = events.find((event) => event.action === 'run_started')
    const local = input.localRuns[id]
    if (!start && !local) continue // its start is older than the records loaded
    if (start && input.database && start.database && start.database !== input.database) continue // another database's run
    if (start && start.index < resetAt) continue // from before the database was reset
    const ticketId = typeof start?.ticket === 'number' ? start.ticket : local?.ticketId
    if (typeof ticketId !== 'number') continue
    const own = events.filter((event) => event.ticket == null || event.ticket === ticketId)
    runs.push(describeRun(id, ticketId, own, start, local, input))
  }
  runs.sort((a, b) => a.order - b.order)

  const byId: Record<string, RunView> = {}
  const byTicket: Record<number, RunView[]> = {}
  for (const run of runs) {
    const ticketRuns = (byTicket[run.ticketId] ??= [])
    run.number = ticketRuns.length + 1
    ticketRuns.push(run)
    byId[run.id] = run
  }
  return { byId, byTicket }
}

function describeRun(
  id: string,
  ticketId: number,
  events: AuditEvent[],
  start: AuditEvent | undefined,
  local: LocalRun | undefined,
  input: BuildInput,
): RunView {
  const end = events.find((event) => event.action === 'run_finished' || event.action === 'run_failed')
  const backend = input.backendRuns[id]
  const last = events.at(-1)
  let status: RunStatus
  let stopReason: string | null = null
  if (end) {
    status = end.action === 'run_failed' ? 'failed' : end.outcome === 'ok' ? 'finished' : 'stopped'
    stopReason = status === 'finished' ? null : text(end.error) || null
  } else if (backend && backend.status !== 'running') {
    status = backend.status
    stopReason = status === 'finished' ? null : backend.stop_reason ?? null
  } else if (input.activeRunIds.has(id) || backend?.status === 'running' || (local && !input.missingRuns[id])) {
    status = 'running'
  } else if (last && input.clock - Date.parse(last.time) < QUIET_RUN_MS && !input.missingRuns[id]) {
    status = 'running' // started elsewhere, for example from the command line
  } else {
    status = 'interrupted'
    stopReason = 'The run ended without a final record, for example because the backend stopped.'
  }

  const bossReport = [...events].reverse().find((event) => event.action === 'final_report' && (event.depth ?? 0) === 0)
  const report = toReport(backend?.report) ?? toReport(end?.result) ?? toReport(bossReport?.input)
  const usage = backend?.usage ?? end?.usage ?? [...events].reverse().find((event) => event.usage)?.usage ?? null
  return {
    id,
    ticketId,
    number: 0,
    order: start?.index ?? Number.MAX_SAFE_INTEGER,
    events,
    startedAt: start?.time ?? local?.startedAt ?? '',
    model: start?.model ?? null,
    trigger: start?.trigger ?? null,
    simulated: isSimulatedModel(start?.model),
    status,
    stopReason,
    report,
    usage,
  }
}

// --- What each agent did in a run --------------------------------------------------------------------------------

export type AgentState = 'idle' | 'working' | 'waiting' | 'done' | 'stopped'

export type AgentActivity = {
  name: AgentName
  involved: boolean
  state: AgentState
  firstIndex: number
  askedBy: AgentName[]
  delegatedTo: AgentName[]
  tools: { tool: string; outcome: string }[]
  highlights: string[]
  report: AgentReport | null
}

export function agentActivity(run: RunView | null): AgentActivity[] {
  const activity = new Map<AgentName, AgentActivity>(
    AGENTS.map((agent) => [
      agent.name,
      { name: agent.name, involved: false, state: 'idle', firstIndex: Infinity, askedBy: [], delegatedTo: [], tools: [], highlights: [], report: null },
    ]),
  )
  if (!run) return [...activity.values()]

  const touch = (name: AgentName, index: number): AgentActivity => {
    const entry = activity.get(name)!
    if (!entry.involved) {
      entry.involved = true
      entry.firstIndex = index
    }
    return entry
  }
  const stack: AgentName[] = [] // who is working: the Boss, then whoever it asked, and so on
  for (const event of run.events) {
    const agent = isAgentName(event.agent) ? event.agent : null
    const target = rec(event.input)?.agent
    switch (event.action) {
      case 'run_started':
        touch('Boss', event.index)
        stack.splice(0, stack.length, 'Boss')
        break
      case 'delegation_started':
        if (agent && isAgentName(target)) {
          const asker = touch(agent, event.index)
          if (!asker.delegatedTo.includes(target)) asker.delegatedTo.push(target)
          const asked = touch(target, event.index)
          if (!asked.askedBy.includes(agent)) asked.askedBy.push(agent)
          stack.push(target)
        }
        break
      case 'tool':
        if (agent) {
          const entry = touch(agent, event.index)
          entry.tools.push({ tool: event.tool ?? 'tool', outcome: event.outcome ?? 'ok' })
          const highlight = toolHighlight(event)
          if (highlight) entry.highlights.push(highlight)
        }
        break
      case 'final_report':
        if (agent) {
          touch(agent, event.index).report = toReport(event.input)
          if (stack.at(-1) === agent) stack.pop()
        }
        break
      case 'delegation': // the asker got its answer, or an error, back
        if (isAgentName(target) && stack.at(-1) === target) stack.pop()
        if (agent && event.outcome !== 'ok') {
          const why = text(rec(event.result)?.refused) || text(event.error)
          touch(agent, event.index).highlights.push(`Couldn't get ${isAgentName(target) ? target : 'a teammate'}'s report: ${shorten(why, 150)}`)
        }
        break
      case 'run_finished':
      case 'run_failed':
        stack.length = 0
        break
    }
  }

  const current = run.status === 'running' ? (stack.at(-1) ?? 'Boss') : null
  for (const entry of activity.values()) {
    if (!entry.involved) entry.state = current === entry.name ? 'working' : 'idle'
    else if (entry.name === current) entry.state = 'working'
    else if (entry.report) entry.state = 'done'
    else entry.state = run.status === 'running' ? 'waiting' : run.status === 'finished' ? 'done' : 'stopped'
  }
  return [...activity.values()]
}
