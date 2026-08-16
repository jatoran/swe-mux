// Pure helpers behind the drawer's Git tab.
//
// Two independent facts are joined here, because neither is enough on its own:
//
//  * `GET /api/git/worktrees` starts with `git worktree list --porcelain`, then annotates
//    every tree with an explicit local-file summary and, where measurable, the files its
//    checked-out branch changes relative to the shared trunk.
//  * `git_monitor.py` polls the cwd of every *attached* session and mirrors branch, dirty
//    count, and upstream divergence into that session's snapshot. Its remaining unique
//    contribution here is live upstream divergence and session attribution.
//
// Joining them by path is what makes the tab worth opening: the porcelain list supplies the
// inventory/file summaries, the session snapshots supply live attribution and divergence.
// Nothing here runs Git — this module only rearranges what the daemon already reported.
//
// Explicit `.ts` extension: this module is reachable from the node test runner, whose
// type-stripping ESM loader does not resolve extensionless specifiers.
import type { Session } from './types.ts'

/** One record of `git worktree list --porcelain`, as `server.py`'s `_parse_worktrees` emits it:
 *  `key value` lines become strings, valueless flag lines become `true`. */
export type WorktreePorcelain = Record<string, unknown>

export type GitChangeFile = {
  path: string
  status: string
  oldPath: string | null
}

export type GitChangeSummary = {
  total: number
  files: GitChangeFile[]
  truncated: boolean
}

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
  /** Commits on this branch that the shared trunk does not have, so work that would be
   *  lost if the tree were removed. `null` means the daemon could not measure it (no
   *  trunk, or the Git call failed) — deliberately distinct from 0, because rendering an
   *  unmeasured tree as "nothing to land" is the one wrong answer here. */
  unlanded: number | null
  /** Uncommitted files in this worktree, measured when the drawer requested the inventory. */
  workingTree: GitChangeSummary | null
  /** Files changed by this branch relative to the shared trunk's merge base. */
  branchDelta: GitChangeSummary | null
}

export type GitGraphCommit = {
  kind: 'commit'
  graph: string
  oid: string
  parents: string[]
  refs: string[]
  author: string
  committedAt: number
  subject: string
}

export type GitGraphConnector = { kind: 'connector'; graph: string }
export type GitGraphLine = GitGraphCommit | GitGraphConnector
export type GitGraph = { lines: GitGraphLine[]; limit: number; hasMore: boolean }

export type GitProvenance = {
  id: string
  sessionId: string
  sessionName: string
  agentRunId: string | null
  projectId: string
  worktreeRoot: string
  commitOid: string
  parentOids: string[]
  subject: string
  committedAt: number | null
  previousHead: string | null
  relationship: 'created' | 'rewrote' | 'observed'
  confidence: 'exact' | 'correlated' | 'ambiguous'
  ambiguous: boolean
  source: 'session_tool' | 'git_monitor'
  observedAt: number
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

/** A flag line carries either a reason or nothing at all; normalize both to a string.
 *  The porcelain record also carries numeric fields now (`unlanded`), which no flag ever
 *  is, so a number here means a malformed row rather than a lock reason. */
function reason(value: unknown): string | null {
  if (value === true) return ''
  return typeof value === 'string' ? value : null
}

function changeSummary(value: unknown): GitChangeSummary | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  const total = record.total
  if (typeof total !== 'number' || !Number.isFinite(total) || total < 0) return null
  const files: GitChangeFile[] = []
  if (Array.isArray(record.files)) {
    for (const raw of record.files) {
      if (!raw || typeof raw !== 'object') continue
      const item = raw as Record<string, unknown>
      if (typeof item.path !== 'string' || typeof item.status !== 'string') continue
      files.push({
        path: item.path,
        status: item.status,
        oldPath: typeof item.old_path === 'string' ? item.old_path : null,
      })
    }
  }
  return { total, files, truncated: record.truncated === true }
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
      unlanded: typeof entry.unlanded === 'number' && Number.isFinite(entry.unlanded)
        ? entry.unlanded
        : null,
      workingTree: changeSummary(entry.working_tree),
      branchDelta: changeSummary(entry.branch_delta),
    })
  }
  return items
}

