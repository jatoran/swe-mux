import assert from 'node:assert/strict'
import test from 'node:test'
import { EDITOR_INPUT_PART, uncommittedEditorText, type CompositionHost } from '../src/noteComposition.ts'

/** A Continuity element as this module sees it: an engine snapshot behind a textarea. */
function fakeEditor(options: {
  composing: boolean
  engineText: string
  inputValue?: string | null
  destroyed?: boolean
}): CompositionHost {
  return {
    composing: options.composing,
    shadowRoot: {
      querySelector: (selectors: string) =>
        selectors === EDITOR_INPUT_PART && options.inputValue !== null
          ? { value: options.inputValue ?? options.engineText }
          : null,
    },
    snapshot: () => {
      if (options.destroyed) throw new Error('Continuity editor was destroyed')
      return { text: options.engineText }
    },
  }
}

test('an open composition surrenders the word the engine has not absorbed', () => {
  const editor = fakeEditor({ composing: true, engineText: 'the quick brown ', inputValue: 'the quick brown fox' })
  assert.equal(uncommittedEditorText(editor), 'the quick brown fox')
})

test('a settled editor is left alone: the engine, not the textarea, is the authority', () => {
  // The textarea is deliberately made to disagree. Outside a composition it is only a mirror
  // of the engine, so rescuing from it could write a stale reflection over committed text.
  const editor = fakeEditor({ composing: false, engineText: 'committed', inputValue: 'stale mirror' })
  assert.equal(uncommittedEditorText(editor), null)
})

test('a composition that has nothing new to give is not resubmitted', () => {
  const editor = fakeEditor({ composing: true, engineText: 'same', inputValue: 'same' })
  assert.equal(uncommittedEditorText(editor), null)
})

test('textarea newlines are normalised the way the engine would normalise them', () => {
  const editor = fakeEditor({ composing: true, engineText: 'one\n', inputValue: 'one\r\ntwo\rthree' })
  assert.equal(uncommittedEditorText(editor), 'one\ntwo\nthree')
})

test('an editor that is already destroyed has nothing to rescue', () => {
  const editor = fakeEditor({ composing: true, engineText: 'gone', inputValue: 'gone plus', destroyed: true })
  assert.equal(uncommittedEditorText(editor), null)
})

test('a missing textarea part is not a rescue', () => {
  const editor = fakeEditor({ composing: true, engineText: 'text', inputValue: null })
  assert.equal(uncommittedEditorText(editor), null)
  assert.equal(uncommittedEditorText(null), null)
  assert.equal(uncommittedEditorText(undefined), null)
})
