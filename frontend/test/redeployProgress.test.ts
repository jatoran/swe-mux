import assert from 'node:assert/strict'
import test from 'node:test'
import {
  applyProbe, beginRedeploy, elapsedLabel, enterOutage, IDLE_REDEPLOY, loadRedeploy,
  markResultPending, outcomeIsFresh, outcomeNotice, REDEPLOY_DOWN_PROBES, REDEPLOY_MAX_MS,
  REDEPLOY_RESULT_KEY, REDEPLOY_STORAGE_KEY, saveRedeploy, takeResultPending,
  type ProbeVerdict,
} from '../src/redeployProgress.ts'

/** The state a verdict carries, or a failure naming what came back instead.
 *  Keeps every assertion below reading as one thought rather than a narrowing
 *  dance, and fails loudly when a verdict changes shape. */
function stateOf(verdict: ProbeVerdict) {
  assert.notEqual(verdict.action, 'reload', 'expected a verdict carrying state')
  return (verdict as Exclude<ProbeVerdict, { action: 'reload' }>).state
}

function fakeStore() {
  const data = new Map<string, string>()
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value) },
    removeItem: (key: string) => { data.delete(key) },
  }
}

test('the build stage never blocks: it stays out of the down phase while the daemon answers', () => {
  const start = 1_000_000
  const state = beginRedeploy(start)
  assert.equal(state.phase, 'building')
  const verdict = applyProbe(state, { healthy: true, status: { running: true } }, start + 5_000)
  assert.equal(verdict.action, 'wait')
  assert.equal(stateOf(verdict).phase, 'building')
})

test('one failed probe is a blip; the second is the outage', () => {
  const start = 1_000_000
  const first = applyProbe(beginRedeploy(start), { healthy: false }, start + 2_000)
  assert.equal(first.action, 'wait')
  // A phone waking or a momentary network stall must not slam a full-screen
  // overlay over an app that is still perfectly usable.
  assert.equal(stateOf(first).phase, 'building')
  assert.equal(stateOf(first).sawDown, false)
  const second = applyProbe(stateOf(first), { healthy: false }, start + 4_000)
  assert.equal(second.action, 'wait')
  assert.equal(stateOf(second).phase, 'down')
  assert.equal(stateOf(second).sawDown, true)
  assert.equal(REDEPLOY_DOWN_PROBES, 2)
})

test('a healthy probe between failures resets the strike count', () => {
  const start = 1_000_000
  const once = applyProbe(beginRedeploy(start), { healthy: false }, start + 2_000)
  const recovered = applyProbe(stateOf(once), { healthy: true, status: { running: true } }, start + 4_000)
  assert.equal(recovered.action, 'wait')
  assert.equal(stateOf(recovered).downProbes, 0)
  assert.equal(stateOf(recovered).phase, 'building')
})

test('the daemon coming back is only a reload once it was seen to go away', () => {
  const start = 1_000_000
  // Never went down: the script stopped before the stop stage, so this daemon is
  // the same one that has been serving all along and there is nothing to load.
  const finished = applyProbe(
    beginRedeploy(start), { healthy: true, status: { running: false } }, start + 6_000,
  )
  assert.equal(finished.action, 'finished')
  // Went down and came back: the successor (or the rolled-back previous build).
  const down = enterOutage(beginRedeploy(start))
  const back = applyProbe(down, { healthy: true, status: { running: false } }, start + 90_000)
  assert.equal(back.action, 'reload')
})

test('the stopping broadcast enters the outage without waiting for probes', () => {
  const state = enterOutage(beginRedeploy(1_000_000))
  assert.equal(state.phase, 'down')
  assert.equal(state.sawDown, true)
  // Idle is not a redeploy; a stray broadcast cannot manufacture one.
  assert.equal(enterOutage(IDLE_REDEPLOY).phase, 'idle')
})

test('a redeploy that never resolves times out instead of blocking forever', () => {
  const start = 1_000_000
  const state = beginRedeploy(start)
  const verdict = applyProbe(state, { healthy: false }, start + REDEPLOY_MAX_MS + 1)
  assert.equal(verdict.action, 'timeout')
})

