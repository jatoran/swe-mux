import assert from 'node:assert/strict'
import test from 'node:test'
import {
  branchRows,
  changeStatusLabel,
  divergenceLabel,
  isAbsolutePath,
  normalizePath,
  parseGitGraph,
  parseWorktrees,
  pathTail,
  repoSessions,
  sessionGitCwd,
  shortBranch,
  worktreeForPath,
  type SessionGit,
} from '../src/gitWorktrees.ts'

// Real `git worktree list --porcelain` output as `server.py`'s `_parse_worktrees` hands it
// over: `key value` lines become strings, valueless flag lines become `true`.
const PORCELAIN = [
  { worktree: 'D:/PROJECTS/swe-mux', HEAD: '9299950aa1bb2cc3dd4ee5ff6001122334455667', branch: 'refs/heads/master' },
  { worktree: 'D:/PROJECTS/.worktrees/swe-mux/git-pane', HEAD: 'aa11bb22cc33dd44ee55ff6677889900aabbccdd', branch: 'refs/heads/agent/git-pane' },
  { worktree: 'D:/PROJECTS/.worktrees/swe-mux/detached', HEAD: 'ffeeddccbbaa99887766554433221100ffeeddcc', detached: true as const },
]

const session = (over: Partial<SessionGit> = {}): SessionGit => ({
  id: 's1', name: 'one', cwd: 'D:\\PROJECTS\\swe-mux', branch: 'master', dirty: 0, ahead: 0, behind: 0, ...over,
})

test('porcelain records become worktrees, with the main tree marked first', () => {
  const items = parseWorktrees(PORCELAIN)
  assert.equal(items.length, 3)
  assert.deepEqual(items.map(item => item.branch), ['master', 'agent/git-pane', null])
  assert.deepEqual(items.map(item => item.main), [true, false, false])
  assert.deepEqual(items.map(item => item.detached), [false, false, true])
  // Flags default to null rather than false, because Git distinguishes "locked with no
  // reason given" from "not locked" and the row renders the reason when there is one.
  assert.deepEqual(items.map(item => item.locked), [null, null, null])
})

test('flag lines carry their reason, or an empty string when Git gave none', () => {
  const [locked, prunable] = parseWorktrees([
    { worktree: '/repo/a', locked: true as const },
    { worktree: '/repo/b', prunable: 'gitdir file points to non-existent location' },
  ])
  assert.equal(locked.locked, '')
  assert.equal(locked.prunable, null)
  assert.equal(prunable.prunable, 'gitdir file points to non-existent location')
})

test('unlanded counts survive parsing, and an unmeasured tree is null not zero', () => {
  // The distinction is the whole point: null means "the daemon could not measure this",
  // and rendering that as 0 would claim there is nothing waiting to be landed.
  const [measured, empty, missing, bogus] = parseWorktrees([
    { worktree: '/wt/busy', branch: 'refs/heads/agent/busy', unlanded: 3 },
    { worktree: '/wt/done', branch: 'refs/heads/agent/done', unlanded: 0 },
    { worktree: '/wt/unknown', branch: 'refs/heads/agent/unknown' },
    { worktree: '/wt/bogus', branch: 'refs/heads/agent/bogus', unlanded: 'lots' },
  ])
  assert.equal(measured.unlanded, 3)
  assert.equal(empty.unlanded, 0)
  assert.equal(missing.unlanded, null)
  assert.equal(bogus.unlanded, null, 'a non-numeric count is unmeasured, not zero')
})

test('worktree and trunk-relative file summaries remain separate', () => {
  const [item] = parseWorktrees([{
    worktree: '/repo',
    branch: 'refs/heads/agent/map',
    working_tree: {
      total: 2,
      files: [
        { status: '.M', path: 'frontend/src/GitTab.tsx' },
        { status: '??', path: 'frontend/src/new.ts' },
      ],
      truncated: false,
    },
    branch_delta: {
      total: 1,
      files: [{ status: 'R100', path: 'new-name.ts', old_path: 'old-name.ts' }],
      truncated: true,
    },
  }])
  assert.equal(item.workingTree?.total, 2)
  assert.equal(item.workingTree?.files[1].path, 'frontend/src/new.ts')
  assert.equal(item.branchDelta?.total, 1)
  assert.equal(item.branchDelta?.files[0].oldPath, 'old-name.ts')
  assert.equal(item.branchDelta?.truncated, true)
})

test('malformed change summaries remain unmeasured instead of clean', () => {
  const [item] = parseWorktrees([{
    worktree: '/repo',
    working_tree: { total: 'zero', files: [] },
    branch_delta: null,
  }])
  assert.equal(item.workingTree, null)
  assert.equal(item.branchDelta, null)
})

