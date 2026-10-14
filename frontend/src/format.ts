// Formatting, labels and plain-language descriptions of the audit trail's records.
import type { AgentReport, ApprovalRequest, AuditEvent, Ticket } from './api.ts'

// --- Values ------------------------------------------------------------------------------------------------

export type Rec = Record<string, unknown>

export const rec = (value: unknown): Rec | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Rec) : null
export const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])
export const text = (value: unknown): string => (typeof value === 'string' ? value : value == null ? '' : String(value))
const num = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)

export const cx = (...names: (string | false | null | undefined)[]): string => names.filter(Boolean).join(' ')
export const shorten = (value: string, max = 160): string => (value.length > max ? `${value.slice(0, max - 1).trimEnd()}…` : value)
export const plural = (count: number, one: string, many = `${one}s`): string => `${count} ${count === 1 ? one : many}`
export const round2 = (value: number): number => Math.round(value * 100) / 100
// A run's stop reason as a sentence, without the backend's leading "stopped:".
export const reasonSentence = (value: string): string => {
  const reason = value.replace(/^stopped:\s*/i, '').trim()
  return /[.!?…]$/.test(reason) ? reason : `${reason}.`
}

const MONEY = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })
export const money = (value: unknown): string => {
  const amount = num(value)
  return amount === null ? '—' : MONEY.format(amount)
}
export const signedMoney = (value: number): string => `${value < 0 ? '−' : '+'}${MONEY.format(Math.abs(value))}`
export const tokens = (value: number): string => value.toLocaleString('en-US')

