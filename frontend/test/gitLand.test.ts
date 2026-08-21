import assert from 'node:assert/strict'
import test from 'node:test'
import {
  formatDuration,
  isActiveLand,
  landHistoryOrder,
  landQueueOrder,
  landStateLabel,
  landStateTone,
  landingSummary,
  parseLandQueue,
  parseLandVerifyCommand,
  parseVerifyProgress,
  verifyCommandEditable,
  verifyPlanNote,
  verifyProgressLabel,
} from '../src/gitLand.ts'

test('a land row parses with its detail and its two OIDs', () => {
  const queue = parseLandQueue({
    hourly_budget: 12,
    hold_timeout_seconds: 1800,
    retry_verification: false,
    requests: [
      {
        id: 'lnd_1',
        project_id: 'p1',
        project_root: 'D:/repo',
        worktree_root: 'D:/wt/alpha',
        branch: 'worktree-alpha',
        trunk_ref: 'main',
        origin: 'agent',
        origin_session_id: 'sess-1',
        state: 'handed_back',
        reason: 'merging main into worktree-alpha conflicted in 1 file(s)',
        detail: { step: 'reconcile', paths: ['shared.txt', 7] },
        trunk_before: 'a'.repeat(40),
        landed_oid: '',
        created_at: 100,
        updated_at: 120,
        finished_at: 120,
      },
    ],
  })
  assert.equal(queue.hourlyBudget, 12)
  assert.equal(queue.requests.length, 1)
  const [row] = queue.requests
  assert.equal(row.branch, 'worktree-alpha')
  assert.equal(row.origin, 'agent')
  // A non-string path is dropped rather than rendered as "7".
  assert.deepEqual(row.paths, ['shared.txt'])
  assert.equal(row.waitingSince, null)
})

test('a row with an unknown state is dropped rather than rendered blank', () => {
  const queue = parseLandQueue({
    requests: [
      { id: 'lnd_1', state: 'exploded' },
      { id: '', state: 'queued' },
      { id: 'lnd_2', state: 'queued' },
      'not an object',
    ],
  })
  assert.deepEqual(queue.requests.map(row => row.id), ['lnd_2'])
})

test('a malformed queue response is empty rather than throwing', () => {
  for (const raw of [null, undefined, 'text', 42, {}, { requests: 'no' }]) {
    const queue = parseLandQueue(raw)
    assert.deepEqual(queue.requests, [])
    assert.equal(queue.hourlyBudget, 0)
  }
})

test('the gate reads as unapproved unless the daemon says otherwise', () => {
  // The default matters more here than anywhere else in the drawer: a parse that
  // fell back to `approved: true` would draw a green gate over bytes nobody read.
  for (const raw of [null, {}, { configured: true }, { approved: 'yes' }]) {
    assert.equal(parseLandVerifyCommand(raw).approved, false)
  }
  const parsed = parseLandVerifyCommand({
    configured: true,
    source: 'convention',
    display: '.worktree-verify',
    digest: 'abc',
    approved: true,
    previously_approved: true,
    approved_source: 'old',
    current_source: 'new',
  })
  assert.equal(parsed.approved, true)
  assert.equal(parsed.approvedSource, 'old')
  assert.equal(parsed.currentSource, 'new')
})

test('active and finished rows are partitioned by their state', () => {
  const active = ['queued', 'waiting', 'reconciling', 'verifying', 'landing']
  const finished = ['landed', 'already_landed', 'handed_back', 'refused', 'cancelled']
  for (const state of active) {
    const queue = parseLandQueue({ requests: [{ id: 'x', state }] })
    assert.equal(isActiveLand(queue.requests[0]), true, state)
  }
  for (const state of finished) {
    const queue = parseLandQueue({ requests: [{ id: 'x', state }] })
    assert.equal(isActiveLand(queue.requests[0]), false, state)
  }
})

test('every state has a label, and a hold does not read as a failure', () => {
  const states = [
    'queued', 'waiting', 'reconciling', 'verifying', 'landing',
    'landed', 'already_landed', 'handed_back', 'refused', 'cancelled',
  ] as const
  for (const state of states) assert.ok(landStateLabel(state).length > 0, state)
  // A waiting row is a hold with a cause, not a failure: an agent that asked to land
  // and kept working is the common case, and a warn tone there would train the
  // operator to intervene where nothing is wrong.
  assert.equal(landStateTone('waiting'), 'idle')
  assert.equal(landStateTone('landed'), 'ok')
  assert.equal(landStateTone('handed_back'), 'warn')
  assert.equal(landStateTone('refused'), 'warn')
  assert.equal(landStateTone('verifying'), 'busy')
  // Already on the trunk is neither a success nor a failure: nothing moved, and
  // nothing went wrong. Rendering it 'ok' would claim a land that did not happen.
  assert.equal(landStateTone('already_landed'), 'idle')
  assert.equal(landStateLabel('already_landed'), 'Already on trunk')
})

