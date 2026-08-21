import { useCallback, useEffect, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
import { GrantButton, GrantGate } from './GrantGate'
import {
  isActiveLand,
  landStateLabel,
  landStateTone,
  parseLandQueue,
  parseLandVerifyCommand,
  type LandQueue,
  type LandRequest,
  type LandVerifyCommand,
} from './gitLand'
import { pathTail, shortSha } from './gitWorktrees'
import type { Project } from './types'

// The Land segment: what is queued onto this Project's trunk, and the one gate that
// has to be approved before anything can run.
//
// Read-mostly like the rest of the Git tab. The two writes here are *requests* - enqueue
// and cancel - plus approving the verification command's exact bytes. Nothing in this
// component can move a trunk; the daemon's own sweep is the only thing that does, which
// is why there is no "land now" button to look for.

type Props = {
  project: Project | null
  /** Worktree roots the Map already knows about, so a request names a real checkout.
   *  Handed over in the Map's own order — most recently committed first
   *  (`sortWorktreesByActivity`) — because the two lists answer the same question and
   *  a reader who found a branch near the top of one must not have to hunt for it in
   *  the other. */
  worktrees: { path: string; branch: string | null; main: boolean }[]
}

function ago(seconds: number): string {
  if (!seconds) return ''
  const delta = Math.max(0, Date.now() / 1000 - seconds)
  if (delta < 60) return `${Math.round(delta)}s`
  if (delta < 3600) return `${Math.round(delta / 60)}m`
  return `${Math.round(delta / 3600)}h`
}

export function GitLandPanel({ project, worktrees }: Props) {
  const [queue, setQueue] = useState<LandQueue | null>(null)
  const [gate, setGate] = useState<LandVerifyCommand | null>(null)
  const [gateFor, setGateFor] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [showGate, setShowGate] = useState(false)

  // A detached worktree is excluded rather than offered and then refused: the daemon
  // has no branch name to fast-forward from, and a button that always fails is worse
  // than an absent one.
  const landable = worktrees.filter(tree => !tree.main && !!tree.branch)
  const detached = worktrees.filter(tree => !tree.main && !tree.branch)

  const refresh = useCallback(async () => {
    if (!project) { setQueue(null); return }
    try {
      const raw = await api<unknown>('GET', `/api/land?project_id=${encodeURIComponent(project.id)}`)
      setQueue(parseLandQueue(raw)); setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [project?.id])

  const refreshGate = useCallback(async (worktreeRoot: string) => {
    if (!project || !worktreeRoot) { setGate(null); return }
    try {
      const raw = await api<unknown>(
        'GET',
        `/api/land/verify-command?project_id=${encodeURIComponent(project.id)}&worktree_root=${encodeURIComponent(worktreeRoot)}`,
      )
      setGate(parseLandVerifyCommand(raw)); setGateFor(worktreeRoot)
    } catch { setGate(null) }
  }, [project?.id])

  useEffect(() => {
    setQueue(null); setGate(null); setGateFor(''); setError(''); setShowGate(false)
    void refresh()
    const changed = () => void refresh()
    window.addEventListener('mux:land-changed', changed)
    window.addEventListener('mux:events-connected', changed)
    // The queue advances on the daemon's own cadence, so a slow poll keeps a step
    // change visible without an event for every tick of a five-second sweep.
    const timer = window.setInterval(changed, 5000)
    return () => {
      window.removeEventListener('mux:land-changed', changed)
      window.removeEventListener('mux:events-connected', changed)
      window.clearInterval(timer)
    }
  }, [refresh])

  useEffect(() => {
    const first = landable[0]?.path || project?.root || ''
    if (first && first !== gateFor) void refreshGate(first)
  }, [landable.length, project?.root, refreshGate])

  async function enqueue(worktreeRoot: string) {
    if (!project) return
    setBusy(true)
    try {
      await api('POST', '/api/land', { project_id: project.id, worktree_root: worktreeRoot })
      setError(''); await refresh()
    } catch (cause) {
      const detail = (cause as ApiError)?.detail
      setError(typeof detail?.error === 'string' ? detail.error : cause instanceof Error ? cause.message : String(cause))
    } finally { setBusy(false) }
  }

  async function cancel(requestId: string) {
    setBusy(true)
    try { await api('DELETE', `/api/land/${encodeURIComponent(requestId)}`); await refresh() }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false) }
  }

  async function approveGate() {
    if (!project || !gate?.digest) return
    setBusy(true)
    try {
      const raw = await api<unknown>('POST', '/api/land/verify-command/approve', {
        project_id: project.id, worktree_root: gateFor, digest: gate.digest,
      })
      setGate(parseLandVerifyCommand(raw)); setShowGate(false); setError('')
    } catch (cause) {
      const detail = (cause as ApiError)?.detail
      setError(typeof detail?.error === 'string' ? detail.error : cause instanceof Error ? cause.message : String(cause))
      await refreshGate(gateFor)
    } finally { setBusy(false) }
  }

  const active = (queue?.requests || []).filter(isActiveLand)
  const history = (queue?.requests || []).filter(request => !isActiveLand(request))

  return <section class="git-land" aria-label="Land queue">
    {error && <p class="git-state error" role="alert">{error}</p>}

    {/* Before the verification gate, because it is further out: the sweep that runs the
        pipeline checks this one switch and stops dead, so with it off a request enqueues
        and then sits at `queued` forever. That looked identical to a busy queue, and the
        switch had no control in any overlay to reach either. */}
    {queue && !queue.installedEnabled && <GrantGate ids={['automation.landQueue']}
      heading="The land queue is switched off for this install."
      onGranted={refresh}>
      <p>Branches can still be queued here, and nothing will move them: the daemon's
      sweep is the only thing that reconciles, verifies, and fast-forwards, and it stops
      on this switch before reading anything else.</p>
    </GrantGate>}

    {/* The gate first. Nothing below it can run until these bytes are approved, so a
        queue drawn above an unapproved gate would read as ready when it is not. */}
    <div class={`git-land-gate ${gate?.approved ? 'ok' : 'warn'}`}>
      {!gate?.configured && <p class="git-state">
        No verification command. Add an executable <code>.worktree-verify</code> to the
        repository, or set <code>[worktree] verify_command</code> in <code>.swe-mux/config.toml</code>.
      </p>}
      {gate?.configured && <>
        <div class="git-land-gate-head">
          <strong>{gate.approved ? 'Verification approved' : 'Verification not approved'}</strong>
          <code>{gate.display}</code>
          <button onClick={() => setShowGate(value => !value)}>{showGate ? 'Hide' : 'Review'}</button>
        </div>
        {!gate.approved && <p class="git-state">
          {gate.previouslyApproved
            ? 'It changed since it was approved. Review it again before anything runs it.'
            : 'Review the exact command a land will run, then approve it.'}
        </p>}
        {showGate && <div class="git-land-gate-body">
          {gate.previouslyApproved && gate.approvedSource && gate.approvedSource !== gate.currentSource && <>
            <h5>Previously approved</h5>
            <pre class="git-land-source">{gate.approvedSource}</pre>
          </>}
          <h5>Will run</h5>
          <pre class="git-land-source">{gate.currentSource || gate.display}</pre>
          {!gate.approved && <button disabled={busy} onClick={() => void approveGate()}>Approve these bytes</button>}
        </div>}
      </>}
    </div>

    <div class="git-land-actions">
      {landable.length === 0 && <p class="git-state">No worktrees to land.</p>}
      {detached.length > 0 && <p class="git-state">
        {detached.length} worktree(s) are on a detached HEAD. Create a named branch there before landing.
      </p>}
      {landable.map(tree => {
        const queued = active.find(request => request.branch === tree.branch)
        return <div class="git-land-launch" key={tree.path}>
          <span class="git-land-branch"><strong>{tree.branch || pathTail(tree.path)}</strong><small>{pathTail(tree.path)}</small></span>
          <button disabled={busy || !!queued} onClick={() => void enqueue(tree.path)}>
            {queued ? landStateLabel(queued.state) : 'Land'}
          </button>
        </div>
      })}
    </div>

    {active.length > 0 && <div class="git-land-list" aria-label="Queued lands">
      {active.map(request => <LandRow key={request.id} request={request} busy={busy} onCancel={() => void cancel(request.id)} />)}
    </div>}

    {history.length > 0 && <details class="git-land-history">
      <summary>{history.length} finished</summary>
      {history.map(request => <LandRow key={request.id} request={request} busy={busy} />)}
    </details>}

    {/* Who besides you may start one. This is the second half of the land queue's
        authority and it had no control anywhere until now — only `land_grant` in a
        committed TOML file, which made "a human approves each one" both unreachable to
        change and impossible to discover. It belongs beside the verification gate
        because it is the same question asked of a different actor. */}
    {queue && project && <details class="git-land-authority">
      <summary>
        <span>Agent-initiated landing</span>
        <em class={queue.projectEnabled && queue.agentGrant === 'granted' ? 'ok' : 'warn'}>{
          !queue.projectEnabled ? 'not permitted'
            : queue.agentGrant === 'granted' ? 'starts without asking' : 'you approve each one'
        }</em>
      </summary>
      <p>Your own Land button never consults this — you are the authority it defers to.
      It decides what happens when an agent in a worktree here calls
      <code>mux.request_land</code> instead.</p>
      {!queue.projectEnabled
        ? <GrantGate ids={['project.landQueue']} projectId={project.id}
            heading="This Project has not permitted the land queue for agents."
            onGranted={refresh}>
            <p>With it off, an agent's <code>request_land</code> is refused outright.
            With it on, the request is still drafted for you to approve until you raise
            the authority below.</p>
          </GrantGate>
        : queue.agentGrant === 'draft'
          ? <p class="git-land-authority-row">Each agent request writes a row you decide,
            here.{' '}
            <GrantButton id="project.landGrant" projectId={project.id} onGranted={refresh}
              title="Lets an agent start a land without waiting for you · writes to this Project’s .swe-mux/config.toml"
            >Let agents start one directly</GrantButton>{' '}
            (still fast-forward-only, still gated on the approved verification command,
            and still inside {queue.hourlyBudget} requests per session per hour).</p>
          : <p class="git-land-authority-row">Agents start lands directly, inside
            {' '}{queue.hourlyBudget} requests per session per hour. Lower it back to
            approve-each-one in this Project's settings.</p>}
    </details>}
  </section>
}

function LandRow({ request, busy, onCancel }: { request: LandRequest; busy: boolean; onCancel?: () => void }) {
  return <article class={`git-land-row ${landStateTone(request.state)}`}>
    <div class="git-land-row-head">
      <strong>{request.branch}</strong>
      <em class={`git-land-state ${landStateTone(request.state)}`}>{landStateLabel(request.state)}</em>
      {request.origin !== 'operator' && <small class="git-land-origin">requested by agent</small>}
      {request.waitingSince && <small>{ago(request.waitingSince)} waiting</small>}
      {onCancel && <button disabled={busy} onClick={onCancel}>Cancel</button>}
    </div>
    {request.reason && <p class="git-land-reason">{request.reason}</p>}
    {request.paths.length > 0 && <ul class="git-land-paths">
      {request.paths.slice(0, 12).map(path => <li key={path}><code>{path}</code></li>)}
      {request.paths.length > 12 && <li><small>and {request.paths.length - 12} more</small></li>}
    </ul>}
    {request.landedOid && <small class="git-land-oid">
      {shortSha(request.trunkBefore)} → {shortSha(request.landedOid)}
    </small>}
  </article>
}
