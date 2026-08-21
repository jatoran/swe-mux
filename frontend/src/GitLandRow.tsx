import { useState } from 'preact/hooks'
import { api } from './api'
import {
  isActiveLand,
  landGateNote,
  landKindNote,
  landStateLabel,
  landStateTone,
  verifyProgressLabel,
  type LandQueue,
  type LandRequest,
} from './gitLand'
import { landErrorText } from './landState'
import { normalizePath, shortSha } from './gitWorktrees'
import type { Project } from './types'

/**
 * Landing, inside the Map row of the worktree being landed.
 *
 * **Only what is true of this checkout.** The button, what its request is doing right
 * now, and what stopped it last time - a conflict's paths, a refusal's reason. Nothing
 * here is a property of the Project, because a Project-wide fact drawn on a row is drawn
 * once per worktree: the verification command's approval block shipped that way first,
 * and reading the same paragraph about approved bytes under each of eight expansions is
 * what sent it to the landing strip above the map (`GitLandBar.tsx`).
 *
 * That leaves the row with a real question when landing is blocked, since it can no
 * longer offer the switch itself. It answers it by *sending the reader to the control*:
 * one press opens the strip, which holds the grant and the approval. Naming a switch
 * still obliges offering it (`setting-links.md`); pointing one section up on the same
 * pane is offering it, and is the thing that rule was written against overlays for.
 *
 * Nothing here moves a trunk. The button enqueues a request and the daemon's supervised
 * sweep is the only thing that reconciles, verifies, and fast-forwards, which is why
 * there is no "land now" to look for.
 */