test('the log tail follows the daemon while it is still answering', () => {
  const state = beginRedeploy(1_000)
  const verdict = applyProbe(
    state, { healthy: true, status: { running: true, log_tail: ['[redeploy] rebuilding'] } }, 2_000,
  )
  assert.equal(verdict.action, 'wait')
  assert.deepEqual(stateOf(verdict).logTail, ['[redeploy] rebuilding'])
})

test('restored state never blocks and never claims to have seen an outage', () => {
  const store = fakeStore()
  const now = 5_000_000
  saveRedeploy(store, { ...enterOutage(beginRedeploy(now)), logTail: ['secret'] })
  const restored = loadRedeploy(store, now + 1_000)
  // A page that loaded at all was served by a live daemon, so resuming 'down'
  // would flash an overlay over a working app, and resuming `sawDown` would make
  // the first healthy probe reload the page for no reason.
  assert.equal(restored.phase, 'building')
  assert.equal(restored.sawDown, false)
  assert.equal(restored.downProbes, 0)
  assert.deepEqual(restored.logTail, [])
  assert.equal(restored.startedAt, now)
})

test('an expired or absent sentinel restores nothing', () => {
  const store = fakeStore()
  assert.equal(loadRedeploy(store, 1_000).phase, 'idle')
  saveRedeploy(store, beginRedeploy(1_000))
  assert.equal(loadRedeploy(store, 1_000 + REDEPLOY_MAX_MS).phase, 'idle')
  store.setItem(REDEPLOY_STORAGE_KEY, 'not json')
  assert.equal(loadRedeploy(store, 1_000).phase, 'idle')
  assert.equal(loadRedeploy(null, 1_000).phase, 'idle')
})

test('going idle clears the sentinel rather than leaving a tab stuck', () => {
  const store = fakeStore()
  saveRedeploy(store, beginRedeploy(1_000))
  assert.ok(store.getItem(REDEPLOY_STORAGE_KEY))
  saveRedeploy(store, IDLE_REDEPLOY)
  assert.equal(store.getItem(REDEPLOY_STORAGE_KEY), null)
})

test('the post-reload result request is one-shot and drops the in-flight sentinel', () => {
  const store = fakeStore()
  saveRedeploy(store, enterOutage(beginRedeploy(1_000)))
  markResultPending(store)
  // Leaving the sentinel would restart a wait loop for a finished redeploy.
  assert.equal(store.getItem(REDEPLOY_STORAGE_KEY), null)
  assert.equal(store.getItem(REDEPLOY_RESULT_KEY), '1')
  assert.equal(takeResultPending(store), true)
  assert.equal(takeResultPending(store), false)
  assert.equal(takeResultPending(null), false)
})

test('a rollback is reported; a clean redeploy says nothing', () => {
  assert.equal(outcomeNotice({ outcome: 'succeeded', detail: 'fine' }), '')
  assert.equal(outcomeNotice(null), '')
  assert.equal(outcomeNotice({ outcome: 'succeeded' }), '')
  const rolled = outcomeNotice({ outcome: 'rolled_back', detail: 'Your change did NOT ship.' })
  assert.match(rolled, /previous app was restored/)
  assert.match(rolled, /did NOT ship/)
  // An outcome this build does not know about still says something.
  assert.match(outcomeNotice({ outcome: 'something_new' }), /did not complete/)
})

test('only a recent result is worth surfacing', () => {
  const now = 10_000_000
  assert.equal(outcomeIsFresh({ finished_at: now / 1000 }, now), true)
  assert.equal(outcomeIsFresh({ finished_at: (now - REDEPLOY_MAX_MS - 60_000) / 1000 }, now), false)
  assert.equal(outcomeIsFresh({}, now), false)
  assert.equal(outcomeIsFresh(null, now), false)
  // A clock skewed far into the future is not a fresh result either.
  assert.equal(outcomeIsFresh({ finished_at: (now + 600_000) / 1000 }, now), false)
})

test('the elapsed clock is the only honest progress signal once the daemon is gone', () => {
  assert.equal(elapsedLabel(1_000, 1_000), '0:00')
  assert.equal(elapsedLabel(1_000, 1_000 + 9_000), '0:09')
  assert.equal(elapsedLabel(1_000, 1_000 + 605_000), '10:05')
  assert.equal(elapsedLabel(5_000, 1_000), '0:00')
})
