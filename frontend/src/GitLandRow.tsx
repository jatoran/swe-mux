import { useState } from 'preact/hooks'
import { api } from './api'
import { GrantGate } from './GrantGate'
import {
  isActiveLand,
  landStateLabel,
  landStateTone,
  verifyProgressLabel,
  type LandQueue,
  type LandRequest,
} from './gitLand'
import { landErrorText, useVerifyCommand } from './landState'
import { normalizePath, shortSha } from './gitWorktrees'
import type { Project } from './types'

/**
 * Landing, inside the Map row of the worktree being landed.
 *
 * Landing a branch is an act on a checkout you are already looking at, and it used to
 * live on a separate segment with its own list of the same worktrees. Two lists of one
 * thing is how a reader ends up finding a branch twice, and it put the button furthest
 * from the diff that decides whether to press it.
 *
 * What stayed behind on the Land segment is everything that is *not* about one
 * checkout: the queue's order, its history, the Project's verification command and who
 * besides the operator may start a land. This component holds only what is true of this
 * row, plus the one gate that makes this row's own button inert.
 *
 * Nothing here moves a trunk. The button enqueues a request and the daemon's supervised
 * sweep is the only thing that reconciles, verifies, and fast-forwards, which is why
 * there is no "land now" to look for.
 */
export function GitLandRow({ project, worktreeRoot, branch, detached, queue, onChanged }: {
  project: Project
  worktreeRoot: string
  /** `null` for a detached HEAD, which cannot be landed and says so rather than failing. */
  branch: string | null
  detached: boolean
  /** The shared queue read (`landState.useLandQueue`), so this row and the Land segment
   *  cannot disagree about which request is running. */
  queue: LandQueue | null
  onChanged: () => void | Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [showGate, setShowGate] = useState(false)
  // Resolved for *this* checkout: the script convention is fingerprinted from the
  // worktree's own copy, so a branch that edited its gate is unapproved here while the
  // primary checkout's reading still says approved.
  const { gate, refresh: refreshGate } = useVerifyCommand(project.id, worktreeRoot, true)

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

  const approve = async () => {
    if (!gate?.digest) return
    setBusy(true)
    try {
      await api('POST', '/api/land/verify-command/approve', {
        project_id: project.id, worktree_root: worktreeRoot, digest: gate.digest,
      })
      setShowGate(false); setError('')
    } catch (cause) { setError(landErrorText(cause)) }
    finally { setBusy(false); await refreshGate() }
  }

  const installStopped = queue !== null && !queue.installedEnabled

  return <section class="git-land-row-section" aria-label={`Land ${branch || worktreeRoot}`}>
    <h4>LANDING<small>reconcile · verify · fast-forward onto the trunk</small></h4>
    {error && <p class="git-state error" role="alert">{error}</p>}

    {/* A detached worktree is stated rather than offered and then refused: the daemon
        has no branch name to fast-forward from. */}
    {detached && <p class="git-change-empty">
      This worktree is on a detached HEAD. Create a named branch here before landing.
    </p>}

    {!detached && <>
      {/* Outermost first. The sweep checks this one switch before it reads anything
          else, so with it off a request enqueues and then sits at `queued` forever -
          which looks exactly like a busy queue from here. */}
      {installStopped && <GrantGate ids={['automation.landQueue']}
        heading="The land queue is switched off for this install."
        onGranted={onChanged}>
        <p>This branch can still be queued, and nothing will move it: the daemon's sweep
        is the only thing that reconciles, verifies, and fast-forwards, and it stops on
        this switch before reading anything else.</p>
      </GrantGate>}

      {active
        ? <LandProgress request={active} busy={busy} onCancel={() => void cancel(active.id)} />
        : <>
          {/* The gate before the button. Nothing this button starts can get past an
              unapproved gate, so a Land offered above it would read as ready. */}
          <VerifyGate gate={gate} busy={busy} showing={showGate}
            onToggle={() => setShowGate(value => !value)} onApprove={() => void approve()} />
          <div class="git-land-launch">
            <button disabled={busy || !branch} onClick={() => void enqueue()}>
              Land {branch}
            </button>
            <small>fast-forward only · the daemon runs it, not this button</small>
          </div>
        </>}

      {/* What happened last time, kept short. The full trail is the request's own
          events; this is the one line that says whether to try again. */}
      {!active && last && <p class={`git-land-last ${landStateTone(last.state)}`}>
        <em class={`git-land-state ${landStateTone(last.state)}`}>{landStateLabel(last.state)}</em>
        {last.reason && <span>{last.reason}</span>}
        {last.landedOid && <code>{shortSha(last.trunkBefore)} → {shortSha(last.landedOid)}</code>}
      </p>}
    </>}
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

/**
 * The verification gate as it stands for this checkout, and the human act that approves it.
 *
 * Shown only while it blocks: an approved gate is one line, and an unapproved one opens
 * the exact bytes beside the ones that were approved before. The approve button is the
 * only place in swe-mux where a verification command gains authority, and it is
 * deliberately not reachable from anything an agent can call.
 */
function VerifyGate({ gate, busy, showing, onToggle, onApprove }: {
  gate: ReturnType<typeof useVerifyCommand>['gate']
  busy: boolean
  showing: boolean
  onToggle: () => void
  onApprove: () => void
}) {
  if (!gate) return null
  if (!gate.configured) {
    return <p class="git-state warn">
      No verification command resolves here, so a land would be refused rather than run.
      Add an executable <code>{gate.scriptName}</code> to this worktree, or set one for
      the Project in the Land segment.
    </p>
  }
  return <div class={`git-land-gate ${gate.approved ? 'ok' : 'warn'}`}>
    <div class="git-land-gate-head">
      <strong>{gate.approved ? 'Verification approved' : 'Verification not approved'}</strong>
      <code>{gate.display}</code>
      <button onClick={onToggle}>{showing ? 'Hide' : 'Review'}</button>
    </div>
    {!gate.approved && <p class="git-state">
      {gate.previouslyApproved
        ? 'It changed since it was approved. Review it again before anything runs it.'
        : 'Review the exact command a land will run, then approve it.'}
    </p>}
    {showing && <div class="git-land-gate-body">
      {gate.previouslyApproved && gate.approvedSource && gate.approvedSource !== gate.currentSource && <>
        <h5>Previously approved</h5>
        <pre class="git-land-source">{gate.approvedSource}</pre>
      </>}
      <h5>Will run</h5>
      <pre class="git-land-source">{gate.currentSource || gate.display}</pre>
      {!gate.approved && <button disabled={busy} onClick={onApprove}>Approve these bytes</button>}
    </div>}
  </div>
}
