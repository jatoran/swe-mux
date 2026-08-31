/**
 * The demo's control plane, as a small stateful daemon rather than four constants.
 *
 * The prompt queue, the notification history, the fleet queue's spawn requests and the
 * land queue all used to answer with a fixed, correct, empty shape (`supportPayloads.ts`),
 * which was exactly right while nothing could fill them. The scenario director changed
 * that: the control plane is the half of swe-mux a terminal recording cannot show, and a
 * surface that cannot change cannot demonstrate it. So each one is a reducer over the
 * shared demo store now, which also means a scenario running in one frame reaches the
 * other for free - a payload builder mutating a module-level constant would not.
 *
 * Two rules carried over from the failure that produced `supportPayloads.ts`, and neither
 * is negotiable here:
 *
 * - **Every payload is complete.** A view rendering `payload.items.map(...)` throws on a
 *   partial one, a throw tears the Preact tree down, and the visitor gets a frozen page.
 *   Empty *lists* are fine; missing ones are a crash.
 * - **Every field is invented.** Same rule as the screenshots: no branch, path, session
 *   name or number here may come from a real install.
 */
import type {
  FleetQueueAuthor, QueueAutoSession, QueueMessage, QueueMessageState, SpawnRequestRow,
} from '../queueApi.ts'
import { scriptedTurn } from './fakeSocket.ts'
import { DEMO_PROJECT_ID } from './fixtures.ts'
import { apply, demoId, nowSeconds, session, state, type DemoLandRequest } from './store.ts'

/** Authored by something other than a person at a keyboard. The fleet queue's only
 *  partition, and the daemon derives it from transport rather than from a claim. */
const NON_HUMAN: readonly string[] = ['agent', 'rule', 'queue_draft']

const label = (sessionId: string): string | null => session(sessionId)?.name ?? null

// ------------------------------------------------------------------ prompt queue

/** States that hold a place in the strict head-of-line order. Mirrors `queueApi`'s own
 *  list; kept local so this module answers the daemon's question rather than the
 *  client's reading of it. */
const PENDING: readonly QueueMessageState[] = ['draft', 'armed', 'blocked', 'delivering']

const pending = (message: QueueMessage): boolean => PENDING.includes(message.state)

/** Build a queue row. Every field the reader touches is present, including the ones a
 *  demo never sets, because `null` renders and `undefined` throws one level down. */
export function makeQueueMessage(input: {
  targetSessionId: string
  body: string
  state?: QueueMessageState
  senderKind?: QueueMessage['sender_kind']
  senderLabel?: string | null
  originSessionId?: string | null
  reason?: string
  id?: string
}): QueueMessage {
  const target = session(input.targetSessionId)
  const now = nowSeconds()
  const kind = input.senderKind ?? 'user'
  const position = state.queue.filter(item => item.target_session_id === input.targetSessionId).length + 1
  return {
    id: input.id ?? demoId('q'),
    target_session_id: input.targetSessionId,
    target_agent_run_id: target?.agent_run_id ?? null,
    target_backend: target?.backend ?? null,
    target_label: target?.name ?? null,
    project_id: target?.project_id ?? null,
    position,
    state: input.state ?? 'draft',
    body: input.body,
    revision: 1,
    sender_kind: kind,
    sender_id: input.originSessionId ?? null,
    sender_label: input.senderLabel ?? null,
    origin_session_id: input.originSessionId ?? null,
    correlation_id: null,
    chain_depth: kind === 'agent' ? 1 : 0,
    origin: input.reason
      ? { from_name: input.senderLabel ?? undefined, reason: input.reason }
      : null,
    constraints: null,
    blocked_reasons: null,
    stranded_reason: null,
    cancel_kind: null,
    retargeted_from: null,
    created_at: now,
    updated_at: now,
    edited_at: null,
    armed_at: input.state === 'armed' ? now : null,
    sent_at: null,
    target_live: Boolean(target),
  }
}

