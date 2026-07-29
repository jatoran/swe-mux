import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
import {
  branchRows, divergenceLabel, isAbsolutePath, parseWorktrees, pathTail, repoSessions,
  shortSha, worktreeForPath, type Worktree,
} from './gitWorktrees'
import type { Project, Session } from './types'

// The drawer's Git tab: what this Project's repository looks like right now.
//
// It reads two things the daemon already has — the porcelain worktree list and the branch/
// dirty/upstream state `git_monitor.py` polls for attached sessions — and it performs exactly
// one class of mutation, the one the API exposes: `git worktree add` and `git worktree remove`.
// Nothing here commits, switches, stages, fetches, or prunes. That boundary is the Git
// feature's, not this component's (`.docs/design/features/git.md`); a tab that quietly grew a
// commit button would move it.
//
// Live state arrives without polling. Branch/dirty/divergence ride the session snapshots this
// component already receives as props, so `git_changed` updates the rows by re-render. The
// worktree list is refetched on `mux:git-changed` (another client added or removed one) and on
// reconnect — and by the explicit Refresh, because a worktree created in a terminal by
// `git worktree add` emits no event at all.

type Props = {
  project?: Project
  /** Every session; the tab keeps this Project's live ones. */
  sessions: Session[]
}

type RemoveState = { path: string; force: boolean; error: string }

/**
 * Git errors reach the UI as the daemon's own message, with one case worth rewriting.
 *
 * Every Git call the daemon makes is bounded at four seconds and reports code 124 as a typed
 * `git_timeout`. That bound is right for a status poll and short for `worktree add`, which
 * copies a whole tree: the request can time out while Git goes on to finish the job. Saying
 * "it failed" would be wrong, so a timeout says what is actually true and points at Refresh.
 */
function describeGitError(cause: unknown, action: string): string {
  const error = cause as ApiError
  const message = cause instanceof Error ? cause.message : String(cause)
  if (error?.detail?.code !== 'git_timeout' && !error?.timeout) return message
  return `Git did not answer in time. ${action} may still have completed — refresh to see.`
}

