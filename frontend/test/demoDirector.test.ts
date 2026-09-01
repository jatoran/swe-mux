import assert from 'node:assert/strict'
import test from 'node:test'
import {
  fixtureSeconds, mulberry32, nextOrdinal, resetOrdinals, DEMO_EPOCH_MS,
} from '../src/demo/determinism.ts'
import {
  makeLandRequest, makeQueueMessage, makeSpawnRequest, queueAutoPayload, queueMailboxPayload,
  queueMessagesPayload, queueSummaryPayload, notificationsPayload, landPayload, landEventsPayload,
} from '../src/demo/controlPlane.ts'
import { SCENARIOS, scenarioById, NUDGE_SCENARIO_ID } from '../src/demo/scenarios.ts'
import { apply, state } from '../src/demo/store.ts'
import { DEMO_PROJECT_ID } from '../src/demo/fixtures.ts'
import { placeCallouts, gutterSide, wirePath, unionBox, sweepDelays, type Box } from '../src/demo/callouts.ts'
import { verifyCommandPayload } from '../src/demo/gitFixtures.ts'
import { parseLandVerifyCommand } from '../src/gitLand.ts'
import { railConfigFromBlob, type RailBlob } from '../src/commandRail.ts'
import { normalizeSessionRowConfig } from '../src/sessionRowConfig.ts'
import { normalizeSessionTopbarConfig } from '../src/sessionTopbarConfig.ts'

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

