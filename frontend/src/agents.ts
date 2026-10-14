// The five agents' identities: a color, an icon and a one-line duty each, used everywhere an agent appears
// (the roster, the activity feed, the run summary and the approval cards).
import type { ComponentType, CSSProperties } from 'react'
import type { AgentName } from './api.ts'
import { BoxIcon, BuildingIcon, ChatIcon, CoinsIcon, CrownIcon, type IconProps } from './icons.tsx'

export type AgentIdentity = {
  name: AgentName
  duty: string
  color: string
  Icon: ComponentType<IconProps>
}

export const AGENTS: readonly AgentIdentity[] = [
  { name: 'Boss', duty: 'Reads the ticket, delegates and decides', color: '#ff4fa3', Icon: CrownIcon },
  { name: 'Inventory', duty: 'Stock, vendors and restocking', color: '#ff8a5c', Icon: BoxIcon },
  { name: 'Accounting', duty: 'Cash, invoices and payment requests', color: '#b69cff', Icon: CoinsIcon },
  { name: 'Facilities', duty: 'Leases and rent', color: '#ffc85c', Icon: BuildingIcon },
  { name: 'Customer Service', duty: 'Drafts messages; never sends them', color: '#ffb3d9', Icon: ChatIcon },
]

const BY_NAME = new Map<string, AgentIdentity>(AGENTS.map((agent) => [agent.name, agent]))
const OTHER_COLOR = '#b3a9b9'

export function isAgentName(value: unknown): value is AgentName {
  return typeof value === 'string' && BY_NAME.has(value)
}

export function agentIdentity(name: AgentName): AgentIdentity {
  return BY_NAME.get(name) ?? AGENTS[0]
}

// Sets --agent, which the stylesheet uses for the agent's borders, tints and text.
export function agentStyle(name: unknown, extra?: Record<string, string | number>): CSSProperties {
  const color = isAgentName(name) ? agentIdentity(name).color : OTHER_COLOR
  return { '--agent': color, ...extra } as CSSProperties
}