// -- what a running gate reports about itself --------------------------------
//
// Every assertion below is about one property: nothing is rendered that was not
// observed. The tempting failure is the opposite one - a plausible total, a percentage,
// a smooth bar - and each of those makes a running gate *less* trustworthy than the
// opaque "verifying" it replaced, because a wrong number gets acted on.

test('a progress reading is attached only to a verifying row', () => {
  const progress = { step_index: 2, step_name: 'ruff', elapsed_ms: 1000 }
  for (const state of ['queued', 'waiting', 'reconciling', 'landing', 'landed']) {
    const queue = parseLandQueue({ requests: [{ id: 'x', state, verify_progress: progress }] })
    assert.equal(queue.requests[0].verifyProgress, null, state)
  }
  const verifying = parseLandQueue({
    requests: [{ id: 'x', state: 'verifying', verify_progress: progress }],
  })
  assert.equal(verifying.requests[0].verifyProgress?.stepName, 'ruff')
})

test('a total the run has already passed is dropped rather than stretched', () => {
  // Never "step 8 of 7". Both ends refuse it: the daemon withdraws the plan, and the
  // browser refuses a count below the step it is on even if one arrived anyway.
  const beyond = parseVerifyProgress({
    step_index: 8, step_name: 'extra', beyond_plan: true,
    expected_step_count: 7, expected_steps: ['a', 'b'],
  })
  assert.equal(beyond?.expectedStepCount, null)
  assert.deepEqual(beyond?.expectedSteps, [])

  const inconsistent = parseVerifyProgress({ step_index: 5, expected_step_count: 3 })
  assert.equal(inconsistent?.expectedStepCount, null)
})

test('a malformed progress payload degrades to unknown rather than to a number', () => {
  for (const raw of [null, undefined, 'text', 42]) {
    assert.equal(parseVerifyProgress(raw), null)
  }
  const junk = parseVerifyProgress({
    step_index: 'three', expected_step_count: 'seven', lines: -5,
    completed_steps: ['not an object', { name: '' }, { name: 'ok', duration_ms: 'x' }],
  })
  assert.equal(junk?.stepIndex, 0)
  assert.equal(junk?.expectedStepCount, null)
  assert.equal(junk?.lines, 0)
  assert.deepEqual(junk?.completedSteps, [{ name: 'ok', durationMs: 0 }])
  // A missing attempt count is one attempt, never zero: "attempt 0 of 0" is not a thing.
  assert.equal(junk?.attempt, 1)
  assert.equal(junk?.attempts, 1)
})

test('the progress label has three honest forms and no fourth', () => {
  assert.equal(verifyProgressLabel(null), '')

  // A measured total, from a byte-identical run that already passed.
  assert.equal(verifyProgressLabel(parseVerifyProgress({
    step_index: 3, step_name: 'mypy', expected_step_count: 7,
    expected_steps: ['a', 'b', 'c', 'd', 'e', 'f', 'g'], elapsed_ms: 190_000,
  })), 'step 3 of 7 · mypy · 3m 10s')

  // No such run on record: the step number is still a fact, the total is simply absent.
  assert.equal(verifyProgressLabel(parseVerifyProgress({
    step_index: 3, step_name: 'mypy', elapsed_ms: 190_000,
  })), 'step 3 · mypy · 3m 10s')

  // A gate that announces nothing. Lines are evidence of movement, not progress.
  assert.equal(verifyProgressLabel(parseVerifyProgress({
    step_index: 0, elapsed_ms: 252_000, lines: 1204,
  })), `4m 12s · ${(1204).toLocaleString()} lines`)
})

test('a retry says which attempt it is on', () => {
  assert.equal(verifyProgressLabel(parseVerifyProgress({
    step_index: 1, step_name: 'pytest', elapsed_ms: 5000, attempt: 2, attempts: 2,
  })), 'step 1 · pytest · 5s · attempt 2 of 2')
})

