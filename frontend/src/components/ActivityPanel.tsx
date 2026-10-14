// The central agent activity area for the selected ticket: start its agent team, watch each step live, and, once a
// run ends, see its outcome and what each participating agent did.
import { useEffect, useMemo, useRef, useState } from 'react'
import { agentIdentity, agentStyle, isAgentName } from '../agents.ts'
import type { AuditEvent, Ticket } from '../api.ts'
import { clockTime, cx, describeEvent, modelLabel, plural, reasonSentence, shorten, statusMeta, ticketItem, ticketType, tokens, toolArgs } from '../format.ts'
import { ArrowRightIcon, BoltIcon, CheckIcon, FlagIcon, FlaskIcon, PlayIcon, ReturnIcon, SpinnerIcon, XIcon } from '../icons.tsx'
import { agentActivity, RUN_LABELS, type AgentActivity, type AgentState, type RunView } from '../runs.ts'
import { AgentAvatar, AgentChip, RunBadge, StatusBadge } from './Badges.tsx'

type Props = {
  ticket: Ticket | null
  loading: boolean
  runs: RunView[]
  run: RunView | null
  starting: boolean
  startError: string | null
  offline: boolean
  pendingForTicket: number
  paidForTicket: number
  note: string
  onNoteChange: (value: string) => void
  onStart: () => void
  onSelectRun: (runId: string) => void
}

export function ActivityPanel(props: Props) {
  const { ticket, loading, runs, run, starting, startError, offline, pendingForTicket, paidForTicket, note, onNoteChange, onStart, onSelectRun } = props
  const activities = useMemo(() => agentActivity(run), [run])
  const [noteOpen, setNoteOpen] = useState(false)

  if (!ticket) {
    return (
      <section className="panel activity" aria-labelledby="activity-title" aria-busy={loading}>
        <h2 id="activity-title" className="activity-placeholder-title">
          Agent activity
        </h2>
        {loading ? (
          <div className="activity-loading">
            <span className="skeleton skeleton-line" />
            <span className="skeleton skeleton-block" />
          </div>
        ) : (
          <p className="empty">Select a ticket to see its agents at work.</p>
        )}
      </section>
    )
  }

  const running = run?.status === 'running' || Boolean(ticket.active_run_id)
  const item = ticketItem(ticket)
  const hint = offline
    ? 'The backend is offline, so the agent team can’t start.'
    : running
      ? 'The desk checks the backend every second and adds each new agent step below.'
      : ticket.resolved
        ? 'This ticket is resolved. Running the team again only re-checks it.'
        : ticket.status === 'waiting_on_approval' && pendingForTicket === 0 && paidForTicket > 0
          ? 'The payment was approved and made. Run the agent team again so the Boss can take the next step.'
        : 'The Boss reads the ticket, asks teammates for help and records a decision. Payments always wait for your approval.'

  return (
    <section className="panel activity" aria-labelledby="activity-title">
      <div className="activity-head">
        <div className="activity-title-block">
          <p className="eyebrow">
            #{ticket.id} · {ticketType(ticket.type)} · {ticket.requester}
          </p>
          <h2 id="activity-title">{ticket.subject}</h2>
          {(item || ticket.notes) && (
            <p className="ticket-detail">
              {item}
              {item && ticket.notes ? ' · ' : ''}
              {ticket.notes && <q>{ticket.notes}</q>}
            </p>
          )}
        </div>
        <div className="activity-actions">
          <StatusBadge status={ticket.status} />
          <button
            type="button"
            className="btn btn-primary"
            onClick={onStart}
            disabled={running || starting || offline}
            aria-busy={starting || undefined}
            aria-describedby="start-hint"
          >
            {running || starting ? <SpinnerIcon className="spin" /> : <PlayIcon />}
            {running ? 'Agents working…' : starting ? 'Starting…' : runs.length > 0 ? 'Run the agent team again' : 'Start agent team'}
          </button>
        </div>
      </div>
      <p id="start-hint" className="hint">
        {hint}
      </p>
      <div className="boss-note">
        {noteOpen || note ? (
          <>
            <label htmlFor={`note-${ticket.id}`}>Note for the Boss, sent with the next run</label>
            <textarea
              id={`note-${ticket.id}`}
              value={note}
              onChange={(event) => onNoteChange(event.target.value)}
              maxLength={1000}
              rows={3}
              placeholder="For example, a decision only a person can make"
              aria-describedby={`note-hint-${ticket.id}`}
            />
            <p id={`note-hint-${ticket.id}`} className="hint">
              The Boss reads it as information from the operator. It is recorded with the run in the audit trail.
            </p>
          </>
        ) : (
          <button type="button" className="link-button" aria-expanded={false} onClick={() => setNoteOpen(true)}>
            Add a note for the Boss
          </button>
        )}
      </div>
      {startError && (
        <p className="notice notice-error" role="alert">
          {startError}
        </p>
      )}

      {runs.length > 1 && run && <RunTabs runs={runs} current={run} onSelect={onSelectRun} />}
      {run && <RunMeta run={run} />}
      <AgentRoster activities={activities} hasRun={run !== null} />
      {run && run.status !== 'running' && <RunOutcome run={run} ticket={ticket} latest={run === runs.at(-1)} />}
      {run && run.status !== 'running' && <RunSummary run={run} activities={activities} pending={pendingForTicket} />}
      {run ? <EventFeed run={run} activities={activities} /> : <NoRunYet />}
    </section>
  )
}

