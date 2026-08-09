import assert from 'node:assert/strict'
import test from 'node:test'
import {
  deleteQueueMessage,
  isPendingQueueState,
  mapQueueSendError,
  queueHead,
  scheduleStatus,
  senderLabel,
  type QueueMessage,
} from '../src/queueApi.ts'

test('delete uses the distinct queue DELETE contract', async () => {
  const realFetch = globalThis.fetch
  let request: { input: string; method: string | undefined; body: BodyInit | null | undefined } | null = null
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    request = { input: String(input), method: init?.method, body: init?.body }
    return new Response(
      JSON.stringify({ deleted: true, message_id: 'm1', already_deleted: false }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )
  }) as typeof fetch
  try {
    assert.deepEqual(await deleteQueueMessage('m1'), {
      deleted: true,
      message_id: 'm1',
      already_deleted: false,
    })
    assert.deepEqual(request, {
      input: '/api/queue/messages/m1',
      method: 'DELETE',
      body: undefined,
    })
  } finally {
    globalThis.fetch = realFetch
  }
})

const message = (id: string, position: number, state: QueueMessage['state']): QueueMessage => ({
  id,
  target_session_id: 's1',
  target_agent_run_id: 'run-1',
  target_backend: 'claude',
  target_label: 'claude s1',
  project_id: 'p1',
  position,
  state,
  body: `body ${id}`,
  revision: 1,
  sender_kind: 'user',
  sender_id: null,
  sender_label: null,
  origin_session_id: null,
  correlation_id: null,
  chain_depth: 0,
  origin: null,
  constraints: null,
  blocked_reasons: null,
  stranded_reason: null,
  cancel_kind: null,
  retargeted_from: null,
  created_at: 0,
  updated_at: 0,
  edited_at: null,
  armed_at: null,
  sent_at: null,
})

test('the queue head is the earliest pending item, ignoring terminal states', () => {
  const items = [
    message('sent', 0, 'sent'),
    message('cancelled', 1, 'cancelled'),
    message('head', 2, 'blocked'),
    message('later', 3, 'armed'),
  ]
  assert.equal(queueHead(items)?.id, 'head')
  assert.equal(queueHead(items.filter(item => item.state === 'sent')), null)
  assert.equal(queueHead([]), null)
})

test('pending states are exactly the ones that hold a head-of-line place', () => {
  for (const state of ['draft', 'armed', 'blocked', 'delivering'] as const) {
    assert.ok(isPendingQueueState(state), state)
  }
  for (const state of ['sent', 'failed', 'cancelled', 'stranded'] as const) {
    assert.ok(!isPendingQueueState(state), state)
  }
})

const apiError = (detail: Record<string, unknown>): Error => {
  const error = new Error(String(detail.error || 'failed')) as Error & { detail?: unknown }
  error.detail = detail
  return error
}

test('typed daemon refusals map to typed outcomes', () => {
  assert.deepEqual(
    mapQueueSendError(apiError({ code: 'head_of_line_blocked', blocking_message_id: 'm1' })),
    { status: 'queued_behind', blockingMessageId: 'm1' },
  )
  assert.deepEqual(
    mapQueueSendError(apiError({ code: 'delivery_not_safe', reasons: ['root_agent_working'] })),
    { status: 'blocked', reasons: ['root_agent_working'], protected: false },
  )
  assert.deepEqual(
    mapQueueSendError(apiError({ code: 'delivery_protected', reasons: ['approval_required'] })),
    { status: 'blocked', reasons: ['approval_required'], protected: true },
  )
  assert.equal(mapQueueSendError(apiError({ code: 'target_ended', error: 'gone' })).status, 'stranded')
  assert.deepEqual(
    mapQueueSendError(apiError({ code: 'revision_conflict', revision: 4 })),
    { status: 'revision_conflict', revision: 4 },
  )
})

test('the Phase 5 refusals are typed too', () => {
  assert.deepEqual(
    mapQueueSendError(apiError({ code: 'delivery_not_due', not_before: 1800 })),
    { status: 'not_due', notBefore: 1800 },
  )
  assert.equal(
    mapQueueSendError(apiError({ code: 'delivery_expired', error: 'expired' })).status,
    'expired',
  )
})

test('schedule status mirrors the daemon: due, scheduled, or expired', () => {
  const now = 1_000
  const plain = message('m', 0, 'armed')
  assert.equal(scheduleStatus(plain, now), 'due')
  assert.equal(scheduleStatus({ ...plain, constraints: { not_before: 2_000 } }, now), 'scheduled')
  assert.equal(scheduleStatus({ ...plain, constraints: { not_before: 500 } }, now), 'due')
  // Expiry wins over a schedule: an expired item is never "about to send".
  assert.equal(
    scheduleStatus({ ...plain, constraints: { not_before: 2_000, expires_at: 900 } }, now),
    'expired',
  )
})

test('sender labels name non-human authors and stay silent for your own messages', () => {
  const plain = message('m', 0, 'armed')
  assert.equal(senderLabel(plain), '')
  assert.equal(
    senderLabel({ ...plain, sender_kind: 'agent', sender_label: 'claude-worker' }),
    'from claude-worker',
  )
  assert.equal(senderLabel({ ...plain, sender_kind: 'remote_user' }), 'from you (remote)')
  assert.equal(senderLabel({ ...plain, sender_kind: 'queue_draft' }), 'from an observer')
})

test('an unknown failure degrades to a plain error outcome, never a throw', () => {
  const outcome = mapQueueSendError(new Error('network down'))
  assert.equal(outcome.status, 'error')
  assert.ok('error' in outcome && outcome.error.includes('network down'))
})
