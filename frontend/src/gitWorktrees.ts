// Pure helpers behind the drawer's Git tab.
//
// Two independent facts are joined here, because neither is enough on its own:
//
//  * `GET /api/git/worktrees` returns `git worktree list --porcelain`, which knows every
//    working tree of the repository and which branch (or detached commit) each holds. It
//    knows nothing about whether that tree is clean or how far it has drifted from its
//    upstream.
//  * `git_monitor.py` polls the cwd of every *attached* session and mirrors branch, dirty
//    count, and upstream divergence into that session's snapshot. It sees only the
//    directories someone is actually sitting in.
//
// Joining them by path is what makes the tab worth opening: the porcelain list supplies the
// inventory, the session snapshots supply the live state of the rows you are working in.
// Nothing here runs Git — this module only rearranges what the daemon already reported.
//
// Explicit `.ts` extension: this module is reachable from the node test runner, whose
// type-stripping ESM loader does not resolve extensionless specifiers.
import type { Session } from './types.ts'

/** One record of `git worktree list --porcelain`, as `server.py`'s `_parse_worktrees` emits it:
 *  `key value` lines become strings, valueless flag lines become `true`. */
export type WorktreePorcelain = Record<string, string | true>

export type Worktree = {
  path: string
  /** Full commit oid, or null on an unborn branch (a fresh `worktree add -b` before any commit). */
  head: string | null
  /** Short branch name, or null when the tree is detached or bare. */
  branch: string | null
  detached: boolean
  bare: boolean
  /** Lock reason; `''` when Git flagged the lock without giving one. Null when unlocked. */
  locked: string | null
  /** Prune reason (the gitdir is gone or unreadable); null when the tree is healthy. */
  prunable: string | null
  /** Git lists the main working tree first, and refuses to remove it. */
  main: boolean
}

/** A live session sitting somewhere in this repository, with whatever Git state it reported. */
export type SessionGit = {
  id: string
  name: string
  cwd: string
  branch: string
  dirty: number
  ahead: number
  behind: number
}

export type BranchRow = {
  /** Stable key for the row; a branch name, or the path of the detached tree holding it. */
  key: string
  label: string
  detached: boolean
  /** Worktrees of this repository checked out here. Empty for an `external` row. */
  worktrees: Worktree[]
  sessions: SessionGit[]
  /** Live working-tree state, from the first session in this row. Null when nobody is attached. */
  state: { dirty: number; ahead: number; behind: number } | null
  /** The branch came from a session's own cwd, which is not inside any listed worktree —
   *  a nested or sibling repository under the same Project root. */
  external: boolean
}

/** Compare paths the way both platforms need: separators unified, no trailing slash, case-folded.
 *  Git reports forward slashes even on Windows, while a session cwd carries backslashes. */
export function normalizePath(value: string): string {
  const unified = value.replace(/\\/g, '/').replace(/\/+$/, '')
  return unified.toLowerCase()
}

/** `refs/heads/agent/git-pane` → `agent/git-pane`. Anything else is returned unchanged. */
export function shortBranch(ref: string): string {
  return ref.startsWith('refs/heads/') ? ref.slice('refs/heads/'.length) : ref
}

export function shortSha(oid: string | null): string {
  return oid ? oid.slice(0, 8) : ''
}

/** A flag line carries either a reason or nothing at all; normalize both to a string. */
function reason(value: string | true | undefined): string | null {
  return value === undefined ? null : value === true ? '' : value
}

export function parseWorktrees(raw: unknown): Worktree[] {
  if (!Array.isArray(raw)) return []
  const items: Worktree[] = []
  for (const entry of raw as WorktreePorcelain[]) {
    const path = entry?.worktree
    if (typeof path !== 'string' || !path) continue
    const branch = typeof entry.branch === 'string' ? shortBranch(entry.branch) : null
    items.push({
      path,
      head: typeof entry.HEAD === 'string' ? entry.HEAD : null,
      branch,
      detached: entry.detached === true || (!branch && entry.bare !== true),
      bare: entry.bare === true,
      locked: reason(entry.locked),
      prunable: reason(entry.prunable),
      main: items.length === 0,
    })
  }
  return items
}

/** The directory `git_monitor.py` actually polls for a session — the same rule as
 *  `SessionRecord.git_cwd`, which is a backend property and so is not in the snapshot. */
