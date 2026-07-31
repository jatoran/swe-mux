import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
import {
  branchRows,
  changeStatusLabel,
  divergenceLabel,
  isAbsolutePath,
  parseGitGraph,
  parseWorktrees,
  pathTail,
  repoSessions,
  shortSha,
  worktreeForPath,
  type GitChangeSummary,
  type GitGraph,
  type Worktree,
} from './gitWorktrees'
import type { Project, Session } from './types'

// One repository, two readings:
//
//  * Map is the operational projection: one row per worktree, with uncommitted files,
//    commits/files not yet in the agent trunk, and the live sessions using the directory.
//  * Log is the repository's real commit DAG. Git computes the ASCII lanes; the browser
//    styles them and attaches the structured commit metadata returned beside each prefix.
//
// The tab remains deliberately read-mostly. Its only mutations are the worktree add/remove
// operations the Git feature already owns; it never stages, commits, merges, or checks out.

type Props = {
  project?: Project
  /** Every session; the tab keeps this Project's live ones. */
  sessions: Session[]
}

type RemoveState = { path: string; force: boolean; error: string }
type GitView = 'map' | 'log'

const AGENT_TRUNK = 'integration'
const GRAPH_STEP = 80
const GRAPH_MAX = 200

function describeGitError(cause: unknown, action: string): string {
  const error = cause as ApiError
  const message = cause instanceof Error ? cause.message : String(cause)
  if (error?.detail?.code !== 'git_timeout' && !error?.timeout) return message
  return `Git did not answer in time. ${action} may still have completed — refresh to see.`
}

function committedLabel(timestamp: number): string {
  if (!timestamp) return ''
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
    .format(new Date(timestamp * 1000))
}

function GraphGlyph({ value }: { value: string }) {
  return <span class="git-graph-glyph" aria-hidden="true">
    {[...value].map((character, index) =>
      <i class={`lane-${Math.floor(index / 2) % 5}`} key={`${index}:${character}`}>{character}</i>)}
  </span>
}

function ChangeList({
  title,
  summary,
  empty,
  unknown,
}: {
  title: string
  summary: GitChangeSummary | null
  empty: string
  unknown: string
}) {
  return <section class="git-change-group">
    <h4>{title}{summary && <span>{summary.total}</span>}</h4>
    {!summary
      ? <p class="git-change-empty">{unknown}</p>
      : summary.total === 0
        ? <p class="git-change-empty">{empty}</p>
        : <div class="git-change-files">
          {summary.files.map((file, index) => <div class="git-change-file" key={`${file.status}:${file.path}:${index}`}>
            <b class={`status-${changeStatusLabel(file.status).toLowerCase()}`}>{changeStatusLabel(file.status)}</b>
            <span title={file.oldPath ? `${file.oldPath} → ${file.path}` : file.path}>
              {file.oldPath && <del>{file.oldPath}</del>}
              {file.oldPath && <i> → </i>}
              {file.path}
            </span>
          </div>)}
          {summary.truncated && <p class="git-change-empty">Showing the first {summary.files.length} files.</p>}
        </div>}
  </section>
}

