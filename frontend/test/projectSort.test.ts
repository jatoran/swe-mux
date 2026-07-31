import assert from 'node:assert/strict'
import test from 'node:test'
import type { Project, Session } from '../src/types.ts'
import {
  EMPTY_SIDEBAR_ORDER, UNGROUPED_BUCKET_ID, bucketSortMode, loadSidebarOrder, mergeVisibleOrder,
  placeUngrouped, projectActivity, projectSortLabel, pruneSidebarOrder, serializeSidebarOrder,
  setBucketSortMode, sortProjects,
} from '../src/projectSort.ts'

const project = (id: string, name: string, extra: Partial<Project> = {}) =>
  ({ id, name, root: `D:/${id}`, position: 0, layout: null, layout_revision: 0, ...extra }) as Project

const session = (id: string, project_id: string, extra: Partial<Session> = {}) =>
  ({ id, name: id, project_id, state: 'running', created_at: 0, last_activity_ts: 0, ...extra }) as unknown as Session

test('sidebar order load tolerates missing, malformed, and unknown modes', () => {
  assert.deepEqual(loadSidebarOrder(null), EMPTY_SIDEBAR_ORDER)
  assert.deepEqual(loadSidebarOrder('not json'), EMPTY_SIDEBAR_ORDER)
  assert.deepEqual(loadSidebarOrder('[1,2]'), EMPTY_SIDEBAR_ORDER)
  assert.deepEqual(loadSidebarOrder('{"sort":{"g1":"nonsense","g2":"name"},"ungroupedIndex":1}'), {
    sort: { g2: 'name' },
    ungroupedIndex: 1,
  })
  // A negative or fractional slot would splice somewhere nobody asked for.
  assert.equal(loadSidebarOrder('{"ungroupedIndex":-2}').ungroupedIndex, null)
  assert.equal(loadSidebarOrder('{"ungroupedIndex":1.5}').ungroupedIndex, null)
})

test('sidebar order round-trips and never persists the custom default', () => {
  const prefs = { sort: { g1: 'name' as const, g2: 'custom' as const }, ungroupedIndex: 0 }
  const reloaded = loadSidebarOrder(serializeSidebarOrder(prefs))
  assert.deepEqual(reloaded, { sort: { g1: 'name' }, ungroupedIndex: 0 })
})

test('bucket sort mode set/read, with custom clearing the entry', () => {
  const named = setBucketSortMode(EMPTY_SIDEBAR_ORDER, 'g1', 'activity')
  assert.equal(bucketSortMode(named, 'g1'), 'activity')
  assert.equal(bucketSortMode(named, 'g2'), 'custom')
  const cleared = setBucketSortMode(named, 'g1', 'custom')
  assert.deepEqual(cleared.sort, {})
  // Setting the mode it already has must not churn a new object into state.
  assert.equal(setBucketSortMode(named, 'g1', 'activity'), named)
})

test('pruneSidebarOrder drops buckets that no longer exist and keeps identity otherwise', () => {
  const prefs = { sort: { g1: 'name' as const, gone: 'activity' as const }, ungroupedIndex: null }
  assert.deepEqual(pruneSidebarOrder(prefs, ['g1', UNGROUPED_BUCKET_ID]).sort, { g1: 'name' })
  assert.equal(pruneSidebarOrder(prefs, ['g1', 'gone']), prefs)
})

test('custom order is a pass-through of the manual order', () => {
  const items = [project('c', 'Charlie'), project('a', 'Alpha')]
  assert.equal(sortProjects(items, 'custom', new Map()), items)
})

test('name ordering is numeric-aware and reversible, with the id as a stable tie-break', () => {
  const items = [project('p2', 'proj 10'), project('p1', 'proj 2'), project('p3', 'Proj 2')]
  assert.deepEqual(sortProjects(items, 'name', new Map()).map(item => item.id), ['p1', 'p3', 'p2'])
  assert.deepEqual(sortProjects(items, 'name-desc', new Map()).map(item => item.id), ['p2', 'p3', 'p1'])
})

