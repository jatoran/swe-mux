import assert from 'node:assert/strict'
import test from 'node:test'
import { mulberry32, nextOrdinal, resetOrdinals, DEMO_EPOCH_MS } from '../src/demo/determinism.ts'
import {
  makeLandRequest, makeQueueMessage, makeSpawnRequest, queueAutoPayload, queueMailboxPayload,
  queueMessagesPayload, queueSummaryPayload, notificationsPayload, landPayload, landEventsPayload,
} from '../src/demo/controlPlane.ts'
import { SCENARIOS, scenarioById, NUDGE_SCENARIO_ID } from '../src/demo/scenarios.ts'
import { apply, state } from '../src/demo/store.ts'
import { DEMO_PROJECT_ID } from '../src/demo/fixtures.ts'

/**
 * The demo's scenario engine, at the two seams a browser is not needed for: the
 * deterministic sources everything else is derived from, and the control plane the
 * scenarios drive.
 *
 * The engine's *timing* half is exercised end to end instead, by `capture-demo.mjs
 * --check` and by `test/renderer/demo-director.spec.ts` - a scheduler that awaits real
 * timers and reads real chrome has nothing to say to a node assertion. What is here is
 * everything that can be wrong without a screen: a fixture that is not reproducible, a
 * catalogue whose beats are out of order, a payload that would crash the view reading it.
 */

// --------------------------------------------------------------------- determinism

test('the seeded PRNG is a pure function of its seed', () => {
  const first = Array.from({ length: 8 }, mulberry32(1234))
  const second = Array.from({ length: 8 }, mulberry32(1234))
  assert.deepEqual(first, second)
  // And a different seed is a different stream, or the "seed" is decoration.
  assert.notDeepEqual(first, Array.from({ length: 8 }, mulberry32(1235)))
})

test('the seeded PRNG stays inside [0, 1)', () => {
  // A draw outside the range would be silently wrong everywhere `Math.random` is used -
  // an index off the end of the joke pool, a negative jitter on a token count.
  const draw = mulberry32(0xabcdef)
  for (let index = 0; index < 500; index += 1) {
    const value = draw()
    assert.ok(value >= 0 && value < 1, `draw ${index} was ${value}`)
  }
})

test('generated ordinals are a counter, so an id can be named twice', () => {
  resetOrdinals()
  assert.deepEqual([nextOrdinal(), nextOrdinal(), nextOrdinal()], [1, 2, 3])
  resetOrdinals()
  assert.equal(nextOrdinal(), 1)
})

test('the demo epoch is a fixed instant in the past', () => {
  // Fixed, because every fixture offset is measured back from it; in the past, because a
  // fixture that subtracts thirty days from it must not land in the future.
  assert.equal(DEMO_EPOCH_MS, Date.UTC(2026, 2, 14, 9, 41, 0))
  assert.ok(DEMO_EPOCH_MS < Date.now())
})

// ------------------------------------------------------------------- the catalogue

test('every scenario has a unique id and the tour is first', () => {
  const ids = SCENARIOS.map(item => item.id)
  assert.deepEqual([...new Set(ids)], ids, 'two scenarios share an id')
  assert.equal(ids[0], 'tour', 'the walkthrough is scenario one, not a separate system')
})

test('beats run forwards', () => {
  // `at` is the scenario clock, and a beat that goes backwards would be waited on for a
  // negative time - which is silently zero, so the two beats fire together and the
  // scenario plays in an order nobody wrote.
  for (const scenario of SCENARIOS) {
    for (const beats of [scenario.beats, scenario.mobileBeats ?? []]) {
      let previous = -1
      for (const [index, beat] of beats.entries()) {
        assert.ok(beat.at >= previous, `${scenario.id} beat ${index} moves backwards`)
        previous = beat.at
      }
    }
  }
})

test('the walkthrough waits for the visitor at every beat, and nothing else does', () => {
  // This is the line between the two kinds of run, and it has to hold in both directions.
  // A tour beat with no gate would advance past the visitor mid-instruction; a scripted
  // beat with one would hang forever, because nobody is being asked to do anything.
  const tour = scenarioById('tour')!
  for (const beats of [tour.beats, tour.mobileBeats ?? []]) {
    for (const [index, beat] of beats.entries()) {
      assert.ok(beat.gate, `tour beat ${index} has no gate`)
    }
  }
  for (const scenario of SCENARIOS.filter(item => item.id !== 'tour')) {
    for (const beat of scenario.beats) assert.equal(beat.gate, undefined, `${scenario.id} gates a beat`)
  }
})