/** `GET /api/queue`: one row per target that has anything queued. */
export function queueSummaryPayload(): unknown {
  const byTarget = new Map<string, QueueMessage[]>()
  for (const message of state.queue) {
    const found = byTarget.get(message.target_session_id)
    if (found) found.push(message)
    else byTarget.set(message.target_session_id, [message])
  }
  return {
    targets: [...byTarget.entries()].map(([targetId, messages]) => ({
      target_session_id: targetId,
      label: label(targetId),
      project_id: session(targetId)?.project_id ?? null,
      backend: session(targetId)?.backend ?? null,
      pending: messages.filter(pending).length,
      blocked: messages.filter(item => item.state === 'blocked').length,
      stranded: messages.filter(item => item.state === 'stranded').length,
      failed: messages.filter(item => item.state === 'failed').length,
      total: messages.length,
      live: Boolean(session(targetId)),
    })),
  }
}

/** `GET /api/queue/messages?target_session_id=`. */
export function queueMessagesPayload(sessionId: string): unknown {
  const target = session(sessionId)
  const messages = state.queue
    .filter(item => item.target_session_id === sessionId)
    .sort((left, right) => left.position - right.position)
  return {
    target: {
      session_id: sessionId,
      live: Boolean(target),
      agent_run_id: target?.agent_run_id ?? null,
      label: target?.name ?? null,
      state: target?.state ?? null,
      delivery_readiness: target?.delivery_readiness ?? null,
    },
    messages,
    pending: messages.filter(pending).length,
  }
}

/** `GET /api/queue/mailbox?author=`: every queued row across every target. */
export function queueMailboxPayload(author: string): unknown {
  const wanted = (author || 'non_human') as FleetQueueAuthor
  const messages = state.queue.filter(message => {
    if (wanted === 'all') return true
    const nonHuman = NON_HUMAN.includes(message.sender_kind)
    return wanted === 'non_human' ? nonHuman : !nonHuman
  })
  return {
    author: wanted,
    // Newest first, which is what the fleet view draws and what makes an arriving
    // request appear at the top rather than somewhere down the list.
    messages: [...messages].sort((left, right) => right.created_at - left.created_at),
    spawn_requests: [...state.spawnRequests].sort((left, right) => right.created_at - left.created_at),
    spawn_request_errors: [],
    control_requests: [],
    targets: state.sessions.map(item => ({
      target_session_id: item.id,
      label: item.name,
      project_id: item.project_id,
    })),
  }
}

/**
 * `GET /api/queue/auto`: the runtime auto-delivery policy.
 *
 * The master switch is read off the demo's own config rather than written here, so the
 * Settings row and this panel cannot disagree, and the per-session opt-ins are store
 * state because turning one on is the act that makes "the queued prompt delivered by
 * itself" a demonstration rather than a claim.
 */
export function queueAutoPayload(): unknown {
  const sessions: QueueAutoSession[] = state.sessions
    .filter(item => item.backend !== 'shell')
    .map(item => {
      const enabled = state.autoDelivery.includes(item.id)
      return {
        session_id: item.id,
        enabled,
        accept_agent_messages: enabled,
        accept_agent_interjections: false,
        agent_run_id: item.agent_run_id ?? null,
        label: item.name,
        live: true,
        run_matches: true,
        expires_in_s: enabled ? 120 * 60 : null,
        sends_used: 0,
        max_sends: 10,
        sends_remaining: 10,
        disabled_reason: enabled ? null : 'not opted in',
        lapse: null,
        reply_window: null,
      }
    })
  return {
    master_enabled: state.config.auto_delivery_enabled !== false,
    paused: false,
    quiet_hours: { start: '', end: '', active: false },
    stable_seconds: Number(state.config.auto_delivery_stable_seconds ?? 8),
    max_consecutive: Number(state.config.auto_delivery_max_consecutive ?? 10),
    session_ttl_minutes: Number(state.config.auto_delivery_session_ttl_minutes ?? 120),
    reply_window_minutes: Number(state.config.auto_delivery_reply_window_minutes ?? 60),
    sessions,
    counters: { auto_sends: state.queue.filter(item => item.state === 'sent').length },
    promotion: {
      criteria: {}, met: false, auto_sends: 0, unsafe_reports: 0,
      proving_days: 0, required_sends: 50, required_days: 7, fixture_classes: [],
    },
    last_error: '',
  }
}

/**
 * Deliver a queued message into its target pane.
 *
 * Delivery is *typing*, not a special channel: the daemon puts the body into the
 * session's own input, which is why a queued prompt is answered exactly as one the human
 * typed would be. Going through `term-input` here means the demo inherits that for free -
 * the composer fills, the turn opens, the transcript and the scan timeline both record
 * it - instead of a second, parallel path that could tell a different story.
 */