test('date ordering keeps undated projects last in both directions', () => {
  const items = [
    project('old', 'Old', { created_at: 100 }),
    project('unknown', 'Unknown'),
    project('new', 'New', { created_at: 900 }),
  ]
  assert.deepEqual(sortProjects(items, 'created', new Map()).map(item => item.id), ['old', 'new', 'unknown'])
  assert.deepEqual(sortProjects(items, 'created-desc', new Map()).map(item => item.id), ['new', 'old', 'unknown'])
})

test('activity ordering is newest first and falls back to manual order on a tie', () => {
  const items = [project('a', 'A'), project('b', 'B'), project('c', 'C')]
  const activity = new Map([['a', 10], ['b', 0], ['c', 10]])
  assert.deepEqual(sortProjects(items, 'activity', activity).map(item => item.id), ['a', 'c', 'b'])
})

test('projectActivity takes the later of the history stamp and live sessions', () => {
  const projects = [project('p1', 'One', { last_activity: 1_000 }), project('p2', 'Two')]
  const sessions = [
    // Ahead of history, and rounded down to the minute so a busy PTY cannot
    // re-sort the sidebar on every chunk of output.
    session('s1', 'p1', { last_activity_ts: 1_799 }),
    // History wins when the live session is the older evidence.
    session('s2', 'p1', { last_activity_ts: 5 }),
    // No timestamps yet: an optimistic row must not date a Project.
    session('s3', 'p2', { pending: true, last_activity_ts: 9_999 }),
    // Unknown Project ids are ignored rather than inventing an entry.
    session('s4', 'ghost', { last_activity_ts: 9_999 }),
  ]
  const activity = projectActivity(projects, sessions)
  assert.equal(activity.get('p1'), 1_740)
  assert.equal(activity.get('p2'), 0)
  assert.equal(activity.has('ghost'), false)
})

test('projectActivity falls back to spawn time when a session has no activity stamp', () => {
  const activity = projectActivity([project('p1', 'One')], [session('s1', 'p1', { created_at: 600 })])
  assert.equal(activity.get('p1'), 600)
})

test('the ungrouped bucket lands in its slot, clamped, and last by default', () => {
  assert.deepEqual(placeUngrouped(['g1', 'g2'], null), ['g1', 'g2', UNGROUPED_BUCKET_ID])
  assert.deepEqual(placeUngrouped(['g1', 'g2'], 0), [UNGROUPED_BUCKET_ID, 'g1', 'g2'])
  assert.deepEqual(placeUngrouped(['g1', 'g2'], 1), ['g1', UNGROUPED_BUCKET_ID, 'g2'])
  // Deleting the Groups it used to sit after must not strand it off the end.
  assert.deepEqual(placeUngrouped(['g1'], 7), ['g1', UNGROUPED_BUCKET_ID])
  assert.deepEqual(placeUngrouped([], 3), [UNGROUPED_BUCKET_ID])
})

test('mergeVisibleOrder permutes only the rendered subset, leaving hidden rows put', () => {
  // b and d are on screen; a, c, e are hidden and keep their slots.
  assert.deepEqual(mergeVisibleOrder(['a', 'b', 'c', 'd', 'e'], ['d', 'b']), ['a', 'd', 'c', 'b', 'e'])
  assert.deepEqual(mergeVisibleOrder(['a', 'b'], ['b', 'a']), ['b', 'a'])
  // Ids the full list has never heard of are dropped rather than shifting the fold.
  assert.deepEqual(mergeVisibleOrder(['a', 'b'], ['b', 'ghost', 'a']), ['b', 'a'])
  assert.deepEqual(mergeVisibleOrder(['a', 'b'], []), ['a', 'b'])
})

test('every sort mode has a label; an unknown one reads as manual', () => {
  assert.equal(projectSortLabel('activity'), 'Recently active')
  assert.equal(projectSortLabel('custom'), 'Manual order')
})