test('a scenario that plays by itself can be interrupted, and a gated one cannot', () => {
  // Real input aborts anything autoplaying, and must not abort the walkthrough - whose
  // gates *are* real input, so aborting on it would make the tour impossible to finish.
  for (const scenario of SCENARIOS) {
    assert.equal(scenario.interruptible, scenario.id !== 'tour', scenario.id)
  }
})

test('the idle nudge names a scenario that exists and plays by itself', () => {
  const nudge = scenarioById(NUDGE_SCENARIO_ID)
  assert.ok(nudge, 'the nudge names a scenario the catalogue does not have')
  assert.ok(nudge.interruptible, 'the nudge must abort on the first touch')
})

test('no scenario writes a harness name', () => {
  // `tests/test_harness_name_literals.py` allowlists three demo files and this is not one
  // of them; a backend belongs to the fleet fixture, and a scenario reads it off a session.
  const source = JSON.stringify(SCENARIOS.map(scenario => ({
    beats: scenario.beats.map(beat => [beat.say, beat.eyebrow, beat.body, beat.type?.text]),
    label: scenario.label,
    blurb: scenario.blurb,
  })))
  for (const name of ['claude', 'codex', 'opencode']) {
    assert.equal(source.toLowerCase().includes(name), false, `a scenario names ${name}`)
  }
})

// ------------------------------------------------------------------ control plane

/** Every test starts from the seed, because the store is one module-level value. */
const fresh = (): void => { apply({ kind: 'reset' }) }

test('the prompt queue starts empty and answers with a list either way', () => {
  fresh()
  const summary = queueSummaryPayload() as { targets: unknown[] }
  assert.deepEqual(summary.targets, [])
  // The shape matters more than the emptiness: a view rendering `messages.map(...)` throws
  // on a missing list, and a throw during render tears the whole demo down.
  const target = queueMessagesPayload('s-claude') as { messages: unknown[]; pending: number }
  assert.deepEqual(target.messages, [])
  assert.equal(target.pending, 0)
})

test('a queued message reaches the summary, the target view and the fleet view', () => {
  fresh()
  const message = makeQueueMessage({
    targetSessionId: 's-claude', body: 'have another look at the coupon table', state: 'armed',
  })
  apply({ kind: 'queue-add', message })

  const summary = queueSummaryPayload() as { targets: Array<{ target_session_id: string; pending: number }> }
  assert.deepEqual(summary.targets.map(row => [row.target_session_id, row.pending]), [['s-claude', 1]])

  const view = queueMessagesPayload('s-claude') as { messages: Array<{ id: string }>; pending: number }
  assert.deepEqual(view.messages.map(row => row.id), [message.id])
  assert.equal(view.pending, 1)

  // A human wrote it, so the fleet view's default partition (non-human authors) excludes it.
  const nonHuman = queueMailboxPayload('non_human') as { messages: unknown[] }
  assert.deepEqual(nonHuman.messages, [])
  const human = queueMailboxPayload('human') as { messages: Array<{ id: string }> }
  assert.deepEqual(human.messages.map(row => row.id), [message.id])
})

test('an agent-authored message is the one the fleet view is for', () => {
  fresh()
  apply({
    kind: 'queue-add',
    message: makeQueueMessage({
      targetSessionId: 's-claude', body: 'readers are done', senderKind: 'agent',
      senderLabel: 'coupon readers', originSessionId: 's-codex', state: 'armed',
    }),
  })
  const nonHuman = queueMailboxPayload('non_human') as { messages: Array<{ sender_label: string }> }
  assert.deepEqual(nonHuman.messages.map(row => row.sender_label), ['coupon readers'])
  const everything = queueMailboxPayload('all') as { messages: unknown[] }
  assert.equal(everything.messages.length, 1)
})

test('a queue patch moves the revision, so a stale write can be refused', () => {
  fresh()
  const message = makeQueueMessage({ targetSessionId: 's-claude', body: 'first' })
  apply({ kind: 'queue-add', message })
  assert.equal(state.queue[0].revision, 1)
  apply({ kind: 'queue-patch', id: message.id, patch: { body: 'second' } })
  assert.equal(state.queue[0].revision, 2)
  assert.equal(state.queue[0].body, 'second')
})

test('a sent message stops holding a place in the queue', () => {
  fresh()
  const message = makeQueueMessage({ targetSessionId: 's-claude', body: 'go', state: 'armed' })
  apply({ kind: 'queue-add', message })
  apply({ kind: 'queue-patch', id: message.id, patch: { state: 'sent' } })
  const view = queueMessagesPayload('s-claude') as { messages: unknown[]; pending: number }
  assert.equal(view.pending, 0, 'a sent message is history, not a pending one')
  assert.equal(view.messages.length, 1, 'and it is still on the record')
})

