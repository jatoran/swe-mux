// Time arithmetic and wording for the Quota surface's history chart and readouts.
//
// Pure, so the chart's claims about *when* a percentage was true are testable without a
// renderer. Two clocks meet here and must not be confused:
//
// - A raw sample is an instant the daemon stamped. It is drawn and labelled in the
//   viewer's local time, because "what was this at 3pm" is asked in the viewer's zone.
// - A daily point is a **UTC calendar day** (`date(sampled_at,'unixepoch')` in
//   `operational_telemetry.quota_series`). The browser cannot re-bucket it into local
//   days, so it is drawn at that day's UTC noon and always labelled as a UTC day rather
//   than silently presented as a local one.
import type {
  QuotaDailyPoint,
  QuotaRawPoint,
  QuotaSeriesStatus,
  QuotaUsageBilling,
  QuotaUsageDay,
  QuotaUsageInterval,
  QuotaUsageSource,
} from './usageAnalytics'

export type QuotaWindowName = 'session'|'weekly'
export type QuotaZone = 'local'|'utc'

const HOUR = 3600
const DAY = 86400

/** One name per window, used by every Quota view so a window never has two names. */
export const QUOTA_WINDOW_LABELS: Record<QuotaWindowName|'fable', string> = {
  weekly: 'Weekly',
  session: '5-hour',
  fable: 'Fable weekly',
}

/** The label for a window name the daemon sent, keeping an unknown one readable rather than blank. */
export const quotaWindowLabel = (window: string): string =>
  QUOTA_WINDOW_LABELS[window as keyof typeof QUOTA_WINDOW_LABELS] ?? window

/** Sources in drawing order: swe-mux first, so it sits at the base of every stacked cell. */
export const QUOTA_SOURCES: readonly QuotaUsageSource[] = ['swe_mux', 'outside', 'elsewhere', 'uncertain']

export const QUOTA_SOURCE_LABELS: Record<QuotaUsageSource, string> = {
  swe_mux: 'swe-mux sessions',
  outside: 'not swe-mux',
  elsewhere: 'used elsewhere',
  uncertain: 'unclear',
}

/** What each source claims, worded for a legend or a tooltip. */
export const QUOTA_SOURCE_DETAIL: Record<QuotaUsageSource, string> = {
  swe_mux: 'swe-mux sessions billed to this account were running',
  outside: 'the account was in use here, but swe-mux sessions do not explain the rise: another app on this computer, or use elsewhere at the same time',
  elsewhere: 'this computer was not using the account: another device, the provider\'s own apps, or another login',
  uncertain: 'the account was switched in a way the daemon did not record',
}

export const QUOTA_BILLING_LABELS: Record<QuotaUsageBilling, string> = {
  selected: 'selected here throughout',
  not_selected: 'not selected here',
  switched: 'switched during the interval',
  uncertain: 'switched without a record of when',
  per_session: 'billed per session, by the account each was started under',
}

/** One drawn stretch of an attribution lane, in percentage points of one window. */
export type UsageCell = {
  start: number
  end: number
  parts: Record<QuotaUsageSource, number>
  total: number
  requests: number
  costUsd: number
}

const isDay = (row: QuotaUsageInterval | QuotaUsageDay): row is QuotaUsageDay => 'day' in row

/** The rising intervals (or UTC days) of one window, ascending, as drawable cells. */
export function usageCells(
  usage: ReadonlyArray<QuotaUsageInterval | QuotaUsageDay> | undefined,
  window: QuotaWindowName,
): UsageCell[] {
  const cells: UsageCell[] = []
  for (const row of usage ?? []) {
    const parts = Object.fromEntries(QUOTA_SOURCES.map(source => [source, Math.max(0, row[window][source] || 0)])) as Record<QuotaUsageSource, number>
    const total = QUOTA_SOURCES.reduce((sum, source) => sum + parts[source], 0)
    if (total <= 0) continue
    const [start, end] = isDay(row) ? [utcDayNoon(row.day) - DAY / 2, utcDayNoon(row.day) + DAY / 2] : [row.start, row.end]
    cells.push({start, end, parts, total, requests: row.requests, costUsd: row.cost_usd})
  }
  return cells.sort((a, b) => a.start - b.start)
}

