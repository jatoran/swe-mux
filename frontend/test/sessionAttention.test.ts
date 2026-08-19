import assert from 'node:assert/strict'
import test from 'node:test'
import type { Session } from '../src/types.ts'
import { ackedSeq, isUnread, pendingAcks, projectRailStatus, projectSetRailStatus, pruneAcks, trackPinVisits, turnSeq, type AckMap, type PinVisits } from '../src/sessionAttention.ts'

const session = (
  id: string,
  backend: Session['backend'],
  turn_seq: number,
  extra: Partial<Session> = {},
) => ({
  id, name: id, project_id: 'p1', backend, state: 'working',
  // Deliberately noisy: activity is the signal the old model used, and every
  // test below has to keep passing while it moves for reasons unrelated to a
  // turn (a resize repaint, an idle spinner).
  last_activity_ts: 9_999, turn_seq, read_turn_seq: 0, ...extra,
}) as unknown as Session

test('a settled agent with an unacknowledged turn is unread', () => {
  assert.equal(isUnread(session('a', 'claude', 1, { state: 'idle' }), {}), true)
  assert.equal(isUnread(session('a', 'claude', 1, { state: 'awaiting' }), {}), true)
  assert.equal(isUnread(session('a', 'claude', 1, { state: 'idle', read_turn_seq: 1 }), {}), false)
})

test('repaint traffic can never raise unread, however loud', () => {
  // The whole point of the rewrite: activity moves, turns do not, row stays read.
  const quiet = session('a', 'claude', 3, { state: 'idle', read_turn_seq: 3, last_activity_ts: 1 })
  const repainting = { ...quiet, last_activity_ts: 5_000_000 } as Session
  assert.equal(isUnread(repainting, {}), false)
})

test('a session that has never completed a turn is not unread', () => {
  // Both counters at zero is what a freshly spawned session looks like, and what
  // a daemon that predates the counter serves for every session it has.
  assert.equal(isUnread(session('fresh', 'claude', 0, { state: 'idle' }), {}), false)
  const legacy = { id: 'l', project_id: 'p1', backend: 'claude', state: 'idle' } as unknown as Session
  assert.equal(isUnread(legacy, {}), false)
  assert.equal(turnSeq(legacy), 0)
})

test('a working agent is never unread, however far behind it is', () => {
  assert.equal(isUnread(session('a', 'claude', 9, { state: 'working' }), {}), false)
  assert.equal(isUnread(session('a', 'claude', 9, { state: 'starting' }), {}), false)
})

test('shell and ended agent sessions never participate in unread', () => {
  assert.equal(isUnread(session('s', 'shell', 5, { state: 'idle' }), {}), false)
  assert.equal(isUnread(session('d', 'claude', 5, { state: 'exited' }), {}), false)
  assert.equal(isUnread(session('c', 'claude', 5, { state: 'crashed' }), {}), false)
})

test('the local overlay clears a row before the daemon confirms it', () => {
  const unread = session('a', 'claude', 4, { state: 'idle' })
  assert.equal(isUnread(unread, {}), true)
  assert.equal(isUnread(unread, { a: 4 }), false)
  // A stale overlay never masks a *newer* turn.
  assert.equal(isUnread(session('a', 'claude', 5, { state: 'idle' }), { a: 4 }), true)
  // Whichever of the two is further ahead wins.
  assert.equal(ackedSeq(session('a', 'claude', 5, { read_turn_seq: 3 }), { a: 1 }), 3)
  assert.equal(ackedSeq(session('a', 'claude', 5, { read_turn_seq: 1 }), { a: 3 }), 3)
})

test('a hand-marked session is unread whatever the counters and the state say', () => {
  // The pin is a statement, not a measurement. Every ordinary reason a row would
  // read as caught up has to lose to it, or the click looks ignored.
  const caughtUp = session('a', 'claude', 4, { state: 'idle', read_turn_seq: 4, unread_pin: true })
  assert.equal(isUnread(caughtUp, { a: 4 }), true)
  assert.equal(isUnread(session('a', 'claude', 9, { state: 'working', unread_pin: true }), {}), true)
  assert.equal(isUnread(session('fresh', 'claude', 0, { state: 'idle', unread_pin: true }), {}), true)
  // Not a way to light up a row that has no read state at all, though.
  assert.equal(isUnread(session('s', 'shell', 5, { state: 'idle', unread_pin: true }), {}), false)
  assert.equal(isUnread(session('d', 'claude', 5, { state: 'exited', unread_pin: true }), {}), false)
})

