import assert from 'node:assert/strict'
import test from 'node:test'
import { paletteScope, PALETTE_PREFIXES } from '../src/commands.ts'
import {
  advance, cancel, claims, installKeymap, options, pendingChords,
  setTerminalSelection, terminalSelection,
} from '../src/keymapDispatch.ts'
import type { ResolvedBindings } from '../src/keymap.ts'

const BINDINGS: ResolvedBindings = {
  'ctrl+shift+p': [{ command: 'palette.open', when: '' }],
  'ctrl+shift+space p n': [{ command: 'pane.next', when: '' }],
  'ctrl+c': [{ command: 'terminal.copy', when: 'hasSelection' }],
}

const install = (flags: Record<string, boolean> = {}) => {
  installKeymap(BINDINGS, () => flags)
}

test('claims is a pure question and advance is the single mutation', () => {
  // The terminal asks before App dispatches, and asking twice must not consume a
  // chord. Getting this backwards eats the first half of every sequence.
  install()
  assert.ok(claims('ctrl+shift+space'))
  assert.ok(claims('ctrl+shift+space'))
  assert.deepEqual(pendingChords(), [])
  assert.equal(advance('ctrl+shift+space').kind, 'pending')
  assert.deepEqual(pendingChords(), ['ctrl+shift+space'])
  cancel()
})

test('an armed sequence claims everything, including its own abandonment', () => {
  install()
  advance('ctrl+shift+space')
  assert.ok(claims('q'))
  assert.ok(claims(''))
  assert.equal(advance('q').kind, 'abandon')
  assert.deepEqual(pendingChords(), [])
})

test('a chord nothing binds is not claimed', () => {
  install()
  assert.ok(!claims('ctrl+shift+q'))
  assert.ok(!claims(''))
})

test('a keymap that changes under an armed sequence drops it', () => {
  // The pending chords would otherwise mean something else, or nothing, and firing
  // whatever they now resolve to is a command the user did not ask for.
  install()
  advance('ctrl+shift+space')
  assert.equal(pendingChords().length, 1)
  install()
  assert.deepEqual(pendingChords(), [])
})

test('the terminal selection reaches the when-flags that need it', () => {
  // Knowable only inside xterm, and the pane writes it on every keydown - which
  // runs before App's window listener, so App reads this keystroke's value.
  setTerminalSelection(true)
  assert.ok(terminalSelection())
  install({ hasSelection: terminalSelection() })
  assert.ok(claims('ctrl+c'))
  setTerminalSelection(false)
  install({ hasSelection: terminalSelection() })
  assert.ok(!claims('ctrl+c'))
})

test('the which-key options come from the live sequence', () => {
  install()
  advance('ctrl+shift+space')
  assert.deepEqual(options().map(option => option.chord), ['p'])
  cancel()
  // With nothing armed this is the root's children, which the overlay never draws:
  // `WhichKey` renders only while `pending` is non-empty.
  assert.deepEqual(options().map(option => option.chord), ['ctrl+c', 'ctrl+shift+p', 'ctrl+shift+space'])
})

test('a palette prefix names a scope and is stripped from the term', () => {
  assert.deepEqual(paletteScope('@web'), { scope: 'sessions', term: 'web' })
  assert.deepEqual(paletteScope('#swe'), { scope: 'projects', term: 'swe' })
  assert.deepEqual(paletteScope(':App.tsx'), { scope: 'files', term: 'App.tsx' })
  assert.deepEqual(paletteScope('>split'), { scope: 'commands', term: 'split' })
  assert.deepEqual(paletteScope('split'), { scope: 'commands', term: 'split' })
  assert.deepEqual(paletteScope(''), { scope: 'commands', term: '' })
  assert.deepEqual(Object.keys(PALETTE_PREFIXES).sort(), ['#', ':', '>', '@'])
})
