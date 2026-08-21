import assert from 'node:assert/strict'
import test from 'node:test'
import {
  formatDuration,
  isActiveLand,
  landAttentionRow,
  landGateNote,
  landHistoryOrder,
  landKindNote,
  landedAtByBranch,
  landQueueOrder,
  landStateLabel,
  landStateTone,
  landingSummary,
  parseLandQueue,
  parseLandVerifyCommand,
  parseVerifyProgress,
  recentLandings,
  verifyCommandEditable,
  verifyPlanNote,
  verifyProgressLabel,
} from '../src/gitLand.ts'
import { verifySetupPrompt } from '../src/landSetupPrompt.ts'

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

// -- a bounce stops speaking once the branch gets another answer ---------------
//
// Nothing ever closes a handed-back row: the agent's redo is a *new* request with a new
// id, so the bounced row stays terminal forever and the summary, which picks the most
// interesting terminal row, resurrects it for good. Observed 2026-08-21 - the collapsed
// strip read `worktree-watch-session-settle · returned to agent` for hours after that
// branch's redo had landed, through several unrelated landings.

test('a handback is superseded the moment a later request for its branch lands', () => {
  // The exact observed sequence: A hands back, B for the same branch lands, and the strip
  // must read idle rather than resurrecting A.
  const now = 1_800_000_000
  const summary = landingSummary(queueOf({
    requests: [
      { id: 'a', state: 'handed_back', branch: 'worktree-watch-session-settle',
        created_at: now - 7200, updated_at: now - 7000, finished_at: now - 7000 },
      { id: 'b', state: 'landed', branch: 'worktree-watch-session-settle',
        created_at: now - 3600, updated_at: now - 3400, finished_at: now - 3400 },
    ],
  }), gateOf({}), now)
  assert.equal(summary.queue, 'nothing queued · 1 landed recently')
  assert.equal(summary.tone, 'idle')
  assert.equal(summary.blocked, false)
})

test('supersession needs a *later* request, and needs it on the same branch', () => {
  const rows = (extra: Record<string, unknown>[]) => parseLandQueue({
    requests: [
      { id: 'a', state: 'handed_back', branch: 'worktree-alpha', created_at: 100, finished_at: 120 },
      ...extra,
    ],
  }).requests

  // Another branch landing says nothing about this one.
  assert.equal(landAttentionRow(rows([
    { id: 'b', state: 'landed', branch: 'worktree-beta', created_at: 200, finished_at: 220 },
  ]))?.id, 'a')

  // An *earlier* request for the same branch is what the handback already followed.
  assert.equal(landAttentionRow(rows([
    { id: 'b', state: 'landed', branch: 'worktree-alpha', created_at: 50, finished_at: 60 },
  ]))?.id, 'a')

  // A later one closes it, whichever terminal answer it reached.
  for (const state of ['landed', 'already_landed', 'refused', 'handed_back']) {
    const attention = landAttentionRow(rows([
      { id: 'b', state, branch: 'worktree-alpha', created_at: 200, finished_at: 220 },
    ]))
    // Either nothing needs attention, or it is the *newer* row - never the stale one.
    assert.notEqual(attention?.id, 'a', state)
  }
})

test('a withdrawn re-request does not close the handback it followed', () => {
  // `cancelled` is terminal but is not an answer about the branch: nothing ran, nothing
  // was decided, and the earlier handback is still the standing fact.
  const queue = parseLandQueue({
    requests: [
      { id: 'a', state: 'handed_back', branch: 'worktree-alpha', created_at: 100, finished_at: 120 },
      { id: 'b', state: 'cancelled', branch: 'worktree-alpha', created_at: 200, finished_at: 220 },
    ],
  })
  assert.equal(landAttentionRow(queue.requests)?.id, 'a')
})

test('the newest unanswered bounce speaks, not the stalest', () => {
  const queue = parseLandQueue({
    requests: [
      { id: 'old', state: 'handed_back', branch: 'worktree-alpha', created_at: 100, finished_at: 120 },
      { id: 'new', state: 'refused', branch: 'worktree-beta', created_at: 200, finished_at: 220 },
    ],
  })
  assert.equal(landAttentionRow(queue.requests)?.id, 'new')
})

