// Phase 14 land queue: defensive parsing and the vocabulary the panel renders.
//
// Parsed rather than cast, like every other daemon response this drawer reads. A
// response shape that drifted would otherwise surface as a blank row rather than as a
// thing anyone can debug.

export type LandState =
  | 'queued'
  | 'waiting'
  | 'reconciling'
  | 'verifying'
  | 'landing'
  | 'landed'
  | 'verified'
  | 'already_landed'
  | 'handed_back'
  | 'refused'
  | 'cancelled'

const LAND_STATES: readonly LandState[] = [
  'queued', 'waiting', 'reconciling', 'verifying', 'landing',
  'landed', 'verified', 'already_landed', 'handed_back', 'refused', 'cancelled',
]

/**
 * What a request asked the pipeline for.
 *
 * `verify` runs everything a `land` runs except the fast-forward, so its rows move
 * through the same states and must be told apart by this rather than by inference from
 * where they stopped. Anything unrecognised reads as `land`, which is what every row
 * written before verify-only existed actually was.
 */
export type LandKind = 'land' | 'verify'

/**
 * A running gate's own reading of itself.
 *
 * Every field here was *observed*, and the shape is chosen so nothing can be rendered
 * as a guess. `stepIndex` counts the step markers the gate printed; `expectedStepCount`
 * is `null` unless a byte-identical run has already passed and recorded its steps, and
 * it goes back to `null` the moment a run overruns that plan. There is deliberately no
 * percentage anywhere: a gate whose steps take 175s and 3s in the same run has no
 * honest denominator, and inventing one is the exact failure this replaced.
 */
export type LandVerifyProgress = {
  /** Steps started. `0` means the gate announced none, not that it is on its first. */
  stepIndex: number
  stepName: string
  /** Only from a previous *passing* run of the same bytes. `null` means "not known". */
  expectedStepCount: number | null
  expectedSteps: string[]
  /** This run has passed the plan it was measured against, so prediction stopped. */
  beyondPlan: boolean
  completedSteps: { name: string; durationMs: number }[]
  /** Output lines. Evidence of movement, never progress toward an end. */
  lines: number
  elapsedMs: number
  stepElapsedMs: number | null
  attempt: number
  attempts: number
}

/**
 * Which gate a request ran, decided by the daemon from the change set's paths alone.
 *
 * `''` is "not classified yet", which is every row before it reaches the gate and every
 * row written by a build that predates the classifier. It is deliberately *not*
 * collapsed into `'full'`: a row that claims a classification nobody recorded is the
 * wrong direction for a field whose entire job is making a skipped gate visible.
 *
 * `'reused'` is that same job for the other skip: the full gate, run earlier by this
 * queue over this exact tree with these exact bytes, and therefore not run again here.
 */
export type LandGate = '' | 'full' | 'docs_only' | 'reused'

export type LandRequest = {
  id: string
  projectId: string
  projectRoot: string
  worktreeRoot: string
  branch: string
  /** What the request asked for. A `verify` row never moves a trunk. */
  kind: LandKind
  trunkRef: string
  origin: string
  originSessionId: string
  state: LandState
  reason: string
  paths: string[]
  waitingSince: number | null
  verifiedOid: string
  landedOid: string
  trunkBefore: string
  createdAt: number
  updatedAt: number
  finishedAt: number | null
  /** Present only while this row's gate is actually running under this daemon. */
  verifyProgress: LandVerifyProgress | null
  /** Which gate this land ran, once the daemon has classified its change set. */
  verifyGate: LandGate
}

/** The Project's authority for *agent*-initiated landing. An operator never needs it. */
export type LandGrant = 'draft' | 'granted'

export type LandQueue = {
  requests: LandRequest[]
  hourlyBudget: number
  holdTimeoutSeconds: number
  retryVerification: boolean
  /** The install-wide stop. With it off the sweep never runs, so a request enqueues and
   *  then sits at `queued` forever — which looked exactly like a busy queue. */
  installedEnabled: boolean
  /** Whether this Project opted into the land queue. Only agent requests are refused
   *  without it; the operator is the authority the opt-in defers to. */
  projectEnabled: boolean
  agentGrant: LandGrant
}

/** What a byte-identical run last did, when one has passed. Never a prediction of a
 *  gate nobody has watched finish. */
export type LandVerifyPlan = {
  steps: string[]
  durationMs: number
  observedAt: number
}