function RunTabs({ runs, current, onSelect }: { runs: RunView[]; current: RunView; onSelect: (runId: string) => void }) {
  return (
    <div className="run-tabs" role="group" aria-label="Runs of this ticket">
      {runs.map((run) => (
        <button
          key={run.id}
          type="button"
          className={cx('run-tab', run.id === current.id && 'is-current', `run-${run.status}`)}
          aria-pressed={run.id === current.id}
          onClick={() => onSelect(run.id)}
        >
          Run {run.number}
          <span className="run-tab-status">
            {run.status === 'running' ? 'running' : run.status === 'finished' ? 'finished' : RUN_LABELS[run.status].replace('Run ', '')}
          </span>
        </button>
      ))}
    </div>
  )
}

function RunMeta({ run }: { run: RunView }) {
  return (
    <div className="run-meta">
      <RunBadge status={run.status} />
      <span>
        Run {run.number} · started {clockTime(run.startedAt)}
        {run.trigger ? ` from the ${run.trigger}` : ''}
      </span>
      <code className="run-id">{run.id}</code>
      {run.model &&
        (run.simulated ? (
          <span className="badge badge-simulated">
            <FlaskIcon />
            Simulated agents
          </span>
        ) : (
          <span className="run-model">{modelLabel(run.model)}</span>
        ))}
      {run.usage && (
        <span className="run-usage">
          {plural(run.usage.steps, 'step')} · {plural(run.usage.tool_calls, 'tool call')} ·{' '}
          {run.simulated ? 'no Claude tokens (simulated)' : `${tokens(run.usage.total_tokens)} tokens`}
        </span>
      )}
    </div>
  )
}

const STATE_LABELS: Record<AgentState, string> = {
  idle: 'Not involved',
  working: 'Working now',
  waiting: 'Waiting on a teammate',
  done: 'Done',
  stopped: 'Stopped',
}

function AgentRoster({ activities, hasRun }: { activities: AgentActivity[]; hasRun: boolean }) {
  return (
    <ul className="roster" aria-label="The five agents">
      {activities.map((activity) => {
        const { Icon, duty } = agentIdentity(activity.name)
        return (
          <li key={activity.name} className={cx('agent-card', `state-${activity.state}`)} style={agentStyle(activity.name)} title={`${activity.name}: ${duty}`}>
            <span className="agent-avatar" aria-hidden="true">
              <Icon />
            </span>
            <span className="agent-card-text">
              <span className="agent-name">{activity.name}</span>
              <span className="agent-duty">{duty}</span>
              <span className="agent-state">
                {activity.state === 'working' && <span className="pulse-dot" aria-hidden="true" />}
                {hasRun ? STATE_LABELS[activity.state] : 'Ready'}
                {activity.tools.length > 0 && ` · ${plural(activity.tools.length, 'tool')}`}
              </span>
            </span>
          </li>
        )
      })}
    </ul>
  )
}

