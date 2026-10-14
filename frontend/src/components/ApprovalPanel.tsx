// The approval and cash panel: the checking balance, the payment and purchase requests waiting for a person, and
// the Approve button that calls the backend's approval route (which checks the request and makes the payment).
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { ApprovalRequest, Cash } from '../api.ts'
import { cx, money, requestTitle, round2, shopDate, signedMoney } from '../format.ts'
import { AlertIcon, BuildingIcon, CheckIcon, DollarIcon, SpinnerIcon, XIcon } from '../icons.tsx'
import type { CashDelta, Outcome } from '../useDesk.ts'
import { AgentChip } from './Badges.tsx'

type Props = {
  cash: Cash | null
  cashDelta: CashDelta | null
  approvals: ApprovalRequest[] | null
  deciding: number | null
  approver: string
  offline: boolean
  onApproverChange: (value: string) => void
  onDecide: (request: ApprovalRequest, decision: 'approve' | 'reject') => Promise<Outcome>
  onSelectTicket: (id: number) => void
}

export function ApprovalPanel({ cash, cashDelta, approvals, deciding, approver, offline, onApproverChange, onDecide, onSelectTicket }: Props) {
  const pending = (approvals ?? []).filter((request) => request.status === 'pending')
  const decided = (approvals ?? [])
    .filter((request) => request.status !== 'pending')
    .sort((a, b) => b.id - a.id)
    .slice(0, 6)
  const [notice, setNotice] = useState<string | null>(null)
  const [nameError, setNameError] = useState(false)
  const nameInput = useRef<HTMLInputElement>(null)
  const fresh = useNewIds(approvals === null ? null : pending.map((request) => request.id))
  const announcement = pending.filter((request) => fresh.has(request.id)).map((request) => `New request: ${requestTitle(request)}, ${money(request.amount)}.`).join(' ')

  const decide = async (request: ApprovalRequest, decision: 'approve' | 'reject'): Promise<Outcome> => {
    if (!approver.trim()) {
      setNameError(true)
      nameInput.current?.focus()
      return { ok: false, message: '', ignored: true }
    }
    const outcome = await onDecide(request, decision)
    if (outcome.ok) setNotice(outcome.message)
    return outcome
  }

  return (
    <aside id="approvals" className="panel approvals" aria-labelledby="approvals-title" tabIndex={-1}>
      <div className="panel-head">
        <h2 id="approvals-title">Approvals &amp; cash</h2>
      </div>
      <CashCard cash={cash} delta={cashDelta} />

      <div className="approver-field">
        <label htmlFor="approver">Approving as</label>
        <input
          id="approver"
          ref={nameInput}
          value={approver}
          onChange={(event) => {
            onApproverChange(event.target.value)
            if (event.target.value.trim()) setNameError(false)
          }}
          placeholder="Your name"
          autoComplete="name"
          maxLength={80}
          aria-invalid={nameError || undefined}
          aria-describedby="approver-hint"
        />
        <p id="approver-hint" className={cx('hint', nameError && 'hint-error')}>
          {nameError ? 'Enter your name before deciding: it is recorded on the payment.' : 'Recorded on each payment. Agent and system names are refused.'}
        </p>
      </div>

      {notice && (
        <div className="notice notice-success" role="status">
          <CheckIcon />
          <span>{notice}</span>
          <button type="button" className="notice-close" onClick={() => setNotice(null)} aria-label="Dismiss message">
            <XIcon />
          </button>
        </div>
      )}

      <section className="request-section" aria-labelledby="pending-title">
        <h3 id="pending-title">
          Waiting for you <span className="count">{pending.length}</span>
        </h3>
        {approvals === null ? (
          <div className="request-card is-loading" aria-busy="true">
            <span className="skeleton skeleton-line short" />
            <span className="skeleton skeleton-line" />
            <span className="skeleton skeleton-line medium" />
          </div>
        ) : pending.length === 0 ? (
          <div className="empty-approvals">
            <CheckIcon />
            <p>
              <strong>Nothing to approve.</strong> When an agent asks to pay a vendor invoice or rent, the request appears here with its purpose
              and amount. Nothing is paid until you approve it.
            </p>
          </div>
        ) : (
          <ul className="request-list">
            {pending.map((request) => (
              <RequestCard
                key={request.id}
                request={request}
                cash={cash}
                busy={deciding === request.id}
                locked={offline || (deciding !== null && deciding !== request.id)}
                isNew={fresh.has(request.id)}
                onDecide={decide}
                onSelectTicket={onSelectTicket}
              />
            ))}
          </ul>
        )}
      </section>

      {decided.length > 0 && (
        <section className="request-section" aria-labelledby="decided-title">
          <h3 id="decided-title">Recently decided</h3>
          <ul className="decided-list">
            {decided.map((request) => (
              <li key={request.id} className={cx('decided', `decided-${request.status}`)}>
                <span className="decided-status">
                  {request.status === 'paid' ? <CheckIcon /> : <XIcon />}
                  {request.status === 'paid' ? 'Paid' : request.status === 'rejected' ? 'Rejected' : request.status}
                </span>
                <span className="decided-title">
                  {requestTitle(request)} · {money(request.amount)}
                </span>
                <span className="decided-meta">
                  {request.status === 'paid' ? `Payment #${request.payment_id}, approved by ${request.decided_by}` : `By ${request.decided_by ?? 'a person'}`} ·{' '}
                  {shopDate(request.decided_at, 'short')}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {announcement}
      </p>
    </aside>
  )
}

function CashCard({ cash, delta }: { cash: Cash | null; delta: CashDelta | null }) {
  const shown = useTweenedNumber(cash?.balance ?? null)
  if (!cash) {
    return (
      <section className="cash-card is-loading" aria-label="Checking balance" aria-busy="true">
        <span className="skeleton skeleton-line short" />
        <span className="skeleton skeleton-line tall" />
        <span className="skeleton skeleton-line" />
      </section>
    )
  }
  const waiting = cash.waiting_for_approval_total
  const after = round2(cash.balance - waiting)
  const share = cash.balance > 0 ? Math.min(100, (waiting / cash.balance) * 100) : waiting > 0 ? 100 : 0
  return (
    <section className="cash-card" aria-labelledby="cash-title">
      <div className="cash-head">
        <h3 id="cash-title">Checking balance</h3>
        <span className="cash-asof">as of {shopDate(cash.as_of)}</span>
      </div>
      <p className="cash-balance" aria-live="polite" aria-atomic="true">
        <span aria-hidden="true">{money(shown ?? cash.balance)}</span>
        <span className="sr-only">Checking balance {money(cash.balance)}</span>
      </p>
      {delta && (
        <p key={delta.key} className={cx('cash-delta', delta.amount < 0 ? 'is-down' : 'is-up')}>
          {delta.cause === 'payment' ? 'Paid out' : 'Balance changed'} <strong>{signedMoney(delta.amount)}</strong>
        </p>
      )}
      <div
        className="cash-meter"
        role="img"
        aria-label={`${money(waiting)} of the ${money(cash.balance)} balance is requested by payments waiting for approval`}
      >
        <span className="cash-meter-fill" style={{ width: `${share}%` }} />
      </div>
      <dl className="cash-figures">
        <div>
          <dt>Waiting for approval</dt>
          <dd>{money(waiting)}</dd>
        </div>
        <div>
          <dt>Left if all are approved</dt>
          <dd className={cx(after < 0 && 'is-negative')}>{money(after)}</dd>
        </div>
      </dl>
      {after < 0 && (
        <p className="notice notice-error">
          <AlertIcon />
          <span>The requests waiting add up to {money(-after)} more than checking holds, so not all of them can be paid.</span>
        </p>
      )}
      <p className="cash-note">Only an approved payment lowers this balance. Sales revenue is never added.</p>
    </section>
  )
}

function RequestCard({ request, cash, busy, locked, isNew, onDecide, onSelectTicket }: {
  request: ApprovalRequest
  cash: Cash | null
  busy: boolean
  locked: boolean
  isNew: boolean
  onDecide: (request: ApprovalRequest, decision: 'approve' | 'reject') => Promise<Outcome>
  onSelectTicket: (id: number) => void
}) {
  const [mode, setMode] = useState<'idle' | 'approve' | 'reject'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const submitting = useRef(false) // a second click before the first finishes does nothing
  const confirmButton = useRef<HTMLButtonElement>(null)
  const startButton = useRef<HTMLButtonElement>(null)
  const refocusStart = useRef(false)
  const ticketId = request.ticket_id

  // Focus follows the step: the confirm button when it opens, back to Approve when it closes.
  useEffect(() => {
    if (mode !== 'idle') {
      confirmButton.current?.focus()
    } else if (refocusStart.current) {
      refocusStart.current = false
      startButton.current?.focus()
    }
  }, [mode])

  const cancel = () => {
    refocusStart.current = true
    setMode('idle')
  }
  const confirm = async () => {
    if (submitting.current || mode === 'idle') return
    submitting.current = true
    setError(null)
    const outcome = await onDecide(request, mode)
    submitting.current = false
    if (outcome.ignored) return
    if (outcome.ok) {
      setDone(mode === 'approve' ? 'Paid' : 'Rejected')
    } else {
      setError(outcome.message)
      cancel() // back to the Approve button, with the reason shown above it
    }
  }
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape' && !busy) cancel()
  }
  const after = cash ? round2(cash.balance - request.amount) : null

  return (
    <li className={cx('request-card', busy && 'is-busy', isNew && 'is-new', done && 'is-done')} aria-busy={busy || undefined}>
      <div className="request-top">
        <span className="request-kind">
          {request.kind === 'rent' ? <BuildingIcon /> : <DollarIcon />}
          {request.kind === 'rent' ? 'Rent payment' : 'Vendor purchase'}
        </span>
        {isNew && <span className="badge badge-new">New</span>}
        <span className="request-id">Request #{request.id}</span>
      </div>
      <p className="request-title">{requestTitle(request)}</p>
      <p className="request-amount">{money(request.amount)}</p>
      <p className="request-purpose">
        <span className="request-label">Purpose</span> {request.reason}
      </p>
      <dl className="request-facts">
        <div>
          <dt>Due</dt>
          <dd>{shopDate(request.due_date, 'short')}</dd>
        </div>
        <div>
          <dt>Asked by</dt>
          <dd>
            <AgentChip name={request.requested_by} />
          </dd>
        </div>
        {ticketId !== null && (
          <div>
            <dt>Ticket</dt>
            <dd>
              <button type="button" className="link-button" onClick={() => onSelectTicket(ticketId)}>
                #{ticketId}
              </button>
            </dd>
          </div>
        )}
      </dl>
      {error && (
        <p className="notice notice-error" role="alert">
          <AlertIcon />
          <span>{error}</span>
        </p>
      )}
      {done ? (
        <p className="request-done" role="status">
          <CheckIcon />
          {done}. Updating the balance…
        </p>
      ) : mode === 'idle' ? (
        <div className="request-actions">
          <button ref={startButton} type="button" className="btn btn-primary" disabled={locked} onClick={() => setMode('approve')}>
            <CheckIcon />
            Approve &amp; pay {money(request.amount)}
          </button>
          <button type="button" className="btn btn-ghost" disabled={locked} onClick={() => setMode('reject')}>
            Reject
          </button>
        </div>
      ) : (
        <div className={cx('request-confirm', mode === 'reject' && 'is-reject')} onKeyDown={onKeyDown}>
          <p>
            {mode === 'approve'
              ? `Pay ${money(request.amount)} from checking now?${after !== null && cash ? ` Checking goes from ${money(cash.balance)} to ${money(after)}.` : ''}`
              : 'Reject this request? Nothing is paid, and an agent can ask again later.'}
          </p>
          <div className="request-actions">
            <button
              ref={confirmButton}
              type="button"
              className={cx('btn', mode === 'approve' ? 'btn-primary' : 'btn-danger')}
              disabled={busy || locked}
              aria-busy={busy || undefined}
              onClick={confirm}
            >
              {busy ? <SpinnerIcon className="spin" /> : mode === 'approve' ? <CheckIcon /> : <XIcon />}
              {busy ? (mode === 'approve' ? 'Paying…' : 'Rejecting…') : mode === 'approve' ? 'Confirm payment' : 'Reject request'}
            </button>
            <button type="button" className="btn btn-ghost" disabled={busy} onClick={cancel}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {after !== null && after < 0 && !done && (
        <p className="request-warning">
          <AlertIcon />
          Checking holds {money(cash?.balance)}, less than this request. Approving it will be refused.
        </p>
      )}
    </li>
  )
}

// Counts a number up or down to its new value, so a payment visibly lowers the balance.
function useTweenedNumber(target: number | null, durationMs = 800): number | null {
  const [value, setValue] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    const start = from.current
    from.current = target
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (target === null || start === null || start === target || reduced) {
      setValue(target)
      return
    }
    let frame = 0
    const began = performance.now()
    const step = (now: number) => {
      const progress = Math.min(1, (now - began) / durationMs)
      setValue(start + (target - start) * (1 - Math.pow(1 - progress, 3)))
      if (progress < 1) frame = window.requestAnimationFrame(step)
    }
    frame = window.requestAnimationFrame(step)
    return () => window.cancelAnimationFrame(frame)
  }, [target, durationMs])
  return value
}

// The ids that appeared after the first load, highlighted as new for a while.
function useNewIds(ids: number[] | null, highlightMs = 20_000): ReadonlySet<number> {
  const seen = useRef<Set<number> | null>(null)
  const timers = useRef<number[]>([])
  const [fresh, setFresh] = useState<ReadonlySet<number>>(() => new Set())
  const key = ids === null ? null : ids.join(',')
  useEffect(() => {
    if (key === null) {
      seen.current = null
      return
    }
    const current = key ? key.split(',').map(Number) : []
    if (seen.current === null) {
      seen.current = new Set(current)
      return
    }
    const known = seen.current
    const added = current.filter((id) => !known.has(id))
    if (added.length === 0) return
    added.forEach((id) => known.add(id))
    setFresh((previous) => new Set([...previous, ...added]))
    timers.current.push(window.setTimeout(() => setFresh((previous) => new Set([...previous].filter((id) => !added.includes(id)))), highlightMs))
  }, [key, highlightMs])
  useEffect(() => {
    const pending = timers.current
    return () => pending.forEach((timer) => window.clearTimeout(timer))
  }, [])
  return fresh
}
