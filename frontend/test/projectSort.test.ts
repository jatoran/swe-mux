import assert from 'node:assert/strict'
import test from 'node:test'
import type { Project } from '../src/types.ts'
import {
  EMPTY_SIDEBAR_ORDER, bucketRecency, bucketStamp, isBucketCollapsed,
  loadSidebarOrder, mergeVisibleOrder, projectRecency, projectSortLabel,
  pruneSidebarOrder, serializeSidebarOrder, setAllBucketsCollapsed,
  setProjectSortMode, sidebarRootRows, sortProjects, sortRootEntries, toggleBucketCollapsed,
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
    collapsed: [],
  })
  assert.equal(loadSidebarOrder('{"projectSort":"nonsense"}').projectSort, 'custom')
  // The old synthetic ungrouped section's slot and fold state are discarded.
  assert.equal('ungroupedIndex' in loadSidebarOrder('{"ungroupedIndex":1}'), false)
  assert.deepEqual(loadSidebarOrder('{"collapsed":["ungrouped","g1"]}').collapsed, ['g1'])
  // The separate Group sort mode is gone from the prefs entirely.
  assert.equal('sectionSort' in loadSidebarOrder('{"sectionSort":"activity"}'), false)
  assert.deepEqual(loadSidebarOrder('{"collapsed":["g1",7,"g2"]}').collapsed, ['g1', 'g2'])
})

