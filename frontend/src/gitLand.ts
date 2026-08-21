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
  | 'already_landed'
  | 'handed_back'
  | 'refused'
  | 'cancelled'

const LAND_STATES: readonly LandState[] = [
  'queued', 'waiting', 'reconciling', 'verifying', 'landing',
  'landed', 'already_landed', 'handed_back', 'refused', 'cancelled',
]

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

export type LandRequest = {
  id: string
  projectId: string
  projectRoot: string
  worktreeRoot: string
  branch: string
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
  return !['landed', 'already_landed', 'handed_back', 'refused', 'cancelled'].includes(request.state)
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

export function landStateTone(state: LandState): 'ok' | 'warn' | 'busy' | 'idle' {
  if (state === 'landed') return 'ok'
  // Not 'ok': nothing moved. Not 'warn' either - nothing went wrong, the answer is
  // simply that the request was already true.
  if (state === 'already_landed') return 'idle'
  if (state === 'handed_back' || state === 'refused') return 'warn'
  if (state === 'reconciling' || state === 'verifying' || state === 'landing') return 'busy'
  return 'idle'
}