test('a hand-marked session is never acknowledged by the dwell timer of the visit that marked it', () => {
  // The case the pin exists for: the pane the menu was opened on is on screen,
  // so without this it would be re-read a dwell later and the mark would appear
  // to have done nothing.
  const pinned = [session('here', 'claude', 3, { state: 'idle', unread_pin: true })]
  assert.deepEqual(pendingAcks(pinned, ['here'], {}, { here: 'held' }), [])
  // No visit state at all is the same answer, so a caller that does not track
  // visits cannot start acknowledging pins by omission.
  assert.deepEqual(pendingAcks(pinned, ['here'], {}), [])
})

test('coming back to a hand-marked pane acknowledges it, explicitly', () => {
  // The complaint this exists for: a pin the dwell could never touch left the row
  // unread until somebody hand-marked it read, so the mark behaved like a flag
  // rather than like "I have not read this yet".
  const pinned = [session('here', 'claude', 3, { state: 'idle', unread_pin: true })]
  assert.deepEqual(pendingAcks(pinned, ['here'], {}, { here: 'released' }), [
    { id: 'here', turnSeq: 3, explicit: true },
  ])
  // `mark_unread` rolls the mark back only one turn, and a pin can sit on a
  // session with no counted turn at all, so a released pin is acknowledged even
  // when the counters already read as caught up - that is the stranded row.
  const caughtUp = [session('here', 'claude', 3, { state: 'idle', read_turn_seq: 3, unread_pin: true })]
  assert.deepEqual(pendingAcks(caughtUp, ['here'], { here: 3 }, { here: 'released' }), [
    { id: 'here', turnSeq: 3, explicit: true },
  ])
  // Off screen is still off screen: a released pin is not acknowledged from afar.
  assert.deepEqual(pendingAcks(pinned, [], {}, { here: 'released' }), [])
})

test('a pin is held by the visit that set it and released when the pane leaves', () => {
  const pinned = [session('here', 'claude', 3, { state: 'idle', unread_pin: true })]
  // Marked while on screen: held, and it stays held for as long as the visit lasts.
  const held = trackPinVisits({}, pinned, ['here'])
  assert.deepEqual(held, { here: 'held' })
  assert.equal(trackPinVisits(held, pinned, ['here']), held)
  // The pane goes away: the visit is over.
  const released = trackPinVisits(held, pinned, [])
  assert.deepEqual(released, { here: 'released' })
  // ...and coming back does not re-arm it, or the row would light again every
  // time it scrolled out of view and back.
  assert.deepEqual(trackPinVisits(released, pinned, ['here']), { here: 'released' })
})

test('a pin set on a pane nobody is looking at is released at once', () => {
  // Marking a row unread from the sidebar of a session that is not on screen has
  // no visit to protect, so the first visit reads it.
  const pinned = [session('elsewhere', 'claude', 3, { state: 'idle', unread_pin: true })]
  const visits = trackPinVisits({}, pinned, ['other'])
  assert.deepEqual(visits, { elsewhere: 'released' })
  assert.deepEqual(trackPinVisits(visits, pinned, ['elsewhere']), { elsewhere: 'released' })
})

test('pin visits forget sessions that lost their pin, and hold their reference otherwise', () => {
  const unpinned = [session('here', 'claude', 4, { state: 'idle' })]
  // The daemon retires the pin on the next completed turn, and marking read
  // clears it; either way the entry goes with it.
  assert.deepEqual(trackPinVisits({ here: 'released' }, unpinned, ['here']), {})
  assert.deepEqual(trackPinVisits({ gone: 'held' }, [], []), {})
  const prev: PinVisits = { here: 'held' }
  const pinned = [session('here', 'claude', 3, { state: 'idle', unread_pin: true })]
  assert.equal(trackPinVisits(prev, pinned, ['here']), prev)
})

test('marking unread discards the overlay that would report the row read', () => {
  // `ackedSeq` takes the max of server mark and overlay, so an entry left from
  // the acknowledgement being undone would outvote the rolled-back mark.
  const pinned = [session('a', 'claude', 4, { state: 'idle', read_turn_seq: 3, unread_pin: true })]
  assert.deepEqual(pruneAcks({ a: 4 }, pinned), {})
})