export type LandVerifyCommand = {
  configured: boolean
  source: string
  display: string
  digest: string
  approved: boolean
  previouslyApproved: boolean
  approvedSource: string
  currentSource: string
  /** The editable half: `[worktree] verify_command` as it stands, empty when unset. */
  configCommand: string
  /** The Project config's revision, echoed back on a write so a concurrent edit loses
   *  the race rather than being clobbered. */
  configRevision: string
  configStatus: string
  configPath: string
  scriptName: string
  scriptPresent: boolean
  plan: LandVerifyPlan | null
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function num(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function nullableNum(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function nonNegative(value: unknown): number {
  const parsed = num(value)
  return parsed > 0 ? parsed : 0
}

/**
 * A live gate reading, parsed so that every uncertain field lands on "unknown".
 *
 * The direction of every default here is deliberate: an absent or malformed
 * `expected_step_count` becomes `null` (rendered as a step number with no total) rather
 * than a number, because a wrong total is the one failure mode that makes progress
 * *less* trustworthy than the opaque "verifying" it replaced.
 */
export function parseVerifyProgress(raw: unknown): LandVerifyProgress | null {
  if (!raw || typeof raw !== 'object') return null
  const body = raw as Record<string, unknown>
  const expectedSteps = Array.isArray(body.expected_steps)
    ? body.expected_steps.filter((item): item is string => typeof item === 'string')
    : []
  const beyondPlan = body.beyond_plan === true
  const expectedCount = typeof body.expected_step_count === 'number'
    && Number.isFinite(body.expected_step_count)
    && body.expected_step_count > 0
    ? Math.floor(body.expected_step_count)
    : null
  const stepIndex = Math.floor(nonNegative(body.step_index))
  const completed = Array.isArray(body.completed_steps)
    ? body.completed_steps.flatMap(item => {
      if (!item || typeof item !== 'object') return []
      const step = item as Record<string, unknown>
      const name = text(step.name)
      return name ? [{ name, durationMs: nonNegative(step.duration_ms) }] : []
    })
    : []
  return {
    stepIndex,
    stepName: text(body.step_name),
    // A total that the run has already passed is not a total. Dropping it is what
    // stops "step 8 of 7" from ever being rendered.
    expectedStepCount: beyondPlan || expectedCount === null || expectedCount < stepIndex
      ? null
      : expectedCount,
    expectedSteps: beyondPlan ? [] : expectedSteps,
    beyondPlan,
    completedSteps: completed,
    lines: Math.floor(nonNegative(body.lines)),
    elapsedMs: nonNegative(body.elapsed_ms),
    stepElapsedMs: typeof body.step_elapsed_ms === 'number'
      && Number.isFinite(body.step_elapsed_ms) ? Math.max(0, body.step_elapsed_ms) : null,
    attempt: Math.max(1, Math.floor(nonNegative(body.attempt)) || 1),
    attempts: Math.max(1, Math.floor(nonNegative(body.attempts)) || 1),
  }
}

function parseRequest(raw: unknown): LandRequest | null {
  if (!raw || typeof raw !== 'object') return null
  const row = raw as Record<string, unknown>
  const id = text(row.id)
  const state = text(row.state) as LandState
  if (!id || !LAND_STATES.includes(state)) return null
  const detail = (row.detail && typeof row.detail === 'object' ? row.detail : {}) as Record<string, unknown>
  const paths = Array.isArray(detail.paths) ? detail.paths.filter((item): item is string => typeof item === 'string') : []
  return {
    id,
    projectId: text(row.project_id),
    projectRoot: text(row.project_root),
    worktreeRoot: text(row.worktree_root),
    branch: text(row.branch),
    // Unrecognised reads as `land`, which is the truth for every row written before
    // verify-only existed and the safe direction for one written after: a row drawn as
    // a land that only verified is a smaller lie than a land drawn as a verify.
    kind: row.kind === 'verify' ? 'verify' : 'land',
    trunkRef: text(row.trunk_ref),
    origin: text(row.origin) || 'operator',
    originSessionId: text(row.origin_session_id),
    state,
    reason: text(row.reason),
    paths,
    waitingSince: nullableNum(row.waiting_since),
    verifiedOid: text(row.verified_oid),
    landedOid: text(row.landed_oid),
    trunkBefore: text(row.trunk_before),
    createdAt: num(row.created_at),
    updatedAt: num(row.updated_at),
    finishedAt: nullableNum(row.finished_at),
    // Attached by the daemon only while this row's gate is running under it. A row that
    // a restart returned to `queued` carries none rather than a stale snapshot.
    verifyProgress: state === 'verifying' ? parseVerifyProgress(row.verify_progress) : null,
    // Anything the parser does not recognise reads as "not classified", never as a
    // gate that ran. An unknown value must not be able to render as `docs_only`.
    verifyGate: row.verify_gate === 'docs_only' ? 'docs_only'
      : row.verify_gate === 'reused' ? 'reused'
        : row.verify_gate === 'full' ? 'full' : '',
  }
}

export function parseLandQueue(raw: unknown): LandQueue {
  const empty: LandQueue = {
    requests: [], hourlyBudget: 0, holdTimeoutSeconds: 0, retryVerification: false,
    // An unparseable payload must not read as "the queue is switched off": the panel
    // would draw a gate over a queue that is running fine. Absent defaults to on, the
    // way the daemon's own field does.
    installedEnabled: true, projectEnabled: false, agentGrant: 'draft',
  }
  if (!raw || typeof raw !== 'object') return empty
  const body = raw as Record<string, unknown>
  const rows = Array.isArray(body.requests) ? body.requests : []
  return {
    requests: rows.map(parseRequest).filter((item): item is LandRequest => item !== null),
    hourlyBudget: num(body.hourly_budget),
    holdTimeoutSeconds: num(body.hold_timeout_seconds),
    retryVerification: body.retry_verification === true,
    installedEnabled: body.installed_enabled !== false,
    projectEnabled: body.project_enabled === true,
    agentGrant: body.agent_grant === 'granted' ? 'granted' : 'draft',
  }
}

function parseVerifyPlan(raw: unknown): LandVerifyPlan | null {
  if (!raw || typeof raw !== 'object') return null
  const body = raw as Record<string, unknown>
  const steps = Array.isArray(body.steps)
    ? body.steps.filter((item): item is string => typeof item === 'string')
    : []
  if (!steps.length) return null
  return {
    steps,
    durationMs: nonNegative(body.duration_ms),
    observedAt: num(body.observed_at),
  }
}

export function parseLandVerifyCommand(raw: unknown): LandVerifyCommand {
  const empty: LandVerifyCommand = {
    configured: false, source: '', display: '', digest: '',
    approved: false, previouslyApproved: false, approvedSource: '', currentSource: '',
    configCommand: '', configRevision: 'missing', configStatus: 'missing', configPath: '',
    scriptName: '.worktree-verify', scriptPresent: false, plan: null,
  }
  if (!raw || typeof raw !== 'object') return empty
  const body = raw as Record<string, unknown>
  return {
    configured: body.configured === true,
    source: text(body.source),
    display: text(body.display),
    digest: text(body.digest),
    approved: body.approved === true,
    previouslyApproved: body.previously_approved === true,
    approvedSource: text(body.approved_source),
    currentSource: text(body.current_source),
    configCommand: text(body.config_command),
    // "missing" rather than "" so a write always carries a revision the daemon can
    // compare; an empty string would read as "no expectation" and skip the guard.
    configRevision: text(body.config_revision) || 'missing',
    configStatus: text(body.config_status) || 'missing',
    configPath: text(body.config_path),
    scriptName: text(body.script_name) || '.worktree-verify',
    scriptPresent: body.script_present === true,
    plan: parseVerifyPlan(body.plan),
  }
}

/** Whether this Project's config can be written at all, in the editor's terms. */
export function verifyCommandEditable(gate: LandVerifyCommand): boolean {
  return gate.configStatus !== 'read-only' && gate.configStatus !== 'malformed'
}

/** Terminal states are history; the rest are the queue as it stands. */
export function isActiveLand(request: LandRequest): boolean {
  return !['landed', 'verified', 'already_landed', 'handed_back', 'refused', 'cancelled']
    .includes(request.state)
}

/**
 * `verify only`, or `''` for an ordinary land.
 *
 * Only the verify-only case is named, for the same reason only a *skipped* gate is: a
 * land is what a row in this queue is unless something says otherwise, so labelling it
 * would put a redundant chip on every row and bury the one that matters. A verify-only
 * row, meanwhile, passes through the same states with the same words - `Merging trunk`,
 * `Verifying` - and without this reads as a landing right up until it stops one step
 * early, which is exactly when nobody is still watching.
 */
export function landKindNote(request: LandRequest): string {
  return request.kind === 'verify' ? 'verify only' : ''
}

/**
 * What a row is doing, in the operator's words rather than the schema's.
 *
 * `waiting` deliberately reads as a hold with its cause rather than as a failure: an
 * agent that asked to land and kept working is the common case, and a row that looked
 * broken for it would train the operator to intervene where nothing is wrong.
 */
export function landStateLabel(state: LandState): string {
  switch (state) {
    case 'queued': return 'Queued'
    case 'waiting': return 'Waiting'
    case 'reconciling': return 'Merging trunk'
    case 'verifying': return 'Verifying'
    case 'landing': return 'Fast-forwarding'
    case 'landed': return 'Landed'
    // Not 'Landed', because nothing moved. A verify-only request's green is its own
    // answer, and reporting a land would claim a trunk movement that did not happen.
    case 'verified': return 'Verified'
    case 'already_landed': return 'Already on trunk'
    case 'handed_back': return 'Returned to agent'
    case 'refused': return 'Refused'
    case 'cancelled': return 'Cancelled'
  }
}

/**
 * Queue order: the order the pipeline will actually reach these, oldest first.
 *
 * The daemon lists newest-first because that is what a history read wants. A queue read
 * backwards puts the request about to run at the bottom, which is the one place a
 * reader looks last.
 */
export function landQueueOrder(requests: LandRequest[]): LandRequest[] {
  return requests.filter(isActiveLand).sort((left, right) => left.createdAt - right.createdAt)
}

/** Terminal rows, newest first: this is history, and history reads backwards. */
export function landHistoryOrder(requests: LandRequest[]): LandRequest[] {
  return requests.filter(request => !isActiveLand(request))
    .sort((left, right) => (right.finishedAt || right.updatedAt) - (left.finishedAt || left.updatedAt))
}

/**
 * The states that count as *the branch got an answer*, for supersession.
 *
 * `cancelled` is deliberately absent even though it is terminal. Withdrawing a re-request
 * is not an answer about the branch, so an earlier handback it followed is still the
 * standing fact about that branch and must keep speaking for it.
 *
 * `verified` *is* present, and has to be. A verify-only run is how an agent answers a
 * bounced gate before asking to land again, so without it the redo loop the handback
 * asks for is exactly the loop that cannot close the handback - which is the defect this
 * rule was written for, reappearing one request kind over.
 */
const ANSWERING_STATES: readonly LandState[] = [
  'landed', 'verified', 'already_landed', 'handed_back', 'refused',
]

/** The durable uniqueness key a request is claimed under (`land_requests_active`). */
function branchKey(request: LandRequest): string {
  return `${request.projectRoot} ${request.branch}`
}

/**
 * The bounce that still stands, or `null`.
 *
 * A handed-back or refused request is a *terminal* row, and nothing ever closes it: the
 * agent's redo is a **new** request with a new id, so the old bounced row sits in the
 * history forever and the summary, which picks the most interesting terminal row, keeps
 * resurrecting it. Observed 2026-08-21: the collapsed strip read
 * `worktree-watch-session-settle · returned to agent` for hours after that very branch's
 * redo had landed, through several unrelated landings.
 *
 * The supersession rule: a bounced request for a branch stops speaking for the queue the
 * moment a **later** request for the same branch reaches a state that answered the branch.
 * Ties do not supersede - two rows created in the same second are not ordered by anything
 * this can read, and the safe direction for a row whose whole job is asking for attention
 * is to keep asking.
 *
 * It is derived here rather than written back onto the row on purpose. The handback really
 * did happen, and `land_events` and the history disclosure are an audit that must go on
 * saying so; what was wrong was never the record, only which row speaks for the queue.
 * That also keeps the fix out of the daemon, where a "closed" column would be a second
 * writer's opinion about a terminal row.
 */
export function landAttentionRow(requests: LandRequest[]): LandRequest | null {
  const answeredAt = new Map<string, number>()
  for (const request of requests) {
    if (!ANSWERING_STATES.includes(request.state)) continue
    const key = branchKey(request)
    const seen = answeredAt.get(key)
    if (seen === undefined || request.createdAt > seen) answeredAt.set(key, request.createdAt)
  }
  return landHistoryOrder(requests).find(request =>
    (request.state === 'handed_back' || request.state === 'refused')
    && (answeredAt.get(branchKey(request)) ?? request.createdAt) <= request.createdAt) || null
}

/** How far back the idle summary's "recently" reaches, in seconds. */
const RECENT_LANDING_SECONDS = 24 * 60 * 60

/**
 * Lands that actually moved the trunk inside the recent window - a floor, not a total.
 *
 * Two bounds make it one, and both are the honest direction: `GET /api/land` returns the
 * newest 100 rows for the Project, and `already_landed` is not counted because nothing
 * moved. It is drawn only where the alternative is a bare "nothing queued", so a count
 * that under-reports says less rather than something untrue.
 */
export function recentLandings(requests: LandRequest[], now: number): number {
  const since = now - RECENT_LANDING_SECONDS
  return requests.filter(request => request.state === 'landed'
    && (request.finishedAt || request.updatedAt) >= since).length
}

/** A duration a human reads at a glance: `9s`, `1m 12s`, `1h 04m`. */
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000))
  if (total < 60) return `${total}s`
  if (total < 3600) return `${Math.floor(total / 60)}m ${String(total % 60).padStart(2, '0')}s`
  return `${Math.floor(total / 3600)}h ${String(Math.floor((total % 3600) / 60)).padStart(2, '0')}m`
}