export function parseGitGraph(raw: unknown): GitGraph {
  if (!raw || typeof raw !== 'object') return { lines: [], limit: 0, hasMore: false }
  const record = raw as Record<string, unknown>
  const lines: GitGraphLine[] = []
  if (Array.isArray(record.lines)) {
    for (const rawLine of record.lines) {
      if (!rawLine || typeof rawLine !== 'object') continue
      const line = rawLine as Record<string, unknown>
      if (line.kind === 'connector' && typeof line.graph === 'string') {
        lines.push({ kind: 'connector', graph: line.graph })
        continue
      }
      if (
        line.kind !== 'commit'
        || typeof line.graph !== 'string'
        || typeof line.oid !== 'string'
        || typeof line.author !== 'string'
        || typeof line.committed_at !== 'number'
        || typeof line.subject !== 'string'
      ) continue
      lines.push({
        kind: 'commit',
        graph: line.graph,
        oid: line.oid,
        parents: Array.isArray(line.parents)
          ? line.parents.filter((item): item is string => typeof item === 'string')
          : [],
        refs: Array.isArray(line.refs)
          ? line.refs.filter((item): item is string => typeof item === 'string')
          : [],
        author: line.author,
        committedAt: line.committed_at,
        subject: line.subject,
      })
    }
  }
  return {
    lines,
    limit: typeof record.limit === 'number' && Number.isFinite(record.limit) ? record.limit : 0,
    hasMore: record.has_more === true,
  }
}

export function parseGitProvenance(raw: unknown): GitProvenance[] {
  if (!raw || typeof raw !== 'object' || !Array.isArray((raw as { items?: unknown }).items)) return []
  const items: GitProvenance[] = []
  for (const value of (raw as { items: unknown[] }).items) {
    if (!value || typeof value !== 'object') continue
    const row = value as Record<string, unknown>
    if (
      typeof row.id !== 'string'
      || typeof row.session_id !== 'string'
      || typeof row.session_name !== 'string'
      || typeof row.project_id !== 'string'
      || typeof row.worktree_root !== 'string'
      || typeof row.commit_oid !== 'string'
      || !['created', 'rewrote', 'observed'].includes(String(row.relationship))
      || !['exact', 'correlated', 'ambiguous'].includes(String(row.confidence))
      || !['session_tool', 'git_monitor'].includes(String(row.source))
      || typeof row.observed_at !== 'number'
    ) continue
    items.push({
      id: row.id,
      sessionId: row.session_id,
      sessionName: row.session_name,
      agentRunId: typeof row.agent_run_id === 'string' ? row.agent_run_id : null,
      projectId: row.project_id,
      worktreeRoot: row.worktree_root,
      commitOid: row.commit_oid,
      parentOids: Array.isArray(row.parent_oids)
        ? row.parent_oids.filter((item): item is string => typeof item === 'string')
        : [],
      subject: typeof row.subject === 'string' ? row.subject : '',
      committedAt: typeof row.committed_at === 'number' ? row.committed_at : null,
      previousHead: typeof row.previous_head === 'string' ? row.previous_head : null,
      relationship: row.relationship as GitProvenance['relationship'],
      confidence: row.confidence as GitProvenance['confidence'],
      ambiguous: row.ambiguous === true,
      source: row.source as GitProvenance['source'],
      observedAt: row.observed_at,
    })
  }
  return items
}

