import assert from 'node:assert/strict'
import test from 'node:test'
import type { Project, Session } from '../src/types.ts'
import {
  EMPTY_SIDEBAR_ORDER, UNGROUPED_BUCKET_ID, bucketActivity, isBucketCollapsed,
  loadSidebarOrder, mergeVisibleOrder, placeUngrouped, projectActivity, projectSortLabel,
  pruneSidebarOrder, sectionSortLabel, serializeSidebarOrder, setAllBucketsCollapsed,
  setProjectSortMode, sortBuckets, sortProjects, toggleBucketCollapsed,
} from '../src/projectSort.ts'

const project = (id: string, name: string, extra: Partial<Project> = {}) =>
  ({ id, name, root: `D:/${id}`, position: 0, layout: null, layout_revision: 0, ...extra }) as Project

const session = (id: string, project_id: string, extra: Partial<Session> = {}) =>
  ({ id, name: id, project_id, state: 'running', created_at: 0, last_activity_ts: 0, ...extra }) as unknown as Session

const prefs = (extra: Partial<typeof EMPTY_SIDEBAR_ORDER> = {}) => ({ ...EMPTY_SIDEBAR_ORDER, ...extra })

test('sidebar order load tolerates missing, malformed, and unknown modes', () => {
  assert.deepEqual(loadSidebarOrder(null), EMPTY_SIDEBAR_ORDER)
  assert.deepEqual(loadSidebarOrder('not json'), EMPTY_SIDEBAR_ORDER)
  assert.deepEqual(loadSidebarOrder('[1,2]'), EMPTY_SIDEBAR_ORDER)
  assert.deepEqual(loadSidebarOrder('{"projectSort":"name","ungroupedIndex":1}'), {
    projectSort: 'name',
    sectionSort: 'custom',
    ungroupedIndex: 1,
    collapsed: [],
  })
  assert.equal(loadSidebarOrder('{"projectSort":"nonsense"}').projectSort, 'custom')
  // A negative or fractional slot would splice somewhere nobody asked for.
  assert.equal(loadSidebarOrder('{"ungroupedIndex":-2}').ungroupedIndex, null)
  assert.equal(loadSidebarOrder('{"ungroupedIndex":1.5}').ungroupedIndex, null)
  // Section modes are a narrower set than Project modes; a Project-only mode here
  // would order the sections by a key they do not have.
  assert.equal(loadSidebarOrder('{"sectionSort":"created-desc"}').sectionSort, 'custom')
  assert.equal(loadSidebarOrder('{"sectionSort":"activity"}').sectionSort, 'activity')
  assert.deepEqual(loadSidebarOrder('{"collapsed":["g1",7,"g2"]}').collapsed, ['g1', 'g2'])
})

test('a legacy per-bucket sort map migrates to the single mode that replaced it', () => {
  // Sort used to be stored per bucket id. Whichever bucket was actually set wins;
  // dropping it instead would silently reset the sidebar on upgrade.
  assert.equal(loadSidebarOrder('{"sort":{"g1":"name"}}').projectSort, 'name')
  assert.equal(loadSidebarOrder('{"sort":{"g1":"nonsense","g2":"activity"}}').projectSort, 'activity')
  assert.equal(loadSidebarOrder('{"sort":{"g1":"custom"}}').projectSort, 'custom')
  assert.equal(loadSidebarOrder('{"sort":"nonsense"}').projectSort, 'custom')
  // An explicit new-format value always beats the legacy map.
  assert.equal(loadSidebarOrder('{"projectSort":"created","sort":{"g1":"name"}}').projectSort, 'created')
  // And the legacy key is not written back, so the migration fires exactly once.
  assert.equal(serializeSidebarOrder(loadSidebarOrder('{"sort":{"g1":"name"}}')).includes('"sort"'), false)
})

test('sidebar order round-trips', () => {
  const stored = prefs({
    projectSort: 'name', sectionSort: 'activity', ungroupedIndex: 0, collapsed: ['g2'],
  })
  assert.deepEqual(loadSidebarOrder(serializeSidebarOrder(stored)), stored)
})