/**
 * What a running gate is doing, stated only from what was observed.
 *
 * Three honest forms, in descending order of what the gate told us about itself:
 *
 *  - `step 3 of 7 · mypy · 3m 10s` — the gate announces steps, and a byte-identical run
 *    has passed before, so the total is a measurement rather than an estimate.
 *  - `step 3 · mypy · 3m 10s` — it announces steps and no such run is on record. The
 *    step number is still a fact; the total is simply not known and is not invented.
 *  - `4m 12s · 1,204 lines` — it announces nothing, so the only truthful statement is
 *    that it is still producing output.
 *
 * Never a percentage. The steps of this repository's own gate take 175s and 3s, so any
 * denominator would be fiction, and "20/37" of a script's *lines* would be worse: the
 * lines are not the work.
 */
export function verifyProgressLabel(progress: LandVerifyProgress | null): string {
  if (!progress) return ''
  const parts: string[] = []
  if (progress.stepIndex > 0) {
    parts.push(progress.expectedStepCount
      ? `step ${progress.stepIndex} of ${progress.expectedStepCount}`
      : `step ${progress.stepIndex}`)
    if (progress.stepName) parts.push(progress.stepName)
  }
  parts.push(formatDuration(progress.elapsedMs))
  if (progress.stepIndex === 0) {
    parts.push(`${progress.lines.toLocaleString()} line${progress.lines === 1 ? '' : 's'}`)
  }
  if (progress.attempts > 1) parts.push(`attempt ${progress.attempt} of ${progress.attempts}`)
  return parts.join(' · ')
}