/** Compact porcelain/name-status code for a narrow file list. */
export function changeStatusLabel(status: string): string {
  if (status === '??') return '?'
  const code = status.replace(/[\s.]/g, '')
  if (!code) return '·'
  if (code.startsWith('R')) return 'R'
  if (code.startsWith('C')) return 'C'
  return [...new Set(code)].join('')
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

export type GitComparisonSource = 'project_override' | 'origin_head' | 'single_remote_head' | 'local_fallback' | 'none'

export type ReviewFileChange = {
  path: string
  oldPath: string | null
  status: string
  additions: number | null
  deletions: number | null
  binary: boolean
  submodule: boolean
  currentExists: boolean | null
}

export type ReviewChangeSummary = {
  total: number
  additions: number
  deletions: number
  binaryFiles: number
  files: ReviewFileChange[]
  truncated: boolean
}

export type GitComparison = {
  ref: string | null
  display: string | null
  source: GitComparisonSource
  available: boolean
  reason: string | null
  candidates: string[]
}

export type GitOverviewWorktree = {
  path: string
  head: string | null
  branch: string | null
  detached: boolean
  bare: boolean
  locked: string | null
  prunable: string | null
  main: boolean
  comparisonCounts: { ahead: number; behind: number } | null
  unstaged: ReviewChangeSummary | null
  staged: ReviewChangeSummary | null
  conflicted: ReviewChangeSummary | null
  branchDelta: ReviewChangeSummary | null
}

export type GitWorktreeOverview = {
  repository: { root: string; commonDir: string }
  comparison: GitComparison
  worktrees: GitOverviewWorktree[]
}

export type GitGraphDecorationKind =
  | 'head'
  | 'comparison'
  | 'worktree-ref'
  | 'tag'
  | 'other-ref'
  | 'main-tree'
  | 'worktree-tip'

export type GitGraphDecoration = {
  kind: GitGraphDecorationKind
  label: string
  title: string
}

/** The graph lane containing this commit's node, folded onto the five-color TUI palette. */
export function graphNodeLane(graph: string): number {
  const node=graph.indexOf('*')
  return node<0?0:Math.floor(node/2)%5
}

/**
 * Turn Git's ref strings plus the registered checkout inventory into semantic badges.
 *
 * A linear ahead branch has no fork in the commit DAG. Its ref and worktree badges are
 * therefore the only truthful way to distinguish its tip from the comparison ref until
 * another branch advances and Git has an actual fork to draw.
 */
export function graphDecorations(
  line: GitGraphCommit,
  overview: GitWorktreeOverview | null,
): GitGraphDecoration[] {
  const tips=(overview?.worktrees||[]).filter(tree=>tree.head?.toLowerCase()===line.oid.toLowerCase())
  const linked=tips.filter(tree=>!tree.main)
  const comparisonNames=new Set(
    [overview?.comparison.ref,overview?.comparison.display].filter((value):value is string=>Boolean(value)),
  )
  const worktreeBranches=new Map<string,GitOverviewWorktree[]>()
  for(const tree of tips){
    if(!tree.branch)continue
    const trees=worktreeBranches.get(tree.branch)||[]
    trees.push(tree)
    worktreeBranches.set(tree.branch,trees)
  }
  const rank:Record<GitGraphDecorationKind,number>={
    head:0,comparison:1,'worktree-ref':2,tag:3,'other-ref':4,'main-tree':5,'worktree-tip':6,
  }
  const refs=line.refs.map((ref):GitGraphDecoration=>{
    if(ref==='HEAD')return {kind:'head',label:'HEAD',title:'Project root HEAD'}
    if(comparisonNames.has(ref))return {kind:'comparison',label:ref,title:`Comparison ref ${ref}`}
    const checkedOut=worktreeBranches.get(ref)
    if(checkedOut?.length){
      const names=checkedOut.map(tree=>pathTail(tree.path)).join(', ')
      return {kind:'worktree-ref',label:ref,title:`Checked out in ${names}`}
    }
    if(ref.startsWith('tag: '))return {kind:'tag',label:ref.slice(5),title:`Tag ${ref.slice(5)}`}
    return {kind:'other-ref',label:ref,title:`Git ref ${ref}`}
  }).sort((left,right)=>rank[left.kind]-rank[right.kind])
  if(tips.some(tree=>tree.main)){
    refs.push({kind:'main-tree',label:'MAIN TREE',title:'The Project root checkout points at this commit'})
  }
  if(linked.length===1){
    const tree=linked[0]
    refs.push({
      kind:'worktree-tip',label:`WT ${pathTail(tree.path)}`,
      title:`Linked worktree ${tree.path}${tree.branch?` on ${tree.branch}`:' in detached HEAD'}`,
    })
  }else if(linked.length>1){
    refs.push({
      kind:'worktree-tip',label:`${linked.length} WORKTREES`,
      title:linked.map(tree=>`${pathTail(tree.path)}${tree.branch?` on ${tree.branch}`:' (detached)'}`).join('\n'),
    })
  }
  return refs
}

export function localMeasurement(tree: GitOverviewWorktree): { measured: boolean; total: number } {
  const summaries=[tree.conflicted,tree.unstaged,tree.staged]
  return {
    measured:summaries.every(summary=>summary!==null),
    total:summaries.reduce((sum,summary)=>sum+(summary?.total||0),0),
  }
}

export type GitCommitChanges = {
  commit: string
  parent: string | null
  parents: string[]
  parentLabel: string
  /** The whole commit message, subject and body, exactly as it was written. */
  message: string
  summary: ReviewChangeSummary
}

export type GitPatchSnapshot = {
  scope: 'unstaged' | 'staged' | 'conflicted' | 'branch' | 'commit'
  path: string
  oldPath: string | null
  worktree: string | null
  commit: string | null
  parent: string | null
  comparisonRef: string | null
  headOid: string | null
  patchSha256: string
  patch: string | null
  binary: boolean
  tooLarge: boolean
  unavailableReason: string | null
  additions: number | null
  deletions: number | null
}

const record=(value:unknown):Record<string,unknown>|null=>value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:null
const textOrNull=(value:unknown):string|null=>typeof value==='string'?value:null
const finiteNonnegative=(value:unknown):number|null=>typeof value==='number'&&Number.isFinite(value)&&value>=0?value:null

export function parseReviewSummary(value:unknown):ReviewChangeSummary|null {
  const raw=record(value)
  if(!raw)return null
  const total=finiteNonnegative(raw.total),additions=finiteNonnegative(raw.additions),deletions=finiteNonnegative(raw.deletions),binaryFiles=finiteNonnegative(raw.binary_files)
  if(total===null||additions===null||deletions===null||binaryFiles===null||!Array.isArray(raw.files))return null
  const files:ReviewFileChange[]=[]
  for(const value of raw.files){
    const item=record(value)
    if(!item||typeof item.path!=='string'||!item.path||typeof item.status!=='string')continue
    const binary=item.binary===true
    const fileAdditions=finiteNonnegative(item.additions),fileDeletions=finiteNonnegative(item.deletions)
    files.push({
      path:item.path,
      oldPath:textOrNull(item.old_path),
      status:item.status,
      additions:binary?null:fileAdditions,
      deletions:binary?null:fileDeletions,
      binary,
      submodule:item.submodule===true,
      currentExists:typeof item.current_exists==='boolean'?item.current_exists:null,
    })
  }
  return {total,additions,deletions,binaryFiles,files,truncated:raw.truncated===true}
}

export function parseGitOverview(value:unknown):GitWorktreeOverview|null {
  const raw=record(value),repository=record(raw?.repository),comparison=record(raw?.comparison)
  if(!raw||!repository||typeof repository.root!=='string'||typeof repository.common_dir!=='string'||!comparison||!Array.isArray(raw.worktrees))return null
  const source=comparison.source
  if(!['project_override','origin_head','single_remote_head','local_fallback','none'].includes(String(source)))return null
  const parsedComparison:GitComparison={
    ref:textOrNull(comparison.ref),display:textOrNull(comparison.display),source:source as GitComparisonSource,
    available:comparison.available===true,reason:textOrNull(comparison.reason),
    candidates:Array.isArray(comparison.candidates)?comparison.candidates.filter((item):item is string=>typeof item==='string'):[],
  }
  const worktrees:GitOverviewWorktree[]=[]
  for(const value of raw.worktrees){
    const item=record(value)
    if(!item||typeof item.worktree!=='string'||!item.worktree)continue
    const counts=record(item.comparison_counts)
    const ahead=finiteNonnegative(counts?.ahead),behind=finiteNonnegative(counts?.behind)
    worktrees.push({
      path:item.worktree,head:textOrNull(item.HEAD),branch:textOrNull(item.branch)?.replace(/^refs\/heads\//,'')||null,
      detached:item.detached===true,bare:item.bare===true,locked:reason(item.locked),prunable:reason(item.prunable),main:item.main===true,
      comparisonCounts:ahead===null||behind===null?null:{ahead,behind},
      unstaged:parseReviewSummary(item.unstaged),staged:parseReviewSummary(item.staged),conflicted:parseReviewSummary(item.conflicted),branchDelta:parseReviewSummary(item.branch_delta),
    })
  }
  return {repository:{root:repository.root,commonDir:repository.common_dir},comparison:parsedComparison,worktrees}
}

export function parseCommitChanges(value:unknown):GitCommitChanges|null {
  const raw=record(value),summary=parseReviewSummary(raw?.summary)
  if(!raw||typeof raw.commit!=='string'||!Array.isArray(raw.parents)||typeof raw.parent_label!=='string'||!summary)return null
  const parents=raw.parents.filter((item):item is string=>typeof item==='string')
  const parent=textOrNull(raw.parent)
  if(parent&&!parents.includes(parent))return null
  // The message is not required: an older daemon serving a newer UI omits it, and a commit
  // body is an annotation on the changes rather than the reason this response exists.
  return {commit:raw.commit,parent,parents,parentLabel:raw.parent_label,message:typeof raw.message==='string'?raw.message:'',summary}
}

export function parsePatchSnapshot(value:unknown):GitPatchSnapshot|null {
  const raw=record(value)
  if(!raw||!['unstaged','staged','conflicted','branch','commit'].includes(String(raw.scope))||typeof raw.path!=='string'||typeof raw.patch_sha256!=='string')return null
  return {
    scope:raw.scope as GitPatchSnapshot['scope'],path:raw.path,oldPath:textOrNull(raw.old_path),worktree:textOrNull(raw.worktree),
    commit:textOrNull(raw.commit),parent:textOrNull(raw.parent),comparisonRef:textOrNull(raw.comparison_ref),headOid:textOrNull(raw.head_oid),
    patchSha256:raw.patch_sha256,patch:textOrNull(raw.patch),binary:raw.binary===true,tooLarge:raw.too_large===true,
    unavailableReason:textOrNull(raw.unavailable_reason),additions:finiteNonnegative(raw.additions),deletions:finiteNonnegative(raw.deletions),
  }
}

export function comparisonSourceLabel(comparison:GitComparison):string {
  if(!comparison.available)return comparison.reason||'No comparison ref could be inferred.'
  const source={project_override:'Project override',origin_head:'origin default',single_remote_head:'remote default',local_fallback:'local fallback',none:'Auto'}[comparison.source]
  return `${source}: ${comparison.display||comparison.ref}`
}

export function fileStatLabel(file:Pick<ReviewFileChange,'binary'|'submodule'|'additions'|'deletions'>):string {
  if(file.submodule)return 'submodule'
  if(file.binary)return 'binary'
  if(file.additions===null||file.deletions===null)return 'unmeasured'
  return `+${file.additions} -${file.deletions}`
}