test('an idle strip counts what recently landed, as a floor and never as a total', () => {
  const now = 1_800_000_000
  const requests = parseLandQueue({
    requests: [
      { id: 'a', state: 'landed', branch: 'one', created_at: now - 600, finished_at: now - 500 },
      { id: 'b', state: 'landed', branch: 'two', created_at: now - 1200, finished_at: now - 1100 },
      // Outside the window.
      { id: 'c', state: 'landed', branch: 'three', created_at: now - 200_000, finished_at: now - 190_000 },
      // Nothing moved, so it is not a landing.
      { id: 'd', state: 'already_landed', branch: 'four', created_at: now - 300, finished_at: now - 290 },
      { id: 'e', state: 'cancelled', branch: 'five', created_at: now - 300, finished_at: now - 290 },
    ],
  }).requests
  assert.equal(recentLandings(requests, now), 2)
  // A queue that has landed nothing lately says only the fact it is sure of.
  assert.equal(landingSummary(queueOf({ requests: [] }), gateOf({}), now).queue, 'nothing queued')
})

test('a summary with no queue read at all still renders', () => {
  const summary = landingSummary(null, null)
  assert.equal(summary.blocked, false)
  assert.equal(summary.queue, 'nothing queued')
})

// -- the prompt for setting a gate up somewhere else --------------------------
//
// The prompt is a *proposal* being handed to an agent, and the last section is the only
// thing that keeps a copyable setup prompt from reading as "an agent sets up its own
// gate". The daemon enforces the separation regardless of what any prompt says; what
// these assert is that the prompt does not send an agent off to do work whose final step
// it is not allowed to take without telling it so.

test('the setup prompt states the contract a verification command has to satisfy', () => {
  const prompt = verifySetupPrompt('.worktree-verify')
  // What the gate is used for, in the pipeline's own order.
  for (const term of ['reconcile', 'gate', 'fast-forward']) {
    assert.match(prompt, new RegExp(term, 'i'), term)
  }
  // The exit code is the only verdict, and nothing may launder it through a pipeline.
  assert.match(prompt, /exit code is the only verdict/i)
  assert.match(prompt, /\btail\b/)
  assert.match(prompt, /\bgrep\b/)
  // The contract's four bounds.
  assert.match(prompt, /working directory/i)
  assert.match(prompt, /parallel-safe/i)
  assert.match(prompt, /fixed port/i)
  assert.match(prompt, /bounded/i)
  // How to prove it honest: two at once, then a deliberate break.
  assert.match(prompt, /at the same time/i)
  assert.match(prompt, /nonzero/i)
})

test('the setup prompt names both conventions and which of them wins', () => {
  const prompt = verifySetupPrompt('.worktree-verify')
  assert.match(prompt, /\.worktree-verify/)
  assert.match(prompt, /\[worktree\] verify_command/)
  assert.match(prompt, /\.swe-mux\/config\.toml/)
  // An override, not a fallback: stating the preference without stating the resolution
  // order would leave an agent that wrote both expecting the script to run.
  assert.match(prompt, /override, not a fallback/i)
  // The script convention is named as this install resolves it, not hard-coded.
  assert.match(verifySetupPrompt('.verify-me'), /\.verify-me/)
  // An empty name still produces a usable prompt rather than a blank filename.
  assert.match(verifySetupPrompt(''), /\.worktree-verify/)
})

test('the setup prompt ends by telling the agent it cannot approve its own bytes', () => {
  // This is the assertion that fails if someone trims the prompt to "just the useful
  // part". Without it the button is a nicer way of saying an agent set up its own gate.
  const prompt = verifySetupPrompt('.worktree-verify')
  assert.match(prompt, /You cannot approve this/i)
  assert.match(prompt, /A human presses approve\./)
  // And it says where that act happens, because naming an authority obliges pointing at it.
  assert.match(prompt, /Approve these bytes/)
  // The instruction is last: anything after it reads as the real ending.
  assert.ok(prompt.trimEnd().endsWith('A human presses approve.'))
})