/**
 * A one-line note about the gate's own history, or ''.
 *
 * Kept apart from the progress label because it is a statement about a *previous* run.
 * Merging the two would let "took 4m 30s" read as a prediction of the one on screen.
 */
export function verifyPlanNote(plan: LandVerifyPlan | null): string {
  if (!plan || !plan.steps.length) return ''
  const steps = `${plan.steps.length} step${plan.steps.length === 1 ? '' : 's'}`
  return plan.durationMs > 0
    ? `Last passing run of these exact bytes: ${steps} in ${formatDuration(plan.durationMs)}.`
    : `Last passing run of these exact bytes: ${steps}.`
}

/**
 * A note about which gate a land ran, or '' when there is nothing worth saying.
 *
 * **Only the skip is drawn.** A full gate is what every land does and what the states
 * already narrate — `Verifying`, with the running gate's own step under it — so labelling
 * it would put a redundant chip on every row. A *skipped* gate has no state of its own to
 * show it: the row goes from merging the trunk straight to fast-forwarding, and finishes
 * reading exactly like one that passed three minutes of pytest. That is the one thing
 * here that must never be silent, so it is the one thing that gets a line.
 *
 * A **reused** verdict is the same defect wearing a different cause — a gate that did not
 * run on this row — so it gets the same treatment and says which cause it was. The two are
 * not interchangeable to a reader deciding whether to trust the row: one means nobody has
 * ever run this content through the suite, the other means this queue ran exactly it.
 */