/** The source a cell is entirely, or null when it is a mix. */
export function soleSource(cell: UsageCell): QuotaUsageSource | null {
  const present = QUOTA_SOURCES.filter(source => cell.parts[source] > 0)
  return present.length === 1 ? present[0] : null
}

/**
 * Join cells that are wholly one source into a single stretch across gaps up to `joinGap`.
 *
 * A week of fifteen-minute intervals is several hundred cells a pixel or two wide; a
 * run of them from one source draws as one rectangle and reads as one period. The gap
 * matters because providers report whole percents: a window ticks up once every few
 * polls while the usage behind that tick accrued across all of them, so a short run of
 * no-rise intervals between two rises of one source belongs to the same period. A mixed
 * cell keeps its own stack, because what it says is that the mix happened there.
 */
export function mergeUsageCells(cells: readonly UsageCell[], joinGap = 1): UsageCell[] {
  const merged: UsageCell[] = []
  for (const cell of cells) {
    const last = merged[merged.length - 1]
    const source = soleSource(cell)
    if (last && source && soleSource(last) === source && cell.start - last.end <= joinGap) {
      merged[merged.length - 1] = {
        start: last.start,
        end: cell.end,
        parts: {...last.parts, [source]: last.parts[source] + cell.parts[source]},
        total: last.total + cell.total,
        requests: last.requests + cell.requests,
        costUsd: last.costUsd + cell.costUsd,
      }
    } else {
      merged.push({...cell, parts: {...cell.parts}})
    }
  }
  return merged
}

/** Points per source over a set of cells, and their total. */
export function usageTotals(cells: readonly UsageCell[]): {parts: Record<QuotaUsageSource, number>; total: number} {
  const parts = Object.fromEntries(QUOTA_SOURCES.map(source => [source, 0])) as Record<QuotaUsageSource, number>
  for (const cell of cells) for (const source of QUOTA_SOURCES) parts[source] += cell.parts[source]
  return {parts, total: QUOTA_SOURCES.reduce((sum, source) => sum + parts[source], 0)}
}

/** `65% swe-mux sessions · 35% used elsewhere`: shares of a total, largest first. */
export function describeShares(parts: Record<QuotaUsageSource, number>, total: number): string {
  if (total <= 0) return ''
  return QUOTA_SOURCES
    .filter(source => parts[source] > 0)
    .sort((a, b) => parts[b] - parts[a])
    .map(source => `${Math.round(parts[source] / total * 100)}% ${QUOTA_SOURCE_LABELS[source]}`)
    .join(' · ')
}

/** `3 swe-mux sessions · 1 not swe-mux`: points per source, for one interval or day. */
export function describePoints(parts: Record<QuotaUsageSource, number>): string {
  return QUOTA_SOURCES
    .filter(source => parts[source] > 0)
    .map(source => `${formatQuotaPercent(parts[source])} ${QUOTA_SOURCE_LABELS[source]}`)
    .join(' · ')
}

/** The cell a reading closes (raw) or the day it falls on (daily), if that one rose. */
export function usageCellAt(cells: readonly UsageCell[], instant: number, daily: boolean): UsageCell | null {
  return cells.find(cell => daily ? cell.start <= instant && instant < cell.end : Math.abs(cell.end - instant) < 1) ?? null
}

/** One plotted reading of one window on one account. */
export type QuotaReading = {
  /** Where it is drawn: the sample instant, or the UTC day's noon for a daily point. */
  time: number
  /** Percent of the window used: the sample, or the day's last reading. */
  used: number
  /** Raw only: the window's reset instant as reported with this reading. */
  resetsAt?: number|null
  /** Daily only: the UTC calendar day and its spread. */
  day?: string
  first?: number|null
  peak?: number|null
  samples?: number
}

export const utcDayNoon = (day: string) => Date.parse(`${day}T12:00:00Z`)/1000
export const utcDayOf = (seconds: number) => new Date(seconds*1000).toISOString().slice(0, 10)

