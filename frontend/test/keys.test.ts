import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { altGrHazard, chordLabel, displayChord, isModifierOnly, keyChord, tokenForCode } from '../src/keys.ts'

const event = (code: string, held: Partial<Record<'ctrlKey'|'shiftKey'|'altKey'|'metaKey', boolean>> = {}) =>
  ({ code, ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, ...held })

test('a chord names the physical key, not the character the layout produced', () => {
  // The bug this replaced: `event.key` is layout-mapped, so a binding recorded on
  // Dvorak meant a different physical key on QWERTY - and a shifted chord reported
  // its shifted character (`Ctrl+Shift+5` -> key `%`), which could never match a
  // table written as `ctrl+shift+5`. That last shape is exactly tmux's `prefix %`.
  assert.equal(keyChord(event('KeyK', { ctrlKey: true, shiftKey: true })), 'ctrl+shift+k')
  assert.equal(keyChord(event('Digit5', { ctrlKey: true, shiftKey: true })), 'ctrl+shift+5')
  assert.equal(keyChord(event('Quote', { ctrlKey: true })), "ctrl+'")
})

test('modifiers are emitted in one fixed order so a chord has one spelling', () => {
  assert.equal(keyChord(event('KeyK', { metaKey: true, shiftKey: true })), 'shift+meta+k')
  assert.equal(keyChord(event('KeyK', { shiftKey: true, metaKey: true })), 'shift+meta+k')
})

test('an unmappable key is not a chord at all', () => {
  assert.equal(keyChord(event('ControlLeft', { ctrlKey: true })), '')
  assert.equal(keyChord(event('Unrecognised')), '')
  assert.equal(tokenForCode('Unrecognised'), null)
})

test('a modifier held on its own never advances a sequence', () => {
  // Reaching the second half of `leader ctrl+w` means holding Ctrl, and treating
  // that as a keystroke would abandon the sequence half way through.
  for (const code of ['ControlLeft', 'ShiftRight', 'AltLeft', 'MetaLeft', 'OSLeft']) {
    assert.ok(isModifierOnly(code), code)
  }
  assert.ok(!isModifierOnly('KeyA'))
})

test('AltGr is exactly Ctrl+Alt, which is why no shipped preset uses it', () => {
  assert.ok(altGrHazard('ctrl+alt+n'))
  assert.ok(altGrHazard('ctrl+shift+alt+n'))
  assert.ok(!altGrHazard('ctrl+shift+n'))
  assert.ok(!altGrHazard('ctrl+alt+meta+n'))
})

test('labels follow the platform rather than the storage order', () => {
  assert.equal(chordLabel('ctrl+shift+k'), 'Ctrl+Shift+K')
  assert.equal(chordLabel('ctrl+shift+arrowleft'), 'Ctrl+Shift+←')
  // Apple documents ⌃⌥⇧⌘, which is not the order chords are stored in.
  assert.equal(chordLabel('ctrl+shift+alt+meta+p', 'mac'), '⌃⌥⇧⌘P')
  assert.equal(chordLabel('shift+meta+p', 'mac'), '⇧⌘P')
})

test('a whole sequence is spelled chord by chord', () => {
  assert.equal(displayChord('ctrl+shift+space p n'), 'Ctrl+Shift+Space P N')
  assert.equal(displayChord(''), '')
  assert.equal(displayChord(undefined), '')
})

test('the tokenizer agrees with the daemon that shares its table', () => {
  // A tokenizer that disagrees with the recorder on the other side produces
  // bindings that can never fire, and neither side can see it alone.
  const python = readFileSync(new URL('../../src/swe_mux/keychords.py', import.meta.url), 'utf8')
  for (const [code, token] of [['Minus', '-'], ['BracketLeft', '['], ['Backquote', '`'], ['Slash', '/']]) {
    assert.equal(tokenForCode(code), token)
    assert.ok(python.includes(`"${code}": "${token === '\\' ? '\\\\' : token}"`), `${code} is missing from keychords.py`)
  }
  for (const named of ['pageup', 'arrowdown', 'printscreen', 'numpadenter']) {
    assert.equal(tokenForCode(named.charAt(0).toUpperCase() + named.slice(1)), named)
    assert.ok(python.includes(`"${named}"`), `${named} is missing from keychords.py`)
  }
})