test('collapsing a section toggles without mutating the input', () => {
  const start = EMPTY_SIDEBAR_ORDER
  const folded = toggleBucketCollapsed(start, 'g1')
  assert.deepEqual(start.collapsed, [])
  assert.equal(isBucketCollapsed(folded, 'g1'), true)
  assert.equal(isBucketCollapsed(folded, 'g2'), false)
  assert.deepEqual(toggleBucketCollapsed(folded, 'g1').collapsed, [])
})

test('collapse-all folds every section and expand-all clears the list outright', () => {
  const folded = setAllBucketsCollapsed(EMPTY_SIDEBAR_ORDER, ['g1', 'g1', UNGROUPED_BUCKET_ID], true)
  assert.deepEqual(folded.collapsed, ['g1', UNGROUPED_BUCKET_ID])
  // Expanding clears rather than subtracting, so a stale id from a Group that
  // vanished while folded cannot survive an explicit "expand everything".
  const stale = prefs({ collapsed: ['g1', 'gone'] })
  assert.deepEqual(setAllBucketsCollapsed(stale, ['g1'], false).collapsed, [])
})

test('project sort mode set/read, keeping identity when unchanged', () => {
  const named = setProjectSortMode(EMPTY_SIDEBAR_ORDER, 'activity')
  assert.equal(named.projectSort, 'activity')
  assert.equal(setProjectSortMode(named, 'custom').projectSort, 'custom')
  // Setting the mode it already has must not churn a new object into state.
  assert.equal(setProjectSortMode(named, 'activity'), named)
})

test('pruneSidebarOrder drops buckets that no longer exist and keeps identity otherwise', () => {
  const stored = prefs({ collapsed: ['g1', 'gone'] })
  const pruned = pruneSidebarOrder(stored, ['g1', UNGROUPED_BUCKET_ID])
  // A deleted Group's fold would otherwise be inherited by whatever bucket id came back.
  assert.deepEqual(pruned.collapsed, ['g1'])
  assert.equal(pruneSidebarOrder(stored, ['g1', 'gone']), stored)
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

const bucket = (id: string, name: string, items: string[]) =>
  ({ id, name, items: items.map(item => project(item, item)) })

test('sections sort by name with the ungrouped remainder competing on its own label', () => {
  const buckets = [bucket('g1', 'Tools', []), bucket(UNGROUPED_BUCKET_ID, 'Projects', []), bucket('g2', 'Clients', [])]
  assert.deepEqual(sortBuckets(buckets, 'name', new Map()).map(item => item.id), ['g2', UNGROUPED_BUCKET_ID, 'g1'])
  assert.deepEqual(sortBuckets(buckets, 'name-desc', new Map()).map(item => item.id), ['g1', UNGROUPED_BUCKET_ID, 'g2'])
  // Manual order is a pass-through, so the caller's arrangement is untouched.
  assert.equal(sortBuckets(buckets, 'custom', new Map()), buckets)
})

test('a section is as recent as the most recent Project in it; empty ones sort last', () => {
  const activity = new Map([['a', 10], ['b', 900], ['c', 40]])
  const buckets = [
    bucket('quiet', 'Quiet', ['a']),
    bucket('empty', 'Empty', []),
    bucket('busy', 'Busy', ['b', 'c']),
  ]
  assert.equal(bucketActivity(buckets[2], activity), 900)
  assert.equal(bucketActivity(buckets[1], activity), 0)
  assert.deepEqual(sortBuckets(buckets, 'activity', activity).map(item => item.id), ['busy', 'quiet', 'empty'])
})

test('sections tie-break on the manual order they came in with', () => {
  const buckets = [bucket('second', 'Second', []), bucket('first', 'First', [])]
  // Both unmeasured, so neither may jump the arrangement underneath the sort.
  assert.deepEqual(sortBuckets(buckets, 'activity', new Map()).map(item => item.id), ['second', 'first'])
})

test('every section mode has a label; an unknown one reads as manual', () => {
  assert.equal(sectionSortLabel('activity'), 'Recently active')
  assert.equal(sectionSortLabel('custom'), 'Manual order')
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