/** The readings of one window, ascending, with unreadable samples dropped. */
export function quotaReadings(
  points: Array<QuotaRawPoint|QuotaDailyPoint>,
  window: QuotaWindowName,
): QuotaReading[] {
  const readings: QuotaReading[] = []
  for (const point of points) {
    if ('sampled_at' in point) {
      const reading = point[window]
      if (reading && typeof reading.used_percent === 'number') {
        readings.push({time: point.sampled_at, used: reading.used_percent, resetsAt: reading.resets_at ?? null})
      }
      continue
    }
    const used = point[`${window}_last`]
    if (typeof used !== 'number') continue
    readings.push({
      time: utcDayNoon(point.day),
      used,
      day: point.day,
      first: point[`${window}_first`] ?? null,
      peak: point[`${window}_max`] ?? null,
      samples: point.samples,
    })
  }
  return readings.sort((a, b) => a.time-b.time)
}

/**
 * The span the chart's time axis covers, in seconds.
 *
 * The requested range, not merely the span of the data: a week with two days of readings
 * is drawn as a week with a gap, so where a reading sits says when it was taken. A daily
 * domain runs from the first UTC day's midnight to the day after the last, so every day
 * owns a full slot and its point sits in the middle of it.
 */
export function quotaDomain(
  status: Pick<QuotaSeriesStatus, 'resolution'|'since'|'until'>,
  readings: QuotaReading[],
  now: number,
): [number, number] {
  const times = readings.map(reading => reading.time)
  const earliest = times.length ? Math.min(...times) : null
  const latest = times.length ? Math.max(...times) : null
  if (status.resolution === 'daily') {
    const days = readings.map(reading => reading.day).filter((day): day is string => !!day).sort()
    const firstDay = [status.since != null ? utcDayOf(status.since) : null, days[0]].filter((day): day is string => !!day).sort()[0] ?? utcDayOf(now)
    const lastDay = [status.until != null ? utcDayOf(status.until) : utcDayOf(now), days[days.length-1]].filter((day): day is string => !!day).sort().reverse()[0]
    return [utcDayNoon(firstDay)-DAY/2, utcDayNoon(lastDay)+DAY/2]
  }
  const start = Math.min(status.since ?? earliest ?? now-7*DAY, earliest ?? Infinity)
  const end = Math.max(status.until ?? now, latest ?? -Infinity, start+HOUR)
  return [start, end]
}

/**
 * The largest spacing between two readings that a line may still bridge.
 *
 * Anything wider is an outage - the daemon was off, or the account failed to read - and
 * a line drawn across it would claim a steady percentage nobody observed. Raw spacing is
 * measured from the series itself because the poll interval is a setting.
 */
export function quotaGapLimit(readings: QuotaReading[], resolution: 'raw'|'daily'): number {
  if (resolution === 'daily') return 1.5*DAY
  const gaps = readings.slice(1).map((reading, index) => reading.time-readings[index].time).sort((a, b) => a-b)
  const median = gaps.length ? gaps[Math.floor(gaps.length/2)] : 0
  return Math.max(30*60, median*3)
}

/** Split readings wherever the gap between neighbours exceeds `gapLimit`. */
export function readingSegments(readings: QuotaReading[], gapLimit: number): QuotaReading[][] {
  const segments: QuotaReading[][] = []
  for (const reading of readings) {
    const current = segments[segments.length-1]
    if (current && reading.time-current[current.length-1].time <= gapLimit) current.push(reading)
    else segments.push([reading])
  }
  return segments
}

/**
 * Every distinct moment an inspector can stop at, ascending.
 *
 * One poll reads every account within a second or two, so readings closer than
 * `mergeWithin` to the start of a cluster are one moment, represented by the cluster's
 * latest time. That keeps "as of" semantics exact: every reading in the cluster is at or
 * before the moment it belongs to.
 */
export function readingInstants(series: QuotaReading[][], mergeWithin = 60): number[] {
  const times = series.flatMap(readings => readings.map(reading => reading.time)).sort((a, b) => a-b)
  const instants: number[] = []
  let clusterStart = -Infinity
  for (const time of times) {
    if (instants.length && time-clusterStart <= mergeWithin) {
      instants[instants.length-1] = time
    } else {
      instants.push(time)
      clusterStart = time
    }
  }
  return instants
}

