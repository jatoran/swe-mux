import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import type { AwaitingReason, Session, SessionState, StandingActivity, StandingActivityKind } from '../src/types.ts'
import { activityBadges, awaitingLabel, idleLabel, sessionDotClass, sessionStatus, stateDotClass } from '../src/sessionStatus.ts'
import { classifySoundEvent } from '../src/sessionSounds.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

const ALL_STATES: SessionState[] = ['starting', 'running', 'working', 'idle', 'awaiting', 'exited', 'crashed']
const ALL_AWAITING: AwaitingReason[] = ['approval', 'question', 'elicitation', 'rate_limit', 'authentication']

const agent = (state: SessionState, extra: Partial<Session> = {}) =>
  ({ id: 's1', name: 's1', backend: 'claude', state, context_pct: 0, compaction_count: 0, ...extra }) as unknown as Session

test('state → indicator mapping is total and unambiguous', () => {
  const classes = new Set<string>()
  for (const state of ALL_STATES) {
    const dot = stateDotClass(state)
    assert.match(dot, /^state-dot \S+$/, `${state} must render a non-blank dot class`)
    classes.add(dot)
    const label = sessionStatus(agent(state))
    assert.ok(label.trim().length > 0, `${state} must render a non-empty status label`)
  }
  assert.equal(classes.size, ALL_STATES.length, 'each state must render a distinct indicator')
})

test('a missing session (pending mobile tab) falls back to the neutral dot', () => {
  assert.equal(stateDotClass(undefined), 'state-dot running')
})

test('terminal states never render the blinking working indicator', () => {
  for (const state of ['idle', 'exited', 'crashed'] as SessionState[]) {
    assert.ok(!stateDotClass(state).includes('working'), `${state} must clear the working dot`)
    assert.ok(!sessionStatus(agent(state)).startsWith('working'), `${state} must not read as working`)
  }
})

test('interrupt intent is visible and stops the working pulse', () => {
  const pending = agent('working', { interrupt_pending_at: 1_700_000_000 })
  assert.equal(sessionStatus(pending), 'interrupt requested')
  assert.equal(sessionDotClass(pending), 'state-dot working interrupting')
})

test('awaiting distinguishes approval, question, elicitation, rate limit, and SSH auth', () => {
  const labels = new Set(ALL_AWAITING.map(reason => awaitingLabel(agent('awaiting', { awaiting_reason: reason }))))
  assert.equal(labels.size, ALL_AWAITING.length, 'each awaiting sub-reason needs a distinct affordance')
  assert.equal(awaitingLabel(agent('awaiting', { awaiting_reason: 'question' })), 'awaiting answer')
  assert.equal(awaitingLabel(agent('awaiting', { awaiting_reason: 'elicitation' })), 'awaiting input')
  assert.equal(awaitingLabel(agent('awaiting', { awaiting_reason: 'rate_limit' })), 'rate limited')
  assert.equal(awaitingLabel(agent('awaiting', { awaiting_reason: 'authentication' })), 'awaiting SSH authentication')
  // Missing sub-reason keeps the conservative historical default.
  assert.equal(awaitingLabel(agent('awaiting')), 'awaiting approval')
  assert.ok(sessionStatus(agent('awaiting', { awaiting_reason: 'question' })).startsWith('awaiting answer'))
})

test('idle_prompt-driven idle renders ready, never awaiting approval', () => {
  // The backend maps idle_prompt notifications to state idle; the frontend must
  // render that as ready-for-input with no approval affordance.
  const label = sessionStatus(agent('idle'))
  assert.ok(label.startsWith('ready'))
  assert.ok(!label.includes('awaiting'))
})

test('idle distinguishes a finished turn from one waiting on background work', () => {
  // Both are genuinely idle (composer accepts input, delivery is safe), so this
  // stays a sub-reason rather than a state — but "turn complete" reads as
  // "nothing more is coming", which is wrong while the agent will self-resume.
  const done = sessionStatus(agent('idle'))
  const waiting = sessionStatus(agent('idle', { idle_reason: 'waiting_on_background' }))
  assert.equal(done, 'ready · turn complete')
  assert.equal(waiting, 'ready · background work running')
  assert.notEqual(done, waiting)
  assert.equal(idleLabel(agent('idle', { idle_reason: null })), 'ready · turn complete')
  // The state axis is untouched: the dot is the same idle dot.
  assert.equal(stateDotClass('idle'), 'state-dot idle')
})

