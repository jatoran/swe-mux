import assert from 'node:assert/strict'
import test from 'node:test'
import {
  KILL_TOMBSTONE_TTL_MS, applyKillTombstones, clearableEndedSessions, expiredKillIds,
  killRemovedTheSession, killedSessionIds, nextActiveAfterKill, type KillTombstones,
} from '../src/sessionKills.ts'
import {
  openTab, removeLeaf, splitTerminal, terminalLeaf, visibleTerminalIds, type PaneLayout,
} from '../src/layout.ts'
import type { Session } from '../src/types.ts'

const session = (id: string, extra: Partial<Session> = {}) => ({
  id, name: id, project_id: 'p1', backend: 'codex', state: 'idle', last_activity_ts: 1,
  ...extra,
}) as unknown as Session

const tombstone = (sessionId: string, startedAt = 0, projectId = 'p1') =>
  ({ sessionId, projectId, startedAt })

const tombstones = (...entries: ReturnType<typeof tombstone>[]): KillTombstones =>
  Object.fromEntries(entries.map(entry => [entry.sessionId, entry]))

const emptyLayout: PaneLayout = { version: 7, root: null }

/** One stack holding every id, the last one active - what `openTab` builds. */
const stacked = (...ids: string[]): PaneLayout =>
  ids.reduce<PaneLayout>((layout, id) => openTab(layout, null, terminalLeaf(id)), emptyLayout)

/** The layout `killNow` hands over: the killed leaf is already gone from it. */
const afterKilling = (layout: PaneLayout, killedId: string): PaneLayout =>
  removeLeaf(layout, 'terminal', killedId)

test('a tombstoned session is hidden from the fleet the daemon still reports', () => {
  const fleet = [session('s1'), session('s2'), session('s3')]
  const visible = applyKillTombstones(fleet, tombstones(tombstone('s2')))
  assert.deepEqual(visible.map(item => item.id), ['s1', 's3'])
})

test('no tombstones returns the same array, so an ordinary refresh allocates nothing', () => {
  const fleet = [session('s1')]
  assert.equal(applyKillTombstones(fleet, {}), fleet)
})

test('killed ids cover every tombstone', () => {
  const killed = killedSessionIds(tombstones(tombstone('s1'), tombstone('s2')))
  assert.deepEqual([...killed].sort(), ['s1', 's2'])
})

test('a tombstone expires only once its deadline has fully elapsed', () => {
  const pending = tombstones(tombstone('s1', 1000), tombstone('s2', 5000))
  assert.deepEqual(expiredKillIds(pending, 1000 + KILL_TOMBSTONE_TTL_MS - 1), [])
  assert.deepEqual(expiredKillIds(pending, 1000 + KILL_TOMBSTONE_TTL_MS), ['s1'])
  assert.deepEqual(expiredKillIds(pending, 5000 + KILL_TOMBSTONE_TTL_MS), ['s1', 's2'])
})

test('a 404 means the kill got what it wanted; anything else is a real failure', () => {
  assert.equal(killRemovedTheSession(404), true)
  assert.equal(killRemovedTheSession(500), false)
  assert.equal(killRemovedTheSession(409), false)
  // A transport failure or the client-side deadline carries no status at all.
  assert.equal(killRemovedTheSession(undefined), false)
})

test('killing an unfocused session leaves focus exactly where it was', () => {
  const layout = stacked('s1', 's2', 's3')
  const next = nextActiveAfterKill({
    layout: afterKilling(layout, 's3'),
    sessions: [session('s1'), session('s2'), session('s3')],
    killedId: 's3', projectId: 'p1', activeId: 's1',
  })
  assert.equal(next, 's1')
})

test('focus lands on a visible sibling when the focused session is killed', () => {
  const layout = splitTerminal(stacked('s1'), 's1', 's2', 'horizontal')
  const remaining = afterKilling(layout, 's2')
  assert.deepEqual(visibleTerminalIds(remaining), ['s1'])
  const next = nextActiveAfterKill({
    layout: remaining, sessions: [session('s1'), session('s2')],
    killedId: 's2', projectId: 'p1', activeId: 's2',
  })
  assert.equal(next, 's1')
})

