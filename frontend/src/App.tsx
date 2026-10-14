// The Campus Customs operations desk: tickets on the left, the agents' work in the middle, approvals and cash on
// the right. All data comes from the FastAPI backend's routes (see useDesk.ts).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ActivityPanel } from './components/ActivityPanel.tsx'
import { ApprovalPanel } from './components/ApprovalPanel.tsx'
import { Header, OfflineBanner, SandboxBanner } from './components/Header.tsx'
import { TicketList } from './components/TicketList.tsx'
import type { ApprovalRequest } from './api.ts'
import { statusMeta } from './format.ts'
import { RUN_LABELS, type RunStatus } from './runs.ts'
import { useDesk } from './useDesk.ts'

const APPROVER_KEY = 'campus-customs.approver'

function readStored(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? ''
  } catch {
    return '' // storage blocked: the name is kept for this visit only
  }
}

function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // storage blocked: the name is kept for this visit only
  }
}

function countByTicket(requests: ApprovalRequest[]): Record<number, number> {
  const counts: Record<number, number> = {}
  for (const request of requests) if (request.ticket_id !== null) counts[request.ticket_id] = (counts[request.ticket_id] ?? 0) + 1
  return counts
}

// #ticket-102 selects a ticket; #ticket-102/run-<run id> also shows one of its runs (a shareable link to a run).
function selectionFromHash(): { ticket: number | null; run: string | null } {
  const match = /^#ticket-(\d+)(?:\/run-([\w-]+))?$/.exec(window.location.hash)
  return match ? { ticket: Number(match[1]), run: match[2] ?? null } : { ticket: null, run: null }
}

