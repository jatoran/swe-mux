import assert from 'node:assert/strict'
import test from 'node:test'

import {
  canOfferRestart,
  restartLossSummary,
  restartMenuView,
  restartRefusalMessage,
  type RestartEligibility,
} from '../src/restartAgent.ts'
import type { Session } from '../src/types'

const session = (overrides: Partial<Session> = {}): Session => ({
  id: 's1', name: 'worker', project_id: 'p', backend: 'claude', state: 'idle', cwd: '.',
  ...overrides,
} as Session)

const probe = (overrides: Partial<RestartEligibility> = {}): RestartEligibility => ({
  eligible: false, code: null, reason: null, force_allowed: false, blockers: [], would_lose: [],
  ...overrides,
})

test('only a live, retained-in-place agent is offered a restart', () => {
  assert.equal(canOfferRestart(session()), true)
  assert.equal(canOfferRestart(session({ state: 'working' })), true)
  assert.equal(canOfferRestart(session({ backend: 'shell' })), false)
  assert.equal(canOfferRestart(session({ state: 'exited' })), false)
  assert.equal(canOfferRestart(session({ state: 'crashed' })), false)
  assert.equal(canOfferRestart(session({ inactive: true })), false)
  assert.equal(canOfferRestart(session({ cold: true })), false)
})

test('an eligible probe offers the plain restart', () => {
  const view = restartMenuView(probe({ eligible: true }), { armed: false, running: false })
  assert.equal(view.action, 'restart')
  assert.equal(view.unavailable, false)
  assert.equal(view.danger, false)
  assert.equal(view.label, 'Restart agent (fresh process)')
})

test('a pending probe and a running restart are unavailable and do nothing', () => {
  assert.equal(restartMenuView(null, { armed: false, running: false }).action, 'none')
  assert.equal(restartMenuView(null, { armed: false, running: false }).unavailable, true)
  const running = restartMenuView(probe({ eligible: true }), { armed: false, running: true })
  assert.equal(running.action, 'none')
  assert.match(running.label, /Restarting/)
})

test('an unforceable refusal explains itself on the row, not only in a tooltip', () => {
  const reason = 'the conversation is stale'
  const view = restartMenuView(probe({ code: 'transcript_stale', reason }), { armed: false, running: false })
  assert.equal(view.action, 'explain')
  assert.equal(view.unavailable, true)
  assert.equal(view.title, reason)
  // A touch screen has no tooltip, so the reason is drawn under the row as well.
  assert.equal(view.detail, reason)
})

test('a forceable refusal takes two presses and names what it loses', () => {
  const refused = probe({
    code: 'agent_working', reason: 'busy', force_allowed: true,
    would_lose: ["the agent's current turn will be interrupted", '1 queued message will be stranded'],
  })
  const first = restartMenuView(refused, { armed: false, running: false })
  assert.equal(first.action, 'arm')
  assert.equal(first.danger, true)
  assert.match(first.label, /anyway/)
  const second = restartMenuView(refused, { armed: true, running: false })
  assert.equal(second.action, 'force')
  assert.match(second.detail, /current turn will be interrupted; 1 queued message/)
})

test('the loss summary and the refusal toast use the daemon words', () => {
  assert.equal(restartLossSummary(probe({ would_lose: ['a', '', 'b'] })), 'a; b')
  assert.equal(restartLossSummary(probe()), 'nothing the daemon could name')
  assert.match(restartRefusalMessage({ error: 'busy', force_allowed: true }, 'x'), /Restart agent anyway/)
  assert.equal(restartRefusalMessage({ error: 'stale' }, 'x'), 'Restart refused: stale.')
  assert.equal(restartRefusalMessage(undefined, 'network'), 'Restart refused: network.')
})