test('a tab stacked behind another is taken when no visible survivor is left', () => {
  // s2 and s3 share a stack with s3 active, so s2 is in the layout but not on screen.
  // Killing s1 leaves s3 visible, but s3 has already exited, so s2 is the only survivor.
  const layout = splitTerminal(stacked('s2', 's3'), 's2', 's1', 'horizontal')
  const remaining = afterKilling(layout, 's1')
  assert.deepEqual(visibleTerminalIds(remaining), ['s3'])
  const next = nextActiveAfterKill({
    layout: remaining,
    sessions: [session('s1'), session('s2'), session('s3', { state: 'exited' })],
    killedId: 's1', projectId: 'p1', activeId: 's1',
  })
  assert.equal(next, 's2')
})

test('an ended sibling is never handed the focus', () => {
  const next = nextActiveAfterKill({
    layout: emptyLayout,
    sessions: [session('s1'), session('s2', { state: 'exited' }), session('s3', { state: 'crashed' })],
    killedId: 's1', projectId: 'p1', activeId: 's1',
  })
  assert.equal(next, null)
})

test('focus never crosses into another project', () => {
  const next = nextActiveAfterKill({
    layout: emptyLayout,
    sessions: [session('s1'), session('other', { project_id: 'p2' })],
    killedId: 's1', projectId: 'p1', activeId: 's1',
  })
  assert.equal(next, null)
})

test('focus follows the previously focused session, not the first one in the layout', () => {
  // s1 is leftmost and was the old fallback. The operator had been bouncing between s3
  // and s2, so closing s3 belongs on s2.
  const layout = splitTerminal(splitTerminal(stacked('s1'), 's1', 's2', 'horizontal'), 's2', 's3', 'horizontal')
  const next = nextActiveAfterKill({
    layout: afterKilling(layout, 's3'),
    sessions: [session('s1'), session('s2'), session('s3')],
    killedId: 's3', projectId: 'p1', activeId: 's3',
    recent: ['s3', 's2', 's1'],
  })
  assert.equal(next, 's2')
})

test('the vacated pane wins over a more recent session elsewhere', () => {
  // s2 and s3 are tabs of one stack; s1 is its own pane and was focused more recently
  // than s2. Closing s3 settles inside the split it was in rather than jumping across.
  const layout = splitTerminal(stacked('s2', 's3'), 's2', 's1', 'horizontal')
  const next = nextActiveAfterKill({
    layout: afterKilling(layout, 's3'),
    sessions: [session('s1'), session('s2'), session('s3')],
    killedId: 's3', projectId: 'p1', activeId: 's3',
    recent: ['s3', 's1', 's2'],
    paneIds: ['s2', 's3'],
  })
  assert.equal(next, 's2')
})

test('a recently focused session that has since died is skipped', () => {
  const layout = splitTerminal(splitTerminal(stacked('s1'), 's1', 's2', 'horizontal'), 's2', 's3', 'horizontal')
  const next = nextActiveAfterKill({
    layout: afterKilling(layout, 's3'),
    sessions: [session('s1'), session('s2', { state: 'crashed' }), session('s3')],
    killedId: 's3', projectId: 'p1', activeId: 's3',
    recent: ['s3', 's2', 's1'],
  })
  assert.equal(next, 's1')
})

test('what is on screen outranks a recent session that has left the layout', () => {
  // s2 was focused more recently, but it is no longer placed anywhere; s1 is visible.
  const next = nextActiveAfterKill({
    layout: afterKilling(stacked('s1', 's3'), 's3'),
    sessions: [session('s1'), session('s2'), session('s3')],
    killedId: 's3', projectId: 'p1', activeId: 's3',
    recent: ['s3', 's2', 's1'],
  })
  assert.equal(next, 's1')
})

test('an empty recency stack falls back to layout order exactly as before', () => {
  const layout = splitTerminal(stacked('s1'), 's1', 's2', 'horizontal')
  const next = nextActiveAfterKill({
    layout: afterKilling(layout, 's2'),
    sessions: [session('s1'), session('s2')],
    killedId: 's2', projectId: 'p1', activeId: 's2',
    recent: [],
  })
  assert.equal(next, 's1')
})

test('killing an unfocused session ignores recency entirely', () => {
  const next = nextActiveAfterKill({
    layout: afterKilling(stacked('s1', 's2', 's3'), 's3'),
    sessions: [session('s1'), session('s2'), session('s3')],
    killedId: 's3', projectId: 'p1', activeId: 's1',
    recent: ['s2', 's1'],
  })
  assert.equal(next, 's1')
})