export function landGateNote(request: LandRequest): string {
  if (request.verifyGate === 'docs_only') return 'documentation only · verification skipped'
  if (request.verifyGate === 'reused') return 'verified earlier · gate reused'
  return ''
}

export function landStateTone(state: LandState): 'ok' | 'warn' | 'busy' | 'idle' {
  if (state === 'landed' || state === 'verified') return 'ok'
  // Not 'ok': nothing moved. Not 'warn' either - nothing went wrong, the answer is
  // simply that the request was already true.
  if (state === 'already_landed') return 'idle'
  if (state === 'handed_back' || state === 'refused') return 'warn'
  if (state === 'reconciling' || state === 'verifying' || state === 'landing') return 'busy'
  return 'idle'
}

export type LandingSummary = {
  /** The gate's standing, in five words or so. */
  gate: string
  gateTone: 'ok' | 'warn' | 'idle'
  /** What the queue is doing, right now, including a running gate's own step. */
  queue: string
  tone: 'ok' | 'warn' | 'busy' | 'idle'
  /**
   * Nothing on this tab can land until a human acts here.
   *
   * Exactly two causes, and both have their remedy inside the landing strip: the
   * install-wide sweep is off, or the bytes a land would run are not approved. It is
   * what opens the strip by default, because a surface that cannot work must not render
   * as merely quiet (`setting-links.md`).
   */
  blocked: boolean
}