test('a background-wait turn end does not fire the completion sound', () => {
  const event = (payload: Record<string, unknown>) =>
    ({ type: 'turn_ended', session_id: 's1', payload }) as never
  assert.ok(classifySoundEvent(event({ scope: 'root' })))
  assert.equal(classifySoundEvent(event({ scope: 'root', idle_reason: 'waiting_on_background' })), null)
})

test('the ready sound is suppressed while the agent still has work running', () => {
  // Mirrors `running_work` in push.py: the same idle raises a sound here and a
  // lock-screen push there, so the two classifiers have to agree on what "still
  // working" means or one device stays noisy after the other is fixed.
  const ready = (payload: Record<string, unknown> = {}) =>
    classifySoundEvent({ type: 'state_changed', session_id: 's1', payload: { scope: 'root', state: 'idle', previous: 'working', ...payload } } as never)
  assert.equal(ready()?.event, 'waiting')
  assert.equal(ready({ standing: ['subagents'] }), null)
  assert.equal(ready({ standing: ['background_tasks'] }), null)
  assert.equal(ready({ idle_reason: 'waiting_on_background' }), null)
  // A scheduled engagement is not running work: an idle session with an armed
  // loop has genuinely finished its turn.
  assert.equal(ready({ standing: ['loop', 'cron'] })?.event, 'waiting')
})

test('a session settling after startup does not fire the ready sound', () => {
  // Startup idle is inferred from PTY quiet ~1s after spawn and is not even
  // input-ready; nothing about it means the agent wants the human.
  const event = (previous: string) =>
    ({ type: 'state_changed', session_id: 's1', payload: { scope: 'root', state: 'idle', previous } }) as never
  assert.equal(classifySoundEvent(event('starting')), null)
  assert.equal(classifySoundEvent(event('working'))?.event, 'waiting')
})

test('shell sessions render the raw state', () => {
  const shell = { ...agent('running'), backend: 'shell' } as unknown as Session
  assert.equal(sessionStatus(shell), 'running')
})

test('remote boundaries and SSH authentication are prominent', () => {
  assert.equal(
    sessionStatus(agent('idle', { runtime_boundary: 'unknown' })),
    'terminal boundary unknown',
  )
  assert.equal(
    sessionStatus(agent('idle', { runtime_boundary: 'remote', remote_authority: 'example.test' })),
    'remote boundary · idle',
  )
  assert.equal(
    sessionStatus(agent('awaiting', { runtime_boundary: 'remote', awaiting_reason: 'authentication' })),
    'awaiting SSH authentication',
  )
  assert.equal(
    sessionStatus(agent('running', { runtime_boundary: 'remote', remote_transport_state: 'ended' })),
    'SSH connection ended',
  )
})

test('dense status chrome omits compaction counts', () => {
  const status = sessionStatus(agent('working', { context_pct: 0.42, compaction_count: 3 }))
  assert.equal(status, 'working · 42%')
  assert.ok(!status.includes('compact'))
})

const ALL_ACTIVITY: StandingActivityKind[] = ['loop', 'cron', 'background_tasks', 'subagents']

const annotation = (kind: StandingActivityKind, extra: Partial<StandingActivity> = {}): StandingActivity =>
  ({ kind, source: 'transcript', evidence: 'test', since: 0, expires_at: null, count: 1, detail: null, ...extra })

test('every standing-activity kind renders a glyph and a label', () => {
  for (const kind of ALL_ACTIVITY) {
    const badges = activityBadges(agent('idle', { standing_activity: [annotation(kind)] }))
    assert.equal(badges.length, 1, `${kind} must render exactly one badge`)
    assert.ok(badges[0].glyph.trim().length > 0, `${kind} needs a glyph`)
    assert.ok(badges[0].label.trim().length > 0, `${kind} needs a label`)
    assert.ok(badges[0].title.trim().length > 0, `${kind} needs a tooltip`)
  }
  assert.equal(activityBadges(agent('idle')).length, 0)
  assert.equal(activityBadges(agent('idle', { standing_activity: [] })).length, 0)
})

test('scheduled annotations never change the dot: idle with a loop stays green', () => {
  // Green keeps meaning "ready — you can type and send". A loop or cron is a
  // *scheduled* engagement with nothing running now, so it adds a glyph and
  // nothing else.
  const armed = agent('idle', { standing_activity: [annotation('loop', { detail: 'watching CI' })] })
  assert.equal(sessionDotClass(armed), 'state-dot idle')
  assert.equal(
    sessionDotClass(agent('idle', { standing_activity: [annotation('cron', { count: 2 })] })),
    'state-dot idle',
  )
  const label = sessionStatus(armed)
  assert.ok(label.startsWith('ready'), 'idle with a loop must still read ready')
  assert.ok(!label.includes('awaiting'))
  assert.ok(!label.includes('working'))
})