test('a session outside the layout is still a better landing spot than nothing', () => {
  const next = nextActiveAfterKill({
    layout: emptyLayout,
    sessions: [session('s1'), session('s2')],
    killedId: 's1', projectId: 'p1', activeId: 's1',
  })
  assert.equal(next, 's2')
})

test('killing the last session in a project leaves nothing focused', () => {
  const next = nextActiveAfterKill({
    layout: afterKilling(stacked('s1'), 's1'),
    sessions: [session('s1')],
    killedId: 's1', projectId: 'p1', activeId: 's1',
  })
  assert.equal(next, null)
})

test('a sweep takes both ways a session can end, and only in the Project asked for', () => {
  const fleet = [
    session('live'),
    session('exited', { state: 'exited' }),
    session('crashed', { state: 'crashed' }),
    session('other-exited', { state: 'exited', project_id: 'p2' }),
  ]
  assert.deepEqual(
    clearableEndedSessions(fleet, 'p1', {}).map(item => item.id),
    ['exited', 'crashed'])
  assert.deepEqual(
    clearableEndedSessions(fleet, 'p2', {}).map(item => item.id),
    ['other-exited'])
})

test('a sweep leaves out a row whose own DELETE is still in the air', () => {
  const fleet = [
    session('e1', { state: 'exited' }),
    session('e2', { state: 'crashed' }),
    session('e3', { state: 'exited' }),
  ]
  assert.deepEqual(
    clearableEndedSessions(fleet, 'p1', tombstones(tombstone('e2'))).map(item => item.id),
    ['e1', 'e3'])
})

test('a sweep never removes intentionally inactive sessions', () => {
  const fleet = [
    session('ended', { state: 'exited' }),
    session('inactive', { state: 'exited', inactive: true }),
  ]
  assert.deepEqual(
    clearableEndedSessions(fleet, 'p1', {}).map(item => item.id),
    ['ended'],
  )
})

test('a sweep of a Project with nothing dead in it is empty, not the whole Project', () => {
  const fleet = [session('s1'), session('s2', { state: 'awaiting' })]
  assert.deepEqual(clearableEndedSessions(fleet, 'p1', {}), [])
})

test('a sweep keeps the order the sidebar drew, so its layout write is deterministic', () => {
  const fleet = [
    session('e3', { state: 'exited' }),
    session('e1', { state: 'exited' }),
    session('e2', { state: 'crashed' }),
  ]
  assert.deepEqual(
    clearableEndedSessions(fleet, 'p1', {}).map(item => item.id),
    ['e3', 'e1', 'e2'])
})

test('a sweep that takes the focused session hands focus to a survivor, not to another corpse', () => {
  // What `clearEndedSessions` computes once for the whole batch: the layout it passes
  // already has every ended leaf out, so the successor can only be a live session.
  const fleet = [
    session('e1', { state: 'exited' }),
    session('e2', { state: 'crashed' }),
    session('live'),
  ]
  const layout = stacked('e1', 'e2', 'live')
  const swept = clearableEndedSessions(fleet, 'p1', {})
  const nextLayout = swept.reduce((current, item) => removeLeaf(current, 'terminal', item.id), layout)
  const next = nextActiveAfterKill({
    layout: nextLayout, sessions: fleet, killedId: 'e1', projectId: 'p1', activeId: 'e1',
    recent: ['e1', 'e2', 'live'],
  })
  assert.equal(next, 'live')
  assert.deepEqual(visibleTerminalIds(nextLayout), ['live'])
})

test('a sweep that empties the Project leaves nothing focused', () => {
  const fleet = [session('e1', { state: 'exited' }), session('e2', { state: 'exited' })]
  const swept = clearableEndedSessions(fleet, 'p1', {})
  const nextLayout = swept.reduce(
    (current, item) => removeLeaf(current, 'terminal', item.id), stacked('e1', 'e2'))
  const next = nextActiveAfterKill({
    layout: nextLayout, sessions: fleet, killedId: 'e2', projectId: 'p1', activeId: 'e2',
    recent: ['e2', 'e1'],
  })
  assert.equal(next, null)
})
