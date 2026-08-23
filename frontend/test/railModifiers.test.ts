import assert from 'node:assert/strict'
import test from 'node:test'

import {
  activeRailModifiers,
  applyRailModifiers,
  consumeRailModifiers,
  EMPTY_RAIL_MODIFIERS,
  RAIL_MODIFIERS,
  railModifierForItem,
  railModifierLabel,
  railModifierParam,
  railModifierPhase,
  toggleRailModifier,
} from '../src/railModifiers.ts'
import { BUILTIN_RAIL } from '../src/commandRail.ts'

test('each modifier chip in the catalog maps to exactly one modifier, and back', () => {
  for (const modifier of RAIL_MODIFIERS) {
    const ids = BUILTIN_RAIL.filter(item => railModifierForItem(item.id) === modifier).map(item => item.id)
    assert.equal(ids.length, 1, `${modifier} needs exactly one chip`)
  }
  assert.equal(railModifierForItem('esc'), null)
  assert.equal(railModifierForItem('padArrows'), null)
})

test('a modifier cycles off, armed, locked, off', () => {
  let state = EMPTY_RAIL_MODIFIERS
  assert.equal(railModifierPhase(state, 'ctrl'), 'off')
  state = toggleRailModifier(state, 'ctrl')
  assert.equal(railModifierPhase(state, 'ctrl'), 'armed')
  state = toggleRailModifier(state, 'ctrl')
  assert.equal(railModifierPhase(state, 'ctrl'), 'locked')
  state = toggleRailModifier(state, 'ctrl')
  assert.equal(railModifierPhase(state, 'ctrl'), 'off')
  // A modifier is never in both lists at once, whichever way it got there.
  assert.deepEqual(state, { armed: [], locked: [] })
})

test('a key consumes the armed set and leaves the locked one standing', () => {
  let state = toggleRailModifier(EMPTY_RAIL_MODIFIERS, 'ctrl')
  state = toggleRailModifier(state, 'alt')
  state = toggleRailModifier(state, 'alt')
  assert.deepEqual(activeRailModifiers(state), ['ctrl', 'alt'])
  state = consumeRailModifiers(state)
  assert.deepEqual(activeRailModifiers(state), ['alt'])
  // Consuming again is a no-op, so a repeat that calls it every repetition is harmless.
  assert.equal(consumeRailModifiers(state), state)
})

test('active modifiers come back in one stable order however they were armed', () => {
  let a = toggleRailModifier(EMPTY_RAIL_MODIFIERS, 'shift')
  a = toggleRailModifier(a, 'ctrl')
  let b = toggleRailModifier(EMPTY_RAIL_MODIFIERS, 'ctrl')
  b = toggleRailModifier(b, 'shift')
  assert.deepEqual(activeRailModifiers(a), activeRailModifiers(b))
  assert.deepEqual(activeRailModifiers(a), ['ctrl', 'shift'])
})

test('the CSI modifier parameter is 1 plus the standard bitmask', () => {
  assert.equal(railModifierParam([]), 1)
  assert.equal(railModifierParam(['shift']), 2)
  assert.equal(railModifierParam(['alt']), 3)
  assert.equal(railModifierParam(['shift', 'alt']), 4)
  assert.equal(railModifierParam(['ctrl']), 5)
  assert.equal(railModifierParam(['ctrl', 'alt', 'shift']), 8)
})

test('cursor and Home/End keys gain a modifier parameter', () => {
  assert.equal(applyRailModifiers('\x1b[A', ['ctrl']), '\x1b[1;5A')
  assert.equal(applyRailModifiers('\x1b[B', ['shift']), '\x1b[1;2B')
  assert.equal(applyRailModifiers('\x1b[C', ['alt']), '\x1b[1;3C')
  assert.equal(applyRailModifiers('\x1b[H', ['ctrl']), '\x1b[1;5H')
  assert.equal(applyRailModifiers('\x1b[F', ['ctrl', 'shift']), '\x1b[1;6F')
})

test('an already-modified key gains the new modifier rather than replacing it', () => {
  // `^Home` is a shipped chip, so this is the round trip the rail actually performs.
  assert.equal(applyRailModifiers('\x1b[1;5H', ['shift']), '\x1b[1;6H')
  assert.equal(applyRailModifiers('\x1b[1;5H', ['ctrl']), '\x1b[1;5H', 'idempotent')
  assert.equal(applyRailModifiers('\x1b[1;5F', ['alt']), '\x1b[1;7F')
})

test('the tilde family keeps its key number in the first parameter', () => {
  assert.equal(applyRailModifiers('\x1b[3~', ['ctrl']), '\x1b[3;5~')
  assert.equal(applyRailModifiers('\x1b[5~', ['shift']), '\x1b[5;2~')
  assert.equal(applyRailModifiers('\x1b[3;5~', ['shift']), '\x1b[3;6~')
})

test('Shift+Tab is back-tab, not a CSI parameter nothing reads', () => {
  assert.equal(applyRailModifiers('\t', ['shift']), '\x1b[Z')
  assert.equal(applyRailModifiers('\t', ['shift', 'alt']), '\x1b\x1b[Z')
  assert.equal(applyRailModifiers('\t', ['alt']), '\x1b\t')
})

test('Ctrl folds a single character onto its control code', () => {
  assert.equal(applyRailModifiers('c', ['ctrl']), '\x03')
  assert.equal(applyRailModifiers('C', ['ctrl']), '\x03')
  assert.equal(applyRailModifiers('u', ['ctrl']), '\x15')
  assert.equal(applyRailModifiers('[', ['ctrl']), '\x1b')
  assert.equal(applyRailModifiers('?', ['ctrl']), '\x7f')
})

test('Alt prefixes with escape, over a control code as readily as a letter', () => {
  assert.equal(applyRailModifiers('b', ['alt']), '\x1bb')
  assert.equal(applyRailModifiers('\x03', ['alt']), '\x1b\x03')
  assert.equal(applyRailModifiers('b', ['ctrl', 'alt']), '\x1b\x02')
  assert.equal(applyRailModifiers('b', ['shift']), 'B')
})

test('a sequence with no encoding for the modifier asked for is left alone', () => {
  // Ctrl over a multi-byte sequence has no encoding; inventing one would send noise.
  assert.equal(applyRailModifiers('\x1b[Z', ['ctrl']), '\x1b[Z')
  assert.equal(applyRailModifiers('hello', ['ctrl']), 'hello')
  assert.equal(applyRailModifiers('hello', ['shift']), 'hello')
  // And the identity cases, which is what makes it safe to run over everything the rail
  // sends rather than only over what it knows how to modify.
  assert.equal(applyRailModifiers('\x1b[A', []), '\x1b[A')
  assert.equal(applyRailModifiers('', ['ctrl']), '')
})

test('the tooltip prefix reads in the same order the state does', () => {
  assert.equal(railModifierLabel('Home', ['ctrl', 'shift']), 'Ctrl+Shift+Home')
  assert.equal(railModifierLabel('Home', []), 'Home')
})
