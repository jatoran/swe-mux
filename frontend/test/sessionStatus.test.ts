import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import type { AwaitingReason, Session, SessionState } from '../src/types.ts'
import { awaitingLabel, sessionStatus, stateDotClass } from '../src/sessionStatus.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

const ALL_STATES: SessionState[] = ['starting', 'running', 'working', 'idle', 'awaiting', 'exited', 'crashed']
const ALL_AWAITING: AwaitingReason[] = ['approval', 'question', 'elicitation', 'rate_limit']

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

test('awaiting distinguishes approval, question, elicitation, and rate limit', () => {
  const labels = new Set(ALL_AWAITING.map(reason => awaitingLabel(agent('awaiting', { awaiting_reason: reason }))))
  assert.equal(labels.size, ALL_AWAITING.length, 'each awaiting sub-reason needs a distinct affordance')
  assert.equal(awaitingLabel(agent('awaiting', { awaiting_reason: 'question' })), 'awaiting answer')
  assert.equal(awaitingLabel(agent('awaiting', { awaiting_reason: 'elicitation' })), 'awaiting input')
  assert.equal(awaitingLabel(agent('awaiting', { awaiting_reason: 'rate_limit' })), 'rate limited')
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

test('shell sessions render the raw state', () => {
  const shell = { ...agent('running'), backend: 'shell' } as unknown as Session
  assert.equal(sessionStatus(shell), 'running')
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
