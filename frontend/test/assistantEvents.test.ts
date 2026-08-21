import assert from 'node:assert/strict'
import test from 'node:test'

import { applyAssistantEvent, type AssistantAction, type AssistantMessage } from '../src/assistant.ts'

type State = { messages: AssistantMessage[]; actions: AssistantAction[]; thinking: string | null }
const empty = (): State => ({ messages: [], actions: [], thinking: null })

const payload = (extra: Record<string, unknown>) => ({
  dialog_id: 'd1', turn_id: 't1', ...extra,
})

test('a turn start records the user message once and marks thinking', () => {
  const started = applyAssistantEvent(empty(), {
    type: 'assistant_turn_started', payload: payload({ text: 'what needs me' }),
  })
  assert.equal(started.messages.length, 1)
  assert.equal(started.messages[0].role, 'user')
  assert.equal(started.thinking, 'thinking')
  const duplicate = applyAssistantEvent(started, {
    type: 'assistant_turn_started', payload: payload({ text: 'what needs me' }),
  })
  assert.equal(duplicate.messages.length, 1)
})

test('a queued turn shows the words immediately and does not double them', () => {
  // Spoken over a running turn, what the operator said is accepted and held.
  // It must appear at once — the old refusal simply lost it — and the later
  // start event must update that same bubble rather than add a second copy.
  const queued = applyAssistantEvent(empty(), {
    type: 'assistant_turn_queued', payload: payload({ text: 'I have three groups' }),
  })
  assert.equal(queued.messages.length, 1)
  assert.equal(queued.messages[0].display, 'I have three groups')
  assert.equal(queued.thinking, 'queued — waiting for the current turn')

  const merged = applyAssistantEvent(queued, {
    type: 'assistant_turn_queued',
    payload: payload({ text: 'I have three groups of notes', merged: true }),
  })
  assert.equal(merged.messages.length, 1, 'a merge updates the bubble in place')
  assert.equal(merged.messages[0].display, 'I have three groups of notes')

  const started = applyAssistantEvent(merged, {
    type: 'assistant_turn_started',
    payload: payload({ text: 'I have three groups of notes' }),
  })
  assert.equal(started.messages.length, 1, 'starting must not repeat the words')
  assert.equal(started.thinking, 'thinking')
})

test('sentences stream into one assistant message and done finalizes it', () => {
  let state = empty()
  state = applyAssistantEvent(state, {
    type: 'assistant_sentence',
    payload: payload({ message_id: 'm1', index: 0, display: 'Two live.', speech: 'Two live.' }),
  })
  state = applyAssistantEvent(state, {
    type: 'assistant_sentence',
    payload: payload({ message_id: 'm1', index: 1, display: 'Nothing needs you.', speech: '' }),
  })
  assert.equal(state.messages.length, 1)
  assert.equal(state.messages[0].display, 'Two live. Nothing needs you.')
  assert.equal(state.messages[0].status, 'streaming')
  state = applyAssistantEvent(state, {
    type: 'assistant_turn_done',
    payload: payload({
      message_id: 'm1', display: 'Two live. Nothing needs you.', speech: 'Two live.',
    }),
  })
  assert.equal(state.messages[0].status, 'done')
  assert.equal(state.thinking, null)
})

test('action events upsert by id so status transitions replace the card', () => {
  const pending = {
    id: 'a1', dialog_id: 'd1', turn_id: 't1', created_at: 1, kind: 'send_to_session',
    action_class: 'reversible', restatement: 'queue a draft', arguments: {}, status: 'pending',
  }
  let state = applyAssistantEvent(empty(), { type: 'assistant_action', payload: pending })
  assert.equal(state.actions.length, 1)
  state = applyAssistantEvent(state, {
    type: 'assistant_action', payload: { ...pending, status: 'executed' },
  })
  assert.equal(state.actions.length, 1)
  assert.equal(state.actions[0].status, 'executed')
})

test('a failed turn lands as a failed assistant message', () => {
  const state = applyAssistantEvent(empty(), {
    type: 'assistant_turn_failed', payload: payload({ error: 'budget exhausted' }),
  })
  assert.equal(state.messages[0].status, 'failed')
  assert.equal(state.messages[0].error, 'budget exhausted')
})
