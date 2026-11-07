import { locale as uiLocale } from './localization/index.ts'
import { t } from './localization/index.ts'
import { serverNow } from './serverClock.ts'

// Types and pure presentation helpers for the Schedule tab.
//
// Everything here is a function of data the daemon already sent. The one thing the
// browser deliberately does NOT do is compute a next fire time: cron plus a timezone
// plus daylight saving is exactly the arithmetic that must have one implementation,
// and it lives in `schedules.py`. The editor asks `/api/schedules/preview` instead,
// so what the user is shown before saving is produced by the same code that will
// actually fire it.

export type ScheduleTrigger = 'cron' | 'interval' | 'once'
export type ScheduleOverlap = 'skip' | 'allow'

/** What the trigger is attached to. */
export type ScheduleAction = 'spawn' | 'resume'

/** Which conversation a resume reopens, and how the answer is allowed to move.
 *
 *  Three, because "resume this session later" has three genuinely different answers
 *  and picking one for the author would be picking wrong. See `TARGET_KIND_COPY`. */
export type ScheduleTargetKind = 'run' | 'latest_of_session' | 'fork_point'

/** What the daemon says a resume schedule currently points at.
 *
 *  Resolved on read, never stored beside the id: a label kept with the schedule would
 *  go on naming a conversation that has since been deleted, and `missing` is the
 *  honest answer to that. `resolved_*` differs from the pinned row only for a rolling
 *  continuation, which is the one kind whose target moves. */
export type ScheduleTarget = {
  run_id: string
  missing: boolean
  backend?: string
  name?: string
  resolved_run_id?: string
  resolved_name?: string
  resolved_at?: number | null
  context_pct?: number | null
}

export type ScheduleFollowUp = {
  body: string
  delay_seconds: number
  armed: boolean
}

/** What a resume does when its conversation is already open at fire time. */
export type ScheduleWhenLive = 'skip' | 'deliver'

/** Who wrote a schedule. Every row from before agents could write one is a person's. */
export type ScheduleAuthorKind = 'user' | 'agent'

/** How an agent's schedule came to be armed, or why it is not. Empty for a person's. */
export type ScheduleApproval = '' | 'granted' | 'pending' | 'approved' | 'dismissed'

export type ScheduleRun = {
  id: string
  schedule_id: string
  project_id: string
  fire_key: string
  due_at: number | null
  started_at: number
  finished_at: number | null
  /** `delivered`: a resume found its conversation already open and queued the prompt into it. */
  outcome: 'started' | 'spawned' | 'delivered' | 'skipped' | 'failed' | 'missed'
  reason: string
  session_id: string | null
  origin: 'timer' | 'manual'
  queued_messages: number
}

export type Schedule = {
  id: string
  project_id: string
  project_name: string
  label: string
  enabled: boolean
  trigger_kind: ScheduleTrigger
  cron: string
  interval_seconds: number | null
  run_at: number | null
  timezone: string
  catch_up: boolean
  overlap: ScheduleOverlap
  backend: string
  profile_id: string
  cwd: string
  session_name: string
  prompt: string
  follow_ups: ScheduleFollowUp[]
  daily_run_cap: number
  next_fire_at: number | null
  last_fire_at: number | null
  last_session_id: string | null
  last_outcome: string
  last_reason: string
  disabled_reason: string
  /** Live permission answer, not a stored flag. Empty means it can fire. */
  blocked: '' | 'project_missing' | 'automation_disabled' | 'install_disabled'
  runs: ScheduleRun[]
  revision: number
  created_at: number
  updated_at: number
  action: ScheduleAction
  target_run_id: string
  target_kind: ScheduleTargetKind
  target_cut_message_id: string
  target_cut_mode: '' | 'before' | 'after'
  context_ceiling_pct: number
  /** Null on a spawn, which has nothing to point at. */
  target: ScheduleTarget | null
  /** A spawn's model in its CLI's own spelling; empty leaves it to the profile. */
  model: string
  /** When it stops being armed, as epoch seconds. Null for no end date. */
  expires_at: number | null
  when_live: ScheduleWhenLive
  author_kind: ScheduleAuthorKind
  author_session_id: string
  author_run_id: string
  author_name: string
  /** The agent's own account of why, shown to whoever approves it. */
  request_reason: string
  approval_state: ScheduleApproval
  /** Why an agent's schedule needs a person, in the daemon's words. */
  approval_reason: string
  decided_by: string
  /** The most fires in any 24 hours, measured by the daemon. 97 means "more than 96". */
  fires_per_day: number
}

