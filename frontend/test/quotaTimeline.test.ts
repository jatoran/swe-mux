import assert from 'node:assert/strict'
import test from 'node:test'
import {
  formatQuotaDay,
  formatQuotaPercent,
  nearestInstant,
  quotaDomain,
  quotaDomainLabel,
  quotaGapLimit,
  quotaReadings,
  quotaTimeTicks,
  quotaWindowLabel,
  readingAsOf,
  readingInstants,
  readingSegments,
  utcDayNoon,
  type QuotaReading,
} from '../src/quotaTimeline.ts'
import type { QuotaRawPoint } from '../src/usageAnalytics.ts'

const at = (iso: string) => Date.parse(iso)/1000
const raw = (sampled_at: number, weekly: number|null, extra: Partial<QuotaRawPoint> = {}): QuotaRawPoint => ({
  id: sampled_at, provider: 'claude', account_id: 'a', sampled_at, status: weekly == null ? 'error' : 'ready',
  freshness: weekly == null ? 'error' : 'fresh', raw_precision: 0, active: true,
  weekly: weekly == null ? null : {used_percent: weekly, resets_at: at('2026-10-11T09:00:00Z')},
  session: null, ...extra,
})
const reading = (time: number, used = 10): QuotaReading => ({time, used})

test('raw readings drop failed samples, sort by time, and keep the reset instant they were read with', () => {
  const readings = quotaReadings([raw(300, 40), raw(100, 20), raw(200, null)], 'weekly')
  assert.deepEqual(readings.map(item => [item.time, item.used]), [[100, 20], [300, 40]])
  assert.equal(readings[0].resetsAt, at('2026-10-11T09:00:00Z'))
  assert.deepEqual(quotaReadings([raw(100, 20)], 'session'), [])
})

test('a daily reading sits at its UTC day\'s noon and carries the day\'s start, peak and sample count', () => {
  const [day] = quotaReadings([{
    provider: 'claude', account_id: 'a', day: '2026-10-06', samples: 96, errors: 0,
    weekly_first: 34, weekly_last: 5, weekly_max: 92, session_last: null,
  }], 'weekly')
  assert.equal(day.time, at('2026-10-06T12:00:00Z'))
  assert.deepEqual([day.used, day.first, day.peak, day.samples, day.day], [5, 34, 92, 96, '2026-10-06'])
})

test('a line breaks across an outage instead of bridging it with a reading nobody took', () => {
  const quarterHours = [0, 900, 1800, 2700, 9000, 9900].map(time => reading(time))
  const limit = quotaGapLimit(quarterHours, 'raw')
  assert.equal(limit, 2700, 'three times the median 15-minute spacing')
  assert.deepEqual(readingSegments(quarterHours, limit).map(segment => segment.map(item => item.time)), [[0, 900, 1800, 2700], [9000, 9900]])
  assert.equal(quotaGapLimit([reading(0), reading(60), reading(120)], 'raw'), 1800, 'never tighter than half an hour')
  const days = ['2026-10-01', '2026-10-02', '2026-10-04'].map(day => reading(utcDayNoon(day)))
  assert.equal(readingSegments(days, quotaGapLimit(days, 'daily')).length, 2, 'a missing day is a gap')
})

test('one poll is one inspectable moment, represented by its latest reading, and clusters do not chain', () => {
  const instants = readingInstants([[reading(1000), reading(1900)], [reading(1001), reading(1902)], [reading(1003)]])
  assert.deepEqual(instants, [1003, 1902])
  const chained = readingInstants([[0, 50, 100, 150].map(time => reading(time))], 60)
  assert.deepEqual(chained, [50, 150])
  assert.deepEqual(readingInstants([[reading(5)], [reading(5)]], 0), [5])
})

test('the inspector snaps to the nearest moment and reads each account as of that moment', () => {
  const instants = [100, 200, 400]
  assert.equal(nearestInstant(instants, 0), 100)
  assert.equal(nearestInstant(instants, 149), 100)
  assert.equal(nearestInstant(instants, 151), 200)
  assert.equal(nearestInstant(instants, 1000), 400)
  assert.equal(nearestInstant([], 5), null)
  const readings = [reading(100, 10), reading(200, 20)]
  assert.equal(readingAsOf(readings, 50), null, 'nothing had been read yet')
  assert.equal(readingAsOf(readings, 399)?.used, 20, 'the latest reading at or before the moment')
  assert.equal(readingAsOf(readings, 399, 0), null, 'a daily readout never borrows another day')
  assert.equal(readingAsOf(readings, 200, 0)?.used, 20)
})

test('a raw axis spans the requested range even where the data is sparse', () => {
  const since = at('2026-10-03T14:32:00Z'), until = at('2026-10-10T14:32:00Z')
  const domain = quotaDomain({resolution: 'raw', since, until}, [reading(at('2026-10-09T00:00:00Z'))], until)
  assert.deepEqual(domain, [since, until])
  const now = 1_790_000_000
  const unbounded = quotaDomain({resolution: 'raw', since: null, until: null}, [reading(now-5000), reading(now-1000)], now)
  assert.deepEqual(unbounded, [now-5000, now], 'from the first reading to now')
})

