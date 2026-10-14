// Status badges and agent chips. Every badge pairs an icon and words with its color, so no state relies on color.
import type { ComponentType } from 'react'
import { agentIdentity, agentStyle, isAgentName } from '../agents.ts'
import { cx, statusMeta, type StatusTone } from '../format.ts'
import { CheckIcon, CircleIcon, ClockIcon, FlagIcon, SpinnerIcon, TruckIcon, UserIcon, XIcon, ChatIcon, type IconProps } from '../icons.tsx'
import { RUN_LABELS, type RunStatus } from '../runs.ts'

const STATUS_ICONS: Record<StatusTone, ComponentType<IconProps>> = {
  open: CircleIcon,
  progress: SpinnerIcon,
  approval: ClockIcon,
  vendor: TruckIcon,
  customer: ChatIcon,
  human: UserIcon,
  resolved: CheckIcon,
}

// A ticket's status as the backend stores it. Only "resolved" gets the solid pink Resolved badge.
export function StatusBadge({ status }: { status: string }) {
  const meta = statusMeta(status)
  const Icon = STATUS_ICONS[meta.tone]
  return (
    <span className={cx('badge', `badge-${meta.tone}`)} title={meta.hint || undefined}>
      <Icon />
      {meta.label}
    </span>
  )
}

export function RunBadge({ status }: { status: RunStatus }) {
  if (status === 'running') {
    return (
      <span className="badge badge-running">
        <span className="pulse-dot" aria-hidden="true" />
        {RUN_LABELS.running}
      </span>
    )
  }
  if (status === 'finished') {
    return (
      <span className="badge badge-finished">
        <FlagIcon />
        {RUN_LABELS.finished}
      </span>
    )
  }
  return (
    <span className="badge badge-failed">
      <XIcon />
      {RUN_LABELS[status]}
    </span>
  )
}

export function AgentChip({ name }: { name: string | null | undefined }) {
  if (!isAgentName(name)) return <span className="agent-chip agent-chip-other">{name || 'Someone'}</span>
  const { Icon } = agentIdentity(name)
  return (
    <span className="agent-chip" style={agentStyle(name)}>
      <Icon />
      {name}
    </span>
  )
}

export function AgentAvatar({ name, large = false }: { name: string; large?: boolean }) {
  if (!isAgentName(name)) return null
  const { Icon } = agentIdentity(name)
  return (
    <span className={cx('agent-avatar', large && 'agent-avatar-large')} style={agentStyle(name)} aria-hidden="true">
      <Icon />
    </span>
  )
}
