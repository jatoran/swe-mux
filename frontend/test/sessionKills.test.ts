import assert from 'node:assert/strict'
import test from 'node:test'
import {
  KILL_TOMBSTONE_TTL_MS, applyKillTombstones, expiredKillIds, killRemovedTheSession,
  killedSessionIds, nextActiveAfterKill, type KillTombstones,
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