// -- which gate a land ran ----------------------------------------------------
//
// A skipped gate is the one thing on a finished row that the row's own states cannot
// show: it goes from merging the trunk straight to fast-forwarding, and reads afterwards
// exactly like a land that passed three minutes of pytest. So it is the one thing the
// vocabulary here draws, and the parser refuses to infer it from anything ambiguous.

test('only a skipped gate earns a note; a full one is what the states already say', () => {
  const rowOf = (verify_gate: unknown) => parseLandQueue({
    requests: [{ id: 'a', state: 'landed', branch: 'worktree-alpha', created_at: 10, verify_gate }],
  }).requests[0]

  assert.equal(rowOf('docs_only').verifyGate, 'docs_only')
  assert.equal(landGateNote(rowOf('docs_only')), 'documentation only · verification skipped')

  assert.equal(rowOf('full').verifyGate, 'full')
  assert.equal(landGateNote(rowOf('full')), '')
})

test('an unrecognised gate value reads as unclassified, never as a skip', () => {
  // The direction is the whole point: a value this build does not know must not be able
  // to render as "nothing verified this", and a row from a build that predates the
  // classifier has no classification rather than a full gate it never recorded.
  for (const value of [undefined, null, '', 'DOCS_ONLY', 'partial', 7, { gate: 'docs_only' }]) {
    const row = parseLandQueue({
      requests: [{ id: 'a', state: 'landed', branch: 'b', created_at: 10, verify_gate: value }],
    }).requests[0]
    assert.equal(row.verifyGate, '')
    assert.equal(landGateNote(row), '')
  }
})

test('a land going round the gate says so on the summary line while it runs', () => {
  // `landing` is the only state a skipping row is ever seen in, and it has no progress
  // reading of its own — without this the strip narrates it as an ordinary fast-forward.
  const summary = landingSummary(queueOf({
    requests: [{ id: 'a', state: 'landing', branch: 'worktree-docs', created_at: 10, verify_gate: 'docs_only' }],
  }), gateOf({}))
  assert.equal(
    summary.queue,
    'worktree-docs · fast-forwarding · documentation only · verification skipped',
  )
  assert.equal(summary.tone, 'busy')
  // Skipping the gate is not a *block*: nothing needs a human, and the strip stays shut.
  assert.equal(summary.blocked, false)
})

// -- verify-only rows ---------------------------------------------------------
//
// A verify-only request runs every state a land does except the last, in the same words,
// so the strip narrates it as a landing right up until it stops one step early - which
// is exactly when nobody is still watching. The kind has to be drawn, and its green has
// to be its own state rather than a `landed` that moved nothing.

test('a verify-only row is named as one, before the states it shares with a land', () => {
  const summary = landingSummary(queueOf({
    requests: [{
      id: 'a', state: 'verifying', kind: 'verify', branch: 'worktree-alpha', created_at: 10,
      verify_progress: { step_index: 2, step_name: 'ruff', elapsed_ms: 9000 },
    }],
  }), gateOf({}))
  assert.equal(summary.queue, 'worktree-alpha · verify only · verifying · step 2 · ruff · 9s')
  assert.equal(summary.tone, 'busy')
  assert.equal(summary.blocked, false)
})

test('an ordinary land is not labelled, for the same reason a full gate is not', () => {
  const row = parseLandQueue({
    requests: [{ id: 'a', state: 'landing', branch: 'worktree-alpha', created_at: 10 }],
  }).requests[0]
  assert.equal(row.kind, 'land')
  assert.equal(landKindNote(row), '')
  // And a kind this build does not know reads as a land, which is what every row
  // written before verify-only existed actually was.
  for (const kind of [undefined, null, '', 'VERIFY', 'ship', 7]) {
    const unknown = parseLandQueue({
      requests: [{ id: 'a', state: 'landing', branch: 'b', created_at: 10, kind }],
    }).requests[0]
    assert.equal(unknown.kind, 'land')
  }
})