export function GitTab({ project, sessions }: Props) {
  const [worktrees, setWorktrees] = useState<Worktree[] | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [confirm, setConfirm] = useState<RemoveState | null>(null)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ path: '', branch: '', start: '' })
  const [formError, setFormError] = useState('')
  const [note, setNote] = useState('')
  // A generation guard rather than a cancel flag: an event-driven refetch can land after the
  // active Project changed, and the newest request has to win.
  const generation = useRef(0)
  const root = project?.root || ''

  const refresh = useCallback(async () => {
    if (!root) { setWorktrees(null); return }
    const mine = ++generation.current
    try {
      const raw = await api<unknown>('GET', `/api/git/worktrees?cwd=${encodeURIComponent(root)}`, undefined, { timeoutMs: 20000 })
      if (mine !== generation.current) return
      setWorktrees(parseWorktrees(raw))
      setError('')
    } catch (cause) {
      if (mine !== generation.current) return
      setWorktrees([])
      setError(describeGitError(cause, 'Reading the worktree list'))
    }
  }, [root])

  useEffect(() => {
    setWorktrees(null); setError(''); setConfirm(null); setNote('')
    void refresh()
    const changed = () => void refresh()
    window.addEventListener('mux:git-changed', changed)
    window.addEventListener('mux:events-connected', changed)
    return () => {
      window.removeEventListener('mux:git-changed', changed)
      window.removeEventListener('mux:events-connected', changed)
    }
  }, [refresh])

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

  if (!project) {
    return <>
      <p class="drawer-status">no Project selected</p>
      <p class="drawer-empty">Git state is a property of a Project's repository. Select a Project to see its branches and worktrees.</p>
    </>
  }

  const remove = async (item: Worktree, force: boolean) => {
    setBusy(item.path); setNote('')
    try {
      await api('DELETE', '/api/git/worktrees', { cwd: root, path: item.path, force })
      setConfirm(null)
      setNote(`Removed ${pathTail(item.path)}.`)
      await refresh()
    } catch (cause) {
      // Git refuses a tree holding modified or untracked files. That refusal is the useful
      // answer, so it stays on the row — and the override is offered only when Git itself
      // named it. `--force` deletes uncommitted work, so a timeout, a 409, or any other
      // failure re-arms the ordinary removal rather than escalating to the destructive one.
      const error = describeGitError(cause, 'The removal')
      setConfirm({ path: item.path, force: error.includes('--force'), error })
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
    setBusy('create'); setFormError(''); setNote('')
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
      await refresh()
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
    if (count) return `${count} live session${count === 1 ? '' : 's'} ${count === 1 ? 'is' : 'are'} working in this directory.`
    return ''
  }

  return <>
    <p class="drawer-status" title={root}>{project.name} · {root}</p>
    <div class="git-tab-body">
      {error && <p class="git-state error" role="alert">{error}</p>}
      {!worktrees && !error && <p class="git-state">Reading repository…</p>}

      {!!rows.length && <section class="git-section">
        <h3>branches<span>{rows.length}</span></h3>
        {rows.map(row => <article class="git-row" key={row.key}>
          <div class="git-row-main">
            <strong class={row.detached ? 'detached' : undefined}>{row.label}</strong>
            {row.worktrees[0]?.main && <em class="git-flag main">main</em>}
            {row.external && <em class="git-flag" title="This session's directory is not one of this repository's worktrees — a nested or sibling repository under the Project root.">other repo</em>}
            {row.state && <span class="git-metrics">
              <em class={row.state.dirty ? 'dirty' : undefined}>{row.state.dirty ? `${row.state.dirty} dirty` : 'clean'}</em>
              {divergenceLabel(row.state) && <em class="diverged">{divergenceLabel(row.state)}</em>}
            </span>}
          </div>
          <small>
            {row.worktrees.map(item => pathTail(item.path)).join(', ') || row.sessions.map(item => pathTail(item.cwd)).join(', ')}
            {!row.state && <span class="git-quiet"> · no attached session, so no working-tree state</span>}
            {!!row.sessions.length && <span class="git-quiet"> · {row.sessions.map(item => item.name).join(', ')}</span>}
          </small>
        </article>)}
      </section>}

      {worktrees && !error && !rows.length && <p class="git-state">
        This Project's root is a repository with no listed working tree.
      </p>}

      {!!worktrees?.length && <section class="git-section">
        <h3>worktrees<span>{worktrees.length}</span></h3>
        {worktrees.map(item => {
          const blocked = removeBlocked(item)
          const armed = confirm && confirm.path === item.path ? confirm : null
          const count = sessionsByWorktree.get(item.path) || 0
          return <article class="git-row" key={item.path}>
            <div class="git-row-main">
              <strong title={item.path}>{pathTail(item.path)}</strong>
              {item.main && <em class="git-flag main">main</em>}
              {item.bare && <em class="git-flag">bare</em>}
              {item.locked !== null && <em class="git-flag warn" title={item.locked || 'locked'}>locked</em>}
              {item.prunable !== null && <em class="git-flag warn" title={item.prunable || 'prunable'}>prunable</em>}
              {/* Work that has not reached the agent trunk yet. Null means the daemon could
                  not measure it, which must not render as "nothing to land". */}
              {!!item.unlanded && <em
                class="git-flag warn"
                title={`${item.unlanded} commit${item.unlanded === 1 ? '' : 's'} on ${item.branch || 'this branch'} that the trunk does not have yet. Land the branch to publish them.`}
              >{item.unlanded} unlanded</em>}
              {!!count && <em class="git-flag live">{count} live</em>}
            </div>
            <small title={item.path}>
              {item.branch ? item.branch : item.bare ? '(bare)' : `detached @ ${shortSha(item.head) || 'unknown'}`}
              {item.head && item.branch ? ` · ${shortSha(item.head)}` : ''} · {item.path}
            </small>
            <div class="git-row-actions">
              {blocked
                ? <span class="git-quiet" title={blocked}>{blocked}</span>
                : armed
                  ? <>
                    <button
                      class="danger"
                      disabled={busy === item.path}
                      title={armed.force ? 'Deletes the directory along with the uncommitted work Git just refused to discard.' : undefined}
                      onClick={() => void remove(item, armed.force)}
                    >
                      {busy === item.path ? 'removing…' : armed.force ? 'Force remove ✓' : 'Confirm remove ✓'}
                    </button>
                    <button onClick={() => setConfirm(null)}>Cancel</button>
                    {/* Removing a worktree deletes the directory, never the branch, so
                        committed-but-unlanded work is not at risk here. Say so, because the
                        unlanded flag right above otherwise reads as a reason not to. */}
                    {!!item.unlanded && <span class="git-quiet">
                      {item.branch} keeps its {item.unlanded} unlanded commit{item.unlanded === 1 ? '' : 's'}
                    </span>}
                  </>
                  : <button onClick={() => setConfirm({ path: item.path, force: false, error: '' })}>Remove…</button>}
            </div>
            {armed?.error && <p class="git-row-error" role="alert">{armed.error}</p>}
          </article>
        })}
      </section>}

      {note && <p class="git-state" aria-live="polite">{note}</p>}
    </div>

    {!error && !!worktrees && <div class="drawer-fields git-add">
      <button class="git-add-toggle" aria-expanded={adding} onClick={() => { setAdding(value => !value); setFormError('') }}>
        {adding ? 'Cancel new worktree' : 'Add worktree…'}
      </button>
      {adding && <form onSubmit={create}>
        <label>
          <span>Directory (absolute)</span>
          <input value={form.path} spellcheck={false} placeholder="D:\\PROJECTS\\.worktrees\\repo\\slug" onInput={event => setForm({ ...form, path: event.currentTarget.value })} />
        </label>
        <label>
          <span>New branch (optional)</span>
          <input value={form.branch} spellcheck={false} placeholder="agent/slug" onInput={event => setForm({ ...form, branch: event.currentTarget.value })} />
        </label>
        <label>
          <span>Start point (optional)</span>
          <input value={form.start} spellcheck={false} placeholder="integration" onInput={event => setForm({ ...form, start: event.currentTarget.value })} />
        </label>
        <button type="submit" disabled={busy === 'create'}>{busy === 'create' ? 'Creating…' : 'Create worktree'}</button>
        {formError && <p class="git-row-error" role="alert">{formError}</p>}
      </form>}
    </div>}

    <footer class="drawer-actions">
      <button onClick={() => void refresh()}>Refresh</button>
    </footer>
  </>
}
