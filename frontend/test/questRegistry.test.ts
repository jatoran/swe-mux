import assert from 'node:assert/strict'
import test from 'node:test'
import { QUESTS, openQuests, withQuestDismissed } from '../src/questRegistry.ts'

test('the quest log is capped at three by construction', () => {
  // The cap is the feature: a quest log that grows into a todo list is an
  // obligation handed to a new user. A fourth entry is a deliberate change to
  // this registry AND to QUEST_IDS in src/swe_mux/config.py.
  assert.equal(QUESTS.length, 3)
  assert.deepEqual(QUESTS.map(quest => quest.id), ['voice', 'worktrees', 'phone'])
})

test('a fresh install sees all three', () => {
  assert.equal(openQuests({}).length, 3)
})

test('voice completes from the config the guided setup writes', () => {
  assert.deepEqual(openQuests({ tts_enabled: true }).map(quest => quest.id), ['worktrees', 'phone'])
  assert.deepEqual(openQuests({ stt_enabled: true }).map(quest => quest.id), ['worktrees', 'phone'])
})

test('a dismissal is permanent and never resurrected', () => {
  const dismissed = withQuestDismissed(undefined, 'phone')
  assert.deepEqual(dismissed, ['phone'])
  assert.deepEqual(openQuests({ quests_dismissed: dismissed }).map(quest => quest.id), ['voice', 'worktrees'])
  // Dismissing again is idempotent, and order follows the registry, not clicks.
  const both = withQuestDismissed(withQuestDismissed(dismissed, 'voice'), 'phone')
  assert.deepEqual(both, ['voice', 'phone'])
})

test('everything dismissed hides the log with nothing left over', () => {
  let dismissed: string[] = []
  for (const quest of QUESTS) dismissed = withQuestDismissed(dismissed, quest.id)
  assert.deepEqual(openQuests({ quests_dismissed: dismissed }), [])
})
