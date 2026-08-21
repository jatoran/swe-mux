import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { GrantButton, GrantGate } from './GrantGate'
import {
  formatDuration,
  landGateNote,
  landHistoryOrder,
  landKindNote,
  landQueueOrder,
  landStateLabel,
  landStateTone,
  landingSummary,
  parseLandVerifyCommand,
  verifyCommandEditable,
  verifyPlanNote,
  verifyProgressLabel,
  type LandQueue,
  type LandRequest,
  type LandVerifyCommand,
} from './gitLand'
import { landErrorText, useVerifyCommand } from './landState'
import { verifySetupPrompt } from './landSetupPrompt'
import { shortSha } from './gitWorktrees'
import type { Project } from './types'

// Landing, as one compact section at the head of the Map.
//
// It was a segment of its own until the map rows learned to land their own branch, and
// then it was a second surface holding one copy of everything the rows could not: the
// verification command, the agent grants, and the queue. Keeping it as a parallel view
// meant a reader watching a land had to leave the map to see it, and putting the gate on
// every row instead meant reading the same paragraph about approved bytes under each of
// eight worktrees.
//
// So it is neither. Landing is **one strip above the map**, and the map is still a map:
//
//  * A summary line that is always readable without opening anything - what the gate's
//    standing is, and what the queue is doing right now.
//  * Everything Project-wide, once: the verification command with its source, approval,
//    recorded plan, and editor; who besides the operator may start a land; the queue in
//    the order the pipeline will reach it; and what finished.
//
// The strip opens itself when the gate **blocks**, because a surface that cannot work
// must not render as merely quiet (`setting-links.md`), and stays closed otherwise so
// the tab still opens on a map. An explicit toggle wins over that for the rest of the
// visit; nothing re-collapses under the reader.
//
// Read-mostly like the rest of the Git tab. The writes are a *request*'s cancellation,
// the Project's own `[worktree] verify_command`, and approving the exact bytes that will
// run. Nothing here moves a trunk.

type Props = {
  project: Project | null
  /** The shared queue read, owned by `GitTab` so the map rows and this strip cannot
   *  disagree about which request is running. */
  queue: LandQueue | null
  error: string
  onChanged: () => void | Promise<void>
  /** Open state is lifted so a blocked map row can send the reader here, which is what
   *  lets the rows carry no Project-wide control of their own. `null` means "not decided
   *  by hand yet", so the blocked-gate default still applies. */
  open: boolean | null
  onOpen: (value: boolean) => void
}

function ago(seconds: number): string {
  if (!seconds) return ''
  const delta = Math.max(0, Date.now() / 1000 - seconds)
  if (delta < 60) return `${Math.round(delta)}s`
  if (delta < 3600) return `${Math.round(delta / 60)}m`
  return `${Math.round(delta / 3600)}h`
}

