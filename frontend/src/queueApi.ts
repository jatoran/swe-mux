import { api, type ApiError } from './api.ts'
import { serverNow } from './serverClock.ts'

// Phase 4: the persistent manual prompt queue. Every function here is a thin
// caller over the daemon's typed queue operations — the daemon owns ordering,
// revision checks, readiness, identity, and audit; the browser only asks.

export type QueueMessageState =
  | 'draft' | 'armed' | 'blocked' | 'delivering'
  | 'sent' | 'failed' | 'cancelled' | 'stranded'

/** Who authored a message. The daemon derives it; a client never claims it. */
export type QueueSenderKind = 'user' | 'remote_user' | 'rule' | 'agent' | 'queue_draft'

/** Phase 5 delivery constraints, carried on the item and honoured by both paths. */
export interface QueueConstraints {
  not_before?: number
  expires_at?: number
  /**
   * "now" asks for delivery into a turn that is already running, decided at
   * delivery time by the readiness tracker's strictly narrower interject
   * predicate - never by overriding ordinary readiness. Absent means what every
   * item meant before the mode existed: wait for the target to be idle.
   */
  delivery?: 'when_idle' | 'now'
}

export interface QueueMessage {
  id: string
  target_session_id: string
  target_agent_run_id: string | null
  target_backend: string | null
  target_label: string | null
  project_id: string | null
  position: number
  state: QueueMessageState
  body: string
  revision: number
  sender_kind: QueueSenderKind
  sender_id: string | null
  sender_label: string | null
  origin_session_id: string | null
  correlation_id: string | null
  chain_depth: number
  origin: { from_name?: string; reason?: string; path?: string[] } | null
  constraints: QueueConstraints | null
  blocked_reasons: string[] | null
  stranded_reason: string | null
  cancel_kind: 'cancelled' | 'skipped' | 'revoked' | 'expired' | null
  retargeted_from: { session_id: string; label: string | null } | null
  created_at: number
  updated_at: number
  edited_at: number | null
  armed_at: number | null
  sent_at: number | null
  target_live?: boolean
}

/** Runtime auto-delivery policy: one master switch, one pause, per-session opt-ins. */
export interface QueueAutoSession {
  session_id: string
  enabled: boolean
  accept_agent_messages: boolean
  accept_agent_interjections: boolean
  agent_run_id: string | null
  label: string | null
  live: boolean
  run_matches: boolean
  expires_in_s: number | null
  sends_used: number
  max_sends: number
  sends_remaining: number
  disabled_reason: string | null
  /** Present only while the grant is off *for idleness*, which is the one disable
   *  reason with no act behind it — so the only one nobody can look up afterwards.
   *  Fields are individually null on a row that lapsed before the audit existed. */
  lapse: {
    at: number | null
    idle_seconds: number | null
    window_minutes: number | null
    pending: number | null
  } | null
  /** Present while an active exchange is holding the idle lapse off. Two kinds, and
   *  they are the same fact about two pipes: `message` — this session's own message
   *  reached a peer recently and it is owed an answer; `land` — this session asked to
   *  land a branch and the pipeline has not answered yet. It authorizes nothing —
   *  every other gate still decides each send. An unrecognised `kind` reads as
   *  `message`, which is the shape every pre-`kind` row had. */
  reply_window: {
    kind?: string
    thread_id: string | null
    peer_session_id: string | null
    sent_at: number
    expires_at: number
    window_minutes: number
    thread_messages_used: number
    thread_messages_limit: number
    /** `land` only: which request is holding the window open. */
    request_id?: string
    branch?: string
    state?: string
  } | null
}

export interface QueueAutoStatus {
  master_enabled: boolean
  paused: boolean
  quiet_hours: { start: string; end: string; active: boolean }
  stable_seconds: number
  max_consecutive: number
  session_ttl_minutes: number
  reply_window_minutes: number
  sessions: QueueAutoSession[]
  counters: Record<string, number>
  promotion: {
    criteria: Record<string, boolean>
    met: boolean
    auto_sends: number
    unsafe_reports: number
    proving_days: number
    required_sends: number
    required_days: number
    fixture_classes: string[]
  }
  last_error: string
}

export interface QueueTargetView {
  target: {
    session_id: string
    live: boolean
    agent_run_id: string | null
    label: string | null
    state: string | null
  }
  messages: QueueMessage[]
  pending: number
}

export interface QueueTargetSummary {
  target_session_id: string
  label: string | null
  project_id: string | null
  backend: string | null
  pending: number
  blocked: number
  stranded: number
  failed: number
  total: number
  live: boolean
}

/** Who wrote a queued message. The fleet view's only partition — not direction, and not
 *  "mine vs theirs": provenance is derived by the daemon from transport and token, so it
 *  is the one property of a row a client can trust. */
export type FleetQueueAuthor = 'all' | 'non_human' | 'human'

export interface FleetQueueTarget {
  target_session_id: string
  label: string | null
  project_id: string | null
}

