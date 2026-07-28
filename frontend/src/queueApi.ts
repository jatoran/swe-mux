import { api, type ApiError } from './api.ts'

// Phase 4: the persistent manual prompt queue. Every function here is a thin
// caller over the daemon's typed queue operations — the daemon owns ordering,
// revision checks, readiness, identity, and audit; the browser only asks.

export type QueueMessageState =
  | 'draft' | 'armed' | 'blocked' | 'delivering'
  | 'sent' | 'failed' | 'cancelled' | 'stranded'

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
  sender_kind: 'user' | 'queue_draft'
  sender_id: string | null
  blocked_reasons: string[] | null
  stranded_reason: string | null
  cancel_kind: 'cancelled' | 'skipped' | null
  retargeted_from: { session_id: string; label: string | null } | null
  created_at: number
  updated_at: number
  edited_at: number | null
  armed_at: number | null
  sent_at: number | null
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
  options: { armed?: boolean; insertAfter?: string } = {},
) =>
  api<QueueMessage>('POST', '/api/queue/messages', {
    target_session_id: targetSessionId,
    body,
    armed: options.armed ?? false,
    insert_after: options.insertAfter,
  })

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

export const cancelQueueMessage = (messageId: string, kind: 'cancelled' | 'skipped') =>
  api<QueueMessage>('POST', `/api/queue/messages/${messageId}/cancel`, { kind })

/** What a `send-next` attempt came to, with the daemon's typed refusals made explicit. */
export type QueueSendOutcome =
  | { status: 'sent'; confirmed: boolean }
  | { status: 'queued_behind'; blockingMessageId: string }
  | { status: 'blocked'; reasons: string[]; protected: boolean }
  | { status: 'stranded'; error: string }
  | { status: 'revision_conflict'; revision: number }
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