// A shop date (YYYY-MM-DD) is a calendar day, so build it in local time to avoid a time zone shift.
export function shopDate(iso: string | null | undefined, style: 'long' | 'short' = 'long'): string {
  if (!iso) return '—'
  const [year, month, day] = iso.slice(0, 10).split('-').map(Number)
  if (!year || !month || !day) return iso
  const options: Intl.DateTimeFormatOptions =
    style === 'long' ? { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' } : { month: 'short', day: 'numeric' }
  return new Date(year, month - 1, day).toLocaleDateString('en-US', options)
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit' })
}

// --- Tickets, statuses and agents ----------------------------------------------------------------------------

const TICKET_TYPES: Record<string, string> = {
  customer_order: 'Customer order',
  rent_notice: 'Rent notice',
  price_override: 'Price override',
}
export const ticketType = (type: string): string => TICKET_TYPES[type] ?? type.replaceAll('_', ' ')

export function ticketItem(ticket: Ticket): string | null {
  if (ticket.sku) return `${ticket.qty ?? 1} × ${ticket.sku}${ticket.size ? `, size ${ticket.size}` : ''}`
  if (ticket.lease_id) return `Lease ${ticket.lease_id}`
  if (ticket.invoice_id) return `Invoice ${ticket.invoice_id}`
  return null
}

export type StatusTone = 'open' | 'progress' | 'approval' | 'vendor' | 'customer' | 'human' | 'resolved'

// The ticket statuses the Boss can record (mcp_server/shop.py). Only "resolved" means the shop's internal work is complete.
const STATUSES: Record<string, { label: string; tone: StatusTone; hint: string }> = {
  open: { label: 'Open', tone: 'open', hint: 'No decision recorded yet.' },
  in_progress: { label: 'In progress', tone: 'progress', hint: 'Work has started.' },
  waiting_on_approval: { label: 'Waiting for approval', tone: 'approval', hint: 'A payment request waits for a person.' },
  waiting_on_vendor: { label: 'Waiting for vendor', tone: 'vendor', hint: 'Awaiting vendor action or restocking. Nothing has shipped.' },
  waiting_on_customer: { label: 'Waiting for customer', tone: 'customer', hint: 'The customer must answer an offer.' },
  needs_human: { label: 'Needs a person', tone: 'human', hint: "A person must decide something the rules and data don't settle." },
  resolved: { label: 'Resolved', tone: 'resolved', hint: 'The shop’s own work on this ticket is complete.' },
}

export function statusMeta(status: string): { label: string; tone: StatusTone; hint: string } {
  return STATUSES[status] ?? { label: status.replaceAll('_', ' ') || 'Unknown', tone: 'open', hint: '' }
}

export const isSimulatedModel = (model: string | null | undefined): boolean => Boolean(model && /simulated|scripted/i.test(model))

// "claude-code:claude-opus-5-5" → "Claude Opus 5.5"; scripted stand-ins are labeled as simulated.
export function modelLabel(model: string | null | undefined): string {
  if (!model) return 'Agents'
  if (isSimulatedModel(model)) return 'Simulated agents (scripted, no Claude)'
  const match = /claude-(opus|sonnet|haiku|fable)-(\d+)-(\d+)/i.exec(model)
  return match ? `Claude ${match[1][0].toUpperCase()}${match[1].slice(1)} ${match[2]}.${match[3]}` : model
}

export function requestTitle(request: ApprovalRequest): string {
  if (request.kind === 'invoice') return `Pay vendor invoice ${request.ref_id}`
  if (request.kind === 'rent') return `Pay rent for lease ${request.ref_id}`
  return `${request.kind} ${request.ref_id}`
}

export function toReport(value: unknown): AgentReport | null {
  const report = rec(value)
  if (!report || typeof report.summary !== 'string') return null
  const strings = (field: unknown) => list(field).filter((item): item is string => typeof item === 'string')
  return {
    summary: report.summary,
    facts: strings(report.facts),
    actions_taken: strings(report.actions_taken),
    needs_human: strings(report.needs_human),
  }
}

// --- Tool calls ----------------------------------------------------------------------------------------------

const ACTOR_FIELDS = new Set(['requested_by', 'author', 'agent']) // filled in by the run, not chosen by the model
const LONG_FIELDS = new Set(['reason', 'body', 'note', 'request', 'subject'])

// A tool call's short arguments, e.g. "sku: CC-TEE-WHITE · size: S".
export function toolArgs(input: unknown): string {
  const args = rec(input)
  if (!args) return ''
  return Object.entries(args)
    .filter(([key, value]) => !ACTOR_FIELDS.has(key) && !LONG_FIELDS.has(key) && value !== null && value !== undefined && value !== '')
    .map(([key, value]) => `${key}: ${typeof value === 'string' ? shorten(value, 40) : JSON.stringify(value)}`)
    .join(' · ')
}

// The words an agent wrote in a tool call (a reason, a draft, a note), shown as its message.
export function toolMessage(tool: string | undefined, input: unknown): string | null {
  const args = rec(input)
  if (!args) return null
  switch (tool) {
    case 'request_payment_approval':
      return text(args.reason) || null
    case 'save_draft': {
      const subject = text(args.subject)
      return [subject && `“${subject}”`, text(args.body)].filter(Boolean).join(' ') || null
    }
    case 'update_ticket':
    case 'request_vendor_shipment':
      return text(args.note) || null
    default:
      return null
  }
}

const days = (value: unknown): string => {
  const count = num(value)
  if (count === null) return ''
  if (count === 0) return 'today'
  return count > 0 ? `in ${plural(count, 'day')}` : `${plural(-count, 'day')} ago`
}

// One line on what a tool returned, from the actual result.
export function summarizeResult(tool: string | undefined, result: unknown): string {
  const data = rec(result)
  if (!data) return typeof result === 'string' ? shorten(result, 200) : ''
  if (typeof data.refused === 'string') return data.refused
  if (data.found === false) return text(data.message) || 'Not found.'
  switch (tool) {
    case 'get_shop_date':
      return `Shop date ${shopDate(text(data.date_today))}`
    case 'list_tickets':
      return plural(list(data.tickets).length, 'ticket')
    case 'get_ticket': {
      const ticket = rec(data.ticket)
      const requests = list(data.approval_requests).length
      const drafts = list(data.drafts).length
      return ticket
        ? `Ticket ${text(ticket.id)}: ${statusMeta(text(ticket.status)).label} · ${plural(requests, 'payment request')} · ${plural(drafts, 'draft')}`
        : ''
    }
    case 'get_inventory':
      return `${text(data.qty)} on hand · ${text(data.location)}`
    case 'get_pricing':
      return `Unit cost ${money(data.unit_cost)} · list price ${money(data.list_price)} · margin ${money(data.margin_per_unit)} each`
    case 'evaluate_discount':
      return `${text(data.discount_percent)}% off: ${money(data.unit_price)} each · revenue ${money(data.revenue)} · margin ${money(data.margin)}${data.below_cost ? ' · below cost' : ''}`
    case 'list_vendors': {
      const vendors = list(data.vendors).map(rec)
      return `${plural(vendors.length, 'vendor')} · ${vendors.filter((vendor) => vendor?.can_ship).length} can ship`
    }
    case 'get_vendor': {
      const vendor = rec(data.vendor)
      return vendor
        ? `${text(vendor.name)}: ${vendor.can_ship ? 'can ship' : "can't ship while an invoice is open"} · ${text(vendor.lead_days)}-day lead time`
        : ''
    }
    case 'get_invoice_details': {
      const invoice = rec(data.invoice)
      const vendor = rec(data.vendor)
      if (!invoice) return ''
      const overdue = data.overdue ? `, ${plural(num(data.days_overdue) ?? 0, 'day')} overdue` : ''
      return `Invoice ${text(invoice.id)}: ${money(invoice.amount)}, ${text(invoice.status)}${overdue}${vendor ? ` · ${text(vendor.name)}` : ''}`
    }
    case 'get_lease_details': {
      const lease = rec(data.lease)
      return lease ? `${money(lease.monthly_rent)} rent to ${text(lease.landlord)}, due ${shopDate(text(lease.next_due), 'short')} (${days(data.days_until_due)})` : ''
    }
    case 'get_cash_balance': {
      const checking = list(data.accounts).map(rec).find((account) => account?.name === 'checking')
      return `Checking ${money(checking?.balance)} · ${money(data.waiting_for_approval_total)} waiting for approval`
    }
    case 'list_approval_requests': {
      const requests = list(data.approval_requests).map(rec)
      return requests.length
        ? `${plural(requests.length, 'payment request')}: ${requests.map((request) => `#${text(request?.id)} ${text(request?.status)}`).join(', ')}`
        : 'No payment requests yet'
    }
    case 'list_payments': {
      const payments = list(data.payments)
      return payments.length ? plural(payments.length, 'payment') : 'No payments yet'
    }
    case 'list_board': {
      const drafts = list(data.drafts)
      return drafts.length ? `${plural(drafts.length, 'draft')} on the board, none sent` : 'No drafts yet'
    }
    case 'request_payment_approval':
      return `Request #${text(data.approval_id)} for ${money(data.amount)} is ${text(data.status)}: it waits for a person`
    case 'request_vendor_shipment':
      return `Draft #${text(data.draft_id)} to ${text(data.vendor)}: ${text(data.qty)} × ${text(data.sku)} ${text(data.size)}, estimated arrival ${shopDate(text(data.expected_arrival), 'short')} if fulfilled. Not sent.`
    case 'save_draft':
      return `Draft #${text(data.draft_id)} for ${text(data.recipient)} saved on the board. Not sent.`
    case 'update_ticket':
      return `${statusMeta(text(data.previous_status)).label} → ${statusMeta(text(data.status)).label}`
    default:
      return text(data.message)
  }
}

// What a step changed or found that matters in a summary; plain lookups return null.
export function toolHighlight(event: AuditEvent): string | null {
  const data = rec(event.result)
  const refusal = data && typeof data.refused === 'string' ? shorten(data.refused, 150) : null
  switch (event.tool) {
    case 'request_payment_approval':
      return refusal ? `Payment request refused: ${refusal}` : data ? `Asked a person to approve request #${text(data.approval_id)} (${money(data.amount)})` : null
    case 'request_vendor_shipment':
      return refusal
        ? `Shipment request refused: ${refusal}`
        : data
          ? `Saved draft shipment request #${text(data.draft_id)} to ${text(data.vendor)}, estimated arrival ${shopDate(text(data.expected_arrival), 'short')} (not sent)`
          : null
    case 'save_draft':
      return data && !refusal ? `Saved draft #${text(data.draft_id)} for ${text(data.recipient)} (not sent)` : refusal && `Draft refused: ${refusal}`
    case 'update_ticket':
      return data && !refusal ? `Set the ticket to “${statusMeta(text(data.status)).label}”` : refusal && `Ticket update refused: ${refusal}`
    case 'evaluate_discount':
      return data && !refusal ? `Priced ${text(data.discount_percent)}% off: ${money(data.unit_price)} each, margin ${money(data.margin)}` : null
    default:
      return event.outcome === 'error' ? `${text(event.tool)} failed: ${shorten(text(event.error), 150)}` : null
  }
}

// --- Audit records as feed entries -----------------------------------------------------------------------------

export type EventKind = 'start' | 'delegate' | 'tool' | 'report' | 'handoff' | 'end' | 'fail' | 'other'
export type EventTone = 'ok' | 'refused' | 'error' | 'info'

export type EventView = {
  kind: EventKind
  title: string
  target: string | null // the other agent in a delegation or handoff
  message: string | null // words an agent wrote: a request, a report, a reason or a draft
  detail: string | null // what came back
  list: string[]
  tone: EventTone
}

const toneOf = (event: AuditEvent): EventTone => (event.outcome === 'refused' ? 'refused' : event.outcome === 'error' ? 'error' : 'ok')

export function describeEvent(event: AuditEvent): EventView {
  const input = rec(event.input)
  const base: EventView = { kind: 'other', title: event.action.replaceAll('_', ' '), target: null, message: null, detail: null, list: [], tone: 'info' }
  switch (event.action) {
    case 'run_started': {
      const note = /Note from the operator: ([\s\S]*)$/.exec(text(input?.request))?.[1]?.trim()
      return {
        ...base,
        kind: 'start',
        title: `started the run${event.trigger ? ` from the ${event.trigger}` : ''}`,
        message: note ? `Note from the operator: ${note}` : null,
        detail: modelLabel(event.model),
      }
    }
    case 'delegation_started':
      return { ...base, kind: 'delegate', title: 'asked', target: text(input?.agent) || null, message: text(input?.request) || null }
    case 'tool':
      return {
        ...base,
        kind: 'tool',
        title: 'used',
        message: toolMessage(event.tool, event.input),
        detail: event.outcome === 'error' ? text(event.error) : summarizeResult(event.tool, event.result),
        tone: toneOf(event),
      }
    case 'final_report': {
      const report = toReport(event.input)
      const lead = (event.depth ?? 0) === 0
      return {
        ...base,
        kind: 'report',
        title: lead ? 'handed in the final report' : 'reported back',
        message: report?.summary ?? null,
        list: lead ? (report?.needs_human ?? []) : [],
        tone: 'ok',
      }
    }
    case 'delegation': {
      const result = rec(event.result)
      const refused = result && typeof result.refused === 'string' ? result.refused : null
      return {
        ...base,
        kind: 'handoff',
        title: refused ? "couldn't ask" : event.outcome === 'error' ? 'got no report from' : 'received the report from',
        target: text(input?.agent) || null,
        detail: refused ?? (event.outcome === 'error' ? text(event.error) : null),
        tone: toneOf(event),
      }
    }
    case 'run_finished':
      return {
        ...base,
        kind: event.outcome === 'ok' ? 'end' : 'fail',
        title: event.outcome === 'ok' ? 'Run finished' : 'Run stopped',
        detail: event.outcome === 'ok' ? 'The Boss handed in its final report.' : text(event.error),
        tone: event.outcome === 'ok' ? 'ok' : 'error',
      }
    case 'run_failed':
      return { ...base, kind: 'fail', title: 'Run failed', detail: text(event.error), tone: 'error' }
    default:
      return base
  }
}