test('a green verify-only row is terminal, is not a landing, and does not inflate the count', () => {
  const now = 1_800_000_000
  const queue = parseLandQueue({
    requests: [{
      id: 'a', state: 'verified', kind: 'verify', branch: 'worktree-alpha',
      created_at: now - 600, updated_at: now - 500, finished_at: now - 500,
    }],
  })
  const [row] = queue.requests
  assert.equal(isActiveLand(row), false)
  assert.equal(landStateLabel('verified'), 'Verified')
  assert.equal(landStateTone('verified'), 'ok')
  // Nothing moved, so "landed recently" must not count it.
  assert.equal(recentLandings(queue.requests, now), 0)
  // And the strip reads idle rather than making a headline out of a finished verify.
  const summary = landingSummary(queue, gateOf({}), now)
  assert.equal(summary.queue, 'nothing queued')
  assert.equal(summary.tone, 'idle')
})

test('a verify-only green closes the handback its branch is answering', () => {
  // The redo loop a handback asks for often runs through a verify first, so without
  // this the strip reports a branch as returned-to-agent forever - which is the exact
  // defect the supersession rule was written for, one request kind over.
  const queue = parseLandQueue({
    requests: [
      { id: 'a', state: 'handed_back', branch: 'worktree-alpha', created_at: 100, finished_at: 120 },
      { id: 'b', state: 'verified', kind: 'verify', branch: 'worktree-alpha', created_at: 200, finished_at: 220 },
    ],
  })
  assert.equal(landAttentionRow(queue.requests), null)
})

test('a bounced verify-only request still speaks, and says which act bounced', () => {
  const summary = landingSummary(queueOf({
    requests: [{
      id: 'a', state: 'handed_back', kind: 'verify', branch: 'worktree-alpha',
      created_at: 10, updated_at: 30, finished_at: 30,
    }],
  }), gateOf({}))
  assert.equal(summary.queue, 'worktree-alpha · verify only · returned to agent')
  assert.equal(summary.tone, 'warn')
})

test('a reused verdict is drawn as the skip it is, and never as a documentation one', () => {
  const rowOf = (verify_gate: unknown) => parseLandQueue({
    requests: [{ id: 'a', state: 'landed', branch: 'worktree-alpha', created_at: 10, verify_gate }],
  }).requests[0]
  assert.equal(rowOf('reused').verifyGate, 'reused')
  assert.equal(landGateNote(rowOf('reused')), 'verified earlier · gate reused')
  // The two skips are not interchangeable to a reader deciding whether to trust the
  // row: one means nobody ever ran this content, the other means this queue ran it.
  assert.notEqual(landGateNote(rowOf('reused')), landGateNote(rowOf('docs_only')))
})

// -- a worktree's own gate copy, and the block it causes ----------------------
//
// The gate resolves per worktree, so a branch that edited `.worktree-verify` presents
// bytes the primary's approval says nothing about. When such a land refused, the strip
// drew the Project-resolved copy - "verification approved" - over a refusal for an
// unapproved command, and offered nothing to approve at all (observed 2026-08-21).

const refusal = (body: Record<string, unknown>) => ({
  id: 'r1', state: 'refused', branch: 'worktree-alpha', project_root: 'D:/repo',
  worktree_root: 'D:/wt/alpha', created_at: 10, updated_at: 20, finished_at: 20,
  reason: 'the verification command is not approved',
  detail: { code: 'unapproved' }, ...body,
})

test('a land refused on a worktree copy names that checkout, and opens the strip', () => {
  const summary = landingSummary(
    queueOf({ requests: [refusal({})] }), gateOf({}), 1_800_000_000, 'D:/repo',
  )
  assert.deepEqual(summary.blockedWorktrees, [
    { worktreeRoot: 'D:/wt/alpha', branch: 'worktree-alpha' },
  ])
  // The gate itself reads approved - that is the whole trap - so the closed strip has
  // to say the other thing beside it, and open itself.
  assert.equal(summary.gate, 'verification approved · .worktree-verify · 1 worktree awaiting approval')
  assert.equal(summary.gateTone, 'warn')
  assert.equal(summary.blocked, true)
})