function RunOutcome({ run, ticket, latest }: { run: RunView; ticket: Ticket; latest: boolean }) {
  const status = statusMeta(ticket.status).label
  if (run.status === 'finished') {
    if (latest && ticket.resolved) {
      return (
        <div className="outcome outcome-resolved" role="status">
          <CheckIcon />
          <p>
            <strong>Resolved</strong> The Boss recorded the shop’s work on this ticket as complete.{' '}
            {run.report?.needs_human?.length
              ? 'Steps outside the shop’s system, such as sending drafts, are listed under “Next for a person”.'
              : 'Nothing is left to do.'}
          </p>
        </div>
      )
    }
    if (!latest) {
      // An earlier run: say what it did, and where the ticket stands after the runs that followed it.
      return (
        <div className="outcome outcome-finished" role="status">
          <FlagIcon />
          <p>
            <strong>Run finished</strong> The Boss handed in its report for this run. A later run followed, and the ticket is now “{status}”.
          </p>
        </div>
      )
    }
    return (
      <div className="outcome outcome-finished" role="status">
        <FlagIcon />
        <p>
          <strong>Run finished, ticket not resolved</strong> The ticket is now “{status}”. {statusMeta(ticket.status).hint}
        </p>
      </div>
    )
  }
  return (
    <div className="outcome outcome-failed" role="status">
      <XIcon />
      <p>
        <strong>{RUN_LABELS[run.status]}</strong> {run.stopReason ? reasonSentence(shorten(run.stopReason, 300)) : 'The run ended early.'}{' '}
        {latest ? `The ticket stays “${status}” and is not marked resolved.` : `A later run followed, and the ticket is now “${status}”.`}
      </p>
    </div>
  )
}

function RunSummary({ run, activities, pending }: { run: RunView; activities: AgentActivity[]; pending: number }) {
  const involved = activities.filter((activity) => activity.involved).sort((a, b) => a.firstIndex - b.firstIndex)
  const needs = run.report?.needs_human ?? []
  if (involved.length === 0) return null
  return (
    <section className="summary" aria-labelledby={`summary-${run.id}`}>
      <h3 id={`summary-${run.id}`}>What each agent did</h3>
      <ul className="summary-grid">
        {involved.map((activity) => (
          <li key={activity.name} className="summary-card" style={agentStyle(activity.name)}>
            <div className="summary-head">
              <AgentAvatar name={activity.name} />
              <span>
                <strong>{activity.name}</strong>
                <span className="summary-role">{activity.askedBy.length ? `Asked by ${activity.askedBy.join(', ')}` : 'Led the run'}</span>
              </span>
            </div>
            <p className="summary-text">{activity.report ? shorten(activity.report.summary, 320) : 'No report: the run ended before this agent finished.'}</p>
            {activity.highlights.length > 0 && (
              <ul className="summary-points">
                {activity.highlights.map((highlight, index) => (
                  <li key={index}>{highlight}</li>
                ))}
              </ul>
            )}
            <p className="summary-tools">{toolsLine(activity)}</p>
          </li>
        ))}
      </ul>
      {needs.length > 0 && (
        <div className="next-steps">
          <h4>Next for a person</h4>
          <ul>
            {needs.map((need, index) => (
              <li key={index}>{need}</li>
            ))}
          </ul>
          {pending > 0 && (
            <a className="btn btn-secondary btn-small" href="#approvals">
              Review {plural(pending, 'payment request')}
              <ArrowRightIcon />
            </a>
          )}
        </div>
      )}
    </section>
  )
}

function toolsLine(activity: AgentActivity): string {
  const counts = new Map<string, { count: number; refused: number }>()
  for (const { tool, outcome } of activity.tools) {
    const entry = counts.get(tool) ?? { count: 0, refused: 0 }
    entry.count += 1
    if (outcome !== 'ok') entry.refused += 1
    counts.set(tool, entry)
  }
  const tools = [...counts].map(([tool, { count, refused }]) => `${tool}${count > 1 ? ` ×${count}` : ''}${refused ? ' (refused)' : ''}`)
  const parts = [tools.length ? `Tools: ${tools.join(', ')}` : 'No tools used']
  if (activity.delegatedTo.length) parts.push(`asked ${activity.delegatedTo.join(', ')}`)
  return parts.join(' · ')
}

