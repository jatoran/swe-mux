import assert from 'node:assert/strict'
import test from 'node:test'
import {
  NO_SEARCH_CURSOR, SIDEBAR_SEARCH_IDLE_MS,
  buildSidebarSearchIndex, clampSearchCursor, moveSearchCursor, sameSearchRow,
  sidebarSearchExpired, sidebarTreeFilter,
  type SearchableGroup, type SearchableProject, type SearchableSession,
} from '../src/sidebarSearch.ts'

const project = (id: string, name: string, root = ''): SearchableProject => ({ id, name, root })
const session = (
  id: string, project_id: string, name: string, extra: Partial<SearchableSession> = {},
): SearchableSession => ({ id, project_id, name, created_at: 1, ...extra })
const group = (id: string, name: string, projectIds: string[]): SearchableGroup => ({ id, name, projectIds })

/** Every drawn row, in sidebar order, as `kind:id`. */
const drawn = (filter: ReturnType<typeof sidebarTreeFilter>) =>
  (filter?.order || []).map(item => `${item.kind}:${item.id}`)

const filter = (
  projects: SearchableProject[],
  sessions: SearchableSession[],
  query: string,
  groups: SearchableGroup[] = [],
) => sidebarTreeFilter(buildSidebarSearchIndex(projects, sessions), groups, query)

test('index lists each Project followed by its sessions in creation order', () => {
  const index = buildSidebarSearchIndex(
    [project('p1', 'swe-mux'), project('p2', 'orca')],
    [
      session('s2', 'p1', 'later', { created_at: 20 }),
      session('s1', 'p1', 'earlier', { created_at: 10 }),
      session('s3', 'p2', 'other', { created_at: 5 }),
    ],
  )
  assert.deepEqual(index.map(item => item.id), ['p1', 's1', 's2', 'p2', 's3'])
  assert.deepEqual(index.map(item => item.order), [0, 1, 2, 3, 4])
})

test('a session indexes under the name its row draws, not always its spawned name', () => {
  const [, generated, renamed] = buildSidebarSearchIndex(
    [project('p1', 'swe-mux')],
    [
      session('s1', 'p1', 'bash', { generated_title: 'refactor the parser', created_at: 1 }),
      // A rename is the human overriding the generator, so the title must not take it back.
      session('s2', 'p1', 'my terminal', { generated_title: 'something else', auto_named: false, created_at: 2 }),
    ],
  )
  assert.equal(generated.label, 'refactor the parser')
  assert.equal(renamed.label, 'my terminal')
})

test('a session with no name at all still has something to match and show', () => {
  const [, only] = buildSidebarSearchIndex([project('p1', 'p')], [session('s1', 'p1', '')])
  assert.equal(only.label, 's1')
  assert.equal(only.key, 's1')
})

test('an empty query means not filtering, which is not the same as filtering to nothing', () => {
  // The host draws its ordinary tree for null. Opening the filter must change nothing on
  // screen until a character is typed.
  assert.equal(filter([project('p1', 'swe-mux')], [], ''), null)
  assert.equal(filter([project('p1', 'swe-mux')], [], '   '), null)
})

test('a matching session keeps its Project and its Group on screen', () => {
  // A row with no heading over it does not say where it lives.
  const result = filter(
    [project('p1', 'swe-mux'), project('p2', 'orca')],
    [session('s1', 'p1', 'parser work'), session('s2', 'p2', 'unrelated')],
    'parser',
    [group('g1', 'work', ['p1', 'p2'])],
  )
  assert.deepEqual([...result!.sessions], ['s1'])
  assert.deepEqual([...result!.projects], ['p1'])
  assert.deepEqual([...result!.groups], ['g1'])
  assert.deepEqual(drawn(result), ['project:p1', 'session:s1'])
})

test('a Project matched by name keeps its sessions', () => {
  // Without this, typing a Project name renders it as an empty heading.
  const result = filter(
    [project('p1', 'orca'), project('p2', 'other')],
    [session('a', 'p1', 'nothing alike'), session('b', 'p1', 'also unrelated'), session('c', 'p2', 'orca')],
    'orca',
  )
  // p1 matched by name and so keeps both of its sessions, neither of which matched.
  // p2 is drawn only because its own session did, and brings nothing else with it.
  assert.deepEqual(drawn(result), ['project:p1', 'session:a', 'session:b', 'project:p2', 'session:c'])
})

test('a Group matched by name keeps its Projects and their sessions', () => {
  const result = filter(
    [project('p1', 'alpha'), project('p2', 'beta'), project('p3', 'outside')],
    [session('s1', 'p1', 'one'), session('s3', 'p3', 'three')],
    'infra',
    [group('g1', 'infra', ['p1', 'p2'])],
  )
  assert.deepEqual([...result!.groups], ['g1'])
  assert.deepEqual(drawn(result), ['project:p1', 'session:s1', 'project:p2'])
})

test('a Project outside every Group needs no Group to be drawn', () => {
  const result = filter([project('p1', 'solo')], [], 'solo', [group('g1', 'elsewhere', [])])
  assert.deepEqual([...result!.groups], [])
  assert.deepEqual(drawn(result), ['project:p1'])
})

test('rows keep sidebar order, never rank order', () => {
  // Re-sorting a hand-arranged tree behind the user is the one thing this must not do:
  // "beta" scores higher than "alpha beta" but stays where the sidebar put it.
  const result = filter([project('p1', 'alpha beta'), project('p2', 'beta')], [], 'beta')
  assert.deepEqual(drawn(result), ['project:p1', 'project:p2'])
  assert.equal(result!.best?.id, 'p2')
})