test('the Project root is never offered as a blocked worktree', () => {
  // The primary's copy is the block the strip already draws; drawing it twice is the
  // repetition the strip exists to remove. Path spelling is folded, as the daemon folds it.
  const summary = landingSummary(
    queueOf({ requests: [refusal({ worktree_root: 'd:\\repo' })] }),
    gateOf({ approved: false }), 1_800_000_000, 'D:/repo',
  )
  assert.deepEqual(summary.blockedWorktrees, [])
  assert.equal(summary.blocked, true)
})

test('a refusal that has been answered stops blocking anything', () => {
  // The same supersession rule the attention row uses: a branch that has since verified
  // or landed is not blocked on approving anything.
  const summary = landingSummary(queueOf({
    requests: [
      refusal({}),
      { id: 'r2', state: 'landed', branch: 'worktree-alpha', project_root: 'D:/repo',
        worktree_root: 'D:/wt/alpha', created_at: 40, updated_at: 50, finished_at: 50 },
    ],
  }), gateOf({}), 1_800_000_000, 'D:/repo')
  assert.deepEqual(summary.blockedWorktrees, [])
  assert.equal(summary.blocked, false)
})

test('only an approvable refusal is offered, and only once per checkout', () => {
  const summary = landingSummary(queueOf({
    requests: [
      // No bytes to show and no approval to give.
      refusal({ id: 'r0', branch: 'worktree-none', detail: { code: 'not_configured' } }),
      // A fact about the branch, not something the strip can clear.
      refusal({ id: 'r3', branch: 'worktree-moved', detail: {},
        reason: 'the branch moved after it verified' }),
      refusal({ id: 'r1', branch: 'worktree-alpha', created_at: 10, finished_at: 20 }),
      refusal({ id: 'r2', branch: 'worktree-beta', worktree_root: 'D:/wt/beta',
        created_at: 12, finished_at: 30 }),
      // A second refusal of the same checkout is the same block, not another one.
      refusal({ id: 'r4', branch: 'worktree-alpha', created_at: 14, finished_at: 40 }),
    ],
  }), gateOf({}), 1_800_000_000, 'D:/repo')
  assert.deepEqual(
    summary.blockedWorktrees,
    [
      { worktreeRoot: 'D:/wt/alpha', branch: 'worktree-alpha' },
      { worktreeRoot: 'D:/wt/beta', branch: 'worktree-beta' },
    ],
  )
})

test('a refusal code is read only off a row that refused', () => {
  const landed = parseLandQueue({
    requests: [{ id: 'a', state: 'landed', branch: 'b', created_at: 10,
      detail: { code: 'unapproved' } }],
  }).requests[0]
  // A stale code carried on a landed row would offer an approval for a gate that ran.
  assert.equal(landed.refusalCode, '')
  const refused = parseLandQueue({ requests: [refusal({})] }).requests[0]
  assert.equal(refused.refusalCode, 'unapproved')
  for (const code of [undefined, null, '', 'nope', 7]) {
    const row = parseLandQueue({
      requests: [refusal({ detail: { code } })],
    }).requests[0]
    assert.equal(row.refusalCode, '')
  }
})

// -- when each branch last landed --------------------------------------------

test('the map reads a landing date only from a land that actually moved the trunk', () => {
  const requests = parseLandQueue({
    requests: [
      { id: 'a', state: 'landed', branch: 'worktree-alpha', created_at: 10,
        updated_at: 20, finished_at: 20 },
      { id: 'b', state: 'landed', branch: 'worktree-alpha', created_at: 30,
        updated_at: 44, finished_at: 44 },
      // Nothing moved, so there is no moment to report and none is invented.
      { id: 'c', state: 'already_landed', branch: 'worktree-beta', created_at: 50,
        updated_at: 60, finished_at: 60 },
      { id: 'd', state: 'handed_back', branch: 'worktree-gamma', created_at: 70,
        updated_at: 80, finished_at: 80 },
    ],
  }).requests
  const landed = landedAtByBranch(requests)
  // The newest landing of that branch, not the first one found.
  assert.equal(landed.get('worktree-alpha'), 44)
  assert.equal(landed.has('worktree-beta'), false)
  assert.equal(landed.has('worktree-gamma'), false)
})