test('a daily axis gives every UTC day a whole slot with its point in the middle', () => {
  const since = at('2026-10-03T14:32:00Z'), until = at('2026-10-10T14:32:00Z')
  const [start, end] = quotaDomain({resolution: 'daily', since, until}, [], until)
  assert.equal(start, at('2026-10-03T00:00:00Z'))
  assert.equal(end, at('2026-10-11T00:00:00Z'))
  const retained = quotaDomain({resolution: 'daily', since: null, until: null}, [{...reading(utcDayNoon('2026-09-20')), day: '2026-09-20'}], until)
  assert.deepEqual(retained, [at('2026-09-20T00:00:00Z'), at('2026-10-11T00:00:00Z')])
  assert.equal(quotaDomainLabel(start, end, true, until, 'en-GB'), 'Sat 3 Oct to Sat 10 Oct (UTC days)')
})

test('a week of local time is labelled at each midnight, coarsening rather than crowding when narrow', () => {
  const start = at('2026-10-03T14:32:00Z'), end = at('2026-10-10T14:32:00Z')
  const wide = quotaTimeTicks(start, end, 8, {zone: 'utc', locale: 'en-GB'})
  assert.deepEqual(wide.map(tick => tick.label), ['Sun 4 Oct', 'Mon 5 Oct', 'Tue 6 Oct', 'Wed 7 Oct', 'Thu 8 Oct', 'Fri 9 Oct', 'Sat 10 Oct'])
  assert.equal(wide[0].time, at('2026-10-04T00:00:00Z'))
  const narrow = quotaTimeTicks(start, end, 3, {zone: 'utc', locale: 'en-GB'})
  assert.deepEqual(narrow.map(tick => tick.time), ['2026-10-04', '2026-10-07', '2026-10-10'].map(day => at(`${day}T00:00:00Z`)))
  // The viewer's own zone: whatever it is, every tick lands on one of its midnights.
  const local = quotaTimeTicks(start, end, 8, {zone: 'local'})
  assert.ok(local.length >= 6 && local.length <= 9)
  for (const tick of local) {
    const date = new Date(tick.time*1000)
    assert.deepEqual([date.getHours(), date.getMinutes()], [0, 0])
    assert.ok(tick.time >= start && tick.time <= end)
  }
})

test('daily ticks sit on the UTC noon where each day is drawn, and short spans get hours', () => {
  const ticks = quotaTimeTicks(at('2026-10-03T00:00:00Z'), at('2026-10-06T00:00:00Z'), 8, {zone: 'utc', daily: true, locale: 'en-GB'})
  assert.deepEqual(ticks.map(tick => [tick.time, tick.label]), [
    [at('2026-10-03T12:00:00Z'), 'Sat 3 Oct'],
    [at('2026-10-04T12:00:00Z'), 'Sun 4 Oct'],
    [at('2026-10-05T12:00:00Z'), 'Mon 5 Oct'],
  ])
  const hours = quotaTimeTicks(at('2026-10-05T20:00:00Z'), at('2026-10-06T08:00:00Z'), 4, {zone: 'utc', locale: 'en-GB'})
  assert.deepEqual(hours.map(tick => tick.label), ['21:00', 'Tue 6 Oct', '3:00', '6:00'], 'a midnight inside an hourly axis names its day')
  assert.deepEqual(quotaTimeTicks(10, 10, 5, {zone: 'utc'}), [])
})

test('long ranges fall back to month ticks, and a year boundary is named', () => {
  const ticks = quotaTimeTicks(at('2025-11-15T00:00:00Z'), at('2026-02-15T00:00:00Z'), 4, {zone: 'utc', locale: 'en-GB'})
  assert.deepEqual(ticks.map(tick => tick.label), ['Dec 2025', 'Jan 2026', 'Feb 2026'])
})

test('percentages keep the provider\'s precision and an absent one says so', () => {
  assert.equal(formatQuotaPercent(85), '85%')
  assert.equal(formatQuotaPercent(38.24), '38.2%')
  assert.equal(formatQuotaPercent(99.96), '100%')
  assert.equal(formatQuotaPercent(null), 'no reading')
  assert.equal(formatQuotaPercent(undefined), 'no reading')
})

test('days carry the year only when it is not the current one, and every window has one name', () => {
  const now = at('2026-10-10T12:00:00Z')
  assert.equal(formatQuotaDay('2026-10-06', now, 'en-GB'), 'Tue 6 Oct')
  // Punctuation around the year is the locale's business; that the year is there is ours.
  assert.match(formatQuotaDay('2025-12-30', now, 'en-GB'), /^Tue,? 30 Dec 2025$/)
  assert.equal(quotaWindowLabel('weekly'), 'Weekly')
  assert.equal(quotaWindowLabel('session'), '5-hour')
  assert.equal(quotaWindowLabel('monthly'), 'monthly')
})
