import { t } from './localization/index.ts'
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
  get interrupt_now() { return t("attention.ee788f7ad4") },
  get next_breakpoint() { return t("attention.7c2a293429") },
  get inbox() { return t("attention.a578cca52f") },
  get digest() { return t("attention.0084dd0893") },
}

export const CHANNEL_HINTS: Record<AttentionChannel, string> = {
  get interrupt_now() { return t("attention.1b5b80b9d3") },
  get next_breakpoint() { return t("attention.257b94f68b") },
  get inbox() { return t("attention.907f894f57") },
  get digest() { return t("attention.8261372e90") },
}

export const SUPPRESSED_LABELS: Record<string, string> = {
  get budget_exhausted() { return t("attention.19ac445475") },
  get low_confidence() { return t("attention.b57ce29a04") },
  get superseded_run() { return t("attention.5e554f7580") },
}

export function suppressedLabel(reason: string): string {
  return SUPPRESSED_LABELS[reason] || (reason.startsWith('rule:') ? t("attention.74683c96d7", { value1: reason.slice(5).replaceAll('_', ' ') }) : t("attention.3c681c771a", { value1: reason }))
}

/** The fan-out headline, or an honest statement that it is not measurable yet. */
export function fanoutHeadline(fanout: FanOut): string {
  if (fanout.status !== 'ok' || !fanout.sustainable_agents) {
    return t("attention.d516a46ac4", { value1: fanout.samples, value2: fanout.required, value3: fanout.attended_now })
  }
  return t("attention.47b00ccf01", { value1: fanout.sustainable_agents, value2: fanout.attended_now })
}

export function budgetLine(budget: AttentionBudget): string {
  return t("attention.7037257d9d", { value1: budget.used, value2: budget.daily_budget, value3: budget.burst_used, value4: budget.hourly_cap })
}

/** What the counter does not count, drawn as visible text under it. A counter of
 *  interrupts beside a list of waiting prompts reads as though each prompt spends one,
 *  and a hover title cannot answer that on a phone. Only `interrupt_now` takes a slot:
 *  cheap-blocking work routes to the next breakpoint before the budget is consulted,
 *  and a session's `awaiting` status belongs to status detection, which no budget
 *  state reaches. */
export const budgetNote = () => (t("attention.29aec2ca90"))

export const fetchAttentionInbox = () => api<AttentionInbox>('GET', '/api/attention/inbox')

export const sendAttentionFeedback = (itemId: string, action: 'acted' | 'dismissed') =>
  api<AttentionItem>('POST', `/api/attention/items/${itemId}/feedback`, { action })

export const decideAttentionRule = (incidentClass: string, channel: string, accept: boolean) =>
  api<{rules:AttentionRule[]}>('POST', '/api/attention/rules', { incident_class: incidentClass, channel, accept })
