// The top bar (shop date, database, agents, connection, and the checking balance on every screen size) and the
// banners for the sandbox and for an unreachable backend.
import { API_BASE, WORKING_DATABASE, type Cash, type Health } from '../api.ts'
import { cx, money, modelLabel, plural, shopDate } from '../format.ts'
import { AlertIcon, CalendarIcon, DatabaseIcon, FlagIcon, FlaskIcon, RefreshIcon, SparkIcon } from '../icons.tsx'
import type { Connection } from '../useDesk.ts'

type HeaderProps = { health: Health | null; cash: Cash | null; pendingCount: number; connection: Connection }

export function Header({ health, cash, pendingCount, connection }: HeaderProps) {
  const isolated = health !== null && health.database !== WORKING_DATABASE
  const simulated = health?.agents?.simulated ?? false
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          CC
        </span>
        <span className="brand-text">
          <span className="brand-name">Campus Customs</span>
          <span className="brand-sub">Operations desk</span>
        </span>
      </div>
      <ul className="topbar-chips" aria-label="Backend status">
        <li className="chip">
          <CalendarIcon />
          <span>
            <span className="chip-label">Shop date</span> {health ? shopDate(health.date_today) : '…'}
          </span>
        </li>
        <li className={cx('chip', isolated && 'chip-sandbox')}>
          <DatabaseIcon />
          <span>{health ? (isolated ? 'Isolated test copy' : 'Working database') : '…'}</span>
        </li>
        <li className={cx('chip', simulated ? 'chip-sandbox' : 'chip-agents')}>
          {simulated ? <FlaskIcon /> : <SparkIcon />}
          <span>{health ? (simulated ? 'Simulated agents' : `${modelLabel(health.agents?.model)} agents`) : '…'}</span>
        </li>
        {health?.run_label && (
          <li className="chip chip-agents">
            <FlagIcon />
            <span>{health.run_label}</span>
          </li>
        )}
        <li className={cx('chip', 'chip-connection', `is-${connection}`)}>
          <span className="dot" aria-hidden="true" />
          <span>{connection === 'online' ? 'Live' : connection === 'offline' ? 'Offline' : 'Connecting…'}</span>
        </li>
      </ul>
      <a className="cash-pill" href="#approvals">
        <span className="cash-pill-label">Checking</span>
        <span className="cash-pill-value">{cash ? money(cash.balance) : '—'}</span>
        <span className={cx('cash-pill-count', pendingCount > 0 && 'has-pending')}>
          {pendingCount > 0 ? `${plural(pendingCount, 'request')} to approve` : 'Nothing to approve'}
        </span>
      </a>
    </header>
  )
}

export function SandboxBanner({ health }: { health: Health | null }) {
  if (!health) return null
  const simulated = health.agents?.simulated ?? false
  const isolated = health.database !== WORKING_DATABASE
  if (!simulated && !isolated) return null
  return (
    <div className="sandbox-banner" role="note" aria-label={simulated ? 'Simulated agents' : 'Isolated test copy'}>
      <FlaskIcon />
      <p>
        <strong>{simulated ? 'Simulated agents' : 'Isolated test copy'}</strong>{' '}
        {simulated
          ? 'Scripted stand-ins choose every agent step; no Claude calls. The backend, MCP tools, shop rules, payments and database changes are real, on an isolated copy.'
          : 'Real Claude agents on an isolated copy of the database. The working database and its audit trail are untouched.'}{' '}
        <code>{health.database}</code>
      </p>
    </div>
  )
}

export function OfflineBanner({ message, onRetry }: { message: string | null; onRetry: () => void }) {
  return (
    <div className="offline-banner" role="alert">
      <AlertIcon />
      <div>
        <strong>Can't reach the backend at {API_BASE}.</strong> {message && message !== `Can't reach the backend at ${API_BASE}.` ? message : ''}{' '}
        Start it from the project folder with <code>cd backend && ../.venv/bin/uvicorn main:app --reload --port 8000</code>. The desk
        keeps retrying.
      </div>
      <button type="button" className="btn btn-secondary btn-small" onClick={onRetry}>
        <RefreshIcon />
        Retry now
      </button>
    </div>
  )
}
