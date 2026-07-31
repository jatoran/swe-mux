import assert from 'node:assert/strict'
import test from 'node:test'
import { raisesSoftKeyboard } from '../src/mobileKeyboard.ts'

test('text entry fields are what hold the soft keyboard up', () => {
  // The terminal's mobile live input, and every composer/search field.
  assert.equal(raisesSoftKeyboard({ tagName: 'TEXTAREA' }), true)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'text' }), true)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'search' }), true)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'number' }), true)
  // An input with no type attribute is a text input.
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT' }), true)
  // The Continuity note editor is contenteditable, not a textarea.
  assert.equal(raisesSoftKeyboard({ tagName: 'DIV', isContentEditable: true }), true)
})

test('focus that raises no keyboard is left alone', () => {
  assert.equal(raisesSoftKeyboard(null), false)
  assert.equal(raisesSoftKeyboard(undefined), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'BODY' }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'BUTTON' }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'DIV' }), false)
  // Toggles, sliders, and pickers are focusable but keyboardless.
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'checkbox' }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'range' }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'color' }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'file' }), false)
})

test('readonly fields and inputMode="none" opt out', () => {
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'text', readOnly: true }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'TEXTAREA', readOnly: true }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'TEXTAREA', inputMode: 'none' }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'DIV', isContentEditable: true, inputMode: 'none' }), false)
})

test('tag matching is case-insensitive, as DOM tagName is uppercase', () => {
  assert.equal(raisesSoftKeyboard({ tagName: 'textarea' }), true)
  assert.equal(raisesSoftKeyboard({ tagName: 'input', type: 'TEXT' }), true)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'CHECKBOX' }), false)
})
