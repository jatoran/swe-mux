import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import {
  CRON_PRESETS, TARGET_KIND_COPY, actionLabel, cadenceLabel, draftFromSchedule, draftToBody,
  durationLabel, emptyDraft, needsAttention, orderSchedules, outcomeLabel, presetForCron,
  resumeDraft, targetIsMissing, untilLabel, type Schedule,
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
  action: 'spawn',
  target_run_id: '',
  target_kind: 'run',
  target_cut_message_id: '',
  target_cut_mode: '',
  context_ceiling_pct: 0,
  target: null,
  ...overrides,
})

const resuming = (overrides: Partial<Schedule> = {}): Schedule => schedule({
  action: 'resume',
  target_run_id: 'run_a',
  target: {
    run_id: 'run_a', missing: false, backend: 'claude', name: 'Storage migration',
    resolved_run_id: 'run_a', resolved_name: 'Storage migration',
  },
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
    // Calendar arithmetic over an expression is the thing being forbidden - working out
    // which weekday or hour a field lands on. Splitting one to normalize whitespace for an
    // exact preset match is not that: it decides which label to show, never when to fire.
    for (const arithmetic of ['getDay(', 'getUTCDay(', 'setHours(', 'setDate(', 'setMonth(']) {
      assert.ok(!source.includes(arithmetic), arithmetic)
    }
  }
})

test('the tab renders a blocked schedule as blocked and offers the fix', () => {
  const tab = readFileSync(join(import.meta.dirname, '..', 'src', 'ScheduleTab.tsx'), 'utf8')
  assert.ok(tab.includes('schedule.blocked &&'), 'a row that cannot fire must say so')
  // Both reasons a row cannot fire are switches, and each *grants* the one that is
  // holding it rather than linking to an overlay: a blocked row is exactly the moment
  // the decision is being made, and walking to a settings panel and back to make it is
  // the cost this replaced.
  assert.ok(tab.includes("id=\"project.scheduledRuns\" projectId={schedule.project_id}"), 'and grant the opt-in')
  assert.ok(tab.includes("id=\"schedules.install\""), 'and the install switch')
  assert.ok(tab.includes('<GrantButton'), 'through the shared grant control')
  // Revealing a session or opening settings is acting on something other than the
  // terminal underneath, so the mobile drawer gets out of the way.
  assert.ok(tab.includes('onDone()'))
})

test('every cron preset is a legal expression the field can hold, and each teaches something', () => {
  // The daemon parses these, so a typo here is a preset that produces a 400 on save.
  // Five fields, and nothing our parser does not accept: values, names, ranges, lists,
  // steps, and `*`.
  const FIELD = /^(\*|[0-9a-z]+(-[0-9a-z]+)?)(\/\d+)?(,(\*|[0-9a-z]+(-[0-9a-z]+)?)(\/\d+)?)*$/
  for (const preset of CRON_PRESETS) {
    const fields = preset.cron.split(' ')
    assert.equal(fields.length, 5, preset.label)
    for (const field of fields) assert.match(field, FIELD, `${preset.label}: ${field}`)
    assert.ok(preset.teaches.length > 20, preset.label)
    assert.equal(preset.cron, preset.cron.trim().toLowerCase())
  }
  // Between them they cover every part of the grammar, which is the point of the list.
  const all = CRON_PRESETS.map(preset => preset.cron).join(' ')
  for (const token of ['*', ',', '-', '/', 'mon', 'wed']) {
    assert.ok(all.includes(token), `no preset demonstrates ${token}`)
  }
  assert.equal(new Set(CRON_PRESETS.map(preset => preset.cron)).size, CRON_PRESETS.length)
})

test('a preset is matched from the expression, not remembered from the click', () => {
  // Which is what makes an edited expression read as Custom, and an edit that lands back
  // on a preset read as that preset again.
  assert.equal(presetForCron('0 9 * * *')?.label, 'Every day at 09:00')
  assert.equal(presetForCron('  0   9  *  *  * ')?.label, 'Every day at 09:00')
  assert.equal(presetForCron('0 9 * * MON')?.label, 'Every Monday at 09:00')
  assert.equal(presetForCron('0 10 * * *'), undefined)
  assert.equal(presetForCron(''), undefined)
})

