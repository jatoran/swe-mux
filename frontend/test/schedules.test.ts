import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import {
  cadenceLabel, draftFromSchedule, draftToBody, durationLabel, emptyDraft, needsAttention,
  orderSchedules, outcomeLabel, untilLabel, type Schedule,
} from '../src/schedules.ts'

const schedule = (overrides: Partial<Schedule> = {}): Schedule => ({
  id: 'sch_1',
  project_id: 'p1',
  project_name: 'Main',
  label: 'Nightly health check',
  enabled: true,
  trigger_kind: 'cron',
  cron: '0 3 * * *',
  interval_seconds: null,
  run_at: null,
  timezone: '',
  catch_up: false,
  overlap: 'skip',
  backend: 'claude',
  profile_id: '',
  cwd: '',
  session_name: '',
  prompt: 'Check health.',
  follow_ups: [],
  daily_run_cap: 0,
  next_fire_at: 2_000,
  last_fire_at: null,
  last_session_id: null,
  last_outcome: '',
  last_reason: '',
  disabled_reason: '',
  blocked: '',
  runs: [],
  revision: 1,
  created_at: 0,
  updated_at: 0,
  ...overrides,
})

test('the draft round-trips through a schedule without changing its trigger', () => {
  const source = schedule({ trigger_kind: 'interval', cron: '', interval_seconds: 21_600 })
  const draft = draftFromSchedule(source)
  assert.equal(draft.interval_minutes, 360)
  const body = draftToBody(draft)
  assert.equal(body.trigger_kind, 'interval')
  assert.equal(body.interval_seconds, 21_600)
  // A cron field left over from another trigger must not travel: the daemon would
  // then hold two contradictory definitions of when to fire.
  assert.equal(body.cron, undefined)
})

test('a cron draft sends only its expression, and blank follow-ups are dropped', () => {
  const draft = { ...emptyDraft(), label: ' Nightly ', cron: ' 0 3 * * * ', follow_ups: ['do this', '  '] }
  const body = draftToBody(draft)
  assert.equal(body.cron, '0 3 * * *')
  assert.equal(body.label, 'Nightly')
  assert.deepEqual(body.follow_ups, ['do this'])
  assert.equal(body.interval_seconds, undefined)
  assert.equal(body.run_at, undefined)
})

test('cadence reads as words rather than as stored fields', () => {
  assert.equal(cadenceLabel(schedule({ timezone: 'Europe/Oslo' })), 'cron 0 3 * * * (Europe/Oslo)')
  assert.equal(
    cadenceLabel(schedule({ trigger_kind: 'interval', cron: '', interval_seconds: 21_600 })),
    'every 6 h',
  )
  assert.equal(durationLabel(900), '15 min')
  assert.equal(durationLabel(172_800), '2 d')
})

test('a fire time in the future reads as a countdown and a past one as due', () => {
  assert.equal(untilLabel(1_000, 950), 'in under a minute')
  assert.equal(untilLabel(4_000, 1_000), 'in 50m')
  assert.equal(untilLabel(1_000, 4_000), 'due')
  assert.equal(untilLabel(null, 1_000), 'not scheduled')
})

test('a schedule that never ran says so rather than showing an empty verdict', () => {
  assert.equal(outcomeLabel(schedule()), 'never run')
  assert.match(outcomeLabel(schedule({ last_outcome: 'failed', last_fire_at: 1_700_000_000 })), /^failed /)
})

test('attention counts failures and armed-but-blocked, never a deliberate pause', () => {
  const rows = [
    schedule({ id: 'a', last_outcome: 'failed' }),
    schedule({ id: 'b', blocked: 'automation_disabled' }),
    // Paused on purpose: someone already decided this, so badging it would train
    // the reader to ignore the badge.
    schedule({ id: 'c', enabled: false, blocked: 'automation_disabled' }),
    schedule({ id: 'd', last_outcome: 'spawned' }),
  ]
  assert.deepEqual(needsAttention(rows).map(row => row.id), ['a', 'b'])
})

test('ordering puts armed schedules first, soonest first', () => {
  const rows = [
    schedule({ id: 'later', next_fire_at: 5_000 }),
    schedule({ id: 'paused', enabled: false, next_fire_at: null }),
    schedule({ id: 'soon', next_fire_at: 1_000 }),
    schedule({ id: 'unarmed', next_fire_at: null }),
  ]
  assert.deepEqual(orderSchedules(rows).map(row => row.id), ['soon', 'later', 'unarmed', 'paused'])
})

test('the tab never computes a fire time of its own', () => {
  // Cron plus a timezone plus daylight saving has exactly one implementation, in the
  // daemon. A second one in the browser would disagree with it twice a year, and the
  // editor would then promise a time the schedule does not keep.
  const tab = readFileSync(join(import.meta.dirname, '..', 'src', 'ScheduleTab.tsx'), 'utf8')
  const helpers = readFileSync(join(import.meta.dirname, '..', 'src', 'schedules.ts'), 'utf8')
  assert.ok(tab.includes("'/api/schedules/preview'"), 'the editor must ask the daemon for its preview')
  for (const source of [tab, helpers]) {
    assert.doesNotMatch(source, /cron.*(split|match)\(/i)
  }
})

test('the tab renders a blocked schedule as blocked and offers the fix', () => {
  const tab = readFileSync(join(import.meta.dirname, '..', 'src', 'ScheduleTab.tsx'), 'utf8')
  assert.ok(tab.includes('schedule.blocked &&'), 'a row that cannot fire must say so')
  assert.ok(tab.includes('onOpenProjectSettings(schedule.project_id)'), 'and point at the opt-in')
  // Revealing a session or opening settings is acting on something other than the
  // terminal underneath, so the mobile drawer gets out of the way.
  assert.ok(tab.includes('onDone()'))
})