export type ScheduleStatus = {
  enabled: boolean
  total: number
  armed: number
  live_sessions: number
  max_concurrent: number
}

export type ScheduleDraft = {
  label: string
  prompt: string
  trigger_kind: ScheduleTrigger
  cron: string
  /** Minutes in the editor; seconds on the wire. A field measured in seconds
   *  invites `3600` typed as `360`. */
  interval_minutes: number
  run_at_local: string
  timezone: string
  catch_up: boolean
  overlap: ScheduleOverlap
  backend: string
  profile_id: string
  session_name: string
  daily_run_cap: number
  follow_ups: string[]
  action: ScheduleAction
  target_run_id: string
  target_kind: ScheduleTargetKind
  target_cut_message_id: string
  target_cut_mode: '' | 'before' | 'after'
  /** 0-1. Only a rolling continuation reads it. */
  context_ceiling_pct: number
  /** Display only, never sent: what the target conversation is called, so the editor
   *  can name what it is about to reopen without a second fetch. */
  target_label: string
  target_backend: string
  /** A spawn's model. Round-tripped so editing an agent's schedule cannot drop it. */
  model: string
  /** A resume's answer to "the conversation is already open". */
  when_live: ScheduleWhenLive
  /** `datetime-local` text, or '' for no end date. */
  expires_local: string
}

/** A named cron expression, and the piece of the syntax it demonstrates.
 *
 *  The list is short and each entry earns its place twice: it is a rhythm people
 *  actually want, and between them they cover every part of the five-field grammar -
 *  fixed values, names, ranges, lists, steps, and the day-of-month field. Picking one
 *  fills the field, so the next thing a person does is edit a working expression
 *  rather than write one from a blank box.
 *
 *  `teaches` is shown under the row for whichever preset the current expression
 *  matches, which is why matching is by exact string: a paraphrase of what the user
 *  typed would be a second, weaker cron implementation in the browser. */
export type CronPreset = { label: string; cron: string; teaches: string }

export const CRON_PRESETS: CronPreset[] = [
  {
    get label() { return t("schedules.b197114114") },
    cron: '0 9 * * *',
    get teaches() { return t("schedules.73dd29e19f") },
  },
  {
    get label() { return t("schedules.3378bdf2d0") },
    cron: '0 9 * * mon-fri',
    get teaches() { return t("schedules.8b1213c219") },
  },
  {
    get label() { return t("schedules.50d9d0daf5") },
    cron: '0 9 * * mon',
    get teaches() { return t("schedules.df2b3ea4e1") },
  },
  {
    get label() { return t("schedules.0e0a21cc23") },
    cron: '0 13 * * wed',
    get teaches() { return t("schedules.47c8ba91c6") },
  },
  {
    get label() { return t("schedules.4de22ed297") },
    cron: '0 13 1,15 * *',
    get teaches() { return t("schedules.5d578caf6a") },
  },
  {
    get label() { return t("schedules.81c3c81990") },
    cron: '*/30 * * * *',
    get teaches() { return t("schedules.d8aa1f5f70") },
  },
  {
    get label() { return t("schedules.ab290dae5c") },
    cron: '0 3 1 * *',
    get teaches() { return t("schedules.48f19d9cc4") },
  },
]

/** The preset one expression matches exactly, if any. Whitespace-tolerant, because
 *  the daemon normalizes the expression it stores and the field is free text. */
export function presetForCron(cron: string): CronPreset | undefined {
  const normalized = cron.trim().split(/\s+/).join(' ').toLowerCase()
  return CRON_PRESETS.find(preset => preset.cron === normalized)
}

/** The three target kinds, said in the words of what each one does and costs.
 *
 *  The trade-off is the whole content of the choice, so it is stated on the option
 *  rather than hidden in a doc: a pinned run cannot drift, a rolling continuation
 *  accumulates until compaction eats its own early context, and a pinned point is the
 *  only one a recurring trigger repeats without run N+1 inheriting run N. */
export const TARGET_KIND_COPY: Record<ScheduleTargetKind, { label: string; detail: string }> = {
  run: {
    get label() { return t("schedules.4e2f4255db") },
    get detail() { return t("schedules.c4dd16f041") },
  },
  latest_of_session: {
    get label() { return t("schedules.f2a83b116c") },
    get detail() { return t("schedules.e723a800b1") },
  },
  fork_point: {
    get label() { return t("schedules.f8dbb8e013") },
    get detail() { return t("schedules.0a490b3488") },
  },
}

export const BLOCKED_LABELS: Record<string, string> = {
  get project_missing() { return t("schedules.7211f79522") },
  get automation_disabled() { return t("schedules.bedf1e6863") },
  get install_disabled() { return t("schedules.c38bc66ebf") },
}