test('no progress label ever contains a percentage', () => {
  // The steps of a real gate take 175s and 3s in one run, so a proportion would be
  // fiction. This is the assertion that fails if someone adds one back.
  const cases = [
    { step_index: 3, step_name: 'mypy', expected_step_count: 7, elapsed_ms: 190_000 },
    { step_index: 0, elapsed_ms: 1000, lines: 20 },
    { step_index: 9, beyond_plan: true, elapsed_ms: 1000 },
  ]
  for (const raw of cases) {
    assert.ok(!verifyProgressLabel(parseVerifyProgress(raw)).includes('%'), JSON.stringify(raw))
  }
})

test('durations read as a human would say them', () => {
  assert.equal(formatDuration(0), '0s')
  assert.equal(formatDuration(9400), '9s')
  assert.equal(formatDuration(72_000), '1m 12s')
  assert.equal(formatDuration(3_840_000), '1h 04m')
  // A negative arrival cannot render as "-3s"; it is not a duration.
  assert.equal(formatDuration(-5000), '0s')
})

// -- the queue's own order ---------------------------------------------------

test('the queue reads in the order the pipeline will reach it, history backwards', () => {
  const rows = [
    { id: 'c', state: 'queued', created_at: 300 },
    { id: 'a', state: 'waiting', created_at: 100 },
    { id: 'b', state: 'queued', created_at: 200 },
    { id: 'old', state: 'landed', created_at: 10, finished_at: 50 },
    { id: 'new', state: 'refused', created_at: 20, finished_at: 900 },
  ]
  const queue = parseLandQueue({ requests: rows })
  assert.deepEqual(landQueueOrder(queue.requests).map(row => row.id), ['a', 'b', 'c'])
  assert.deepEqual(landHistoryOrder(queue.requests).map(row => row.id), ['new', 'old'])
})

// -- the verification command's editable half --------------------------------

test('the gate carries what is editable beside what resolved', () => {
  const gate = parseLandVerifyCommand({
    configured: true, source: 'project_config', display: 'pytest -q', digest: 'abc',
    approved: false, config_command: 'pytest -q', config_revision: 'r1',
    config_status: 'ready', config_path: 'D:/repo/.swe-mux/config.toml',
    script_name: '.worktree-verify', script_present: true,
    plan: { steps: ['pytest', 'ruff'], duration_ms: 240_000, observed_at: 5 },
  })
  assert.equal(gate.configCommand, 'pytest -q')
  assert.equal(gate.configRevision, 'r1')
  assert.equal(gate.scriptPresent, true)
  assert.equal(verifyCommandEditable(gate), true)
  assert.deepEqual(gate.plan?.steps, ['pytest', 'ruff'])
})

test('an absent revision reads as "missing" rather than as no expectation', () => {
  // '' would be sent as "I have no opinion", which skips the guard the daemon uses to
  // refuse a write over someone else's edit.
  assert.equal(parseLandVerifyCommand({}).configRevision, 'missing')
  assert.equal(parseLandVerifyCommand({ config_revision: '' }).configRevision, 'missing')
})

test('a config nobody can write is not offered as editable', () => {
  for (const status of ['read-only', 'malformed']) {
    const gate = parseLandVerifyCommand({ configured: true, config_status: status })
    assert.equal(verifyCommandEditable(gate), false, status)
  }
  assert.equal(verifyCommandEditable(parseLandVerifyCommand({ config_status: 'missing' })), true)
})

test('a plan is described as a past run, never as a prediction of the current one', () => {
  assert.equal(verifyPlanNote(null), '')
  const note = verifyPlanNote({ steps: ['a', 'b', 'c'], durationMs: 240_000, observedAt: 1 })
  assert.match(note, /Last passing run/)
  assert.match(note, /3 steps in 4m 00s/)
  // Nothing in it claims anything about a run in progress.
  assert.ok(!/will|remaining|left/i.test(note))
})

test('a plan with no steps is no plan', () => {
  assert.equal(parseLandVerifyCommand({ plan: { steps: [], duration_ms: 10 } }).plan, null)
  assert.equal(parseLandVerifyCommand({ plan: { steps: [1, 2] } }).plan, null)
})

// -- the one line the landing strip shows without being opened ----------------
//
// Landing sits above a map and must not turn the map into a panel. The summary is what
// makes that affordable: a reader who is not landing anything reads one line and moves
// on, and a reader who is does not have to open anything to see a gate running.

