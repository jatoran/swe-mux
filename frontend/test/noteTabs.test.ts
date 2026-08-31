import assert from 'node:assert/strict'
import test from 'node:test'
import {
  SCRATCHPAD_TAB_ID,
  canonicalNoteTabId,
  fallbackNoteTab,
  lastNoteInProject,
  noteTabAfterDelete,
  projectNoteCounts,
  projectNoteTabId,
  stableProjectNoteTabs,
} from '../src/noteTabs.ts'

const notes = [
  { note_id: 'later', created_at: 20 },
  { note_id: 'first-b', created_at: 10 },
  { note_id: 'first-a', created_at: 10 },
]

test('Project note tabs stay in deterministic creation order', () => {
  assert.deepEqual(stableProjectNoteTabs(notes).map(note => note.note_id), ['first-a', 'first-b', 'later'])
})

test('selection restoration canonicalizes migrated session-note ids', () => {
  const ordered = stableProjectNoteTabs(notes)
  assert.equal(canonicalNoteTabId('sessions:first-a'), projectNoteTabId('first-a'))
  assert.equal(fallbackNoteTab('sessions:first-a', ordered), projectNoteTabId('first-a'))
  assert.equal(fallbackNoteTab('note:missing', ordered), projectNoteTabId('first-a'))
  assert.equal(fallbackNoteTab(SCRATCHPAD_TAB_ID, ordered), SCRATCHPAD_TAB_ID)
  assert.equal(fallbackNoteTab(null, []), SCRATCHPAD_TAB_ID)
  assert.equal(fallbackNoteTab(SCRATCHPAD_TAB_ID, ordered, false), projectNoteTabId('first-a'))
  assert.equal(fallbackNoteTab(null, [], false), null)
})

test('a Project protects its last note, counted per Project across a mixed listing', () => {
  const listing = [
    { project_id: 'alpha', note_id: 'a1' },
    { project_id: 'alpha', note_id: 'a2' },
    { project_id: 'beta', note_id: 'b1' },
  ]
  const counts = projectNoteCounts(listing)

  assert.deepEqual([...counts], [['alpha', 2], ['beta', 1]])
  assert.equal(lastNoteInProject(listing[0], counts), false)
  assert.equal(lastNoteInProject(listing[2], counts), true)
  // An uncounted Project claims nothing rather than claiming protection.
  assert.equal(lastNoteInProject({ project_id: 'gamma' }, counts), false)
})

test('deleting the selected tab chooses its next neighbour, then its previous neighbour', () => {
  const ordered = stableProjectNoteTabs(notes)
  assert.equal(noteTabAfterDelete(projectNoteTabId('first-a'), 'first-a', ordered), projectNoteTabId('first-b'))
  assert.equal(noteTabAfterDelete(projectNoteTabId('later'), 'later', ordered), projectNoteTabId('first-b'))
  assert.equal(noteTabAfterDelete(projectNoteTabId('first-a'), 'later', ordered), null)
  assert.equal(noteTabAfterDelete(projectNoteTabId('only'), 'only', [{ note_id: 'only', created_at: 1 }]), SCRATCHPAD_TAB_ID)
  assert.equal(noteTabAfterDelete(projectNoteTabId('only'), 'only', [{ note_id: 'only', created_at: 1 }], false), null)
})