/** Index of the last item, in ascending `timeOf` order, at or before `time`; -1 when none is. */
function lastAtOrBefore<T>(items: T[], time: number, timeOf: (item: T) => number): number {
  let low = 0, high = items.length-1, found = -1
  while (low <= high) {
    const middle = (low+high) >> 1
    if (timeOf(items[middle]) <= time) { found = middle; low = middle+1 } else high = middle-1
  }
  return found
}
const itself = (time: number) => time

/** The instant closest to `time`, or null when there are none. */
export function nearestInstant(instants: number[], time: number): number|null {
  if (!instants.length) return null
  const before = lastAtOrBefore(instants, time, itself)
  if (before < 0) return instants[0]
  if (before === instants.length-1) return instants[before]
  return time-instants[before] <= instants[before+1]-time ? instants[before] : instants[before+1]
}

/**
 * The reading that was current at `instant`: the latest one at or before it.
 *
 * `maxAge` bounds how stale that may be; a daily readout passes 0, so an account with no
 * reading on the inspected day reports none rather than an earlier day's value.
 */
export function readingAsOf(readings: QuotaReading[], instant: number, maxAge = Infinity): QuotaReading|null {
  const index = lastAtOrBefore(readings, instant, reading => reading.time)
  if (index < 0) return null
  return instant-readings[index].time <= maxAge ? readings[index] : null
}

