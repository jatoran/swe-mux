import assert from 'node:assert/strict'
import test from 'node:test'
import {
  bindingFor, boundCommands, buildTrie, emptyKeymapState, optionsAt, pickEntry, step, whenHolds,
  type ResolvedBindings,
} from '../src/keymap.ts'

const BINDINGS: ResolvedBindings = {
  'ctrl+shift+p': [{ command: 'palette.open', when: '' }],
  'ctrl+shift+space p n': [{ command: 'pane.next', when: '' }],
  'ctrl+shift+space p p': [{ command: 'pane.previous', when: '' }],
  'ctrl+shift+space g m': [{ command: 'drawer.git.map', when: '' }],
  'ctrl+shift+f': [
    { command: 'terminal.find', when: '' },
    { command: 'note.find', when: 'editorFocused' },
  ],
}

const trie = () => buildTrie(BINDINGS)

test('a flat chord fires immediately', () => {
  const { state, outcome } = step(trie(), emptyKeymapState(), 'ctrl+shift+p', {})
  assert.deepEqual(outcome, { kind: 'run', command: 'palette.open', sequence: 'ctrl+shift+p' })
  assert.deepEqual(state.pending, [])
})

test('a prefix arms instead of firing, and the next chord completes it', () => {
  const tree = trie()
  const armed = step(tree, emptyKeymapState(), 'ctrl+shift+space', {})
  assert.equal(armed.outcome.kind, 'pending')
  const done = step(tree, armed.state, 'p', {})
  assert.equal(done.outcome.kind, 'pending')
  const fired = step(tree, done.state, 'n', {})
  assert.deepEqual(fired.outcome, { kind: 'run', command: 'pane.next', sequence: 'ctrl+shift+space p n' })
  assert.deepEqual(fired.state.pending, [])
})

test('a stray key abandons the sequence and is swallowed, not passed through', () => {
  // Forwarding it would type a character into a terminal the user believed was
  // listening for the second half of a shortcut, which nobody can attribute.
  const tree = trie()
  const armed = step(tree, emptyKeymapState(), 'ctrl+shift+space', {})
  const lost = step(tree, armed.state, 'q', {})
  assert.deepEqual(lost.outcome, { kind: 'abandon', pending: ['ctrl+shift+space'], chord: 'q' })
  assert.deepEqual(lost.state.pending, [])
})

test('an unbound chord with nothing armed belongs to whatever is focused', () => {
  assert.equal(step(trie(), emptyKeymapState(), 'ctrl+shift+q', {}).outcome.kind, 'idle')
  assert.equal(step(trie(), emptyKeymapState(), '', {}).outcome.kind, 'idle')
})

test('the most specific `when` that holds wins', () => {
  const tree = trie()
  assert.equal(step(tree, emptyKeymapState(), 'ctrl+shift+f', {}).outcome.kind, 'run')
  const inEditor = step(tree, emptyKeymapState(), 'ctrl+shift+f', { editorFocused: true }).outcome
  assert.deepEqual(inEditor, { kind: 'run', command: 'note.find', sequence: 'ctrl+shift+f' })
})

test('the when grammar is a conjunction of optionally negated flags and nothing else', () => {
  assert.ok(whenHolds('', {}))
  assert.ok(whenHolds('a && !b', { a: true }))
  assert.ok(!whenHolds('a && !b', { a: true, b: true }))
  // An unknown flag reads as false, which fails closed.
  assert.ok(!whenHolds('somethingNobodyDefined', {}))
  // No `||` support: the whole expression is one term and fails to match.
  assert.ok(!whenHolds('a || b', { a: true, b: true }))
})

test('a chord whose every binding is scoped away is not claimed', () => {
  const scoped = buildTrie({ 'ctrl+shift+g': [{ command: 'note.find', when: 'editorFocused' }] })
  assert.equal(step(scoped, emptyKeymapState(), 'ctrl+shift+g', {}).outcome.kind, 'idle')
  assert.equal(step(scoped, emptyKeymapState(), 'ctrl+shift+g', { editorFocused: true }).outcome.kind, 'run')
  assert.equal(pickEntry([{ command: 'x', when: 'nope' }], {}), null)
})

test('the which-key options describe one level, leaves and groups apart', () => {
  const tree = trie()
  const top = optionsAt(tree, ['ctrl+shift+space'])
  assert.deepEqual(top.map(option => option.chord), ['g', 'p'])
  assert.deepEqual(top.map(option => option.count), [1, 2])
  assert.deepEqual(top.map(option => option.command), [null, null])
  const panes = optionsAt(tree, ['ctrl+shift+space', 'p'])
  assert.deepEqual(panes.map(option => option.command), ['pane.next', 'pane.previous'])
  assert.deepEqual(optionsAt(tree, ['nowhere']), [])
})

test('the hint shown beside a command is the shortest route, then stable', () => {
  assert.equal(bindingFor('pane.next', BINDINGS), 'ctrl+shift+space p n')
  assert.equal(bindingFor('palette.open', BINDINGS), 'ctrl+shift+p')
  assert.equal(bindingFor('nothing.bound', BINDINGS), undefined)
  assert.equal(
    bindingFor('palette.open', {
      'ctrl+shift+space x': [{ command: 'palette.open', when: '' }],
      f1: [{ command: 'palette.open', when: '' }],
    }),
    'f1',
  )
})

test('every command a map can reach is enumerable', () => {
  assert.deepEqual(
    [...boundCommands(BINDINGS)].sort(),
    ['drawer.git.map', 'note.find', 'palette.open', 'pane.next', 'pane.previous', 'terminal.find'],
  )
})