test('the fortnightly case is named rather than faked', () => {
  // Cron counts days and months, never weeks, so "every other Wednesday" cannot be an
  // expression. The preset people ask for says so and points at the two things that do
  // work, instead of shipping a lookalike that quietly fires weekly.
  const weekly = CRON_PRESETS.filter(preset => preset.cron === '0 13 * * wed')
  assert.equal(weekly.length, 1, 'the Wednesday preset must exist')
  assert.match(weekly[0].teaches, /never weeks/)
  assert.match(weekly[0].teaches, /interval/)
  assert.ok(CRON_PRESETS.some(preset => preset.cron === '0 13 1,15 * *'))
})


test('a resume body carries its target and never a harness', () => {
  // The conversation's History row already fixes the harness, its arguments and the
  // working directory; the daemon refuses all three on a resume rather than ignoring
  // them, so a body that sent one would be rejected outright.
  const seeded = resumeDraft({ run_id: 'run_a', label: 'Migration', backend: 'claude', kind: 'run' })
  const body = draftToBody({ ...seeded, backend: 'codex', profile_id: 'p' })
  assert.equal(body.action, 'resume')
  assert.equal(body.target_run_id, 'run_a')
  assert.equal(body.target_kind, 'run')
  assert.equal(body.backend, undefined)
  assert.equal(body.profile_id, undefined)
  // A conversation opens once, so there is no overlap policy to choose.
  assert.equal(body.overlap, 'skip')
})

test('a resume seeded from a pane follows the work; one seeded from History pins it', () => {
  // The difference is the whole point of the two entry points: a row somebody is reading
  // is *that* conversation, while a live pane's conversation is still moving.
  const fromPane = resumeDraft({ run_id: 'run_a', label: 'Migration', backend: 'claude', kind: 'latest_of_session' })
  assert.equal(fromPane.target_kind, 'latest_of_session')
  assert.ok(fromPane.context_ceiling_pct > 0, 'a rolling target must carry a ceiling')
  const fromHistory = resumeDraft({ run_id: 'run_a', label: 'Migration', backend: 'claude', kind: 'run' })
  assert.equal(fromHistory.target_kind, 'run')
  assert.equal(fromHistory.context_ceiling_pct, 0)
  // `once` rather than a cron expression: "pick this up on Tuesday" must not arm a
  // nightly agent by accident.
  assert.equal(fromHistory.trigger_kind, 'once')
})

test('only the accumulating target carries a ceiling onto the wire', () => {
  const seeded = resumeDraft({ run_id: 'run_a', label: 'M', backend: 'claude', kind: 'latest_of_session' })
  const body = draftToBody({
    ...seeded,
    target_kind: 'fork_point',
    target_cut_message_id: 'm2',
    target_cut_mode: 'after',
  })
  assert.equal(body.context_ceiling_pct, 0, 'a fork starts fresh, so a ceiling would do nothing')
  assert.equal(body.target_cut_message_id, 'm2')
  assert.equal(body.target_cut_mode, 'after')
})

test('a resume row says what it does, and names where rolling work has got to', () => {
  assert.equal(actionLabel(schedule()), 'starts a new session')
  assert.equal(actionLabel(resuming()), 'resumes Storage migration')
  const rolled = resuming({
    target_kind: 'latest_of_session',
    target: {
      run_id: 'run_a', missing: false, backend: 'claude', name: 'Storage migration',
      resolved_run_id: 'run_c', resolved_name: 'Storage migration (continued)',
    },
  })
  assert.equal(actionLabel(rolled), 'continues Storage migration (continued)')
  assert.equal(actionLabel(resuming({ target_kind: 'fork_point' })), 'forks Storage migration')
})

test('a resume whose conversation is gone reads as broken rather than as armed', () => {
  const orphan = resuming({ target: { run_id: 'run_a', missing: true } })
  assert.equal(targetIsMissing(orphan), true)
  assert.equal(actionLabel(orphan), 'resumes a conversation that is gone')
  // Badged before the next fire records it, which may be a week away.
  assert.deepEqual(needsAttention([orphan]).map(item => item.id), ['sch_1'])
  // And a paused one is still somebody's decision, not an alarm.
  assert.deepEqual(needsAttention([{ ...orphan, enabled: false }]), [])
})

test('every target kind states its own cost', () => {
  // The kinds differ only in what they trade, so options that did not say so would be
  // three identical-looking choices with very different consequences at 3 a.m.
  assert.match(TARGET_KIND_COPY.run.detail, /Nothing drifts/)
  assert.match(TARGET_KIND_COPY.latest_of_session.detail, /compacted/)
  assert.match(TARGET_KIND_COPY.fork_point.detail, /identical context/)
})