test('every term has to match, so a second word narrows', () => {
  const projects = [project('p1', 'swe-mux'), project('p2', 'orca')]
  const sessions = [session('s1', 'p1', 'frontend'), session('s2', 'p2', 'frontend')]
  // "frontend" alone reaches both sessions; the Project name is indexed as a keyword,
  // so adding it picks the one in swe-mux.
  assert.deepEqual(drawn(filter(projects, sessions, 'frontend')), ['project:p1', 'session:s1', 'project:p2', 'session:s2'])
  assert.deepEqual(drawn(filter(projects, sessions, 'frontend mux')), ['project:p1', 'session:s1'])
})

test('best is a direct hit, never a row kept only by containment', () => {
  // Landing on a Project you were shown because one of its sessions matched goes to the
  // wrong place.
  const result = filter([project('p1', 'swe-mux')], [session('s1', 'p1', 'zzz-unique')], 'zzz-unique')
  assert.deepEqual(drawn(result), ['project:p1', 'session:s1'])
  assert.equal(result!.best?.kind, 'session')
  assert.equal(result!.best?.id, 's1')
})

test('a Project outranks a session it ties with', () => {
  const result = filter([project('p1', 'orca')], [session('s1', 'p1', 'orca')], 'orca')
  assert.equal(result!.best?.kind, 'project')
})

test('a live session outranks an ended one of the same name', () => {
  const result = filter(
    [project('p1', 'p')],
    [
      session('dead', 'p1', 'build', { state: 'exited', created_at: 1 }),
      session('live', 'p1', 'build', { state: 'working', created_at: 2 }),
    ],
    'build',
  )
  // Both are still drawn - an ended pane stays readable - but the ended one is not best.
  assert.deepEqual(drawn(result), ['project:p1', 'session:dead', 'session:live'])
  assert.equal(result!.best?.id, 'live')
})

test('a name you typed the start of beats the same word buried in secondary text', () => {
  const result = filter(
    [project('p1', 'other')],
    [
      session('buried', 'p1', 'nothing alike', { backend: 'codex', created_at: 1 }),
      session('named', 'p1', 'codex sweep', { backend: 'claude', created_at: 2 }),
    ],
    'codex',
  )
  assert.equal(result!.best?.id, 'named')
})

test('branch and worktree are matchable, and so is the Project root', () => {
  const projects = [project('p1', 'swe-mux', 'D:/PROJECTS/swe-mux')]
  const sessions = [session('s1', 'p1', 'agent', { git: { worktree: 'worktree-sidebar-filter' } })]
  assert.deepEqual(drawn(filter(projects, sessions, 'sidebar-filter')), ['project:p1', 'session:s1'])
  // The Project matched by its root, so it keeps its subtree.
  assert.deepEqual(drawn(filter(projects, sessions, 'projects')), ['project:p1', 'session:s1'])
})

test('an abbreviation lands only while its letters stay in a tight span', () => {
  assert.deepEqual(drawn(filter([project('p1', 'scrollback bytes')], [], 'scrlbck')), ['project:p1'])
  // The same letters scattered across a long name are not an abbreviation of it.
  assert.deepEqual(drawn(filter([project('p2', 'set shortcut for open command palette')], [], 'sound')), [])
})

test('matching is case- and whitespace-insensitive on both sides', () => {
  assert.deepEqual(drawn(filter([project('p1', '  Swe   Mux  ')], [], 'SWE  mux')), ['project:p1'])
})

test('a query that matches nothing draws nothing and names no best', () => {
  const result = filter([project('p1', 'swe-mux')], [session('s1', 'p1', 'agent')], 'zzzz')
  assert.deepEqual(drawn(result), [])
  assert.equal(result!.best, null)
  assert.equal(result!.projects.size, 0)
})

test('rows are identified by kind and id together', () => {
  const [projectRow, sessionRow] = buildSidebarSearchIndex([project('x', 'x')], [session('x', 'x', 'x')])
  assert.equal(sameSearchRow(projectRow, sessionRow), false)
  assert.equal(sameSearchRow(projectRow, projectRow), true)
  assert.equal(sameSearchRow(null, projectRow), false)
  assert.equal(sameSearchRow(projectRow, null), false)
})

test('the cursor clamps into a list that shrank under it, and unset stays unset', () => {
  assert.equal(clampSearchCursor(7, 3), 2)
  assert.equal(clampSearchCursor(2, 3), 2)
  // Unset means "wherever the best match is", which is the state every keystroke returns
  // the cursor to and the state an unfiltered sidebar is always in.
  assert.equal(clampSearchCursor(NO_SEARCH_CURSOR, 3), NO_SEARCH_CURSOR)
  assert.equal(clampSearchCursor(2, 0), NO_SEARCH_CURSOR)
})

test('arrows stop at both ends rather than wrapping, and enter an unset list from its end', () => {
  assert.equal(moveSearchCursor(0, -1, 4), 0)
  assert.equal(moveSearchCursor(3, 1, 4), 3)
  assert.equal(moveSearchCursor(1, 1, 4), 2)
  assert.equal(moveSearchCursor(NO_SEARCH_CURSOR, 1, 4), 0)
  assert.equal(moveSearchCursor(NO_SEARCH_CURSOR, -1, 4), 3)
  assert.equal(moveSearchCursor(NO_SEARCH_CURSOR, 1, 0), NO_SEARCH_CURSOR)
})

test('the filter retires itself only once the idle window has fully elapsed', () => {
  assert.equal(sidebarSearchExpired(1000, 1000 + SIDEBAR_SEARCH_IDLE_MS - 1), false)
  assert.equal(sidebarSearchExpired(1000, 1000 + SIDEBAR_SEARCH_IDLE_MS), true)
  // Any interaction restamps the clock, which is what keeps a filter under an active
  // pointer alive.
  assert.equal(sidebarSearchExpired(9000, 9000 + 10), false)
})
