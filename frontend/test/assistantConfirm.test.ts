import assert from 'node:assert/strict'
import test from 'node:test'

import {
  latestOpenAction, noteAssistantActionEvent, spokenConfirmation,
  type AssistantAction,
} from '../src/assistant.ts'

const action = (id: string, status: AssistantAction['status'], createdAt: number): Partial<AssistantAction> => ({
  id, status, created_at: createdAt, restatement: `do thing ${id}`,
  dialog_id: 'd1', turn_id: 't1', kind: 'edit_project_note',
  action_class: 'reversible', arguments: {},
})

test('spoken confirmation is a closed grammar, never a paraphrase', () => {
  assert.equal(spokenConfirmation('confirm'), 'confirm')
  assert.equal(spokenConfirmation('Yes.'), 'confirm')
  assert.equal(spokenConfirmation('go ahead'), 'confirm')
  assert.equal(spokenConfirmation('cancel that'), 'cancel')
  assert.equal(spokenConfirmation('never mind'), 'cancel')
  // Anything conversational falls through to the model.
  assert.equal(spokenConfirmation('yes but change the wording first'), null)
  assert.equal(spokenConfirmation('confirm the meeting for tomorrow'), null)
})

test('the tracker follows action status and ignores replay', () => {
  noteAssistantActionEvent(action('a1', 'pending', 1), false)
  noteAssistantActionEvent(action('a2', 'scheduled', 2), false)
  assert.equal(latestOpenAction()?.id, 'a2')
  noteAssistantActionEvent(action('a2', 'executed', 2), false)
  assert.equal(latestOpenAction()?.id, 'a1')
  noteAssistantActionEvent(action('a1', 'cancelled', 1), false)
  assert.equal(latestOpenAction(), null)
  // A replayed pending event from history must not revive a card.
  noteAssistantActionEvent(action('a3', 'pending', 3), true)
  assert.equal(latestOpenAction(), null)
})