export function GitLandRow({ project, worktreeRoot, branch, detached, queue, onChanged, onShowLanding }: {
  project: Project
  worktreeRoot: string
  /** `null` for a detached HEAD, which cannot be landed and says so rather than failing. */
  branch: string | null
  detached: boolean
  /** The shared queue read (`landState.useLandQueue`), so this row and the landing strip
   *  cannot disagree about which request is running. */
  queue: LandQueue | null
  onChanged: () => void | Promise<void>
  /** Open the landing strip, which owns every Project-wide control this row names. */
  onShowLanding: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Matched on either coordinate: the daemon records the root Git resolved, which can
  // differ from the one Map listed in separator and case, and a request whose worktree
  // was removed still names the branch this row is on.
  const here = normalizePath(worktreeRoot)
  const rows = queue?.requests || []
  const mine = rows.filter(request => normalizePath(request.worktreeRoot) === here
    || (!!branch && request.branch === branch))
  const active = mine.find(isActiveLand) || null
  const last = mine.find(request => !isActiveLand(request)) || null

  const enqueue = async () => {
    setBusy(true)
    try {
      await api('POST', '/api/land', { project_id: project.id, worktree_root: worktreeRoot })
      setError(''); await onChanged()
    } catch (cause) { setError(landErrorText(cause)) } finally { setBusy(false) }
  }

  const cancel = async (requestId: string) => {
    setBusy(true)
    try {
      await api('DELETE', `/api/land/${encodeURIComponent(requestId)}`)
      setError(''); await onChanged()
    } catch (cause) { setError(landErrorText(cause)) } finally { setBusy(false) }
  }

  const installStopped = queue !== null && !queue.installedEnabled

  return <section class="git-land-row-section" aria-label={`Land ${branch || worktreeRoot}`}>
    {error && <p class="git-state error" role="alert">{error}</p>}

    {/* A detached worktree is stated rather than offered and then refused: the daemon
        has no branch name to fast-forward from. */}
    {detached && <p class="git-change-empty">
      This worktree is on a detached HEAD. Create a named branch here before landing.
    </p>}

    {!detached && (active
      ? <LandProgress request={active} busy={busy} onCancel={() => void cancel(active.id)} />
      : <div class="git-land-launch">
        <button disabled={busy || !branch} onClick={() => void enqueue()}>
          Land {branch}
        </button>
        <small>fast-forward only · the daemon runs it, not this button</small>
        {/* One line, and a way to act on it. The strip states which of the two causes
            it is and holds the control; repeating either here would put a Project-wide
            answer under every worktree, which is the repetition this row shed. */}
        {installStopped && <button class="git-land-elsewhere" onClick={onShowLanding}>
          the land queue is switched off — open Landing
        </button>}
      </div>)}

    {/* What happened last time, and why. A conflict's paths and a refusal's reason are
        properties of *this* branch, so they belong on its row rather than only in the
        queue's history above. */}
    {!detached && !active && last && <div class={`git-land-last ${landStateTone(last.state)}`}>
      <p>
        <em class={`git-land-state ${landStateTone(last.state)}`}>{landStateLabel(last.state)}</em>
        {/* A verify-only request that a session made from inside this worktree finishes
            on this row too, and `Verified` beside a Land button is ambiguous without
            saying which act it was. */}
        {landKindNote(last) && <small class="git-land-kind-note">{landKindNote(last)}</small>}
        {last.reason && <span>{last.reason}</span>}
        {/* A land that went round the gate says so on the row it landed from, where the
            next person to press Land on this branch will read it. Without this, "Landed"
            on a documentation branch and "Landed" after three minutes of pytest are the
            same two words. */}
        {landGateNote(last) && <small class="git-land-gate-note">{landGateNote(last)}</small>}
        {last.landedOid && <code>{shortSha(last.trunkBefore)} → {shortSha(last.landedOid)}</code>}
      </p>
      {last.paths.length > 0 && <ul class="git-land-paths">
        {last.paths.slice(0, 12).map(path => <li key={path}><code>{path}</code></li>)}
        {last.paths.length > 12 && <li><small>and {last.paths.length - 12} more</small></li>}
      </ul>}
    </div>}
  </section>
}

/**
 * A request in flight, with the gate's own reading of itself when it is running one.
 *
 * `verifying` on its own said nothing about whether the gate was thirty seconds or four
 * minutes in, on a step that routinely takes three. What is drawn instead is only what
 * the gate announced: which step it is on, its name, how long it has been running, and
 * a total *only* when a byte-identical run has already passed and recorded one. There is
 * no bar and no percentage, because a gate whose steps take 175s and 3s has no honest
 * denominator to draw one against.
 */
function LandProgress({ request, busy, onCancel }: {
  request: LandRequest
  busy: boolean
  onCancel: () => void
}) {
  const progress = request.verifyProgress
  const label = verifyProgressLabel(progress)
  const cancellable = request.state === 'queued' || request.state === 'waiting'
  return <div class={`git-land-progress ${landStateTone(request.state)}`}>
    <div class="git-land-progress-head">
      <em class={`git-land-state ${landStateTone(request.state)}`}>{landStateLabel(request.state)}</em>
      {/* `Verifying` under a Land button means one thing when a land is running and
          another when the request will stop there, and the states cannot tell them
          apart. */}
      {landKindNote(request) && <small class="git-land-kind-note">{landKindNote(request)}</small>}
      {label && <span class="git-land-progress-detail">{label}</span>}
      {request.origin !== 'operator' && <small>requested by agent</small>}
      {cancellable && <button disabled={busy} onClick={onCancel}>Cancel</button>}
    </div>
    {request.reason && <p class="git-land-reason">{request.reason}</p>}
    {/* Steps this run has finished, with what each of them actually took. A list of
        facts about the run on screen, never an estimate of the one still going. */}
    {progress && progress.completedSteps.length > 0 && <ol class="git-land-steps">
      {progress.completedSteps.map(step => <li key={step.name}>
        <span>{step.name}</span><small>{Math.round(step.durationMs / 1000)}s</small>
      </li>)}
    </ol>}
    {progress && progress.beyondPlan && <small class="git-land-note">
      This run has more steps than the last one that passed, so there is no total to show.
    </small>}
  </div>
}