export const OUTCOME_LABELS: Record<string, string> = {
  get started() { return t("schedules.59dbf36d99") },
  get spawned() { return t("schedules.c8fc6bf296") },
  get delivered() { return t("schedules.outcomeDelivered") },
  get skipped() { return t("schedules.389595a44d") },
  get failed() { return t("schedules.5d28a90f44") },
  get missed() { return t("schedules.1c33629a93") },
}

/** What a disarmed row says instead of a countdown, by the reason it was disarmed.
 *  "Paused" for anything a person paused, or a reason this table does not know. */
export const INACTIVE_LABELS: Record<string, string> = {
  get awaiting_approval() { return t("schedules.inactiveAwaiting") },
  get dismissed() { return t("schedules.inactiveDismissed") },
  get cancelled() { return t("schedules.inactiveCancelled") },
  get expired() { return t("schedules.inactiveExpired") },
  get completed() { return t("schedules.inactiveCompleted") },
}

export const isAwaitingApproval = (schedule: Pick<Schedule, 'approval_state'>): boolean =>
  schedule.approval_state === 'pending'

export const isAgentAuthored = (schedule: Pick<Schedule, 'author_kind'>): boolean =>
  schedule.author_kind === 'agent'

/** The countdown's stand-in on a row that is not armed. */
export function inactiveLabel(schedule: Schedule): string {
  if (isAwaitingApproval(schedule)) return INACTIVE_LABELS.awaiting_approval
  return INACTIVE_LABELS[schedule.disabled_reason] || t("schedules.inactivePaused")
}

/** "set up by agent planner", or '' for a person's own schedule. */
export function authorLabel(schedule: Pick<Schedule, 'author_kind' | 'author_name' | 'author_session_id'>): string {
  if (schedule.author_kind !== 'agent') return ''
  return t("schedules.authorAgent", { name: schedule.author_name || schedule.author_session_id || '?' })
}

/** The measured frequency, said the way a person reads it. */
export function frequencyLabel(firesPerDay: number): string {
  if (firesPerDay > 96) return t("schedules.frequencyMany")
  return t("schedules.frequencyCount", { count: firesPerDay, many: firesPerDay === 1 ? 'no' : 'yes' })
}

export const emptyDraft = (): ScheduleDraft => ({
  label: '',
  prompt: '',
  trigger_kind: 'cron',
  cron: '0 3 * * *',
  interval_minutes: 360,
  run_at_local: '',
  timezone: '',
  catch_up: false,
  overlap: 'skip',
  backend: '',
  profile_id: '',
  session_name: '',
  daily_run_cap: 0,
  follow_ups: [],
  action: 'spawn',
  target_run_id: '',
  target_kind: 'run',
  target_cut_message_id: '',
  target_cut_mode: '',
  context_ceiling_pct: 0,
  target_label: '',
  target_backend: '',
  model: '',
  when_live: 'skip',
  expires_local: '',
})

/** The conversation a resume schedule is seeded from, as the callers can describe it.
 *
 *  `run_id` is a **history run id**, never a session id, and both call sites have to
 *  pass one: History rows are keyed by it, and a live pane's is `agent_run_id` rather
 *  than its own id, because a pane that rolled its conversation owns a row keyed by
 *  the run. */
export type ResumeSeed = {
  run_id: string
  label: string
  backend: string
  /** Pre-selects the kind. A live pane seeds "wherever it has got to"; a History row
   *  seeds the pinned conversation, which is what the reader is looking at. */
  kind: ScheduleTargetKind
}

/** A one-off resume of `seed`, ready for the editor.
 *
 *  Defaults to `once` with an empty time rather than to a cron expression: the reason
 *  somebody reaches this from a conversation is almost always "pick this up on
 *  Tuesday", and offering `0 3 * * *` would arm a nightly agent by accident. */
export function resumeDraft(seed: ResumeSeed): ScheduleDraft {
  return {
    ...emptyDraft(),
    label: seed.label.slice(0, 80),
    trigger_kind: 'once',
    cron: '0 9 * * *',
    action: 'resume',
    target_run_id: seed.run_id,
    target_kind: seed.kind,
    context_ceiling_pct: seed.kind === 'latest_of_session' ? 0.7 : 0,
    target_label: seed.label,
    target_backend: seed.backend,
  }
}

