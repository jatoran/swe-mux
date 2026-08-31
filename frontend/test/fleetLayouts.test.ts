import assert from 'node:assert/strict'
import test from 'node:test'
import { planFleetLayouts, type PendingSpawn } from '../src/fleetLayouts.ts'
import { parseLayout, stackForView, terminalIds } from '../src/layout.ts'
import { recordJoinFailure } from '../src/sessionJoin.ts'
import type { Project, Session } from '../src/types.ts'

const session = (id: string, extra: Partial<Session> = {}) => ({
  id, name: id, project_id: 'p1', backend: 'codex', state: 'idle', last_activity_ts: 1,
  ...extra,
}) as unknown as Session

const project = (id: string, layout: unknown = null, extra: Partial<Project> = {}) => ({
  id, name: id, path: `/tmp/${id}`, layout, layout_revision: 1,
  ...extra,
}) as unknown as Project

const isEnded = (item: Session) => item.state === 'exited' || item.state === 'crashed'

const plan = (over: Partial<Parameters<typeof planFleetLayouts>[0]> = {}) => planFleetLayouts({
  sessions: [],
  projects: [],
  previewIds: new Set<string>(),
  pendingSpawns: {},
  joinAttempts: {},
  joinAnchor: { projectId: '', viewId: null },
  hasPendingLayoutWrite: () => false,
  isEnded,
  ...over,
})

/** One pane already holding a terminal, so a join has somewhere obvious to land. */
const paneWith = (id: string) => ({
  version: 7,
  root: { type: 'stack', id: 'pane', active_child_id: id, children: [{ type: 'leaf', kind: 'terminal', id }] },
})

test('a daemon-started session joins the Project it belongs to', () => {
  const result = plan({
    sessions: [session('term-a'), session('daemon-1')],
    projects: [project('p1', paneWith('term-a'))],
  })
  assert.deepEqual(terminalIds(result.layouts.p1), ['term-a', 'daemon-1'])
  assert.deepEqual(result.joins.map(join => join.projectId), ['p1'])
  assert.deepEqual(result.joins[0].ids, ['daemon-1'])
  // The tab the operator was on keeps the pane; a join never steals focus.
  assert.equal(stackForView(result.layouts.p1, 'daemon-1')?.active_child_id, 'term-a')
})

test('plugin popup sessions never enter the durable layout', () => {
  const result = plan({
    sessions: [session('term-a'), session('popup-1', {
      plugin_id: 'example.plugin', plugin_placement: 'popup',
    })],
    projects: [project('p1', paneWith('term-a'))],
  })
  assert.deepEqual(terminalIds(result.layouts.p1), ['term-a'])
  assert.deepEqual(result.joins, [])
  assert.deepEqual([...result.liveSessionIds], ['term-a', 'popup-1'])
})

test('the reconciler removes a popup leaf written by an older client', () => {
  const layout = {
    version: 7,
    root: { type: 'stack', id: 'pane', active_child_id: 'term-a', children: [
      { type: 'leaf', kind: 'terminal', id: 'term-a' },
      { type: 'leaf', kind: 'terminal', id: 'popup-1' },
    ] },
  }
  const result = plan({
    sessions: [session('term-a'), session('popup-1', {
      plugin_id: 'example.plugin', plugin_placement: 'popup',
    })],
    projects: [project('p1', layout)],
  })
  assert.deepEqual(terminalIds(result.layouts.p1), ['term-a'])
})

test('plugin split sessions use a right-hand split instead of a tab', () => {
  const result = plan({
    sessions: [session('term-a'), session('plugin-split', {
      plugin_id: 'example.plugin', plugin_placement: 'split',
    })],
    projects: [project('p1', paneWith('term-a'))],
  })
  const root = result.layouts.p1.root
  assert.ok(root)
  assert.equal(root.type, 'split')
  if (!root || root.type !== 'split') return
  assert.equal(root.direction, 'vertical')
  assert.deepEqual(terminalIds(result.layouts.p1), ['term-a', 'plugin-split'])
  assert.deepEqual(result.joins[0].ids, ['plugin-split'])
})

test('the reconciler withholds the join from a Project whose own launch is still in flight', () => {
  // The daemon creates and announces a session before the POST that asked for it returns, so a
  // refresh landing in that window carries a session this client cannot yet recognise as its own.
  // Joining it there would give it a second leaf beside the pending one `replaceTerminal` is
  // about to swap the real id into.
  const pendingSpawns: Record<string, PendingSpawn> = { 'pending-1': { projectId: 'p1', placement: null } }
  const result = plan({
    sessions: [session('term-a'), session('daemon-1')],
    projects: [project('p1', paneWith('term-a'))],
    pendingSpawns,
  })
  assert.deepEqual(terminalIds(result.layouts.p1), ['term-a'])
  assert.deepEqual(result.joins, [])
})

test('a session this device already resolved a pending leaf for is not joined a second time', () => {
  const pendingSpawns: Record<string, PendingSpawn> = {
    'pending-1': { projectId: 'p1', placement: null, resolvedId: 'daemon-1' },
  }
  const result = plan({
    sessions: [session('term-a'), session('daemon-1')],
    projects: [project('p1', paneWith('term-a'))],
    pendingSpawns,
  })
  assert.deepEqual(terminalIds(result.layouts.p1), ['term-a'])
})

test('an optimistic leaf is re-placed off the server layout, which never holds a pending id', () => {
  const pendingSpawns: Record<string, PendingSpawn> = {
    'pending-1': { projectId: 'p1', placement: { split: false, targetId: 'term-a', position: 'after' } },
  }
  const result = plan({
    sessions: [session('term-a'), session('pending-1', { pending: true })],
    projects: [project('p1', paneWith('term-a'))],
    pendingSpawns,
  })
  assert.deepEqual(terminalIds(result.layouts.p1), ['term-a', 'pending-1'])
  // Nobody is looking at it, so the pane keeps the tab the server layout says it was showing.
  assert.equal(stackForView(result.layouts.p1, 'pending-1')?.active_child_id, 'term-a')
})