test('idle with running work renders the standing ring, not plain green', () => {
  // The one standing-activity exception to the dot rule: a session whose root
  // turn ended while subagents or background tasks still run renders a hollow
  // blue ring — "an agent is engaged, and you can type". A shape difference,
  // not a pulse, so it survives prefers-reduced-motion.
  for (const kind of ['subagents', 'background_tasks'] as StandingActivityKind[]) {
    assert.equal(
      sessionDotClass(agent('idle', { standing_activity: [annotation(kind)] })),
      'state-dot idle standing',
      `idle + running ${kind} must render the standing ring`,
    )
  }
  // Only the idle axis: a working session is already blue and pulsing, and a
  // terminal state's dot is process ground truth.
  assert.equal(
    sessionDotClass(agent('working', { standing_activity: [annotation('subagents')] })),
    'state-dot working',
  )
  assert.equal(
    sessionDotClass(agent('exited', { standing_activity: [annotation('subagents')] })),
    'state-dot exited',
  )
  // Pending mobile tabs keep the neutral fallback.
  assert.equal(sessionDotClass(undefined), 'state-dot running')
})

test('the status line composes standing activity after the state', () => {
  assert.equal(
    sessionStatus(agent('idle', { standing_activity: [annotation('loop')] })),
    'ready · loop armed',
  )
  assert.equal(
    sessionStatus(agent('idle', { standing_activity: [annotation('background_tasks', { count: 2 })] })),
    'ready · 2 background tasks',
  )
  assert.equal(
    sessionStatus(agent('working', { state_detail: 'Task', standing_activity: [annotation('subagents', { count: 3 })] })),
    'working · Task · 3 subagents',
  )
  // Composable: several annotations render together, in a stable order.
  assert.equal(
    sessionStatus(agent('idle', { standing_activity: [annotation('subagents'), annotation('loop')] })),
    'ready · loop armed · 1 subagent',
  )
})

test('the background annotation supersedes the derived idle_reason text', () => {
  // idle_reason is derived server-side from the same annotation; rendering
  // both would say one fact twice.
  const session = agent('idle', {
    idle_reason: 'waiting_on_background',
    standing_activity: [annotation('background_tasks', { count: 1 })],
  })
  assert.equal(sessionStatus(session), 'ready · 1 background task')
  // Without the annotation (older daemon), the compat sub-reason still renders.
  assert.equal(
    sessionStatus(agent('idle', { idle_reason: 'waiting_on_background' })),
    'ready · background work running',
  )
})

test('degraded measurement confidence hides percentages and marks them stale', () => {
  assert.equal(
    sessionStatus(agent('idle', { context_pct: 0.72, parser_status: 'degraded' })),
    'ready · turn complete · measurements stale',
  )
})

test('loop and cron share one glyph; the tooltip distinguishes them', () => {
  const both = activityBadges(agent('idle', {
    standing_activity: [annotation('loop'), annotation('cron', { count: 2, detail: '*/5 * * * *' })],
  }))
  assert.equal(both.length, 2)
  assert.equal(both[0].glyph, both[1].glyph)
  assert.notEqual(both[0].title, both[1].title)
  assert.ok(both[1].title.includes('*/5 * * * *'))
})

test('every surface renders through the shared mapping — no inline heuristics', () => {
  // Desktop panes/tabs/sidebar and the mobile unified projection must all go
  // through stateDotClass/sessionStatus; a re-introduced inline template like
  // `state-dot ${...}` would be an independent heuristic.
  const app = readFileSync(join(SRC, 'App.tsx'), 'utf8')
  assert.ok(!/state-dot \$\{/.test(app), 'App.tsx must not interpolate state-dot classes inline')
  assert.ok(app.includes("from './sessionStatus'"), 'App.tsx must import the shared mapping')
  const mobile = readFileSync(join(SRC, 'mobileWorkspace.ts'), 'utf8')
  assert.ok(!mobile.includes('state-dot'), 'the mobile projection must stay free of status heuristics')
  assert.ok(!/state === 'working'|state==='working'/.test(mobile), 'the mobile projection must not interpret states')
})
