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

test('the closed grammar tolerates filler, because a miss re-proposes the action', () => {
  // Every phrasing this misses reaches the model as a fresh request, which
  // proposes the same action a second time — the "I confirmed and it asked me
  // again" failure. Shape is forgiving; meaning stays closed.
  assert.equal(spokenConfirmation('confirm it'), 'confirm')
  assert.equal(spokenConfirmation('yeah, confirm that please'), 'confirm')
  assert.equal(spokenConfirmation('um, okay'), 'confirm')
  assert.equal(spokenConfirmation('mux, do it now'), 'confirm')
  assert.equal(spokenConfirmation("that's right"), 'confirm')
  assert.equal(spokenConfirmation('sure'), 'confirm')
  assert.equal(spokenConfirmation('nah'), 'cancel')
  assert.equal(spokenConfirmation('forget it'), 'cancel')
  assert.equal(spokenConfirmation('scratch that'), 'cancel')
})

test('an affirmative wrapping a cancellation cancels', () => {
  // Reading "yes, cancel that" as a confirmation performs exactly the action the
  // operator was stopping, so the cancel word wins wherever it sits.
  assert.equal(spokenConfirmation('yes, cancel that'), 'cancel')
  assert.equal(spokenConfirmation('okay no'), 'cancel')
})

test('a sentence is still conversation, however affirmative it sounds', () => {
  assert.equal(spokenConfirmation('yes and also open the queue tab'), null)
  assert.equal(spokenConfirmation('okay so what did the other session say'), null)
  assert.equal(spokenConfirmation('cancel the deploy in the other project'), null)
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

test('an expired card is forgotten, not merely skipped', () => {
  // The endpointing and patience rules read an open card as "a question is
  // waiting", so a stale entry would hold the microphone in answer mode forever.
  const past = Date.now() / 1000 - 10
  noteAssistantActionEvent({ ...action('e1', 'pending', past), expires_at: past + 1 }, false)
  assert.equal(latestOpenAction(), null)
  noteAssistantActionEvent(action('e2', 'pending', Date.now() / 1000), false)
  assert.equal(latestOpenAction()?.id, 'e2')
  noteAssistantActionEvent(action('e2', 'executed', 0), false)
  assert.equal(latestOpenAction(), null)
})