export interface SpawnRequestRow {
  id: string
  project_id: string
  project_name: string
  created_at: number
  done: boolean
  status: string
  prompt: string
  backend: string
  name: string
  reason: string
  from_session: string
  from_name: string
  from_run_id: string
  session_id: string | null
  decided_by: string | null
}

/** A Phase 7.6 drafted interrupt/end awaiting a human. Approval is what acts. */
export interface ControlRequestRow {
  id: string
  project_id: string
  project_name: string
  created_at: number
  done: boolean
  status: string
  action: string
  target_session_id: string
  target_name: string
  reason: string
  from_session: string
  from_name: string
  from_run_id: string
  outcome: string | null
  decided_by: string | null
}

export interface FleetQueueView {
  author: FleetQueueAuthor
  messages: QueueMessage[]
  spawn_requests: SpawnRequestRow[]
  spawn_request_errors: { project_id: string; error: string }[]
  control_requests: ControlRequestRow[]
  targets: FleetQueueTarget[]
}

/** States that hold a place in the strict head-of-line order. */
export const PENDING_QUEUE_STATES: readonly QueueMessageState[] =
  ['draft', 'armed', 'blocked', 'delivering']

export const isPendingQueueState = (state: QueueMessageState): boolean =>
  PENDING_QUEUE_STATES.includes(state)

/** The first pending message — the only one `send-next` will deliver. */
export function queueHead(messages: readonly QueueMessage[]): QueueMessage | null {
  const pending = messages.filter(item => isPendingQueueState(item.state))
  return pending.length ? pending.reduce((a, b) => (a.position <= b.position ? a : b)) : null
}

export const fetchQueueSummary = () =>
  api<{ targets: QueueTargetSummary[] }>('GET', '/api/queue', undefined, { timeoutMs: 10_000 })

export const fetchQueue = (sessionId: string) =>
  api<QueueTargetView>(
    'GET',
    `/api/queue/messages?target_session_id=${encodeURIComponent(sessionId)}`,
    undefined,
    { timeoutMs: 10_000 },
  )

export const enqueueMessage = (
  targetSessionId: string,
  body: string,
  options: { armed?: boolean; insertAfter?: string; constraints?: QueueConstraints } = {},
) =>
  api<QueueMessage>('POST', '/api/queue/messages', {
    target_session_id: targetSessionId,
    body,
    armed: options.armed ?? false,
    insert_after: options.insertAfter,
    constraints: options.constraints,
  })

/**
 * Schedule (or clear) a queued message. The constraint lives on the item, not
 * in this tab: a browser timer dies with the tab, and the daemon honours the
 * same constraint whether a human or the auto-delivery controller sends.
 */
export const scheduleQueueMessage = (
  messageId: string,
  constraints: QueueConstraints | null,
) => api<QueueMessage>('PATCH', `/api/queue/messages/${messageId}`, { constraints })

export const editQueueMessage = (messageId: string, revision: number, body: string) =>
  api<QueueMessage>('PATCH', `/api/queue/messages/${messageId}`, { body, revision })

export const armQueueMessage = (messageId: string, armed: boolean) =>
  api<QueueMessage>('PATCH', `/api/queue/messages/${messageId}`, { armed })

export const moveQueueMessage = (messageId: string, after: string | null) =>
  api<QueueMessage>('PATCH', `/api/queue/messages/${messageId}`, { after })

export const retargetQueueMessage = (messageId: string, targetSessionId: string) =>
  api<QueueMessage>('PATCH', `/api/queue/messages/${messageId}`, {
    retarget_session_id: targetSessionId,
  })

export const cancelQueueMessage = (
  messageId: string,
  kind: 'cancelled' | 'skipped' | 'revoked',
) => api<QueueMessage>('POST', `/api/queue/messages/${messageId}/cancel`, { kind })

export const deleteQueueMessage = (messageId: string) =>
  api<{ deleted: true; message_id: string; already_deleted: boolean }>(
    'DELETE',
    `/api/queue/messages/${messageId}`,
  )

// ---------------------------------------------------------------- Phase 5

export const fetchAutoStatus = () =>
  api<QueueAutoStatus>('GET', '/api/queue/auto', undefined, { timeoutMs: 10_000 })

/** The emergency disable. Persisted server-side; not a client-side toggle. */
export const setAutoPaused = (paused: boolean) =>
  api<QueueAutoStatus>('POST', '/api/queue/auto/pause', { paused })

export const setSessionAutoPolicy = (
  sessionId: string,
  patch: {
    enabled?: boolean
    ttlMinutes?: number
    maxSends?: number
    acceptAgentMessages?: boolean
    acceptAgentInterjections?: boolean
  },
) =>
  api<QueueAutoStatus>('PUT', `/api/queue/auto/sessions/${sessionId}`, {
    enabled: patch.enabled,
    ttl_minutes: patch.ttlMinutes,
    max_sends: patch.maxSends,
    accept_agent_messages: patch.acceptAgentMessages,
    accept_agent_interjections: patch.acceptAgentInterjections,
  })

/** Operator review: one confirmed bad automatic delivery resets the proving period. */
export const reportUnsafeDelivery = (note: string) =>
  api<QueueAutoStatus>('POST', '/api/queue/auto/report-unsafe', { note })