export function GitLandBar({ project, queue, error, onChanged, open, onOpen }: Props) {
  const [localError, setLocalError] = useState('')
  const [busy, setBusy] = useState(false)

  // Resolved against the Project root: this is the Project's declared gate, and this is
  // the one place it is drawn. A worktree whose own `.worktree-verify` differs resolves
  // differently when its land actually runs, which the row reports as a refusal rather
  // than by carrying a second copy of this block.
  const { gate, refresh: refreshGate, setGate } = useVerifyCommand(
    project?.id, project?.root || '', !!project,
  )

  const cancel = async (requestId: string) => {
    setBusy(true)
    try {
      await api('DELETE', `/api/land/${encodeURIComponent(requestId)}`)
      setLocalError(''); await onChanged()
    } catch (cause) { setLocalError(landErrorText(cause)) } finally { setBusy(false) }
  }

  const queued = landQueueOrder(queue?.requests || [])
  const history = landHistoryOrder(queue?.requests || [])
  const shown = error || localError
  const summary = landingSummary(queue, gate)
  // Blocked means the tab cannot land anything: the install stop is off, or the bytes a
  // land would run are not approved. That is the setting-links "inert surface" case, and
  // the act that clears it is inside this strip - so it opens itself.
  const isOpen = open ?? summary.blocked

  return <section class="git-landing" aria-label="Landing">
    <button class={`git-landing-summary ${summary.tone}`} aria-expanded={isOpen}
      onClick={() => onOpen(!isOpen)}>
      <span class="git-landing-title">LANDING</span>
      <span class="git-landing-facts">
        <em class={summary.gateTone}>{summary.gate}</em>
        <span>{summary.queue}</span>
      </span>
      <span class="git-landing-chevron" aria-hidden="true">{isOpen ? '−' : '+'}</span>
    </button>

    {shown && <p class="git-state error" role="alert">{shown}</p>}

    {/* Outside the disclosure on purpose. A gate is what a surface renders *instead of*
        working, so hiding it behind a collapsed summary would be the same defect as
        rendering the surface empty. The sweep checks this switch before it reads
        anything else, so with it off a request enqueues and then sits at `queued`
        forever - identical on screen to a pipeline working through a backlog. */}
    {queue && !queue.installedEnabled && <GrantGate ids={['automation.landQueue']}
      heading="The land queue is switched off for this install."
      onGranted={onChanged}>
      <p>Branches can still be queued from the rows below, and nothing will move them:
      the daemon's sweep is the only thing that reconciles, verifies, and fast-forwards,
      and it stops on this switch before reading anything else.</p>
    </GrantGate>}

    {isOpen && <div class="git-landing-body">
      <VerifyCommandEditor project={project} gate={gate} busy={busy} setBusy={setBusy}
        onError={setLocalError} onGate={setGate} onRefresh={refreshGate} />

      {/* Who besides you may start one. Project-wide, and therefore here rather than on
          a row: a control that answers "for every branch in this repository" would be a
          standing fixture in each of eight expansions, which is the repetition this
          whole strip exists to remove. */}
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
              onGranted={onChanged}>
              <p>With it off, an agent's <code>request_land</code> is refused outright.
              With it on, the request is still drafted for you to approve until you raise
              the authority below.</p>
            </GrantGate>
          : queue.agentGrant === 'draft'
            ? <p class="git-land-authority-row">Each agent request writes a row you decide,
              here.{' '}
              <GrantButton id="project.landGrant" projectId={project.id} onGranted={onChanged}
                title="Lets an agent start a land without waiting for you · writes to this Project’s .swe-mux/config.toml"
              >Let agents start one directly</GrantButton>{' '}
              (still fast-forward-only, still gated on the approved verification command,
              and still inside {queue.hourlyBudget} requests per session per hour).</p>
            : <p class="git-land-authority-row">Agents start lands directly, inside
              {' '}{queue.hourlyBudget} requests per session per hour. Lower it back to
              approve-each-one in this Project's settings.</p>}
      </details>}

      {/* The queue, in the order the pipeline will reach it. Oldest first: the request
          about to run is the one a reader looks at first, and the daemon's own
          newest-first listing put it at the bottom. */}
      <div class="git-land-list" aria-label="Queued lands">
        <h4>QUEUE<small>{queued.length ? `${queued.length} waiting to land` : 'nothing queued'}</small></h4>
        {queued.length === 0 && <p class="git-change-empty">
          Land a branch from its row below — expand the worktree and press Land.
        </p>}
        {queued.map((request, index) => <LandRow key={request.id} request={request}
          position={index + 1} busy={busy} onCancel={() => void cancel(request.id)} />)}
      </div>

      {history.length > 0 && <details class="git-land-history">
        <summary>{history.length} finished</summary>
        {history.map(request => <LandRow key={request.id} request={request} busy={busy} />)}
      </details>}
    </div>}
  </section>
}

/**
 * The Project's verification command: what resolves, whether it is approved, and the
 * one editor that changes it.
 *
 * **One copy, at the head of the tab.** It shipped under every expanded worktree row
 * first, which put the same paragraph about approved bytes under each of eight
 * checkouts; the fact it states is a property of the Project, not of the row, and the
 * approval act is the same act whichever row you happen to have open.
 *
 * The gate has always been able to *approve* bytes; until it gained this editor the only
 * way to change which bytes those were was to hand-edit a committed TOML file or the
 * script, with nothing on screen saying which of the two was in force.
 *
 * **Editing never approves.** They are separate acts against separate endpoints, and an
 * edit invalidates approval by construction because the approval is a digest over the
 * bytes. That is what keeps "an agent cannot approve the command its own land runs"
 * true regardless of who reaches this editor: writing a verification script is a
 * proposal, and a human is what turns it into an authority.
 *
 * It is deliberately not a Project Action. An action's cwd is bounded by the Project
 * root and denied the sibling-worktree widening, and an action step becomes a terminal
 * session rather than a captured exit code; the pipeline needs the exit code, inside a
 * tree outside the Project root.
 */