test('junk from the daemon yields no worktrees rather than throwing', () => {
  assert.deepEqual(parseWorktrees(null), [])
  assert.deepEqual(parseWorktrees({ error: 'not a git repository' }), [])
  // A record with no `worktree` line is not a working tree; it is skipped, and skipping it
  // must not shift which entry is treated as main.
  assert.deepEqual(parseWorktrees([{ HEAD: 'abc' }, { worktree: '/repo' }]).map(item => item.main), [true])
})

test('refs are shortened, oids abbreviated, paths tailed', () => {
  assert.equal(shortBranch('refs/heads/agent/git-pane'), 'agent/git-pane')
  assert.equal(shortBranch('master'), 'master')
  assert.equal(pathTail('D:/PROJECTS/.worktrees/swe-mux/git-pane'), 'git-pane')
  assert.equal(pathTail('D:\\PROJECTS\\swe-mux\\'), 'swe-mux')
})

test('path comparison crosses the separator and case difference Windows creates', () => {
  // Git reports forward slashes even on Windows; a session cwd carries backslashes.
  assert.equal(normalizePath('D:\\PROJECTS\\swe-mux\\'), 'd:/projects/swe-mux')
  const items = parseWorktrees(PORCELAIN)
  assert.equal(worktreeForPath(items, 'D:\\PROJECTS\\swe-mux')?.branch, 'master')
  // A cwd deeper inside a worktree still belongs to it, and the *longest* match wins, so a
  // nested worktree is never attributed to the repository root that contains it.
  assert.equal(worktreeForPath(items, 'D:/PROJECTS/.worktrees/swe-mux/git-pane/frontend/src')?.branch, 'agent/git-pane')
  assert.equal(worktreeForPath(items, 'D:/elsewhere'), null)
  assert.equal(worktreeForPath(items, ''), null)
})

test('a sibling directory sharing a prefix is not inside the worktree', () => {
  // Substring matching would put `swe-mux-old` inside `swe-mux`; the boundary check is
  // what stops a neighbouring checkout being reported as this repository's tree.
  const items = parseWorktrees([{ worktree: 'D:/PROJECTS/swe-mux' }])
  assert.equal(worktreeForPath(items, 'D:/PROJECTS/swe-mux-old'), null)
  assert.equal(worktreeForPath(items, 'D:/PROJECTS/swe-mux/src')?.path, 'D:/PROJECTS/swe-mux')
})

test('git_cwd follows accepted live telemetry, then the spawn cwd', () => {
  // Mirrors `SessionRecord.git_cwd`, which is a backend property and so never reaches the
  // snapshot: the monitor polls the live cwd only once telemetry has been accepted.
  const record = (over: Record<string, unknown>) =>
    sessionGitCwd({ cwd: '/spawned', runtime_cwd_live: false, ...over } as never)
  assert.equal(record({ runtime_cwd: '/live' }), '/spawned')
  assert.equal(record({ runtime_cwd_live: true, runtime_cwd: '/live' }), '/live')
  assert.equal(record({ spawn_cwd: '/explicit' }), '/explicit')
})

test('only live sessions of this Project contribute state', () => {
  const sessions = [
    { id: 'a', name: 'a', project_id: 'p1', state: 'idle', cwd: '/repo', git: { branch: 'master', dirty: 3, ahead: 1, behind: 0 } },
    { id: 'b', name: 'b', project_id: 'p1', state: 'exited', cwd: '/repo', git: { branch: 'master', dirty: 9, ahead: 0, behind: 0 } },
    { id: 'c', name: 'c', project_id: 'p2', state: 'idle', cwd: '/other', git: { branch: 'main', dirty: 0, ahead: 0, behind: 0 } },
    { id: 'd', name: 'd', project_id: 'p1', state: 'running', cwd: '/repo', pending: true, git: { branch: 'master', dirty: 0, ahead: 0, behind: 0 } },
  ] as never[]
  assert.deepEqual(repoSessions(sessions, 'p1').map(item => item.id), ['a'])
  assert.equal(repoSessions(sessions, 'p1')[0].dirty, 3)
})

