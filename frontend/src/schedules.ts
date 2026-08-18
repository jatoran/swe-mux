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

export type ScheduleFollowUp = {
  body: string
  delay_seconds: number
  armed: boolean
}

export type ScheduleRun = {
  id: string
  schedule_id: string
  project_id: string
  fire_key: string
  due_at: number | null
  started_at: number
  finished_at: number | null
  outcome: 'started' | 'spawned' | 'skipped' | 'failed' | 'missed'
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
    label: 'Every day at 09:00',
    cron: '0 9 * * *',
    teaches: 'The five fields are minute, hour, day-of-month, month, day-of-week. Fix the first two, leave the rest as * for "every".',
  },
  {
    label: 'Every weekday at 09:00',
    cron: '0 9 * * mon-fri',
    teaches: 'The last field is the weekday, and it takes names and ranges: mon-fri is Monday through Friday.',
  },
  {
    label: 'Every Monday at 09:00',
    cron: '0 9 * * mon',
    teaches: 'Weekly is just one weekday. Pin the day-of-week field and leave day-of-month as *.',
  },
  {
    label: 'Every Wednesday at 13:00',
    cron: '0 13 * * wed',
    teaches: 'Cron counts days and months, never weeks, so there is no "every other Wednesday". For a fortnightly rhythm use the 1st and 15th below, or an interval trigger.',
  },
  {
    label: 'The 1st and 15th at 13:00',
    cron: '0 13 1,15 * *',
    teaches: 'A comma is a list: 1,15 is the first and fifteenth of the month. This is the closest cron gets to every other week.',
  },
  {
    label: 'Every 30 minutes',
    cron: '*/30 * * * *',
    teaches: 'A slash is a step: */30 in the minute field means every 30th minute, and the same works in any field.',
  },
  {
    label: 'First of the month at 03:00',
    cron: '0 3 1 * *',
    teaches: 'The third field is the day of the month. With a weekday of * this fires on that date whatever day it lands on.',
  },
]

/** The preset one expression matches exactly, if any. Whitespace-tolerant, because
 *  the daemon normalizes the expression it stores and the field is free text. */
export function presetForCron(cron: string): CronPreset | undefined {
  const normalized = cron.trim().split(/\s+/).join(' ').toLowerCase()
  return CRON_PRESETS.find(preset => preset.cron === normalized)
}

export const BLOCKED_LABELS: Record<string, string> = {
  project_missing: 'this Project is no longer registered',
  automation_disabled: 'Scheduled runs is off for this Project',
  install_disabled: 'scheduled runs are switched off for this install',
}

export const OUTCOME_LABELS: Record<string, string> = {
  started: 'starting',
  spawned: 'ran',
  skipped: 'skipped',
  failed: 'failed',
  missed: 'missed',
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
})

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
  const body: Record<string, unknown> = {
    label: draft.label.trim(),
    prompt: draft.prompt,
    trigger_kind: draft.trigger_kind,
    timezone: draft.timezone.trim(),
    catch_up: draft.catch_up,
    overlap: draft.overlap,
    backend: draft.backend,
    profile_id: draft.profile_id,
    session_name: draft.session_name.trim(),
    daily_run_cap: draft.daily_run_cap || 0,
    follow_ups: draft.follow_ups.map(body => body.trim()).filter(Boolean),
  }
  if (draft.trigger_kind === 'cron') body.cron = draft.cron.trim()
  if (draft.trigger_kind === 'interval') body.interval_seconds = Math.round(draft.interval_minutes * 60)
  if (draft.trigger_kind === 'once') {
    const parsed = draft.run_at_local ? Date.parse(draft.run_at_local) : NaN
    body.run_at = Number.isNaN(parsed) ? 0 : parsed / 1000
  }
  return body
}

/** What the row says under the label: the trigger, in words. */
export function cadenceLabel(schedule: Schedule | ScheduleDraft): string {
  const kind = schedule.trigger_kind
  if (kind === 'cron') {
    const expression = 'cron' in schedule ? schedule.cron : ''
    const zone = schedule.timezone ? ` (${schedule.timezone})` : ''
    return `cron ${expression}${zone}`
  }
  if (kind === 'interval') {
    const seconds = 'interval_seconds' in schedule
      ? Number(schedule.interval_seconds || 0)
      : Math.round(schedule.interval_minutes * 60)
    return `every ${durationLabel(seconds)}`
  }
  const at = 'run_at' in schedule ? schedule.run_at : null
  return at ? `once, ${absoluteLabel(at)}` : 'once'
}

export function durationLabel(seconds: number): string {
  if (!seconds || seconds < 0) return '—'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`
  if (seconds < 86_400) {
    const hours = seconds / 3600
    return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`
  }
  const days = seconds / 86_400
  return `${Number.isInteger(days) ? days : days.toFixed(1)} d`
}

/** "in 4h" / "in 3d" / "now" for a future instant, using the daemon's clock. */
export function untilLabel(epochSeconds: number | null, now: number = serverNow()): string {
  if (!epochSeconds) return 'not scheduled'
  const delta = epochSeconds - now
  if (delta <= 0) return 'due'
  if (delta < 90) return 'in under a minute'
  if (delta < 3600) return `in ${Math.round(delta / 60)}m`
  if (delta < 86_400) return `in ${Math.round(delta / 3600)}h`
  return `in ${Math.round(delta / 86_400)}d`
}

export function absoluteLabel(epochSeconds: number | null): string {
  if (!epochSeconds) return '—'
  return new Date(epochSeconds * 1000).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

/** The one-line verdict a row leads with. */
export function outcomeLabel(schedule: Schedule): string {
  if (!schedule.last_outcome) return 'never run'
  const label = OUTCOME_LABELS[schedule.last_outcome] || schedule.last_outcome
  return `${label} ${absoluteLabel(schedule.last_fire_at)}`
}

/** Rows that need a human: a failure, or armed-but-cannot-fire.
 *
 *  This is what the rail badge counts. A paused schedule is deliberately not
 *  included - pausing is a decision someone already made, and badging it would
 *  train people to ignore the badge. */
export function needsAttention(schedules: Schedule[]): Schedule[] {
  return schedules.filter(schedule =>
    schedule.last_outcome === 'failed' || (schedule.enabled && schedule.blocked !== ''))
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