export function deliverQueuedMessage(messageId: string, reply?: string[]): boolean {
  const message = state.queue.find(item => item.id === messageId)
  if (!message || !pending(message)) return false
  if (!session(message.target_session_id)) {
    apply({
      kind: 'queue-patch', id: messageId,
      patch: { state: 'stranded', stranded_reason: 'the target session ended' },
    })
    return false
  }
  apply({ kind: 'queue-patch', id: messageId, patch: { state: 'sent', sent_at: nowSeconds() } })
  if (reply) {
    // A scenario delivers a *specific* prompt, so it supplies the answer too: routing it
    // through the joke responder would have the pane reply to "open a land request" with
    // whichever joke came next, which reads as the demo not following its own script.
    scriptedTurn({ id: message.target_session_id, prompt: message.body, reply })
  } else {
    apply({ kind: 'term-input', id: message.target_session_id, data: `${message.body}\r` })
  }
  return true
}

// ------------------------------------------------------------------ notifications

/** `GET /api/notifications`. `deliveries` and `notifications` stay empty: the demo has no
 *  push provider and no universal hook, and inventing a delivery record would claim one. */
export function notificationsPayload(): unknown {
  return {
    notifications: [],
    deliveries: [],
    automation: [...state.notifications].sort((left, right) => left.created_at - right.created_at),
  }
}

// --------------------------------------------------------------------- land queue

/** `GET /api/land?project_id=`. */
export function landPayload(projectId: string): unknown {
  return {
    hourly_budget: Number(state.config.land_hourly_budget ?? 12),
    hold_timeout_seconds: Number(state.config.land_hold_timeout_seconds ?? 1800),
    retry_verification: false,
    installed_enabled: true,
    // Enabled for the Project the demo's worktrees belong to, and off for the other one,
    // because "per Project, off by default" is the product's actual posture and a demo
    // that enabled it everywhere would misstate it.
    project_enabled: projectId === DEMO_PROJECT_ID,
    agent_grant: 'draft',
    verify_grant: 'granted',
    requests: state.lands
      .filter(item => item.project_id === projectId)
      .map(item => ({
        ...item,
        landed_at: item.landed_at ?? null,
        error: item.error ?? '',
        verification: item.verification ?? null,
      }))
      .sort((left, right) => right.created_at - left.created_at),
  }
}

/** `GET /api/land/{id}/events`, which `LandMessage` reads to explain a finished row. */
export function landEventsPayload(requestId: string): unknown {
  const request = state.lands.find(item => item.id === requestId)
  return { request_id: requestId, events: request?.events ?? [] }
}

/** Build a land request in its first state. The trail starts with the request itself, so
 *  a row can always say how it got where it is. */
export function makeLandRequest(input: {
  projectId: string
  branch: string
  worktreeRoot: string
  requestedBy: string
  id?: string
}): DemoLandRequest {
  const now = nowSeconds()
  return {
    id: input.id ?? demoId('land'),
    project_id: input.projectId,
    branch: input.branch,
    worktree_root: input.worktreeRoot,
    state: 'queued',
    requested_by: input.requestedBy,
    requested_by_name: label(input.requestedBy) ?? input.requestedBy,
    created_at: now,
    updated_at: now,
    landed_at: null,
    events: [{ at: now, state: 'queued', note: 'Requested from the session standing in this checkout.' }],
  }
}

// ------------------------------------------------------------------ spawn requests

/** A drafted new-session request, awaiting a human. Approval is what acts; drafting
 *  starts nothing, which is the boundary the surface exists to draw. */
export function makeSpawnRequest(input: {
  projectId: string
  projectName: string
  prompt: string
  name: string
  reason: string
  backend: string
  fromSession: string
  model?: string
  id?: string
}): SpawnRequestRow {
  return {
    id: input.id ?? demoId('spawn'),
    project_id: input.projectId,
    project_name: input.projectName,
    created_at: nowSeconds(),
    done: false,
    status: 'pending',
    prompt: input.prompt,
    backend: input.backend,
    model: input.model ?? '',
    name: input.name,
    reason: input.reason,
    from_session: input.fromSession,
    from_name: label(input.fromSession) ?? input.fromSession,
    from_run_id: session(input.fromSession)?.agent_run_id ?? '',
    session_id: null,
    decided_by: null,
  }
}