test('a generated fixture timestamp is a pure function of its id', () => {
  const epoch = Math.floor(DEMO_EPOCH_MS / 1000)
  assert.equal(fixtureSeconds('s-d3'), epoch + 3)
  assert.equal(fixtureSeconds('s-d4'), epoch + 4)
  assert.throws(() => fixtureSeconds('s-live'), /has no ordinal/)
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

// --------------------------------------------------------------------- callouts

/** A box, from the four numbers that actually vary. */
const box = (left: number, top: number, width = 60, height = 16): Box => ({
  left, top, width, height,
  right: left + width, bottom: top + height,
  cx: left + width / 2, cy: top + height / 2,
})

const entry = (target: Box, label = 'x', width = 90, height = 20) =>
  ({ callout: { at: ['.x'], label }, target, width, height })

const VIEWPORT = { width: 1280, height: 800 }

test('labels stack rather than overlap, however close their targets are', () => {
  // The whole reason this is a function rather than `top: target.cy`: a session row is
  // about 40px tall and carries seven facts, so the naive placement puts four labels in
  // the same twenty pixels and the beat reads as one smudge.
  const placed = placeCallouts(
    [30, 38, 46, 54, 62].map(top => entry(box(20, top))),
    VIEWPORT,
  )
  assert.equal(placed.length, 5)
  for (let index = 1; index < placed.length; index += 1) {
    assert.ok(
      placed[index].top >= placed[index - 1].top + 20,
      `label ${index} at ${placed[index].top} overlaps the one above it`,
    )
  }
})

test('a label is placed in target order, not in the order the beat wrote them', () => {
  // The deconfliction pass is only correct on a sorted list, and a scenario ordering its
  // notes by importance is a reasonable thing to do.
  const placed = placeCallouts(
    [entry(box(20, 400), 'lower'), entry(box(20, 100), 'upper')],
    VIEWPORT,
  )
  assert.deepEqual(placed.map(item => item.callout.label), ['upper', 'lower'])
})

test('the gutter goes to whichever side has room', () => {
  // Left-hand chrome (the fleet column) labels to its right; right-hand chrome (the side
  // panel) labels to its left. Measured from the targets, because one beat shape has to
  // serve both.
  assert.equal(gutterSide([box(20, 100)], VIEWPORT), 'right')
  assert.equal(gutterSide([box(1_100, 100)], VIEWPORT), 'left')
  assert.equal(gutterSide([box(20, 100), box(700, 100)], VIEWPORT), 'right')
  // And the case a centre reading gets wrong: a row inside a nearly-full-width dialog.
  // Its centre is the middle of the screen, so a centre rule calls it left-hand chrome
  // and puts the label in the sliver on the right; both slivers are equal here, and what
  // matters is that the rule is asking about space rather than about position.
  const wide = box(119, 200, 1_042, 20)
  assert.equal(gutterSide([wide], { width: 1_280, height: 800 }), 'right')
  assert.equal(gutterSide([box(400, 200, 860, 20)], { width: 1_280, height: 800 }), 'left')
})

test('a label stays inside the viewport on both axes', () => {
  // A wide chip beside chrome near the right edge, and a target below the fold: both
  // clamp, because the two things most worth labelling sit on the frame's edges.
  const [wide] = placeCallouts([entry(box(300, 790), 'edge', 600, 40)], VIEWPORT)
  assert.equal(wide.side, 'right')
  assert.ok(wide.x + 600 <= VIEWPORT.width, 'ran off the right edge')
  assert.ok(wide.top + 40 <= VIEWPORT.height, 'ran off the bottom edge')
  // And on the other side the clamp is the mirror: `x` is the chip's right edge there.
  const [mirrored] = placeCallouts([entry(box(1_240, 40), 'edge', 300, 20)], VIEWPORT)
  assert.equal(mirrored.side, 'left')
  assert.ok(mirrored.x - 300 >= 0, 'ran off the left edge')
})

test('the leader line is orthogonal, so nine of them do not cross', () => {
  const [item] = placeCallouts([entry(box(20, 300))], VIEWPORT)
  const path = wirePath(item)
  assert.match(path, /^M [\d.]+ [\d.]+ H [\d.]+ V [\d.]+ H [\d.]+$/)
})

test('the sweep wakes each label as the band reaches it', () => {
  // The band is one element crossing the column once and the labels are scheduled
  // against its position; deriving the delay from the target is what makes the two read
  // as one effect rather than two that happen to overlap.
  const column = unionBox([box(0, 0, 300, 400)])!
  const delays = sweepDelays([box(20, 40), box(20, 200), box(20, 380)], column, 1_500)
  assert.ok(delays[0] < delays[1] && delays[1] < delays[2], 'delays must follow the band')
  assert.ok(delays[2] <= 1_500, 'nothing may wake after the band has gone')
  // The band leads the labels slightly, so the topmost target has already been passed
  // when its label arrives rather than the other way round.
  assert.equal(sweepDelays([box(20, 0, 60, 8)], column, 1_500)[0], 0)
})

test('every callout names chrome, and every scenario that has one is playable', () => {
  // A callout with no selectors would measure nothing and draw a label pointing at the
  // origin; an empty label would draw an empty chip. Both are silent in a browser.
  for (const scenario of SCENARIOS) {
    for (const beats of [scenario.beats, scenario.mobileBeats ?? []]) {
      for (const [index, beat] of beats.entries()) {
        const show = typeof beat.show === 'function' ? beat.show() : beat.show
        for (const note of show?.notes ?? []) {
          assert.ok(note.at.length, `${scenario.id} beat ${index} has a callout with no target`)
          assert.ok(note.label.trim(), `${scenario.id} beat ${index} has an empty label`)
        }
      }
    }
  }
})

test('the walkthrough labels only fields the seeded row config actually draws', () => {
  fresh()
  const config = normalizeSessionRowConfig(state.deviceSettings.desktop.sessionRows)
  const placedFields = new Set<string>([
    ...config.top.left, ...config.top.right, ...config.bottom.left, ...config.bottom.right,
  ].map(slot => slot.id))
  const named = new Set<string>()
  for (const scenario of SCENARIOS) {
    for (const beats of [scenario.beats, scenario.mobileBeats ?? []]) {
      for (const beat of beats) {
        const show = typeof beat.show === 'function' ? beat.show() : beat.show
        for (const note of show?.notes ?? []) {
          for (const selector of note.at) {
            const field = selector.match(/data-row-field="([a-zA-Z]+)"/)?.[1]
            if (field) named.add(field)
          }
        }
      }
    }
  }
  assert.ok(named.size > 0, 'the anatomy beat names no row field at all any more')
  for (const field of named) {
    assert.ok(placedFields.has(field), `a callout names "${field}", which the seed does not place`)
  }
})

test('the land gate payload parses as a configured, approved gate', () => {
  // Parsed with the app's own parser rather than eyeballed, because the previous fixture
  // answered a 200 with entirely different key names - `command`, `grant`,
  // `approved_digest` - and every field the parser reads fell back to the empty gate. The
  // Git tab then told every visitor "No verification command. A land here would be
  // refused rather than run", directly under a scenario narrating the gate passing.
  const gate = parseLandVerifyCommand(verifyCommandPayload())
  assert.equal(gate.configured, true)
  assert.equal(gate.approved, true)
  assert.equal(gate.scriptPresent, true)
  assert.equal(gate.display, '.worktree-verify')
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

// --------------------------------------------------------- the seeded device settings

/**
 * Two settings the demo does not take the product's default for, checked against the
 * *resolved* config rather than against the blob it is written as.
 *
 * Both are derived from the app's own defaults and then edited, which is what keeps them
 * current - and is also exactly why they need a test. A renamed action id or a reshaped
 * default rail would leave the derivation running happily and silently stop editing
 * anything, and the failure is invisible: the demo would simply go back to showing what
 * these were set to hide.
 */
test('the demo seeds a session top bar with no approval control', () => {
  fresh()
  const config = normalizeSessionTopbarConfig(state.deviceSettings.desktop.sessionTopbar)
  const items = config.rows.flatMap(row => [...row.left, ...row.right])
  assert.ok(items.length > 1, 'the seed must still carry a top bar, not just be emptied')
  assert.deepEqual(items.filter(item => item.kind === 'action' && item.id === 'approvals'), [])
  // The rest of the default row is untouched, so this is a removal rather than a rewrite.
  assert.ok(items.some(item => item.kind === 'action' && item.id === 'drawer:transcript'))
})

test("the demo seeds the phone's Actions rail with one row, and leaves the desktop's alone", () => {
  fresh()
  const rail = railConfigFromBlob(state.deviceSettings.desktop.commandRail as RailBlob)
  assert.equal(rail.layouts.mobile.strip.length, 1)
  assert.ok(rail.layouts.mobile.strip[0].items.length > 4, 'the row that is kept must be the populated one')
  assert.equal(rail.layouts.desktop.strip.length, 1)
})

test('a settings write reaches the store and merges rather than replacing the profile', () => {
  fresh()
  const before = Object.keys(state.deviceSettings.desktop).sort()
  apply({ kind: 'settings-put', profile: 'desktop', domains: { sounds: { chime: true } } })
  assert.deepEqual(
    Object.keys(state.deviceSettings.desktop).sort(),
    [...before, 'sounds'].sort(),
    'a PUT carries only the domains it touched, so the others must survive it',
  )
  assert.deepEqual(state.deviceSettings.desktop.sounds, { chime: true })
})