test('branch rows join the worktree inventory to live working-tree state', () => {
  const items = parseWorktrees(PORCELAIN)
  const rows = branchRows(items, [
    session({ id: 'a', name: 'main tree', cwd: 'D:\\PROJECTS\\swe-mux', branch: 'master', dirty: 4, behind: 2 }),
    session({ id: 'b', name: 'pane', cwd: 'D:\\PROJECTS\\.worktrees\\swe-mux\\git-pane\\frontend', branch: 'agent/git-pane', ahead: 3 }),
  ])
  assert.equal(rows.length, 3)
  // The main tree leads; the rest sort by label.
  assert.equal(rows[0].label, 'master')
  assert.deepEqual(rows[0].state, { dirty: 4, ahead: 0, behind: 2 })
  assert.deepEqual(rows[0].sessions.map(item => item.name), ['main tree'])
  const pane = rows.find(row => row.label === 'agent/git-pane')!
  assert.deepEqual(pane.state, { dirty: 0, ahead: 3, behind: 0 })
  // Nobody is attached to the detached tree, so it has no working-tree state at all — a
  // row with no session is not a clean row, it is an unmeasured one.
  const detached = rows.find(row => row.detached)!
  assert.equal(detached.state, null)
  assert.equal(detached.label, 'detached @ ffeeddcc')
})

test('a detached session folds in by path, never by its branch string', () => {
  // `read_git_reading` writes the short SHA into the branch field of a detached HEAD, so a
  // name match would put that session in a row of its own beside the tree it is sitting in.
  const rows = branchRows(parseWorktrees(PORCELAIN), [
    session({ id: 'x', name: 'detached', cwd: 'D:/PROJECTS/.worktrees/swe-mux/detached', branch: 'ffeeddc', dirty: 2 }),
  ])
  assert.equal(rows.length, 3)
  const detached = rows.find(row => row.detached)!
  assert.deepEqual(detached.sessions.map(item => item.id), ['x'])
  assert.equal(detached.state?.dirty, 2)
})

test('a session outside every worktree gets its own row, marked as another repository', () => {
  const rows = branchRows(parseWorktrees(PORCELAIN), [
    session({ id: 'n', name: 'vendored', cwd: 'D:/PROJECTS/swe-mux-docs', branch: 'main', dirty: 1 }),
  ])
  assert.equal(rows.length, 4)
  const external = rows.filter(row => row.external)
  assert.deepEqual(external.map(row => row.label), ['main'])
  assert.deepEqual(external[0].worktrees, [])
  // External rows sort after every branch the repository itself has checked out.
  assert.equal(rows[rows.length - 1].external, true)
  // A session with no branch at all (a non-repository cwd) contributes no row.
  assert.equal(branchRows([], [session({ branch: '', cwd: '/tmp' })]).length, 0)
})

test('a relative worktree path is refused, because the daemon would resolve it elsewhere', () => {
  assert.ok(isAbsolutePath('D:\\PROJECTS\\.worktrees\\repo\\slug'))
  assert.ok(isAbsolutePath('/home/me/worktrees/slug'))
  assert.ok(isAbsolutePath('  C:/tmp/slug  '))
  assert.ok(!isAbsolutePath('../.worktrees/repo/slug'))
  assert.ok(!isAbsolutePath('slug'))
  assert.ok(!isAbsolutePath(''))
})

test('divergence reads as arrows, and says nothing when level with upstream', () => {
  assert.equal(divergenceLabel({ ahead: 2, behind: 1 }), '↑2 ↓1')
  assert.equal(divergenceLabel({ ahead: 0, behind: 3 }), '↓3')
  assert.equal(divergenceLabel({ ahead: 0, behind: 0 }), '')
  assert.equal(divergenceLabel(null), '')
})

test('commit graph parsing keeps Git connector rows and typed commits', () => {
  const graph = parseGitGraph({
    lines: [
      {
        kind: 'commit',
        graph: '*   ',
        oid: 'aabbccdd00112233',
        parents: ['parent-a', 'parent-b'],
        refs: ['HEAD', 'integration'],
        author: 'Ada',
        committed_at: 123,
        subject: 'Merge the map',
      },
      { kind: 'connector', graph: '|\\  ' },
      { kind: 'bogus', graph: '*' },
    ],
    limit: 80,
    has_more: true,
  })
  assert.equal(graph.lines.length, 2)
  assert.equal(graph.lines[0].kind, 'commit')
  assert.equal(graph.lines[1].kind, 'connector')
  assert.equal(graph.limit, 80)
  assert.equal(graph.hasMore, true)
})

test('file status labels fit the narrow change list', () => {
  assert.equal(changeStatusLabel('??'), '?')
  assert.equal(changeStatusLabel('.M'), 'M')
  assert.equal(changeStatusLabel(' M'), 'M')
  assert.equal(changeStatusLabel('R100'), 'R')
  assert.equal(changeStatusLabel('C75'), 'C')
})