test("re-placing the optimistic leaf the operator is watching keeps it the pane's active tab", () => {
  // Worktree bootstrap runs for minutes, so several refreshes land while the setup splash is
  // the thing on screen. Restoring the server layout's active tab under it would hide the
  // launch behind a sibling while focus still names it.
  const pendingSpawns: Record<string, PendingSpawn> = {
    'pending-1': { projectId: 'p1', placement: { split: false, targetId: 'term-a', position: 'after' } },
  }
  const result = plan({
    sessions: [session('term-a'), session('pending-1', { pending: true })],
    projects: [project('p1', paneWith('term-a'))],
    pendingSpawns,
    joinAnchor: { projectId: 'p1', viewId: 'pending-1' },
  })
  assert.equal(stackForView(result.layouts.p1, 'pending-1')?.active_child_id, 'pending-1')
})

test('the watched-launch exception is keyed on the Project as well as the view', () => {
  const pendingSpawns: Record<string, PendingSpawn> = {
    'pending-1': { projectId: 'p1', placement: { split: false, targetId: 'term-a', position: 'after' } },
  }
  const result = plan({
    sessions: [session('term-a'), session('pending-1', { pending: true })],
    projects: [project('p1', paneWith('term-a'))],
    pendingSpawns,
    joinAnchor: { projectId: 'p2', viewId: 'pending-1' },
  })
  assert.equal(stackForView(result.layouts.p1, 'pending-1')?.active_child_id, 'term-a')
})

test('ended and pending sessions keep their leaf but are never given a new one', () => {
  const withEnded = plan({
    sessions: [session('term-a'), session('gone', { state: 'exited' })],
    projects: [project('p1', paneWith('term-a'))],
  })
  assert.deepEqual(terminalIds(withEnded.layouts.p1), ['term-a'])
  const kept = plan({
    sessions: [session('term-a', { state: 'exited' })],
    projects: [project('p1', paneWith('term-a'))],
  })
  assert.deepEqual(terminalIds(kept.layouts.p1), ['term-a'])
  const stillPending = plan({
    sessions: [session('term-a'), session('optimistic', { pending: true })],
    projects: [project('p1', paneWith('term-a'))],
  })
  assert.deepEqual(terminalIds(stillPending.layouts.p1), ['term-a'])
})

test('a session that left the fleet loses its leaf', () => {
  const result = plan({
    sessions: [],
    projects: [project('p1', paneWith('term-a'))],
  })
  assert.deepEqual(terminalIds(result.layouts.p1), [])
})

test('a Project with a layout write in flight is left out of the plan entirely', () => {
  const result = plan({
    sessions: [session('term-a'), session('daemon-1')],
    projects: [project('p1', paneWith('term-a'))],
    hasPendingLayoutWrite: id => id === 'p1',
  })
  assert.equal(result.layouts.p1, undefined)
  assert.deepEqual(result.joins, [])
})

test('a session whose join the server keeps refusing is not proposed again', () => {
  let attempts = {}
  for (let attempt = 0; attempt < 3; attempt += 1) attempts = recordJoinFailure(attempts, ['daemon-1'])
  const result = plan({
    sessions: [session('term-a'), session('daemon-1')],
    projects: [project('p1', paneWith('term-a'))],
    joinAttempts: attempts,
  })
  assert.deepEqual(terminalIds(result.layouts.p1), ['term-a'])
  assert.deepEqual(result.joins, [])
})

test('the refusal record returned by the plan has forgotten departed sessions', () => {
  const attempts = recordJoinFailure({}, ['daemon-1', 'daemon-2'])
  const result = plan({
    sessions: [session('daemon-2')],
    projects: [],
    joinAttempts: attempts,
  })
  assert.deepEqual(result.joinAttempts, { 'daemon-2': 1 })
  assert.deepEqual([...result.liveSessionIds], ['daemon-2'])
})

test('a persisted history leaf is dropped, because History is a global overlay now', () => {
  const result = plan({
    sessions: [],
    projects: [project('p1', {
      version: 7,
      root: { type: 'stack', id: 'pane', active_child_id: 'history', children: [{ type: 'leaf', kind: 'history', id: 'history' }] },
    })],
  })
  assert.equal(stackForView(result.layouts.p1, 'history'), null)
})

test('a preview the daemon no longer lists loses its leaf while a listed one keeps it', () => {
  const layout = {
    version: 7,
    root: { type: 'stack', id: 'pane', active_child_id: 'preview-live', children: [
      { type: 'leaf', kind: 'preview', id: 'preview-live' },
      { type: 'leaf', kind: 'preview', id: 'preview-gone' },
    ] },
  }
  const result = plan({
    sessions: [],
    projects: [project('p1', layout)],
    previewIds: new Set(['preview-live']),
  })
  assert.ok(stackForView(result.layouts.p1, 'preview-live'))
  assert.equal(stackForView(result.layouts.p1, 'preview-gone'), null)
})

test('each Project only ever joins its own sessions', () => {
  const result = plan({
    sessions: [session('term-a'), session('daemon-2', { project_id: 'p2' })],
    projects: [project('p1', paneWith('term-a')), project('p2', parseLayout(null) as unknown)],
  })
  assert.deepEqual(terminalIds(result.layouts.p1), ['term-a'])
  assert.deepEqual(terminalIds(result.layouts.p2), ['daemon-2'])
})
