import assert from 'node:assert/strict'
import test from 'node:test'
import {
  isPendingQueueState,
  mapQueueSendError,
  queueHead,
  type QueueMessage,
} from '../src/queueApi.ts'

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

test('an unknown failure degrades to a plain error outcome, never a throw', () => {
  const outcome = mapQueueSendError(new Error('network down'))
  assert.equal(outcome.status, 'error')
  assert.ok('error' in outcome && outcome.error.includes('network down'))
})
