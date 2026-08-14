// Ranked attention: the shapes the daemon publishes, and the pure helpers the
// Alerts surface renders from. Kept JSX-free so the ordering and labelling rules
// stay unit-testable under plain `node --experimental-strip-types`.
//
// The four channels are never merged in the UI either. Answering a permission
// prompt costs seconds; discovering the plan is wrong costs an hour, and one
// list sorted by score would hide that difference behind a number.
import { api } from './api.ts'

export type AttentionChannel = 'interrupt_now' | 'next_breakpoint' | 'inbox' | 'digest'

export type AttentionItem = {
  id:string;incident_key:string;project_id?:string;session_id?:string;agent_run_id?:string
  incident_class:string;kinds:string[];title:string;summary:string;action?:string
  channel:AttentionChannel;cost_to_resolve:string;score:number;confidence:number
  evidence:Record<string,unknown>[];contributions:number
  narration?:string;narration_status:string;suppressed_reason?:string;state:string
  delivered_at?:number;created_at:number;updated_at:number
}

export type AttentionBudget = {
  day:string;daily_budget:number;used:number;remaining:number
  hourly_cap:number;burst_used:number;burst_remaining:number
}

export type FanOut = {
  status:'ok'|'insufficient_samples';samples:number;required:number
  interaction_seconds:number|null;neglect_seconds:number|null
  sustainable_agents:number|null;attended_now:number
}

export type AttentionRule = {
  incident_class:string;channel:string;dismissed:number;total:number
  dismiss_rate:number;statement:string;state:'proposed'|'accepted';expires_at:number|null
}

export type AttentionInbox = {
  generated_at:number
  channels:Record<AttentionChannel,AttentionItem[]>
  suppressed:Record<string,number>;suppressed_total:number
  budget:AttentionBudget;fanout:FanOut
  resumption_lag:{samples:number;mean_seconds:number|null;max_seconds:number|null}
  rules:AttentionRule[]
  delivery:{push:boolean;surface:string}
}

/** Display order and copy per channel. Cheap-blocking work never leads. */
export const CHANNEL_ORDER: AttentionChannel[] = ['interrupt_now', 'next_breakpoint', 'inbox', 'digest']

export const CHANNEL_LABELS: Record<AttentionChannel, string> = {
  interrupt_now: 'Interrupt now',
  next_breakpoint: 'At your next breakpoint',
  inbox: 'Inbox',
  digest: 'Digest only',
}

export const CHANNEL_HINTS: Record<AttentionChannel, string> = {
  interrupt_now: 'Worsening, actionable, and confident enough to spend a slot of today’s budget.',
  next_breakpoint: 'Waits until you finish what you are doing. Cheap to resolve, so it never spends budget.',
  inbox: 'Schedulable. Read it when you choose to.',
  digest: 'Kept as a record. No action implied.',
}

export const SUPPRESSED_LABELS: Record<string, string> = {
  budget_exhausted: 'held back: today’s interrupt budget is spent',
  low_confidence: 'held back: not confident enough to interrupt',
  superseded_run: 'held back: the conversation was replaced',
}

export function suppressedLabel(reason: string): string {
  return SUPPRESSED_LABELS[reason] || (reason.startsWith('rule:') ? `held back by your rule for ${reason.slice(5).replaceAll('_', ' ')}` : `held back: ${reason}`)
}

/** The fan-out headline, or an honest statement that it is not measurable yet. */
export function fanoutHeadline(fanout: FanOut): string {
  if (fanout.status !== 'ok' || !fanout.sustainable_agents) {
    return `Sustainable fan-out unknown: ${fanout.samples} of ${fanout.required} interaction samples so far. ${fanout.attended_now} agent sessions live.`
  }
  return `You are sustainable at about ${fanout.sustainable_agents} attended agents right now. ${fanout.attended_now} live.`
}

export function budgetLine(budget: AttentionBudget): string {
  return `${budget.used}/${budget.daily_budget} interrupts today · ${budget.burst_used}/${budget.hourly_cap} this hour`
}

export const fetchAttentionInbox = () => api<AttentionInbox>('GET', '/api/attention/inbox')

export const sendAttentionFeedback = (itemId: string, action: 'acted' | 'dismissed') =>
  api<AttentionItem>('POST', `/api/attention/items/${itemId}/feedback`, { action })

export const decideAttentionRule = (incidentClass: string, channel: string, accept: boolean) =>
  api<{rules:AttentionRule[]}>('POST', '/api/attention/rules', { incident_class: incidentClass, channel, accept })