function VerifyCommandEditor({ project, gate, busy, setBusy, onError, onGate, onRefresh }: {
  project: Project | null
  gate: LandVerifyCommand | null
  busy: boolean
  setBusy: (value: boolean) => void
  onError: (value: string) => void
  onGate: (value: LandVerifyCommand) => void
  onRefresh: () => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [showing, setShowing] = useState(false)

  // The draft follows the daemon's answer until the operator opens the editor; after
  // that it is theirs, and a poll that overwrote it mid-sentence would be the note
  // editor's own retired bug in a new place.
  useEffect(() => { if (!editing) setDraft(gate?.configCommand || '') }, [gate?.configCommand, editing])

  if (!project) return null
  const editable = !gate || verifyCommandEditable(gate)

  const save = async (value: string) => {
    setBusy(true)
    try {
      const raw = await api<unknown>('PUT', '/api/land/verify-command', {
        project_id: project.id,
        worktree_root: project.root,
        command: value,
        revision: gate?.configRevision || 'missing',
      })
      onGate(parseLandVerifyCommand(raw)); setEditing(false); onError('')
    } catch (cause) {
      onError(landErrorText(cause))
      // The bytes may have moved under us (that is what a revision conflict means), so
      // re-read rather than leave the editor claiming the value it tried to write.
      await onRefresh()
    } finally { setBusy(false) }
  }

  const approve = async () => {
    if (!gate?.digest) return
    setBusy(true)
    try {
      const raw = await api<unknown>('POST', '/api/land/verify-command/approve', {
        project_id: project.id, worktree_root: project.root, digest: gate.digest,
      })
      onGate(parseLandVerifyCommand(raw)); setShowing(false); onError('')
    } catch (cause) { onError(landErrorText(cause)); await onRefresh() }
    finally { setBusy(false) }
  }

  const sourceLabel = gate?.source === 'project_config'
    ? '[worktree] verify_command'
    : gate?.source === 'convention' ? `the executable ${gate.scriptName}` : ''

  return <div class={`git-land-gate ${gate?.approved ? 'ok' : 'warn'}`}>
    <div class="git-land-gate-head">
      <strong>{!gate?.configured
        ? 'No verification command'
        : gate.approved ? 'Verification approved' : 'Verification not approved'}</strong>
      {gate?.configured && <code>{gate.display}</code>}
      {gate?.configured && <button onClick={() => setShowing(value => !value)}>
        {showing ? 'Hide' : 'Review'}
      </button>}
    </div>

    {/* Which of the two mechanisms is in force. Both were documented and neither was
        ever stated on screen, so "why is it running that" had no answer here. */}
    {gate?.configured && <p class="git-state">
      Resolved from {sourceLabel}, for every worktree of this Project.{' '}
      {gate.source === 'convention'
        ? 'Set an override below to run something else.'
        : gate.scriptPresent
          ? `The override wins over the ${gate.scriptName} in the checkout.`
          : ''}
    </p>}
    {gate && !gate.configured && <p class="git-state">
      A land here would be refused rather than run. Add an executable
      {' '}<code>{gate.scriptName}</code> to the repository, or set an override below.
    </p>}

    {gate?.configured && !gate.approved && <p class="git-state">
      {gate.previouslyApproved
        ? 'It changed since it was approved. Review it again before anything runs it.'
        : 'Review the exact command a land will run, then approve it.'}
    </p>}

    {showing && gate && <div class="git-land-gate-body">
      {gate.previouslyApproved && gate.approvedSource && gate.approvedSource !== gate.currentSource && <>
        <h5>Previously approved</h5>
        <pre class="git-land-source">{gate.approvedSource}</pre>
      </>}
      <h5>Will run</h5>
      <pre class="git-land-source">{gate.currentSource || gate.display}</pre>
      {!gate.approved && <button disabled={busy} onClick={() => void approve()}>
        Approve these bytes
      </button>}
    </div>}

    {/* A statement about a run that already happened, kept apart from anything a
        running gate reports so it cannot be read as a prediction of one. */}
    {gate?.plan && <p class="git-land-plan">{verifyPlanNote(gate.plan)}
      {gate.plan.steps.length > 0 && <small> {gate.plan.steps.join(' → ')}</small>}
    </p>}

    <div class="git-land-command-editor">
      {!editing && <button disabled={!editable} onClick={() => { setDraft(gate?.configCommand || ''); setEditing(true) }}>
        {gate?.configCommand ? 'Change the command…' : 'Set a command…'}
      </button>}
      {!editable && <small class="warn">
        This Project's <code>.swe-mux/config.toml</code> is {gate?.configStatus === 'malformed'
          ? 'malformed; fix it before editing here'
          : 'read-only on this machine'}.
      </small>}
      {editing && <>
        <label>
          <span>Override <code>[worktree] verify_command</code></span>
          <input value={draft} disabled={busy} placeholder={gate?.scriptName || '.worktree-verify'}
            onInput={event => setDraft(event.currentTarget.value)} />
        </label>
        <p class="git-state">
          Runs in the branch's worktree through this host's shell. It is committed
          repository content, so it travels with the checkout — and it will need
          approving again here before any land runs it.
        </p>
        <div>
          <button disabled={busy} onClick={() => void save(draft.trim())}>Save</button>
          <button disabled={busy} onClick={() => { setEditing(false); setDraft(gate?.configCommand || '') }}>Cancel</button>
          {gate?.configCommand && <button class="danger" disabled={busy} onClick={() => void save('')}>
            Clear the override
          </button>}
        </div>
      </>}
    </div>

    <SetupPromptDisclosure scriptName={gate?.scriptName || '.worktree-verify'} />
  </div>
}

