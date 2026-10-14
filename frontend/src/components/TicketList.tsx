// The ticket list: all tickets with the backend's status, any run in progress or failed, and requests to approve.
import type { Ticket } from '../api.ts'
import { cx, plural, ticketItem, ticketType } from '../format.ts'
import { CheckIcon, DollarIcon } from '../icons.tsx'
import type { RunView } from '../runs.ts'
import { RunBadge, StatusBadge } from './Badges.tsx'

type Props = {
  tickets: Ticket[] | null
  selectedId: number | null
  runsByTicket: Record<number, RunView[]>
  pendingByTicket: Record<number, number>
  onSelect: (id: number) => void
}

export function TicketList({ tickets, selectedId, runsByTicket, pendingByTicket, onSelect }: Props) {
  return (
    <section className="panel tickets" aria-labelledby="tickets-title">
      <div className="panel-head">
        <h2 id="tickets-title">Tickets</h2>
        {tickets && <span className="count">{tickets.length}</span>}
      </div>
      {tickets === null ? (
        <ul className="ticket-list" aria-label="Loading tickets" aria-busy="true">
          {[0, 1, 2].map((key) => (
            <li key={key}>
              <div className="ticket-card is-loading">
                <span className="skeleton skeleton-line short" />
                <span className="skeleton skeleton-line" />
                <span className="skeleton skeleton-line medium" />
              </div>
            </li>
          ))}
        </ul>
      ) : tickets.length === 0 ? (
        <p className="empty">The database has no tickets.</p>
      ) : (
        <ul className="ticket-list">
          {tickets.map((ticket) => (
            <TicketCard
              key={ticket.id}
              ticket={ticket}
              selected={ticket.id === selectedId}
              latest={runsByTicket[ticket.id]?.at(-1) ?? null}
              pending={pendingByTicket[ticket.id] ?? 0}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

function TicketCard({ ticket, selected, latest, pending, onSelect }: {
  ticket: Ticket
  selected: boolean
  latest: RunView | null
  pending: number
  onSelect: (id: number) => void
}) {
  const running = Boolean(ticket.active_run_id) || latest?.status === 'running'
  const failed = !running && latest !== null && ['stopped', 'failed', 'interrupted'].includes(latest.status)
  const item = ticketItem(ticket)
  return (
    <li>
      <button
        type="button"
        className={cx('ticket-card', selected && 'is-selected', ticket.resolved && 'is-resolved', failed && 'has-failed', running && 'is-running')}
        aria-current={selected ? 'true' : undefined}
        onClick={() => onSelect(ticket.id)}
      >
        <span className="ticket-top">
          <span className="ticket-id">#{ticket.id}</span>
          <span className="ticket-type">{ticketType(ticket.type)}</span>
        </span>
        <span className="ticket-subject">{ticket.subject}</span>
        <span className="ticket-meta">
          {ticket.requester}
          {item ? ` · ${item}` : ''}
        </span>
        <span className="ticket-badges">
          <StatusBadge status={ticket.status} />
          {running && <RunBadge status="running" />}
          {failed && latest && <RunBadge status={latest.status} />}
          {pending > 0 && (
            <span className="badge badge-count">
              <DollarIcon />
              {plural(pending, 'request')} to approve
            </span>
          )}
        </span>
        {ticket.resolved && <CheckIcon className="resolved-mark" />}
      </button>
    </li>
  )
}