export function sessionGitCwd(session: Session): string {
  if (session.runtime_cwd_live && session.runtime_cwd) return session.runtime_cwd
  return session.spawn_cwd || session.cwd
}

function isLive(session: Session): boolean {
  return session.state !== 'exited' && session.state !== 'crashed' && !session.pending
}

/** Live sessions of one Project, reduced to the Git facts this tab renders. */
export function repoSessions(sessions: Session[], projectId: string): SessionGit[] {
  return sessions
    .filter(session => session.project_id === projectId && isLive(session))
    .map(session => ({
      id: session.id,
      name: session.name || session.id,
      cwd: sessionGitCwd(session),
      branch: session.git?.branch || '',
      dirty: session.git?.dirty || 0,
      ahead: session.git?.ahead || 0,
      behind: session.git?.behind || 0,
    }))
}

/** The worktree a path sits in: the longest listed prefix, on a path-segment boundary so
 *  `.../swe-mux-old` never matches the worktree `.../swe-mux`. Null when it is in none. */
export function worktreeForPath(worktrees: Worktree[], path: string): Worktree | null {
  const needle = normalizePath(path)
  if (!needle) return null
  let best: Worktree | null = null
  for (const item of worktrees) {
    const root = normalizePath(item.path)
    if (needle !== root && !needle.startsWith(`${root}/`)) continue
    if (!best || root.length > normalizePath(best.path).length) best = item
  }
  return best
}

/**
 * Branch rows: every worktree of the repository, plus any live session whose cwd is not in
 * one of them.
 *
 * Sessions fold into a row by **path**, never by branch name. A detached session reports its
 * short commit SHA in the branch field (that is what `read_git_reading` writes), which would
 * never match the worktree's own detached marker; the path always does.
 */
export function branchRows(worktrees: Worktree[], sessions: SessionGit[]): BranchRow[] {
  const rows = new Map<string, BranchRow>()
  for (const item of worktrees) {
    const key = item.branch || item.path
    const existing = rows.get(key)
    if (existing) { existing.worktrees.push(item); continue }
    rows.set(key, {
      key,
      label: item.branch || (item.bare ? '(bare)' : `detached @ ${shortSha(item.head) || 'unknown'}`),
      detached: item.detached && !item.branch,
      worktrees: [item],
      sessions: [],
      state: null,
      external: false,
    })
  }
  for (const session of sessions) {
    const owner = worktreeForPath(worktrees, session.cwd)
    const key = owner ? owner.branch || owner.path : session.branch
    if (!key) continue
    const row = rows.get(key) || {
      key, label: session.branch, detached: false, worktrees: [], sessions: [],
      state: null, external: true,
    }
    row.sessions.push(session)
    if (!row.state) row.state = { dirty: session.dirty, ahead: session.ahead, behind: session.behind }
    rows.set(key, row)
  }
  // Checked-out branches first (the repository's own inventory), then whatever a session
  // found somewhere else under the Project root.
  return [...rows.values()].sort((first, second) =>
    Number(first.external) - Number(second.external)
    || (second.worktrees[0]?.main ? 1 : 0) - (first.worktrees[0]?.main ? 1 : 0)
    || first.label.localeCompare(second.label))
}

/**
 * Whether a path is absolute.
 *
 * The create endpoint resolves a relative path against the **daemon's** working directory,
 * not the repository, so a relative entry here would silently create the worktree somewhere
 * nobody expects. The form refuses one rather than guessing what it meant.
 */
export function isAbsolutePath(value: string): boolean {
  return /^(?:[a-zA-Z]:[\\/]|[\\/])/.test(value.trim())
}

/** Last path segment, for a row that shows the directory name with the full path in its title. */
export function pathTail(value: string): string {
  const parts = value.replace(/[\\/]+$/, '').split(/[\\/]/)
  return parts[parts.length - 1] || value
}

/** `↑2 ↓1` style divergence summary; empty when the branch is level with its upstream. */
export function divergenceLabel(state: { ahead: number; behind: number } | null): string {
  if (!state) return ''
  const parts: string[] = []
  if (state.ahead) parts.push(`↑${state.ahead}`)
  if (state.behind) parts.push(`↓${state.behind}`)
  return parts.join(' ')
}