export default function App() {
  const desk = useDesk()
  const [chosen, setChosen] = useState<number | null>(() => selectionFromHash().ticket)
  const [viewed, setViewed] = useState<Record<number, string>>(() => {
    const { ticket, run } = selectionFromHash()
    return ticket !== null && run ? { [ticket]: run } : {}
  })
  const [startErrors, setStartErrors] = useState<Record<number, string | null>>({})
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [approver, setApprover] = useState(() => readStored(APPROVER_KEY))
  const [announcement, setAnnouncement] = useState('')

  const tickets = useMemo(() => desk.tickets ?? [], [desk.tickets])
  const selectedId =
    (chosen !== null && tickets.some((ticket) => ticket.id === chosen) ? chosen : null) ??
    tickets.find((ticket) => ticket.active_run_id)?.id ??
    tickets[0]?.id ??
    null
  const ticket = tickets.find((entry) => entry.id === selectedId) ?? null
  const ticketRuns = selectedId === null ? [] : (desk.runs.byTicket[selectedId] ?? [])
  const viewedId = selectedId === null ? undefined : viewed[selectedId]
  const run = ticketRuns.find((entry) => entry.id === viewedId) ?? ticketRuns.at(-1) ?? null

  const pending = useMemo(() => (desk.approvals ?? []).filter((request) => request.status === 'pending'), [desk.approvals])
  const pendingByTicket = useMemo(() => countByTicket(pending), [pending])
  const paidByTicket = useMemo(() => countByTicket((desk.approvals ?? []).filter((request) => request.status === 'paid')), [desk.approvals])

  const select = useCallback((id: number) => {
    setChosen(id)
    try {
      window.history.replaceState(null, '', `#ticket-${id}`)
    } catch {
      // some browsers limit history changes; the selection still works
    }
  }, [])

  const start = async () => {
    if (!ticket) return
    const id = ticket.id
    setStartErrors((previous) => ({ ...previous, [id]: null }))
    const outcome = await desk.startRun(id, notes[id]?.trim() || undefined)
    if (outcome.ignored) return
    if (outcome.ok) setNotes((previous) => ({ ...previous, [id]: '' })) // the note went with this run
    const runId = outcome.runId
    if (runId) setViewed((previous) => ({ ...previous, [id]: runId }))
    if (outcome.ok) setAnnouncement(`The agent team started on ticket ${id}.`)
    else setStartErrors((previous) => ({ ...previous, [id]: outcome.message }))
  }

  const decide = (request: ApprovalRequest, decision: 'approve' | 'reject') => desk.decide(request, decision, approver.trim())

  // Follow #ticket-N in the address bar, for example after going back or pasting a link.
  useEffect(() => {
    const onHash = () => {
      const { ticket: id, run: runId } = selectionFromHash()
      if (id === null) return
      setChosen(id)
      if (runId) setViewed((current) => ({ ...current, [id]: runId }))
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  // Announce runs that end, for people using a screen reader, with the ticket's status as the next refresh reports it.
  const previous = useRef<Record<string, RunStatus>>({})
  const justEnded = useRef<{ ticketId: number; status: RunStatus }[]>([])
  useEffect(() => {
    const before = previous.current
    const now: Record<string, RunStatus> = {}
    for (const entry of Object.values(desk.runs.byId)) {
      now[entry.id] = entry.status
      if (before[entry.id] === 'running' && entry.status !== 'running') justEnded.current.push({ ticketId: entry.ticketId, status: entry.status })
    }
    previous.current = now
  }, [desk.runs])
  useEffect(() => {
    if (justEnded.current.length === 0) return
    const messages = justEnded.current.map(({ ticketId, status }) => {
      const ticketStatus = statusMeta(tickets.find((item) => item.id === ticketId)?.status ?? '').label
      return status === 'finished'
        ? `The run on ticket ${ticketId} finished. The ticket is ${ticketStatus}.`
        : `${RUN_LABELS[status]} on ticket ${ticketId}. The ticket stays ${ticketStatus}.`
    })
    justEnded.current = []
    setAnnouncement(messages.join(' '))
  }, [tickets])

  const offline = desk.connection === 'offline'
  return (
    <div className="desk">
      <a className="skip-link" href="#approvals">
        Skip to approvals and cash
      </a>
      <Header health={desk.health} cash={desk.cash} pendingCount={pending.length} connection={desk.connection} />
      <SandboxBanner health={desk.health} />
      {offline && <OfflineBanner message={desk.connectionError} onRetry={desk.refresh} />}
      {desk.refreshError && !offline && (
        <p className="refresh-error" role="status">
          Part of the desk couldn’t refresh: {desk.refreshError} It will try again shortly.
        </p>
      )}
      <main className="desk-grid">
        <TicketList tickets={desk.tickets} selectedId={selectedId} runsByTicket={desk.runs.byTicket} pendingByTicket={pendingByTicket} onSelect={select} />
        <ActivityPanel
          ticket={ticket}
          loading={desk.tickets === null && !offline}
          runs={ticketRuns}
          run={run}
          starting={ticket ? Boolean(desk.starting[ticket.id]) : false}
          startError={ticket ? (startErrors[ticket.id] ?? null) : null}
          offline={offline}
          pendingForTicket={ticket ? (pendingByTicket[ticket.id] ?? 0) : 0}
          paidForTicket={ticket ? (paidByTicket[ticket.id] ?? 0) : 0}
          note={ticket ? (notes[ticket.id] ?? '') : ''}
          onNoteChange={(value) => ticket && setNotes((previous) => ({ ...previous, [ticket.id]: value }))}
          onStart={start}
          onSelectRun={(runId) => selectedId !== null && setViewed((current) => ({ ...current, [selectedId]: runId }))}
        />
        <ApprovalPanel
          cash={desk.cash}
          cashDelta={desk.cashDelta}
          approvals={desk.approvals}
          deciding={desk.deciding}
          approver={approver}
          offline={offline}
          onApproverChange={(value) => {
            setApprover(value)
            writeStored(APPROVER_KEY, value)
          }}
          onDecide={decide}
          onSelectTicket={select}
        />
      </main>
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {announcement}
      </p>
    </div>
  )
}