/**
 * Every queued message across every target, newest first, partitioned by authorship.
 *
 * The route keeps its original `mailbox` name on purpose: it is the same projection over
 * the same rows, and churning a daemon path for a UI rename would be a breaking change
 * bought with nothing. The surface is named for what it shows; the route is named for
 * when it was added.
 */
export const fetchFleetQueue = (
  author: FleetQueueAuthor,
  filters: { projectId?: string; targetSessionId?: string } = {},
) => {
  const query = new URLSearchParams({ author })
  if (filters.projectId) query.set('project_id', filters.projectId)
  if (filters.targetSessionId) query.set('target_session_id', filters.targetSessionId)
  return api<FleetQueueView>(
    'GET',
    `/api/queue/mailbox?${query.toString()}`,
    undefined,
    { timeoutMs: 10_000 },
  )
}

export const decideSpawnRequest = (
  projectId: string,
  requestId: string,
  decision: 'approve' | 'dismiss',
) => api<{ session?: { id: string; name: string } }>(
  'POST',
  `/api/projects/${encodeURIComponent(projectId)}/observations/${encodeURIComponent(requestId)}/decide`,
  { decision },
)

/** Approve (act) or dismiss a drafted interrupt/end. Same endpoint as spawn
 *  requests; approval performs the control action through the daemon operation. */
export const decideControlRequest = (
  projectId: string,
  requestId: string,
  decision: 'approve' | 'dismiss',
) => api<{ outcome?: string; final_state?: string }>(
  'POST',
  `/api/projects/${encodeURIComponent(projectId)}/observations/${encodeURIComponent(requestId)}/decide`,
  { decision },
)

/** `due` | `scheduled` | `expired` — mirrors the daemon's `schedule_status`.
 *
 *  On the daemon's clock, because "mirrors" is the contract: the constraints were
 *  written there and the daemon will act on them there, so a browser comparing
 *  them against its own clock can show a message as due while the daemon still
 *  holds it (or the reverse). */
export function scheduleStatus(message: QueueMessage, now = serverNow()): string {
  const expires = message.constraints?.expires_at
  if (typeof expires === 'number' && now >= expires) return 'expired'
  const notBefore = message.constraints?.not_before
  if (typeof notBefore === 'number' && now < notBefore) return 'scheduled'
  return 'due'
}

/** Short human label for a sender, used on queue rows and in the fleet queue. */
export function senderLabel(message: QueueMessage): string {
  switch (message.sender_kind) {
    case 'agent':
      return `from ${message.sender_label || message.sender_id || 'another agent'}`
    case 'remote_user':
      return 'from you (remote)'
    case 'rule':
    case 'queue_draft':
      return 'from an observer'
    default:
      return ''
  }
}

/** What a `send-next` attempt came to, with the daemon's typed refusals made explicit. */
export type QueueSendOutcome =
  | { status: 'sent'; confirmed: boolean }
  | { status: 'queued_behind'; blockingMessageId: string }
  | { status: 'blocked'; reasons: string[]; protected: boolean }
  | { status: 'stranded'; error: string }
  | { status: 'revision_conflict'; revision: number }
  | { status: 'not_due'; notBefore: number }
  | { status: 'expired'; error: string }
  | { status: 'error'; error: string }

/** Map a queue-operation ApiError to a typed outcome; unknown failures rethrow-as-error. */
export function mapQueueSendError(cause: unknown): QueueSendOutcome {
  const error = cause as ApiError & { detail?: Record<string, unknown> }
  const detail = error.detail ?? {}
  switch (String(detail.code || '')) {
    case 'head_of_line_blocked':
      return { status: 'queued_behind', blockingMessageId: String(detail.blocking_message_id || '') }
    case 'delivery_not_safe':
      return { status: 'blocked', reasons: (detail.reasons as string[]) || [], protected: false }
    case 'delivery_protected':
      return { status: 'blocked', reasons: (detail.reasons as string[]) || [], protected: true }
    case 'target_ended':
    case 'target_run_replaced':
      return { status: 'stranded', error: error.message }
    case 'revision_conflict':
      return { status: 'revision_conflict', revision: Number(detail.revision) || 0 }
    case 'delivery_not_due':
      return { status: 'not_due', notBefore: Number(detail.not_before) || 0 }
    case 'delivery_expired':
      return { status: 'expired', error: error.message }
    default:
      return { status: 'error', error: error instanceof Error ? error.message : String(cause) }
  }
}

export async function sendQueueMessage(
  messageId: string,
  revision: number,
  options: { confirm?: boolean; idempotencyKey?: string } = {},
): Promise<QueueSendOutcome> {
  try {
    const result = await api<{ status: string; confirmed?: boolean }>(
      'POST',
      '/api/queue/send-next',
      {
        message_id: messageId,
        revision,
        confirm: options.confirm ?? false,
        idempotency_key: options.idempotencyKey,
      },
    )
    return { status: 'sent', confirmed: !!result.confirmed }
  } catch (cause) {
    return mapQueueSendError(cause)
  }
}