test('pending acknowledgements cover on-screen agents that are behind', () => {
  const sessions = [
    session('seen', 'claude', 2, { state: 'idle', read_turn_seq: 2 }),
    session('behind', 'claude', 3, { state: 'idle' }),
    session('offscreen', 'claude', 3, { state: 'idle' }),
    session('shell', 'shell', 3, { state: 'running' }),
  ]
  assert.deepEqual(pendingAcks(sessions, ['seen', 'behind', 'shell'], {}), [
    { id: 'behind', turnSeq: 3, explicit: false },
  ])
  // A mid-turn agent on screen is acknowledged too: watching it work is what
  // keeps the row from lighting up the moment it settles.
  const working = [session('busy', 'claude', 1, { state: 'working' })]
  assert.deepEqual(pendingAcks(working, ['busy'], {}), [{ id: 'busy', turnSeq: 1, explicit: false }])
  // Already covered by the overlay, so no repeat POST while the write is in flight.
  assert.deepEqual(pendingAcks(sessions, ['behind'], { behind: 3 }), [])
})

test('pruning drops overlay entries the daemon has confirmed, and vanished sessions', () => {
  const sessions = [session('a', 'claude', 5, { read_turn_seq: 5 }), session('b', 'claude', 5)]
  assert.deepEqual(pruneAcks({ a: 5, b: 4, gone: 2 }, sessions), { b: 4 })
})

test('pruning returns the same reference when nothing changed', () => {
  const prev: AckMap = { a: 3 }
  const sessions = [session('a', 'claude', 5, { read_turn_seq: 1 })]
  assert.equal(pruneAcks(prev, sessions), prev)
})

test('a session missing from one snapshot pass cannot come back marked read', () => {
  // The old model rebuilt its map from the session list every pass and re-seeded
  // anything it had not seen before as read, so a single incomplete snapshot
  // silently cleared the fleet. Nothing here can do that: the mark is the
  // daemon's, and pruning only ever forgets an acknowledgement already applied.
  const unread = session('a', 'claude', 4, { state: 'idle' })
  const pruned = pruneAcks({}, [])
  assert.equal(isUnread(unread, pruned), true)
})

test('project rail activity keeps unread independent from the strongest live state', () => {
  const sessions = [
    session('working', 'claude', 1, { project_id: 'p1', state: 'working' }),
    session('ready', 'codex', 1, { project_id: 'p1', state: 'idle' }),
    session('approval', 'claude', 0, { project_id: 'p1', state: 'awaiting' }),
    session('other', 'codex', 1, { project_id: 'p2', state: 'working' }),
  ]
  // The strongest state is the approval, while the unread turn belongs to the
  // settled 'ready' row — the chip has to report both.
  assert.deepEqual(projectRailStatus(sessions, 'p1', {}), {
    activity: 'attention', unread: true, liveCount: 3, agentCount: 3,
  })
  // The mid-turn row alone raises the activity axis without raising unread.
  assert.deepEqual(projectRailStatus(sessions, 'p2', {}), {
    activity: 'working', unread: false, liveCount: 1, agentCount: 1,
  })
})

test('a collapsed section answers for every project it folded away', () => {
  const sessions = [
    session('calm', 'claude', 1, { project_id: 'p1', state: 'idle', read_turn_seq: 1 }),
    session('waiting', 'claude', 1, { project_id: 'p2', state: 'awaiting' }),
    session('elsewhere', 'claude', 1, { project_id: 'p3', state: 'working' }),
  ]
  // The approval lives in p2, so folding p1 and p2 together must still surface it —
  // a section that reported only its first Project would hide the one thing that
  // needs the user.
  assert.deepEqual(projectSetRailStatus(sessions, ['p1', 'p2'], {}), {
    activity: 'attention', unread: true, liveCount: 2, agentCount: 2,
  })
  // Projects outside the section never leak into its aggregate.
  assert.equal(projectSetRailStatus(sessions, ['p1'], {}).activity, 'waiting')
  assert.equal(projectSetRailStatus(sessions, [], {}).activity, 'inactive')
  // A Set is accepted as-is, so the caller need not rebuild one per render.
  assert.equal(projectSetRailStatus(sessions, new Set(['p3']), {}).activity, 'working')
})

test('project rail activity distinguishes working, ready, shell-only, and empty projects', () => {
  assert.equal(projectRailStatus([session('a', 'claude', 1)], 'p1', {}).activity, 'working')
  assert.equal(projectRailStatus([session('a', 'claude', 1, { state: 'idle' })], 'p1', {}).activity, 'waiting')
  assert.equal(projectRailStatus([session('s', 'shell', 1, { state: 'running' })], 'p1', {}).activity, 'running')
  assert.deepEqual(projectRailStatus([session('d', 'claude', 1, { state: 'exited' })], 'p1', {}), {
    activity: 'inactive', unread: false, liveCount: 0, agentCount: 0,
  })
})