test('auto-delivery is per session inside the install-wide switch', () => {
  fresh()
  const before = queueAutoPayload() as { master_enabled: boolean; sessions: Array<{ session_id: string; enabled: boolean }> }
  assert.equal(before.master_enabled, true, 'the demo config has auto delivery on')
  assert.equal(before.sessions.every(row => !row.enabled), true, 'and no session opted in')
  apply({ kind: 'auto-delivery-set', id: 's-working', enabled: true })
  const after = queueAutoPayload() as { sessions: Array<{ session_id: string; enabled: boolean }> }
  assert.deepEqual(
    after.sessions.filter(row => row.enabled).map(row => row.session_id),
    ['s-working'],
  )
})

test('notifications arrive one at a time and can be dismissed together', () => {
  fresh()
  assert.deepEqual((notificationsPayload() as { automation: unknown[] }).automation, [])
  apply({
    kind: 'notification-add',
    notification: {
      id: 'n-1', kind: 'queue_delivery', title: 'delivered', message: 'a queued prompt was sent',
      severity: 'info', created_at: 1,
    },
  })
  const payload = notificationsPayload() as {
    automation: Array<{ id: string; read_at?: number }>
    notifications: unknown[]
    deliveries: unknown[]
  }
  assert.deepEqual(payload.automation.map(row => row.id), ['n-1'])
  // Both lists are present and empty rather than absent: the tab reads all three.
  assert.deepEqual(payload.notifications, [])
  assert.deepEqual(payload.deliveries, [])
  apply({ kind: 'notification-read-all', read: true })
  assert.ok((notificationsPayload() as { automation: Array<{ read_at?: number }> }).automation[0].read_at)
})

test('the land queue keeps its trail, and a patch appends rather than replaces', () => {
  fresh()
  const request = makeLandRequest({
    projectId: DEMO_PROJECT_ID, branch: 'agent/coupon-table',
    worktreeRoot: '/code/.worktrees/coupon-table', requestedBy: 's-working', id: 'land-test',
  })
  apply({ kind: 'land-add', request })
  assert.equal(request.state, 'queued')
  apply({
    kind: 'land-patch', id: 'land-test', patch: { state: 'verifying' },
    event: { state: 'verifying', note: 'running the gate' },
  })
  apply({
    kind: 'land-patch', id: 'land-test', patch: { state: 'landed' },
    event: { state: 'landed', note: 'fast-forwarded' },
  })
  const events = (landEventsPayload('land-test') as { events: Array<{ state: string }> }).events
  assert.deepEqual(events.map(row => row.state), ['queued', 'verifying', 'landed'])
})

test('the land queue is per Project, and off for the Project that has no worktrees', () => {
  fresh()
  const owned = landPayload(DEMO_PROJECT_ID) as { project_enabled: boolean; requests: unknown[] }
  assert.equal(owned.project_enabled, true)
  assert.equal(owned.requests.length, 1, 'the seed carries one finished landing')
  const other = landPayload('p-garden') as { project_enabled: boolean; requests: unknown[] }
  assert.equal(other.project_enabled, false, 'per Project and off by default is the real posture')
  assert.deepEqual(other.requests, [])
})

test('a spawn request is drafted rather than started', () => {
  fresh()
  const request = makeSpawnRequest({
    projectId: DEMO_PROJECT_ID, projectName: 'rocket-shop', backend: 'shell',
    prompt: 'move the readers', name: 'coupon readers',
    reason: 'the seams are independent', fromSession: 's-claude',
  })
  apply({ kind: 'spawn-request-add', request })
  assert.equal(request.done, false)
  assert.equal(request.session_id, null, 'drafting starts nothing; approval is what acts')
  const mailbox = queueMailboxPayload('non_human') as { spawn_requests: Array<{ id: string }> }
  assert.deepEqual(mailbox.spawn_requests.map(row => row.id), [request.id])
})

test('a reset puts the control plane back', () => {
  apply({ kind: 'queue-add', message: makeQueueMessage({ targetSessionId: 's-claude', body: 'x' }) })
  apply({ kind: 'auto-delivery-set', id: 's-claude', enabled: true })
  fresh()
  assert.deepEqual(state.queue, [])
  assert.deepEqual(state.autoDelivery, [])
  assert.deepEqual(state.notifications, [])
  assert.deepEqual(state.spawnRequests, [])
})
