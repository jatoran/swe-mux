import assert from 'node:assert/strict'
import test from 'node:test'
import type { Session } from '../src/types.ts'
import {
  canHideProject, describeOpenWork, loadCollapsedProjects, projectInitials, projectOpenWork,
  serializeCollapsedProjects, sidebarProjectsView, toggleCollapsed,
} from '../src/sidebarProjects.ts'

const session = (id: string, project_id: string, state: Session['state'], extra: Partial<Session> = {}) =>
  ({ id, name: id, project_id, state, ...extra }) as unknown as Session

test('loadCollapsedProjects tolerates missing, malformed, and non-string entries', () => {
  assert.deepEqual([...loadCollapsedProjects(null)], [])
  assert.deepEqual([...loadCollapsedProjects('not json')], [])
  assert.deepEqual([...loadCollapsedProjects('{"a":1}')], [])
  assert.deepEqual([...loadCollapsedProjects('["a",2,"b"]')], ['a', 'b'])
})

test('collapse set round-trips through serialize/load', () => {
  const set = new Set(['p1', 'p2'])
  assert.deepEqual([...loadCollapsedProjects(serializeCollapsedProjects(set))], ['p1', 'p2'])
})

test('toggleCollapsed adds then removes without mutating the input', () => {
  const start = new Set<string>()
  const added = toggleCollapsed(start, 'p1')
  assert.deepEqual([...start], [])
  assert.deepEqual([...added], ['p1'])
  assert.deepEqual([...toggleCollapsed(added, 'p1')], [])
})

test('collapsed-rail initials favor the first hyphen boundary or first two letters', () => {
  assert.equal(projectInitials('swe-mux'), 'SM')
  assert.equal(projectInitials('frontend'), 'FR')
  assert.equal(projectInitials('  Alpha-beta-tools  '), 'AB')
  assert.equal(projectInitials('7 wonders'), '7W')
  assert.equal(projectInitials(''), '?')
})

test('open work counts only live sessions of the project; previews supplied by caller', () => {
  const sessions = [
    session('live', 'p1', 'running'),
    session('starting', 'p1', 'starting'),
    session('dead', 'p1', 'exited'),
    session('crashed', 'p1', 'crashed'),
    session('pending', 'p1', 'running', { pending: true }),
    session('other', 'p2', 'running'),
  ]
  const work = projectOpenWork(sessions, 'p1', ['pv1'])
  assert.deepEqual(work.liveSessions.map(item => item.id), ['live', 'starting'])
  assert.deepEqual(work.previewIds, ['pv1'])
})

test('canHideProject only when no live sessions and no previews', () => {
  assert.equal(canHideProject(projectOpenWork([], 'p1', [])), true)
  assert.equal(canHideProject(projectOpenWork([session('live', 'p1', 'running')], 'p1', [])), false)
  assert.equal(canHideProject(projectOpenWork([], 'p1', ['pv1'])), false)
  // exited-only sessions do not block hiding
  assert.equal(canHideProject(projectOpenWork([session('dead', 'p1', 'exited')], 'p1', [])), true)
})

test('describeOpenWork pluralises and joins present kinds only', () => {
  assert.equal(describeOpenWork(projectOpenWork([session('live', 'p1', 'running')], 'p1', ['pv1'])), '1 live session · 1 preview')
  assert.equal(describeOpenWork(projectOpenWork([session('a', 'p1', 'running'), session('b', 'p1', 'idle')], 'p1', [])), '2 live sessions')
  assert.equal(describeOpenWork(projectOpenWork([], 'p1', [])), '')
})

test('an unloaded Project list is "loading", never "you have no Projects"', () => {
  // The defect this closes: `projects` starts `[]`, so before the first read
  // landed the sidebar told an operator with a full workspace to create their
  // first Project - on every load and every browser refresh.
  assert.equal(sidebarProjectsView({ loaded: false, registered: 0, visible: 0 }), 'loading')
  // Still loading even if a stale count is lying around, because `loaded` is the
  // only thing that answers "has the daemon told us yet".
  assert.equal(sidebarProjectsView({ loaded: false, registered: 9, visible: 9 }), 'loading')
})

test('a loaded Project list distinguishes none-registered from none-shown', () => {
  assert.equal(sidebarProjectsView({ loaded: true, registered: 0, visible: 0 }), 'none-registered')
  // Registered but all hidden is a different sentence and a different fix.
  assert.equal(sidebarProjectsView({ loaded: true, registered: 4, visible: 0 }), 'none-shown')
  assert.equal(sidebarProjectsView({ loaded: true, registered: 4, visible: 2 }), 'list')
})

test('exactly one Project-list surface is drawn for any state', () => {
  const states = [
    { loaded: false, registered: 0, visible: 0 },
    { loaded: false, registered: 3, visible: 1 },
    { loaded: true, registered: 0, visible: 0 },
    { loaded: true, registered: 3, visible: 0 },
    { loaded: true, registered: 3, visible: 3 },
  ]
  // The property the function exists for: one decision, so "two surfaces at
  // once" and "no surface at all" are both unrepresentable.
  for (const state of states) {
    const view = sidebarProjectsView(state)
    assert.ok(['loading', 'none-registered', 'none-shown', 'list'].includes(view), JSON.stringify(state))
  }
})
