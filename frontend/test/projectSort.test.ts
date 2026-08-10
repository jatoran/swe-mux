import assert from 'node:assert/strict'
import test from 'node:test'
import type { Project } from '../src/types.ts'
import {
  EMPTY_SIDEBAR_ORDER, bucketRecency, isBucketCollapsed,
  loadSidebarOrder, mergeVisibleOrder, projectRecency, projectSortLabel,
  pruneSidebarOrder, sectionSortLabel, serializeSidebarOrder, setAllBucketsCollapsed,
  setProjectSortMode, sortBuckets, sortProjects, toggleBucketCollapsed,
} from '../src/projectSort.ts'

const project = (id: string, name: string, extra: Partial<Project> = {}) =>
  ({ id, name, root: `D:/${id}`, position: 0, layout: null, layout_revision: 0, ...extra }) as Project

const prefs = (extra: Partial<typeof EMPTY_SIDEBAR_ORDER> = {}) => ({ ...EMPTY_SIDEBAR_ORDER, ...extra })

test('sidebar order load tolerates missing, malformed, and unknown modes', () => {
  assert.deepEqual(loadSidebarOrder(null), EMPTY_SIDEBAR_ORDER)
  assert.deepEqual(loadSidebarOrder('not json'), EMPTY_SIDEBAR_ORDER)
  assert.deepEqual(loadSidebarOrder('[1,2]'), EMPTY_SIDEBAR_ORDER)
  assert.deepEqual(loadSidebarOrder('{"projectSort":"name","ungroupedIndex":1}'), {
    projectSort: 'name',
    sectionSort: 'custom',
    collapsed: [],
  })
  assert.equal(loadSidebarOrder('{"projectSort":"nonsense"}').projectSort, 'custom')
  // The old synthetic ungrouped section's slot and fold state are discarded.
  assert.equal('ungroupedIndex' in loadSidebarOrder('{"ungroupedIndex":1}'), false)
  assert.deepEqual(loadSidebarOrder('{"collapsed":["ungrouped","g1"]}').collapsed, ['g1'])
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
    projectSort: 'name', sectionSort: 'activity', collapsed: ['g2'],
  })
  assert.deepEqual(loadSidebarOrder(serializeSidebarOrder(stored)), stored)
  // The former browser-local MRU is ignored and removed on the next write.
  assert.equal(serializeSidebarOrder(loadSidebarOrder('{"recentProjects":["p2","p1"]}')).includes('recentProjects'), false)
})

test('collapsing a Group toggles without mutating the input', () => {
  const start = EMPTY_SIDEBAR_ORDER
  const folded = toggleBucketCollapsed(start, 'g1')
  assert.deepEqual(start.collapsed, [])
  assert.equal(isBucketCollapsed(folded, 'g1'), true)
  assert.equal(isBucketCollapsed(folded, 'g2'), false)
  assert.deepEqual(toggleBucketCollapsed(folded, 'g1').collapsed, [])
})

test('collapse-all folds every Group and expand-all clears the list outright', () => {
  const folded = setAllBucketsCollapsed(EMPTY_SIDEBAR_ORDER, ['g1', 'g1', 'g2'], true)
  assert.deepEqual(folded.collapsed, ['g1', 'g2'])
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

test('pruneSidebarOrder drops fold state for Groups that no longer exist', () => {
  const stored = prefs({ collapsed: ['g1', 'gone'] })
  const pruned = pruneSidebarOrder(stored, ['g1'])
  // A deleted Group id must not be inherited by a recreated Group with the same id.
  assert.deepEqual(pruned.collapsed, ['g1'])
  assert.equal(pruneSidebarOrder(stored, ['g1', 'gone']), stored)
})

test('pruneSidebarOrder is the identity before the Group registry has loaded', () => {
  // The caller holds an empty array from mount until the first fetch resolves. Reading
  // that as an empty registry wiped fold state on every page load and persisted the
  // wipe, so "not loaded" has to be its own value.
  const stored = prefs({ collapsed: ['g1'] })
  assert.equal(pruneSidebarOrder(stored, null), stored)
  // An empty registry that really is empty stays destructive.
  assert.deepEqual(pruneSidebarOrder(stored, []).collapsed, [])
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

test('recent-use ordering is newest first and falls back to manual order on a tie', () => {
  const items = [project('a', 'A'), project('b', 'B'), project('c', 'C')]
  const recency = new Map([['a', 10], ['b', 0], ['c', 10]])
  assert.deepEqual(sortProjects(items, 'activity', recency).map(item => item.id), ['a', 'c', 'b'])
})

test('projectRecency uses shared daemon timestamps', () => {
  const recency = projectRecency([
    project('p2', 'Two', { last_used_at: 200 }),
    project('p1', 'One', { last_used_at: 100 }),
    project('p3', 'Three'),
  ])
  assert.equal(recency.get('p2'), 200)
  assert.equal(recency.get('p1'), 100)
  assert.equal(recency.get('p3'), 0)
})

const bucket = (id: string, name: string, items: string[]) =>
  ({ id, name, items: items.map(item => project(item, item)) })

test('Groups sort by name without a synthetic ungrouped section', () => {
  const buckets = [bucket('g1', 'Tools', []), bucket('g2', 'Clients', [])]
  assert.deepEqual(sortBuckets(buckets, 'name', new Map()).map(item => item.id), ['g2', 'g1'])
  assert.deepEqual(sortBuckets(buckets, 'name-desc', new Map()).map(item => item.id), ['g1', 'g2'])
  // Manual order is a pass-through, so the caller's arrangement is untouched.
  assert.equal(sortBuckets(buckets, 'custom', new Map()), buckets)
})

test('a Group is as recent as the most recent Project in it; empty ones sort last', () => {
  const recency = new Map([['a', 10], ['b', 900], ['c', 40]])
  const buckets = [
    bucket('quiet', 'Quiet', ['a']),
    bucket('empty', 'Empty', []),
    bucket('busy', 'Busy', ['b', 'c']),
  ]
  assert.equal(bucketRecency(buckets[2], recency), 900)
  assert.equal(bucketRecency(buckets[1], recency), 0)
  assert.deepEqual(sortBuckets(buckets, 'activity', recency).map(item => item.id), ['busy', 'quiet', 'empty'])
})

test('Groups tie-break on the manual order they came in with', () => {
  const buckets = [bucket('second', 'Second', []), bucket('first', 'First', [])]
  // Both unmeasured, so neither may jump the arrangement underneath the sort.
  assert.deepEqual(sortBuckets(buckets, 'activity', new Map()).map(item => item.id), ['second', 'first'])
})

test('every Group sort mode has a label; an unknown one reads as manual', () => {
  assert.equal(sectionSortLabel('activity'), 'Recently used')
  assert.equal(sectionSortLabel('custom'), 'Manual order')
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
  assert.equal(projectSortLabel('activity'), 'Recently used')
  assert.equal(projectSortLabel('custom'), 'Manual order')
})