/**
 * The same command, for a repository that does not have one yet.
 *
 * It sits under the editor because that is where the question arises: an operator reading
 * what this Project's gate is, or that this Project has none, is the one about to set one
 * up somewhere else, and everything the receiving agent needs is already written down in
 * `land-queue.md` rather than being anybody's to remember in a new repository.
 *
 * The prompt is **shown** rather than only copied. It is an instruction being handed to an
 * agent that will write the thing deciding what reaches a trunk unattended, and a copy
 * button whose payload nobody can read before pressing it is the wrong shape for that.
 * The copy itself is best-effort by construction - `navigator.clipboard` is absent in an
 * insecure context and refusable everywhere - so a failure says so and the text is already
 * on screen to select by hand, rather than the button silently doing nothing.
 *
 * **The prompt's last paragraph is what keeps this from being an authority leak**, and it
 * is the reason this is a button rather than something that could ever run: it tells the
 * agent it cannot approve what it just wrote, and that the human presses approve in this
 * very section. See `landSetupPrompt.ts` and `land-queue.md`.
 */
function SetupPromptDisclosure({ scriptName }: { scriptName: string }) {
  const [note, setNote] = useState('')
  const prompt = verifySetupPrompt(scriptName)
  const copy = async () => {
    try { await navigator.clipboard.writeText(prompt); setNote('Setup prompt copied.') }
    catch { setNote('Clipboard access was blocked. Select the text below and copy it.') }
  }
  return <details class="git-land-setup-prompt" onToggle={() => setNote('')}>
    <summary>Setting this up in another repository</summary>
    <p class="git-state">
      A prompt for an agent in a repository that has no verification command yet. It states
      what the gate is used for, the contract the command has to satisfy, the two
      conventions, and how to prove it fails when it should - and it ends by telling that
      agent it cannot approve what it wrote. Approving stays a human act, here, against the
      exact bytes.
    </p>
    <div>
      <button onClick={() => void copy()}>Copy setup prompt</button>
      {note && <small role="status">{note}</small>}
    </div>
    <pre class="git-land-source">{prompt}</pre>
  </details>
}

function LandRow({ request, busy, onCancel, position }: {
  request: LandRequest
  busy: boolean
  onCancel?: () => void
  /** 1-based place in the queue, for the rows that are still waiting for their turn. */
  position?: number
}) {
  const progress = verifyProgressLabel(request.verifyProgress)
  // Drawn in the queue *and* in the history disclosure, because a land that skipped the
  // gate has to still say so after it finishes: the row's own states cannot, having gone
  // from merging the trunk to fast-forwarding without ever passing through `Verifying`.
  const gateNote = landGateNote(request)
  // Beside the branch rather than after the state, because a verify-only row narrates
  // itself in a landing's exact words until it stops one step early.
  const kindNote = landKindNote(request)
  return <article class={`git-land-row ${landStateTone(request.state)}`}>
    <div class="git-land-row-head">
      {position !== undefined && <span class="git-land-position">{position}</span>}
      <strong>{request.branch}</strong>
      {kindNote && <small class="git-land-kind-note">{kindNote}</small>}
      <em class={`git-land-state ${landStateTone(request.state)}`}>{landStateLabel(request.state)}</em>
      {progress && <span class="git-land-progress-detail">{progress}</span>}
      {gateNote && <small class="git-land-gate-note">{gateNote}</small>}
      {request.origin !== 'operator' && <small class="git-land-origin">requested by agent</small>}
      {request.waitingSince && <small>{ago(request.waitingSince)} waiting</small>}
      {onCancel && (request.state === 'queued' || request.state === 'waiting')
        && <button disabled={busy} onClick={onCancel}>Cancel</button>}
    </div>
    {request.reason && <p class="git-land-reason">{request.reason}</p>}
    {request.paths.length > 0 && <ul class="git-land-paths">
      {request.paths.slice(0, 12).map(path => <li key={path}><code>{path}</code></li>)}
      {request.paths.length > 12 && <li><small>and {request.paths.length - 12} more</small></li>}
    </ul>}
    {request.landedOid && <small class="git-land-oid">
      {shortSha(request.trunkBefore)} → {shortSha(request.landedOid)}
      {request.finishedAt && request.createdAt
        && ` · ${formatDuration((request.finishedAt - request.createdAt) * 1000)}`}
    </small>}
  </article>
}