export function draftFromSchedule(schedule: Schedule): ScheduleDraft {
  return {
    label: schedule.label,
    prompt: schedule.prompt,
    trigger_kind: schedule.trigger_kind,
    cron: schedule.cron || '0 3 * * *',
    interval_minutes: schedule.interval_seconds ? Math.round(schedule.interval_seconds / 60) : 360,
    run_at_local: schedule.run_at ? localInputValue(schedule.run_at) : '',
    timezone: schedule.timezone,
    catch_up: schedule.catch_up,
    overlap: schedule.overlap,
    backend: schedule.backend,
    profile_id: schedule.profile_id,
    session_name: schedule.session_name,
    daily_run_cap: schedule.daily_run_cap,
    follow_ups: schedule.follow_ups.map(item => item.body),
    action: schedule.action,
    target_run_id: schedule.target_run_id,
    target_kind: schedule.target_kind,
    target_cut_message_id: schedule.target_cut_message_id,
    target_cut_mode: schedule.target_cut_mode,
    context_ceiling_pct: schedule.context_ceiling_pct,
    target_label: schedule.target?.name || '',
    target_backend: schedule.target?.backend || '',
    model: schedule.model || '',
    when_live: schedule.when_live || 'skip',
    expires_local: schedule.expires_at ? localInputValue(schedule.expires_at) : '',
  }
}