test('a legacy Group-only sort mode migrates into the single mode, behind Project evidence', () => {
  // Group order used to be its own setting. Its modes were a subset of the Project modes,
  // so the value carries over as the one mode that now places Groups too.
  assert.equal(loadSidebarOrder('{"sectionSort":"activity"}').projectSort, 'activity')
  assert.equal(loadSidebarOrder('{"sectionSort":"name-desc"}').projectSort, 'name-desc')
  // An explicit Manual stated nothing a missing mode does not.
  assert.equal(loadSidebarOrder('{"sectionSort":"custom"}').projectSort, 'custom')
  assert.equal(loadSidebarOrder('{"sectionSort":"nonsense"}').projectSort, 'custom')
  // Project-level evidence outranks it, whichever form that evidence took: a device with
  // both was ordering its Projects by the Project setting, and that is the surviving one.
  assert.equal(loadSidebarOrder('{"projectSort":"created","sectionSort":"activity"}').projectSort, 'created')
  assert.equal(loadSidebarOrder('{"sort":{"g1":"name"},"sectionSort":"activity"}').projectSort, 'name')
  // And it is not written back, so the migration fires exactly once.
  assert.equal(serializeSidebarOrder(loadSidebarOrder('{"sectionSort":"activity"}')).includes('sectionSort'), false)
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
  const stored = prefs({ projectSort: 'name', collapsed: ['g2'] })
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

const bucket = (id: string, name: string, items: (string | Project)[]) =>
  ({ id, name, items: items.map(item => (typeof item === 'string' ? project(item, item) : item)) })

/** Root ordering as ids, with a Group written as `[g1 a b]` so placement and contents are
 *  both legible in one assertion. */
const shape = (entries: ReturnType<typeof sortRootEntries>) =>
  entries.map(entry => entry.kind === 'project'
    ? entry.project.id
    : `[${[entry.bucket.id, ...entry.bucket.items.map(item => item.id)].join(' ')}]`)

test('manual order keeps the two-tier tree: root Projects, then Groups', () => {
  const roots = [project('p2', 'Beta'), project('p1', 'Alpha')]
  const buckets = [bucket('g2', 'Tools', ['a']), bucket('g1', 'Clients', [])]
  // A pass-through in both lists, so nothing the user arranged by hand is touched. Group
  // positions are their own order, and interleaving them with Project positions would have
  // no single key to interleave by.
  assert.deepEqual(shape(sortRootEntries(roots, buckets, 'custom', new Map())),
    ['p2', 'p1', '[g2 a]', '[g1]'])
})

test('a sorted sidebar places Groups among the root Projects, not below all of them', () => {
  // The reported bug: under Recently used, a Group holding this minute's work sat under
  // every root Project, including one that had never been opened.
  const roots = [project('cold', 'Cold'), project('warm', 'Warm')]
  const buckets = [bucket('jar', 'JAR', ['busy'])]
  const recency = new Map([['cold', 0], ['warm', 50], ['busy', 900]])
  assert.deepEqual(shape(sortRootEntries(roots, buckets, 'activity', recency)),
    ['[jar busy]', 'warm', 'cold'])
})

test('name ordering compares a Group by its own name, a Project by its', () => {
  const roots = [project('p1', 'Alpha'), project('p2', 'Zulu')]
  const buckets = [bucket('g1', 'Mike', []), bucket('g2', 'Bravo', [])]
  assert.deepEqual(shape(sortRootEntries(roots, buckets, 'name', new Map())),
    ['p1', '[g2]', '[g1]', 'p2'])
  assert.deepEqual(shape(sortRootEntries(roots, buckets, 'name-desc', new Map())),
    ['p2', '[g1]', '[g2]', 'p1'])
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
  assert.deepEqual(shape(sortRootEntries([], buckets, 'activity', recency)),
    ['[busy b c]', '[quiet a]', '[empty]'])
})

test('date modes key a Group on the member that leads it, skipping undated ones', () => {
  const dated = (id: string, created_at?: number) => project(id, id, { created_at })
  const spread = bucket('spread', 'Spread', [dated('mid', 500), dated('new', 900), dated('old', 100)])
  // Newest-first asks for the newest thing in there; oldest-first asks for the oldest.
  assert.equal(bucketStamp(spread, 'created-desc', new Map()), 900)
  assert.equal(bucketStamp(spread, 'created', new Map()), 100)
  // An undated member must not pull the minimum to 0, which reads as "no evidence" and
  // would send a Group full of old Projects to the bottom of Oldest first.
  assert.equal(bucketStamp(bucket('mixed', 'Mixed', [dated('none'), dated('old', 100)]), 'created', new Map()), 100)
  // Nothing measurable in it reads as unmeasured and lands last in either direction.
  assert.equal(bucketStamp(bucket('empty', 'Empty', []), 'created', new Map()), 0)
  assert.equal(bucketStamp(bucket('undated', 'Undated', [dated('none')]), 'created-desc', new Map()), 0)
  const roots = [dated('root', 300)]
  const buckets = [spread, bucket('void', 'Void', [])]
  assert.deepEqual(shape(sortRootEntries(roots, buckets, 'created-desc', new Map())),
    ['[spread mid new old]', 'root', '[void]'])
  assert.deepEqual(shape(sortRootEntries(roots, buckets, 'created', new Map())),
    ['[spread mid new old]', 'root', '[void]'])
})

test('root entries tie-break on the manual order they came in with, root Projects first', () => {
  const roots = [project('p1', 'P1')]
  const buckets = [bucket('second', 'Second', []), bucket('first', 'First', [])]
  // All three unmeasured, so none may jump the arrangement underneath the sort.
  assert.deepEqual(shape(sortRootEntries(roots, buckets, 'activity', new Map())),
    ['p1', '[second]', '[first]'])
})

test('root Projects between Groups render as separate lists, each its own drop target', () => {
  const rows = sidebarRootRows([
    { kind: 'project', project: project('a', 'A') },
    { kind: 'project', project: project('b', 'B') },
    { kind: 'group', bucket: bucket('g1', 'G1', ['x']) },
    { kind: 'project', project: project('c', 'C') },
  ])
  assert.deepEqual(rows.map(row => row.kind), ['root', 'group', 'root'])
  assert.deepEqual(rows.flatMap(row => row.kind === 'root' ? [row.items.map(item => item.id)] : []),
    [['a', 'b'], ['c']])
  // Keys are derived from content, so they are stable across a re-sort of the same tree.
  assert.deepEqual(rows.map(row => row.key), ['root:a', 'group:g1', 'root:c'])
})

test('a tree with every Project grouped still renders one empty root list to drop into', () => {
  const rows = sidebarRootRows([{ kind: 'group', bucket: bucket('g1', 'G1', ['x']) }])
  assert.deepEqual(rows.map(row => row.kind), ['root', 'group'])
  assert.deepEqual(rows[0].kind === 'root' && rows[0].items, [])
  // Exactly one, even with several Groups, and always first: with no root Projects there is
  // nothing to interleave it with.
  const many = sidebarRootRows([
    { kind: 'group', bucket: bucket('g1', 'G1', []) },
    { kind: 'group', bucket: bucket('g2', 'G2', []) },
  ])
  assert.deepEqual(many.map(row => row.kind), ['root', 'group', 'group'])
  // An empty sidebar renders nothing at all, not a bare drop hint.
  assert.deepEqual(sidebarRootRows([]), [])
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
