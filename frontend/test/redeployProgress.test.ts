import assert from 'node:assert/strict'
import test from 'node:test'
import {
  abandonRequest, applyProbe, beginRedeploy, confirmRedeploy, elapsedLabel, enterOutage,
  holderWarning, IDLE_REDEPLOY, loadRedeploy,
  markResultPending, outcomeIsFresh, outcomeNotice, phaseDetail, phaseLabel,
  REDEPLOY_DOWN_PROBES, REDEPLOY_MAX_MS,
  REDEPLOY_RESULT_KEY, REDEPLOY_STORAGE_KEY, requestRedeploy, saveRedeploy, takeResultPending,
  interruptionSummary, waitsOnDaemon,
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

test('the chip goes up at the press, not at the accept', () => {
  const pressed = 1_000_000
  const state = requestRedeploy(pressed)
  assert.equal(state.phase, 'requested')
  assert.equal(state.startedAt, pressed)
  assert.equal(state.expiresAt, pressed + REDEPLOY_MAX_MS)
  // Same headline as the build it is about to become: the two are seconds apart
  // and a label that changed in between would read as churn.
  assert.equal(phaseLabel('requested'), phaseLabel('building'))
  assert.notEqual(phaseDetail('requested'), phaseDetail('building'))
})

test('nothing is probed while the accept is still in flight', () => {
  const pressed = 1_000_000
  const state = requestRedeploy(pressed)
  assert.equal(waitsOnDaemon(state.phase), false)
  // The lock a status read reports on is claimed by the very request being
  // awaited, so `running: false` here is the truth about a redeploy that has not
  // begun. Read as a verdict it would clear the chip the press just raised.
  const verdict = applyProbe(state, { healthy: true, status: { running: false } }, pressed + 3_000)
  assert.equal(verdict.action, 'wait')
  assert.equal(stateOf(verdict).phase, 'requested')
  // Nor can an unreachable daemon turn a request into the blocking overlay.
  const down = applyProbe(state, { healthy: false }, pressed + 3_000)
  assert.equal(down.action, 'wait')
  assert.equal(stateOf(down).phase, 'requested')
  assert.equal(stateOf(down).sawDown, false)
})

test('accepting keeps the clock the press started', () => {
  const pressed = 1_000_000
  // The 202 can land eight seconds later (the daemon scans every process on the
  // host first); an elapsed timer that restarted there would under-report the
  // wait the operator actually sat through.
  const confirmed = confirmRedeploy(requestRedeploy(pressed), pressed + 8_000)
  assert.equal(confirmed.phase, 'building')
  assert.equal(confirmed.startedAt, pressed)
  assert.equal(confirmed.expiresAt, pressed + REDEPLOY_MAX_MS)
  // A client that never pressed anything (the daemon's broadcast) starts now.
  assert.deepEqual(confirmRedeploy(IDLE_REDEPLOY, pressed), beginRedeploy(pressed))
  // Idempotent: broadcast, accept, and boot-time sentinel all call it.
  const running = beginRedeploy(pressed)
  assert.equal(confirmRedeploy(running, pressed + 60_000), running)
  const outage = enterOutage(running)
  assert.equal(confirmRedeploy(outage, pressed + 60_000), outage)
})

test('a refusal clears the press it answered, and only that one', () => {
  const pressed = 1_000_000
  assert.equal(abandonRequest(requestRedeploy(pressed), pressed).phase, 'idle')
  // A refusal must not take the chip away from a redeploy that really is
  // running - somebody else's, learned from the broadcast while ours was
  // in flight.
  const running = beginRedeploy(pressed)
  assert.equal(abandonRequest(running, pressed), running)
  assert.equal(abandonRequest(requestRedeploy(pressed + 5), pressed).phase, 'requested')
})

test('the stopping broadcast reaches the outage from a request in flight', () => {
  // The daemon can be gone before this client ever saw its own 202 - which is
  // the case that would otherwise leave a tab typing into a dead PTY socket.
  const state = enterOutage(confirmRedeploy(requestRedeploy(1_000_000), 1_000_500))
  assert.equal(state.phase, 'down')
  assert.equal(state.sawDown, true)
})

test('a request in flight is never persisted', () => {
  const store = fakeStore()
  saveRedeploy(store, requestRedeploy(1_000))
  // It resolves within seconds and only in the document that made it; a tab
  // restoring one would come up waiting on a 202 answered somewhere else.
  assert.equal(store.getItem(REDEPLOY_STORAGE_KEY), null)
})

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

function preview(port: number) {
  return { id: `p${port}`, url: `http://127.0.0.1:${port}/`, host: '127.0.0.1', port, proxy_path: `/preview/p${port}/` }
}

test('the confirm dialog names what goes dark, and says nothing when nothing does', () => {
  assert.equal(interruptionSummary(null), '')
  assert.equal(interruptionSummary({ previews: [] }), '')
  assert.equal(interruptionSummary(undefined), '')
  assert.equal(
    interruptionSummary({ previews: [preview(5173)] }),
    '1 preview unreachable while the app restarts: :5173.',
  )
  assert.equal(
    interruptionSummary({ previews: [preview(5173), preview(8080)] }),
    '2 previews unreachable while the app restarts: :5173, :8080.',
  )
})

function holder(pid: number, name = 'node.exe', via = 'cwd') {
  return { pid, name, via, path: `D:\\PROJECTS\\swe-mux\\dist\\swe-mux\\_internal\\${pid}` }
}

test('the dialog names a blocker before the operator commits, not after', () => {
  // Null is "not scanned yet" and says nothing; an empty scan says nothing either.
  assert.equal(holderWarning(null), '')
  assert.equal(holderWarning(undefined), '')
  assert.equal(holderWarning([]), '')
  const one = holderWarning([holder(4321)])
  assert.match(one, /^1 process holds the app bundle open/)
  assert.match(one, /refused: pid 4321 node\.exe \(cwd\)\.$/)
  // Paths are deliberately absent: the refusal toast carries them in full, and
  // this has to fit above the dialog's buttons on a phone.
  assert.ok(!one.includes('_internal'))
  assert.match(holderWarning([holder(1), holder(2)]), /^2 processes hold/)
})

test('a swarm of holders cannot push the dialog buttons off a phone screen', () => {
  const many = [1, 2, 3, 4, 5].map(pid => holder(pid))
  const warning = holderWarning(many)
  assert.match(warning, /^5 processes hold/)
  assert.match(warning, /and 2 more\.$/)
  assert.ok(!warning.includes('pid 4 '))
})

test('a Project with many services cannot push the dialog buttons off a phone screen', () => {
  const many = [3000, 3001, 3002, 3003, 3004, 3005].map(preview)
  const summary = interruptionSummary({ previews: many })
  assert.match(summary, /^6 previews unreachable/)
  assert.match(summary, /:3000, :3001, :3002, :3003 and 2 more\.$/)
  // The cap is on the listed ports, never on the count: "6 previews" stays honest.
  assert.ok(!summary.includes(':3004'))
})