export function GitTab({ project, sessions }: Props) {
  const [view, setView] = useState<GitView>('map')
  const [worktrees, setWorktrees] = useState<Worktree[] | null>(null)
  const [graph, setGraph] = useState<GitGraph | null>(null)
  const [graphLimit, setGraphLimit] = useState(GRAPH_STEP)
  const [error, setError] = useState('')
  const [graphError, setGraphError] = useState('')
  const [busy, setBusy] = useState('')
  const [graphBusy, setGraphBusy] = useState(false)
  const [confirm, setConfirm] = useState<RemoveState | null>(null)
  const [expandedPath, setExpandedPath] = useState('')
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ path: '', branch: '', start: '' })
  const [formError, setFormError] = useState('')
  const [note, setNote] = useState('')
  const generation = useRef(0)
  const graphGeneration = useRef(0)
  const root = project?.root || ''

  const refreshMap = useCallback(async () => {
    if (!root) { setWorktrees(null); return }
    const mine = ++generation.current
    try {
      const raw = await api<unknown>(
        'GET',
        `/api/git/worktrees?cwd=${encodeURIComponent(root)}&trunk=${AGENT_TRUNK}`,
        undefined,
        { timeoutMs: 20000 },
      )
      if (mine !== generation.current) return
      setWorktrees(parseWorktrees(raw))
      setError('')
    } catch (cause) {
      if (mine !== generation.current) return
      setWorktrees([])
      setError(describeGitError(cause, 'Reading the repository map'))
    }
  }, [root])

  const refreshGraph = useCallback(async (limit: number) => {
    if (!root) { setGraph(null); return }
    const mine = ++graphGeneration.current
    setGraphBusy(true)
    try {
      const raw = await api<unknown>(
        'GET',
        `/api/git/graph?cwd=${encodeURIComponent(root)}&limit=${limit}`,
        undefined,
        { timeoutMs: 20000 },
      )
      if (mine !== graphGeneration.current) return
      setGraph(parseGitGraph(raw))
      setGraphError('')
    } catch (cause) {
      if (mine !== graphGeneration.current) return
      setGraph({ lines: [], limit, hasMore: false })
      setGraphError(describeGitError(cause, 'Reading the commit graph'))
    } finally {
      if (mine === graphGeneration.current) setGraphBusy(false)
    }
  }, [root])

  useEffect(() => {
    setWorktrees(null)
    setGraph(null)
    setGraphLimit(GRAPH_STEP)
    setError('')
    setGraphError('')
    setConfirm(null)
    setExpandedPath('')
    setNote('')
    setAdding(false)
    void refreshMap()
    const changed = () => void refreshMap()
    window.addEventListener('mux:git-changed', changed)
    window.addEventListener('mux:events-connected', changed)
    return () => {
      window.removeEventListener('mux:git-changed', changed)
      window.removeEventListener('mux:events-connected', changed)
    }
  }, [refreshMap])

  useEffect(() => {
    if (view !== 'log' || !root) return
    void refreshGraph(graphLimit)
    const changed = () => void refreshGraph(graphLimit)
    window.addEventListener('mux:git-changed', changed)
    window.addEventListener('mux:events-connected', changed)
    return () => {
      window.removeEventListener('mux:git-changed', changed)
      window.removeEventListener('mux:events-connected', changed)
    }
  }, [view, root, graphLimit, refreshGraph])

  const live = useMemo(() => repoSessions(sessions, project?.id || ''), [sessions, project?.id])
  const rows = useMemo(() => branchRows(worktrees || [], live), [worktrees, live])
  const sessionsByWorktree = useMemo(() => {
    const counts = new Map<string, number>()
    for (const session of live) {
      const owner = worktreeForPath(worktrees || [], session.cwd)
      if (owner) counts.set(owner.path, (counts.get(owner.path) || 0) + 1)
    }
    return counts
  }, [worktrees, live])
  const rowByWorktree = useMemo(() => {
    const result = new Map<string, ReturnType<typeof branchRows>[number]>()
    for (const row of rows) {
      for (const item of row.worktrees) result.set(item.path, row)
    }
    return result
  }, [rows])
  const externalRows = rows.filter(row => row.external)

  if (!project) {
    return <>
      <p class="drawer-status">no Project selected</p>
      <p class="drawer-empty">Git state is a property of a Project's repository. Select a Project to see its worktrees and commit graph.</p>
    </>
  }

  const remove = async (item: Worktree, force: boolean) => {
    setBusy(item.path)
    setNote('')
    try {
      await api('DELETE', '/api/git/worktrees', { cwd: root, path: item.path, force })
      setConfirm(null)
      setExpandedPath('')
      setNote(`Removed ${pathTail(item.path)}.`)
      await refreshMap()
    } catch (cause) {
      const nextError = describeGitError(cause, 'The removal')
      setConfirm({ path: item.path, force: nextError.includes('--force'), error: nextError })
    } finally {
      setBusy('')
    }
  }

  const create = async (event: Event) => {
    event.preventDefault()
    const path = form.path.trim()
    if (!path) { setFormError('Give the directory to create the worktree in.'); return }
    if (!isAbsolutePath(path)) {
      setFormError('Use an absolute path. A relative one is resolved against the daemon\u2019s working directory, not this repository.')
      return
    }
    setBusy('create')
    setFormError('')
    setNote('')
    try {
      await api('POST', '/api/git/worktrees', {
        cwd: root,
        path,
        branch: form.branch.trim() || undefined,
        start_point: form.start.trim() || undefined,
      }, { timeoutMs: 60000 })
      setForm({ path: '', branch: '', start: '' })
      setAdding(false)
      setNote(`Created ${pathTail(path)}.`)
      await refreshMap()
    } catch (cause) {
      setFormError(describeGitError(cause, 'The worktree'))
    } finally {
      setBusy('')
    }
  }

  const removeBlocked = (item: Worktree): string => {
    if (item.main) return 'Git refuses to remove the main working tree.'
    if (item.locked !== null) return `Locked${item.locked ? `: ${item.locked}` : ''}. Unlock it in a terminal first.`
    const count = sessionsByWorktree.get(item.path) || 0
    if (count) return `${count} live session${count === 1 ? ' is' : 's are'} working in this directory.`
    return ''
  }

  const refreshVisible = () => {
    if (view === 'map') void refreshMap()
    else void refreshGraph(graphLimit)
  }

  return <>
    <p class="drawer-status" title={root}>{project.name} · {root}</p>
    <div class="git-toolbar">
      <div class="git-view-switch" role="tablist" aria-label="Git view">
        <button role="tab" aria-selected={view === 'map'} onClick={() => setView('map')}>Map</button>
        <button role="tab" aria-selected={view === 'log'} onClick={() => setView('log')}>Log</button>
      </div>
      <span class="git-trunk" title="Agent branches are measured against this branch">trunk:{AGENT_TRUNK}</span>
      <button class="git-toolbar-action" onClick={() => { setAdding(value => !value); setFormError('') }} aria-expanded={adding}>
        {adding ? 'Cancel' : '+ Worktree'}
      </button>
      <button class="git-toolbar-action" onClick={refreshVisible} disabled={view === 'log' && graphBusy}>↻</button>
    </div>

    {adding && <form class="git-add-form" onSubmit={create}>
      <label>
        <span>Directory (absolute)</span>
        <input value={form.path} spellcheck={false} placeholder="D:\\PROJECTS\\.worktrees\\repo\\slug" onInput={event => setForm({ ...form, path: event.currentTarget.value })} />
      </label>
      <div>
        <label>
          <span>New branch</span>
          <input value={form.branch} spellcheck={false} placeholder="agent/slug" onInput={event => setForm({ ...form, branch: event.currentTarget.value })} />
        </label>
        <label>
          <span>Start point</span>
          <input value={form.start} spellcheck={false} placeholder={AGENT_TRUNK} onInput={event => setForm({ ...form, start: event.currentTarget.value })} />
        </label>
      </div>
      <button type="submit" disabled={busy === 'create'}>{busy === 'create' ? 'Creating…' : 'Create worktree'}</button>
      {formError && <p class="git-row-error" role="alert">{formError}</p>}
    </form>}

    <div class={`git-tab-body view-${view}`}>
      {view === 'map'
        ? <>
          {error && <p class="git-state error" role="alert">{error}</p>}
          {!worktrees && !error && <p class="git-state">Reading repository map…</p>}
          {!!worktrees?.length && <section class="git-map" aria-label="Repository worktrees">
            {worktrees.map((item, index) => {
              const branchRow = rowByWorktree.get(item.path)
              const count = sessionsByWorktree.get(item.path) || 0
              const blocked = removeBlocked(item)
              const armed = confirm?.path === item.path ? confirm : null
              const expanded = expandedPath === item.path
              const localTotal = item.workingTree?.total ?? branchRow?.state?.dirty ?? null
              const branchTotal = item.branchDelta?.total ?? null
              const landed = item.unlanded === 0 && branchTotal === 0
              const rail = index === 0 ? '●' : index === worktrees.length - 1 ? '└─●' : '├─●'
              return <article class={`git-map-row ${expanded ? 'expanded' : ''}`} key={item.path}>
                <button
                  class="git-map-row-toggle"
                  aria-expanded={expanded}
                  onClick={() => {
                    setExpandedPath(expanded ? '' : item.path)
                    setConfirm(null)
                  }}
                >
                  <span class={`git-map-rail ${item.main ? 'main' : ''}`}>{rail}</span>
                  <span class="git-map-identity">
                    <strong class={item.detached ? 'detached' : undefined}>
                      {item.branch || (item.bare ? '(bare)' : `detached @ ${shortSha(item.head) || 'unknown'}`)}
                    </strong>
                    <small>{pathTail(item.path)}{item.main ? ' · main tree' : ''}</small>
                  </span>
                  <span class="git-map-metrics">
                    {localTotal === 0 && <em class="clean">clean</em>}
                    {localTotal !== null && localTotal > 0 && <em class="local">{localTotal} local</em>}
                    {!!item.unlanded && <em class="unlanded">{item.unlanded} commit{item.unlanded === 1 ? '' : 's'}</em>}
                    {branchTotal !== null && branchTotal > 0 && <em class="branch-files">{branchTotal} branch file{branchTotal === 1 ? '' : 's'}</em>}
                    {landed && <em class="landed">landed</em>}
                    {!!divergenceLabel(branchRow?.state || null) && <em class="diverged">{divergenceLabel(branchRow?.state || null)}</em>}
                    {!!count && <em class="live">{count} live</em>}
                    {item.locked !== null && <em class="warn">locked</em>}
                    {item.prunable !== null && <em class="warn">prunable</em>}
                  </span>
                  <span class="git-map-chevron">{expanded ? '−' : '+'}</span>
                </button>
                {expanded && <div class="git-map-detail">
                  <p class="git-map-path" title={item.path}>{item.path}</p>
                  <ChangeList
                    title="LOCAL — NOT COMMITTED"
                    summary={item.workingTree}
                    empty="No uncommitted files."
                    unknown="Working-tree files could not be measured."
                  />
                  <ChangeList
                    title={`BRANCH — NOT IN ${AGENT_TRUNK.toUpperCase()}`}
                    summary={item.branchDelta}
                    empty={`No files differ from ${AGENT_TRUNK}.`}
                    unknown={`Branch files could not be measured against ${AGENT_TRUNK}.`}
                  />
                  <div class="git-map-actions">
                    {blocked
                      ? <span class="git-quiet" title={blocked}>{blocked}</span>
                      : armed
                        ? <>
                          <button
                            class="danger"
                            disabled={busy === item.path}
                            title={armed.force ? 'Deletes the directory and its uncommitted work.' : undefined}
                            onClick={() => void remove(item, armed.force)}
                          >
                            {busy === item.path ? 'removing…' : armed.force ? 'Force remove ✓' : 'Confirm remove ✓'}
                          </button>
                          <button onClick={() => setConfirm(null)}>Cancel</button>
                          {!!item.unlanded && <span class="git-quiet">
                            Removing the directory keeps {item.branch}'s {item.unlanded} unlanded commit{item.unlanded === 1 ? '' : 's'}.
                          </span>}
                        </>
                        : <button onClick={() => setConfirm({ path: item.path, force: false, error: '' })}>Remove worktree…</button>}
                  </div>
                  {armed?.error && <p class="git-row-error" role="alert">{armed.error}</p>}
                </div>}
              </article>
            })}
          </section>}
          {worktrees && !error && !worktrees.length && <p class="git-state">
            This Project's root is a repository with no listed working tree.
          </p>}
          {!!externalRows.length && <section class="git-external">
            <h3>OTHER REPOSITORIES <span>{externalRows.length}</span></h3>
            {externalRows.map(row => <div key={row.key}>
              <strong>{row.label}</strong>
              <span>{row.state?.dirty ? `${row.state.dirty} local` : 'clean'} · {row.sessions.map(item => item.name).join(', ')}</span>
            </div>)}
          </section>}
        </>
        : <>
          {graphError && <p class="git-state error" role="alert">{graphError}</p>}
          {!graph && !graphError && <p class="git-state">Reading commit graph…</p>}
          {!!graph?.lines.length && <section class="git-graph" aria-label="Commit graph">
            {graph.lines.map((line, index) => line.kind === 'connector'
              ? <div class="git-graph-connector" key={`connector:${index}`}><GraphGlyph value={line.graph} /></div>
              : <article class="git-graph-row" key={line.oid} title={line.oid}>
                <GraphGlyph value={line.graph} />
                <div class="git-commit">
                  <div class="git-commit-title">
                    <strong>{shortSha(line.oid)}</strong>
                    <span>{line.subject}</span>
                  </div>
                  {!!line.refs.length && <div class="git-commit-refs">
                    {line.refs.map(ref => <em class={ref === 'HEAD' ? 'head' : ref.startsWith('tag: ') ? 'tag' : undefined} key={ref}>{ref}</em>)}
                  </div>}
                  <small>{line.author}{line.committedAt ? ` · ${committedLabel(line.committedAt)}` : ''}</small>
                </div>
              </article>)}
            {graph.hasMore && graphLimit < GRAPH_MAX && <button
              class="git-load-more"
              disabled={graphBusy}
              onClick={() => setGraphLimit(value => Math.min(GRAPH_MAX, value + GRAPH_STEP))}
            >{graphBusy ? 'Loading…' : `Load ${Math.min(GRAPH_STEP, GRAPH_MAX - graphLimit)} more commits`}</button>}
            {graph.hasMore && graphLimit >= GRAPH_MAX && <p class="git-graph-limit">Showing the newest {GRAPH_MAX} commits.</p>}
          </section>}
          {graph && !graphError && !graph.lines.length && <p class="git-state">No commits in this repository.</p>}
        </>}
      {note && <p class="git-state" aria-live="polite">{note}</p>}
    </div>
  </>
}