/** `datetime-local` wants the browser's own wall clock, without a zone suffix. */
export function localInputValue(epochSeconds: number): string {
  const moment = new Date(epochSeconds * 1000)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())}`
    + `T${pad(moment.getHours())}:${pad(moment.getMinutes())}`
}

export function draftToBody(draft: ScheduleDraft): Record<string, unknown> {
  const resuming = draft.action === 'resume'
  const body: Record<string, unknown> = {
    label: draft.label.trim(),
    prompt: draft.prompt,
    trigger_kind: draft.trigger_kind,
    timezone: draft.timezone.trim(),
    catch_up: draft.catch_up,
    // A conversation opens once, so a resume has no overlap policy to choose and the
    // daemon refuses one. Sent as the only legal value rather than omitted, so a draft
    // that was a spawn a moment ago cannot carry `allow` across the switch.
    overlap: resuming ? 'skip' : draft.overlap,
    session_name: draft.session_name.trim(),
    daily_run_cap: draft.daily_run_cap || 0,
    follow_ups: draft.follow_ups.map(body => body.trim()).filter(Boolean),
    action: draft.action,
  }
  if (resuming) {
    // The harness, its arguments and the working directory all come from the
    // conversation's own History row. Sending any of them is refused rather than
    // ignored, so they are left off entirely.
    body.target_run_id = draft.target_run_id
    body.target_kind = draft.target_kind
    if (draft.target_kind === 'fork_point') {
      body.target_cut_message_id = draft.target_cut_message_id
      body.target_cut_mode = draft.target_cut_mode || 'after'
    }
    body.context_ceiling_pct = draft.target_kind === 'latest_of_session'
      ? draft.context_ceiling_pct
      : 0
    body.when_live = draft.when_live
  } else {
    body.backend = draft.backend
    body.profile_id = draft.profile_id
    // A model names a harness's own vocabulary, so it only travels with a backend.
    if (draft.backend && draft.model.trim()) body.model = draft.model.trim()
  }
  // Sent only when set: the editor shows the end date it was opened with, so leaving
  // the field empty is the person clearing it, and an absent value means "no end".
  const expires = draft.expires_local ? Date.parse(draft.expires_local) : NaN
  if (!Number.isNaN(expires)) body.expires_at = expires / 1000
  if (draft.trigger_kind === 'cron') body.cron = draft.cron.trim()
  if (draft.trigger_kind === 'interval') body.interval_seconds = Math.round(draft.interval_minutes * 60)
  if (draft.trigger_kind === 'once') {
    const parsed = draft.run_at_local ? Date.parse(draft.run_at_local) : NaN
    body.run_at = Number.isNaN(parsed) ? 0 : parsed / 1000
  }
  return body
}

/** What a row does, in one phrase: the half of a schedule the cadence does not say. */
export function actionLabel(schedule: Schedule): string {
  if (schedule.action !== 'resume') return t("schedules.2edb5380a8")
  const target = schedule.target
  if (!target || target.missing) return t("schedules.19fcb1e1d0")
  if (schedule.target_kind === 'fork_point') return t("schedules.c166c10d2c", { value1: target.name || 'a conversation' })
  if (schedule.target_kind === 'latest_of_session') {
    // Names where the conversation actually is, not where it was pinned. That is the
    // one thing about a rolling target a reader cannot work out from the row.
    return t("schedules.46551c6312", { value1: target.resolved_name || target.name || 'a conversation' })
  }
  return t("schedules.76119080f5", { value1: target.name || 'a conversation' })
}

/** Whether this schedule needs a conversation to exist, and it does not. */
export const targetIsMissing = (schedule: Schedule): boolean =>
  schedule.action === 'resume' && !!schedule.target?.missing

/** Anything that carries a trigger: a saved row, an agent's request card, or a draft. */
export type CadenceSource =
  | Pick<Schedule, 'trigger_kind' | 'cron' | 'interval_seconds' | 'run_at' | 'timezone'>
  | ScheduleDraft

/** What the row says under the label: the trigger, in words. */
export function cadenceLabel(schedule: CadenceSource): string {
  const kind = schedule.trigger_kind
  if (kind === 'cron') {
    const expression = 'cron' in schedule ? schedule.cron : ''
    const zone = schedule.timezone ? ` (${schedule.timezone})` : ''
    return t("schedules.ba94172e7a", { expression: expression, zone: zone })
  }
  if (kind === 'interval') {
    const seconds = 'interval_seconds' in schedule
      ? Number(schedule.interval_seconds || 0)
      : Math.round(schedule.interval_minutes * 60)
    return t("schedules.917f8cddb8", { value1: durationLabel(seconds) })
  }
  const at = 'run_at' in schedule ? schedule.run_at : null
  return at ? t("schedules.6a06931ba3", { value1: absoluteLabel(at) }) : t("schedules.200651a8b2")
}

export function durationLabel(seconds: number): string {
  if (!seconds || seconds < 0) return '—'
  if (seconds < 3600) return t("schedules.ef4135f675", { value1: Math.round(seconds / 60) })
  if (seconds < 86_400) {
    const hours = seconds / 3600
    return t("schedules.b30c713499", { value1: Number.isInteger(hours) ? hours : hours.toFixed(1) })
  }
  const days = seconds / 86_400
  return t("schedules.0e28352d24", { value1: Number.isInteger(days) ? days : days.toFixed(1) })
}

/** "in 4h" / "in 3d" / "now" for a future instant, using the daemon's clock. */
export function untilLabel(epochSeconds: number | null, now: number = serverNow()): string {
  if (!epochSeconds) return t("schedules.a497cf81d9")
  const delta = epochSeconds - now
  if (delta <= 0) return t("schedules.458a4770df")
  if (delta < 90) return t("schedules.5ac462fcf9")
  if (delta < 3600) return t("schedules.7618f5958a", { value1: Math.round(delta / 60) })
  if (delta < 86_400) return t("schedules.4c9882b696", { value1: Math.round(delta / 3600) })
  return t("schedules.ca2efafdf7", { value1: Math.round(delta / 86_400) })
}

export function absoluteLabel(epochSeconds: number | null): string {
  if (!epochSeconds) return '—'
  return new Date(epochSeconds * 1000).toLocaleString(uiLocale.value, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

/** The one-line verdict a row leads with. */
export function outcomeLabel(schedule: Schedule): string {
  if (!schedule.last_outcome) return t("schedules.de5f9d00b8")
  const label = OUTCOME_LABELS[schedule.last_outcome] || schedule.last_outcome
  return t("schedules.e2d7978faa", { label: label, value2: absoluteLabel(schedule.last_fire_at) })
}

/** Rows that need a human: a failure, or armed-but-cannot-fire.
 *
 *  This is what the rail badge counts. A paused schedule is deliberately not
 *  included - pausing is a decision someone already made, and badging it would
 *  train people to ignore the badge. */
export function needsAttention(schedules: Schedule[]): Schedule[] {
  return schedules.filter(schedule =>
    // An agent's request is waiting on exactly one thing, and it is the reader.
    isAwaitingApproval(schedule)
    || schedule.last_outcome === 'failed'
    || (schedule.enabled && schedule.blocked !== '')
    // An armed resume whose conversation has been deleted can never fire. It is the
    // same class of failure as `blocked` - a row that looks armed and is inert - and
    // it is worth badging *before* the next fire disables it, because the fire may be
    // a week away.
    || (schedule.enabled && targetIsMissing(schedule)))
}

/** Sort for display: armed and soonest first, then paused, then unarmed. */
export function orderSchedules(schedules: Schedule[]): Schedule[] {
  return [...schedules].sort((left, right) => {
    if (left.enabled !== right.enabled) return left.enabled ? -1 : 1
    const leftFire = left.next_fire_at ?? Number.POSITIVE_INFINITY
    const rightFire = right.next_fire_at ?? Number.POSITIVE_INFINITY
    if (leftFire !== rightFire) return leftFire - rightFire
    return left.label.localeCompare(right.label)
  })
}