const queueOf = (body: Record<string, unknown>) => parseLandQueue({
  installed_enabled: true, project_enabled: true, agent_grant: 'draft', ...body,
})
const gateOf = (body: Record<string, unknown>) => parseLandVerifyCommand({
  configured: true, display: '.worktree-verify', digest: 'd1', approved: true, ...body,
})

test('a quiet queue still states both halves rather than going blank', () => {
  // "Nothing queued" is a fact worth reading. Blank would make a quiet queue and an
  // unread one look the same, which is the conflation `installed_enabled` exists against.
  const summary = landingSummary(queueOf({ requests: [] }), gateOf({}))
  assert.equal(summary.gate, 'verification approved · .worktree-verify')
  assert.equal(summary.gateTone, 'ok')
  assert.equal(summary.queue, 'nothing queued')
  assert.equal(summary.blocked, false)
})

test('a running gate reaches the summary line, with what is behind it', () => {
  const summary = landingSummary(queueOf({
    requests: [
      { id: 'a', state: 'verifying', branch: 'worktree-alpha', created_at: 10,
        verify_progress: { step_index: 3, step_name: 'mypy', expected_step_count: 7, elapsed_ms: 190_000 } },
      { id: 'b', state: 'queued', branch: 'worktree-beta', created_at: 20 },
    ],
  }), gateOf({}))
  assert.equal(summary.queue, 'worktree-alpha · verifying · step 3 of 7 · mypy · 3m 10s · 1 behind')
  assert.equal(summary.tone, 'busy')
  // The strip does not open itself for a healthy run: nothing needs a human.
  assert.equal(summary.blocked, false)
})

test('the two things that block every land are what open the strip', () => {
  // Exactly two, and both have their remedy inside the strip. A surface that cannot work
  // must not render as merely quiet (`setting-links.md`).
  const stopped = landingSummary(queueOf({ installed_enabled: false, requests: [] }), gateOf({}))
  assert.equal(stopped.blocked, true)
  assert.equal(stopped.tone, 'warn')
  assert.match(stopped.queue, /switched off/)

  const unapproved = landingSummary(queueOf({ requests: [] }), gateOf({ approved: false }))
  assert.equal(unapproved.blocked, true)
  assert.equal(unapproved.gate, 'verification not approved')
  assert.equal(unapproved.gateTone, 'warn')

  const absent = landingSummary(queueOf({ requests: [] }), gateOf({ configured: false }))
  assert.equal(absent.blocked, true)
  assert.equal(absent.gate, 'no verification command')
})

test('an unread gate is not a blocked one', () => {
  // `null` is "the daemon has not answered yet". Treating it as blocked would flash the
  // strip open on every mount, which is a worse lie than saying nothing.
  const summary = landingSummary(queueOf({ requests: [] }), null)
  assert.equal(summary.blocked, false)
  assert.equal(summary.gate, 'verification unread')
  assert.equal(summary.gateTone, 'idle')
})

test('the install stop outranks a running row, because it is the one inert-looking state', () => {
  const summary = landingSummary(queueOf({
    installed_enabled: false,
    requests: [{ id: 'a', state: 'queued', branch: 'worktree-alpha', created_at: 10 }],
  }), gateOf({}))
  assert.match(summary.queue, /nothing will move/)
  assert.equal(summary.tone, 'warn')
})

test('a handback surfaces only once nothing is queued behind it', () => {
  const handed = landingSummary(queueOf({
    requests: [{ id: 'a', state: 'handed_back', branch: 'worktree-alpha', created_at: 10, updated_at: 30, finished_at: 30 }],
  }), gateOf({}))
  assert.equal(handed.queue, 'worktree-alpha · returned to agent')
  assert.equal(handed.tone, 'warn')
  // A handback is not a *block*: the gate is fine and the queue would run the next one.
  assert.equal(handed.blocked, false)

  const busy = landingSummary(queueOf({
    requests: [
      { id: 'a', state: 'handed_back', branch: 'worktree-alpha', created_at: 10, finished_at: 30 },
      { id: 'b', state: 'queued', branch: 'worktree-beta', created_at: 20 },
    ],
  }), gateOf({}))
  assert.equal(busy.queue, '1 queued · next worktree-beta')
})

test('a summary with no queue read at all still renders', () => {
  const summary = landingSummary(null, null)
  assert.equal(summary.blocked, false)
  assert.equal(summary.queue, 'nothing queued')
})