/** A percentage at the precision the provider gave it: `85%`, `38.2%`. */
export function formatQuotaPercent(value: number|null|undefined): string {
  if (value == null || !Number.isFinite(value)) return 'no reading'
  const rounded = Math.round(value*10)/10
  return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)}%`
}

const formatters = new Map<string, Intl.DateTimeFormat>()
function formatter(options: Intl.DateTimeFormatOptions, locale?: string): Intl.DateTimeFormat {
  const key = `${locale ?? ''}|${JSON.stringify(options)}`
  let cached = formatters.get(key)
  if (!cached) {
    cached = new Intl.DateTimeFormat(locale, options)
    formatters.set(key, cached)
  }
  return cached
}

const yearOf = (seconds: number, zone: QuotaZone) => {
  const date = new Date(seconds*1000)
  return zone === 'utc' ? date.getUTCFullYear() : date.getFullYear()
}

/** A local instant with its weekday: `Mon 6 Oct, 14:32`, adding the year only when it is not this one. */
export function formatQuotaInstant(seconds: number, now: number, locale?: string): string {
  return formatter({
    weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
    ...(yearOf(seconds, 'local') !== yearOf(now, 'local') ? {year: 'numeric' as const} : {}),
  }, locale).format(seconds*1000)
}

/** A UTC calendar day with its weekday: `Mon 6 Oct`, adding the year only when it is not this one. */
export function formatQuotaDay(day: string, now: number, locale?: string): string {
  const noon = utcDayNoon(day)
  return formatter({
    weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC',
    ...(yearOf(noon, 'utc') !== yearOf(now, 'utc') ? {year: 'numeric' as const} : {}),
  }, locale).format(noon*1000)
}

/** What a chart's time axis spans, in the clock its points are labelled in. */
export function quotaDomainLabel(start: number, end: number, daily: boolean, now: number, locale?: string): string {
  return daily
    ? `${formatQuotaDay(utcDayOf(start+DAY/2), now, locale)} to ${formatQuotaDay(utcDayOf(end-DAY/2), now, locale)} (UTC days)`
    : `${formatQuotaInstant(start, now, locale)} to ${formatQuotaInstant(end, now, locale)} (local time)`
}

export type TimeTick = {time: number; label: string}

type TickStep = {unit: 'hour'|'day'|'month'; count: number}
const TICK_STEPS: TickStep[] = [
  ...[1, 2, 3, 6, 12].map(count => ({unit: 'hour' as const, count})),
  ...[1, 2, 3, 7, 14].map(count => ({unit: 'day' as const, count})),
  ...[1, 2, 3, 6, 12].map(count => ({unit: 'month' as const, count})),
]
const stepSeconds = (step: TickStep) => step.count*(step.unit === 'hour' ? HOUR : step.unit === 'day' ? DAY : 30*DAY)

/** Calendar fields read and written in one zone, so DST is the Date object's problem. */
function zoned(date: Date, zone: QuotaZone) {
  const utc = zone === 'utc'
  return {
    hours: () => utc ? date.getUTCHours() : date.getHours(),
    month: () => utc ? date.getUTCMonth() : date.getMonth(),
    setHours: (hours: number) => utc ? date.setUTCHours(hours, 0, 0, 0) : date.setHours(hours, 0, 0, 0),
    setDate: (day: number) => utc ? date.setUTCDate(day) : date.setDate(day),
    date: () => utc ? date.getUTCDate() : date.getDate(),
    setMonth: (month: number) => utc ? date.setUTCMonth(month) : date.setMonth(month),
  }
}

function floorToStep(seconds: number, step: TickStep, zone: QuotaZone): Date {
  const date = new Date(seconds*1000)
  const calendar = zoned(date, zone)
  if (step.unit === 'hour') {
    calendar.setHours(Math.floor(calendar.hours()/step.count)*step.count)
    return date
  }
  calendar.setHours(0)
  if (step.unit === 'month') {
    calendar.setDate(1)
    calendar.setMonth(Math.floor(calendar.month()/step.count)*step.count)
  }
  return date
}

function advanceStep(date: Date, step: TickStep, zone: QuotaZone): void {
  const calendar = zoned(date, zone)
  if (step.unit === 'hour') calendar.setHours(calendar.hours()+step.count)
  else if (step.unit === 'day') { calendar.setDate(calendar.date()+step.count); calendar.setHours(0) }
  else { calendar.setDate(1); calendar.setMonth(calendar.month()+step.count); calendar.setHours(0) }
}

/**
 * Labelled ticks for a time axis, at calendar boundaries in `zone`.
 *
 * Picks the finest step that yields at most `maxTicks` ticks. Day and month ticks sit on
 * midnights, so a label names the day that starts there; with `daily` they move to the
 * UTC noon where a daily point is drawn, and only day and month steps are considered.
 */
export function quotaTimeTicks(
  start: number,
  end: number,
  maxTicks: number,
  options: {zone: QuotaZone; daily?: boolean; locale?: string},
): TimeTick[] {
  if (!(end > start) || maxTicks < 1) return []
  const steps = options.daily ? TICK_STEPS.filter(step => step.unit !== 'hour') : TICK_STEPS
  const step = steps.find(candidate => (end-start)/stepSeconds(candidate) <= maxTicks) ?? steps[steps.length-1]
  const offset = options.daily ? DAY/2 : 0
  const spanDays = (end-start)/DAY
  const timeZone = options.zone === 'utc' ? 'UTC' : undefined
  const crossesYear = yearOf(start, options.zone) !== yearOf(end, options.zone)
  const ticks: TimeTick[] = []
  const cursor = floorToStep(start-offset, step, options.zone)
  // Walk a day at a time to the first midnight on the axis, so a multi-day step starts on
  // the first day shown rather than on the day before it. Hour and month steps stay on
  // their aligned boundaries.
  const approach = step.unit === 'day' ? {unit: 'day' as const, count: 1} : step
  while (cursor.getTime()/1000+offset < start) advanceStep(cursor, approach, options.zone)
  for (let guard = 0; guard < 1000; guard++) {
    const time = cursor.getTime()/1000+offset
    if (time > end) break
    if (time >= start) {
      const midnight = zoned(cursor, options.zone).hours() === 0
      const format: Intl.DateTimeFormatOptions = step.unit === 'month'
        ? {month: 'short', year: 'numeric', timeZone}
        : step.unit === 'hour' && !midnight
          ? {hour: 'numeric', minute: '2-digit', timeZone}
          : {
            ...(spanDays <= 45 ? {weekday: 'short' as const} : {}),
            day: 'numeric', month: 'short', timeZone,
            ...(crossesYear ? {year: '2-digit' as const} : {}),
          }
      ticks.push({time, label: formatter(format, options.locale).format(cursor)})
    }
    advanceStep(cursor, step, options.zone)
  }
  return ticks
}