function NoRunYet() {
  return (
    <div className="empty-activity">
      <BoltIcon />
      <p>
        <strong>No agent activity yet.</strong> Start the agent team to watch the Boss read the ticket, delegate to the specialists, use the
        shop’s tools and record a decision. Each step appears here as it happens.
      </p>
    </div>
  )
}

function EventFeed({ run, activities }: { run: RunView; activities: AgentActivity[] }) {
  const scroller = useRef<HTMLDivElement>(null)
  const pinned = useRef(true) // follow new events unless the person scrolled up to read
  const count = run.events.length
  useEffect(() => {
    pinned.current = true
  }, [run.id])
  useEffect(() => {
    const element = scroller.current
    if (element && pinned.current) element.scrollTop = element.scrollHeight
  }, [count, run.id, run.status])
  const working = activities.find((activity) => activity.state === 'working')?.name ?? 'Boss'

  return (
    <section className="feed-panel" aria-labelledby={`feed-${run.id}`}>
      <div className="feed-head">
        <h3 id={`feed-${run.id}`}>Activity</h3>
        <span className="feed-count">
          {plural(count, 'event')}
          {run.status === 'running' && (
            <>
              {' · '}
              <span className="live-dot" aria-hidden="true" /> live
            </>
          )}
        </span>
      </div>
      <div
        className="feed-scroll"
        ref={scroller}
        tabIndex={0}
        aria-label={`Activity of run ${run.number}, oldest first`}
        onScroll={(event) => {
          const element = event.currentTarget
          pinned.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48
        }}
      >
        {count === 0 ? (
          <p className="empty">Starting the agent team…</p>
        ) : (
          <ol className="feed">
            {run.events.map((event) => (
              <EventItem key={event.index} event={event} />
            ))}
          </ol>
        )}
        {run.status === 'running' && (
          <p className="feed-working">
            <AgentChip name={working} /> is working
            <span className="dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
          </p>
        )}
      </div>
    </section>
  )
}

const KIND_ICONS = { start: PlayIcon, delegate: ArrowRightIcon, tool: BoltIcon, report: CheckIcon, handoff: ReturnIcon, end: FlagIcon, fail: XIcon, other: BoltIcon }

function EventItem({ event }: { event: AuditEvent }) {
  const view = describeEvent(event)
  const Icon = KIND_ICONS[view.kind]
  const args = view.kind === 'tool' ? toolArgs(event.input) : ''
  const showData = view.kind === 'tool' || view.kind === 'report' || view.kind === 'delegate'
  const agent = isAgentName(event.agent) ? event.agent : null
  return (
    <li
      className={cx('event', `event-${view.kind}`, `tone-${view.tone}`)}
      style={agentStyle(agent, { '--depth': Math.min(event.depth ?? 0, 3) })}
      data-index={event.index}
    >
      <time className="event-time" dateTime={event.time}>
        {clockTime(event.time)}
      </time>
      <div className="event-body">
        <p className="event-line">
          <span className="event-icon" aria-hidden="true">
            <Icon />
          </span>
          {view.kind === 'end' || view.kind === 'fail' ? (
            <strong className="event-title">{view.title}</strong>
          ) : (
            <>
              <AgentChip name={event.agent} />
              <span className="event-title">{view.title}</span>
              {view.target && <AgentChip name={view.target} />}
              {view.kind === 'tool' && <code className="tool-name">{event.tool}</code>}
            </>
          )}
          {view.tone === 'refused' && <span className="outcome-chip is-refused">refused</span>}
          {view.tone === 'error' && view.kind !== 'fail' && <span className="outcome-chip is-error">error</span>}
        </p>
        {args && <p className="event-args">{args}</p>}
        {view.message && <blockquote className="event-message">{view.message}</blockquote>}
        {view.detail && <p className={cx('event-detail', view.tone !== 'ok' && view.tone !== 'info' && 'is-problem')}>{view.detail}</p>}
        {view.list.length > 0 && (
          <ul className="event-list">
            {view.list.map((entry, index) => (
              <li key={index}>{entry}</li>
            ))}
          </ul>
        )}
        {showData && (event.input !== undefined || event.result !== undefined) && (
          <details className="event-data">
            <summary>Input and result</summary>
            <pre>{JSON.stringify({ input: event.input, result: event.result, error: event.error }, null, 2)}</pre>
          </details>
        )}
      </div>
    </li>
  )
}