/**
 * The one line the landing strip shows without being opened.
 *
 * It exists because the strip sits above a map and must not turn the map into a panel:
 * a reader who is not landing anything should be able to ignore it after reading one
 * line, and a reader who *is* should not have to open it to see that a gate is running.
 *
 * Both halves are stated even when they are boring. "Nothing queued" is a fact worth
 * reading; leaving it blank would make a quiet queue and an unread one look the same,
 * which is the same conflation the `installed_enabled` reporting exists to prevent.
 */
export function landingSummary(
  queue: LandQueue | null,
  gate: LandVerifyCommand | null,
  /** Epoch **seconds**, matching every timestamp on a request row. Injected by tests. */
  now: number = Date.now() / 1000,
): LandingSummary {
  const installStopped = queue !== null && !queue.installedEnabled
  const gateBlocked = gate !== null && (!gate.configured || !gate.approved)
  const blocked = installStopped || gateBlocked
  const gateText = gate === null ? 'verification unread'
    : !gate.configured ? 'no verification command'
      : gate.approved ? `verification approved · ${gate.display}`
        : 'verification not approved'
  const gateTone: 'ok' | 'warn' | 'idle' = gate === null ? 'idle'
    : gate.configured && gate.approved ? 'ok' : 'warn'

  const active = landQueueOrder(queue?.requests || [])
  const running = active.find(request => landStateTone(request.state) === 'busy') || null
  // Only a bounce nothing has answered since. Without the supersession rule this is the
  // stalest handback in the whole history, forever.
  const attention = landAttentionRow(queue?.requests || [])

  let queueText: string
  let tone: LandingSummary['tone']
  if (installStopped) {
    // Loudest, because it is the one state where a queue can look busy and be inert.
    queueText = 'the sweep is switched off — nothing will move'
    tone = 'warn'
  } else if (running) {
    const progress = verifyProgressLabel(running.verifyProgress)
    const behind = active.length - 1
    // The gate note, not the progress label, is what a skipping run has to say about
    // itself: it has no `verifying` state to report a step from, and without this the
    // strip narrates a land that quietly went round the gate as an ordinary one.
    const gateNote = landGateNote(running)
    // Before the state, not after it: a verify-only row runs through `Merging trunk`
    // and `Verifying` word for word like a landing, so the qualification has to reach
    // the reader before the words it qualifies.
    const kindNote = landKindNote(running)
    queueText = `${running.branch}`
      + (kindNote ? ` · ${kindNote}` : '')
      + ` · ${landStateLabel(running.state).toLowerCase()}`
      + (progress ? ` · ${progress}` : '')
      + (gateNote ? ` · ${gateNote}` : '')
      + (behind > 0 ? ` · ${behind} behind` : '')
    tone = 'busy'
  } else if (active.length) {
    queueText = `${active.length} queued · next ${active[0].branch}`
    tone = 'idle'
  } else if (attention) {
    const kindNote = landKindNote(attention)
    queueText = `${attention.branch}`
      + (kindNote ? ` · ${kindNote}` : '')
      + ` · ${landStateLabel(attention.state).toLowerCase()}`
    tone = 'warn'
  } else {
    // Idle, and idle is what it says. The count is here because the alternative reading -
    // "surely something is stuck, it said nothing" - is exactly what the resurrected
    // handback used to answer, wrongly.
    const landed = recentLandings(queue?.requests || [], now)
    queueText = landed > 0 ? `nothing queued · ${landed} landed recently` : 'nothing queued'
    tone = gateTone === 'warn' ? 'warn' : 'idle'
  }
  return { gate: gateText, gateTone, queue: queueText, tone, blocked }
}
