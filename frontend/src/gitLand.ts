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
  | 'handed_back'
  | 'refused'
  | 'cancelled'

const LAND_STATES: readonly LandState[] = [
  'queued', 'waiting', 'reconciling', 'verifying', 'landing',
  'landed', 'handed_back', 'refused', 'cancelled',
]

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
}

export type LandQueue = {
  requests: LandRequest[]
  hourlyBudget: number
  holdTimeoutSeconds: number
  retryVerification: boolean
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
  }
}

export function parseLandQueue(raw: unknown): LandQueue {
  const empty: LandQueue = { requests: [], hourlyBudget: 0, holdTimeoutSeconds: 0, retryVerification: false }
  if (!raw || typeof raw !== 'object') return empty
  const body = raw as Record<string, unknown>
  const rows = Array.isArray(body.requests) ? body.requests : []
  return {
    requests: rows.map(parseRequest).filter((item): item is LandRequest => item !== null),
    hourlyBudget: num(body.hourly_budget),
    holdTimeoutSeconds: num(body.hold_timeout_seconds),
    retryVerification: body.retry_verification === true,
  }
}

export function parseLandVerifyCommand(raw: unknown): LandVerifyCommand {
  const empty: LandVerifyCommand = {
    configured: false, source: '', display: '', digest: '',
    approved: false, previouslyApproved: false, approvedSource: '', currentSource: '',
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
  }
}

/** Terminal states are history; the rest are the queue as it stands. */
export function isActiveLand(request: LandRequest): boolean {
  return !['landed', 'handed_back', 'refused', 'cancelled'].includes(request.state)
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
    case 'handed_back': return 'Returned to agent'
    case 'refused': return 'Refused'
    case 'cancelled': return 'Cancelled'
  }
}

export function landStateTone(state: LandState): 'ok' | 'warn' | 'busy' | 'idle' {
  if (state === 'landed') return 'ok'
  if (state === 'handed_back' || state === 'refused') return 'warn'
  if (state === 'reconciling' || state === 'verifying' || state === 'landing') return 'busy'
  return 'idle'
}
