import assert from 'node:assert/strict'
import test from 'node:test'
import { AGENT_NEWLINE, terminalKeyDecision, type TerminalKey } from '../src/terminalKeys.ts'

const key = (value: string, overrides: Partial<TerminalKey> = {}): TerminalKey => ({
  type: 'keydown', key: value, ctrlKey: false, shiftKey: false, altKey: false, metaKey: false,
  ...overrides,
})

for (const [label, backend] of [['PowerShell', 'shell'], ['CMD', 'shell'], ['pwsh', 'shell'], ['WSL', 'shell'], ['Claude', 'claude'], ['Codex', 'codex'], ['oh-my-pi', 'omp']]) {
  test(`${label}: ordinary input and disabled copy reach the PTY`, () => {
    assert.deepEqual(terminalKeyDecision(key('a'), false, false, backend), { kind: 'pass' })
    assert.deepEqual(terminalKeyDecision(key('c', { ctrlKey: true }), false, false, backend), { kind: 'pass' })
    // A claimed chord is swallowed whatever the selection is. The pane cannot know
    // WHICH command it belongs to any more - a chord halfway through a sequence has
    // no command yet - so `terminal.copy`'s old "pass through when nothing is
    // selected" special case moved into the keymap's `hasSelection` when-flag, where
    // the one owner of the sequence state can apply it.
    assert.deepEqual(terminalKeyDecision(key('c', { ctrlKey: true, shiftKey: true }), true, false, backend), { kind: 'claimed' })
  })

  test(`${label}: copy and bracketed-paste paths stay distinct`, () => {
    assert.deepEqual(terminalKeyDecision(key('c', { ctrlKey: true }), false, true, backend), { kind: 'copySelection' })
    assert.deepEqual(terminalKeyDecision(key('v', { ctrlKey: true }), false, false, backend), { kind: 'browserPaste' })
    assert.deepEqual(terminalKeyDecision(key('v', { ctrlKey: true, shiftKey: true }), false, false, backend), { kind: 'browserPaste' })
  })

  test(`${label}: plain Enter always submits`, () => {
    assert.deepEqual(terminalKeyDecision(key('Enter'), false, false, backend), { kind: 'pass' })
    assert.deepEqual(terminalKeyDecision(key('Enter', { altKey: true }), false, false, backend), { kind: 'pass' })
  })
}

for (const backend of ['claude', 'codex', 'omp']) {
  test(`${backend}: Shift+Enter and Ctrl+Enter both insert a newline`, () => {
    const newline = { kind: 'sendInput', data: AGENT_NEWLINE }
    assert.deepEqual(terminalKeyDecision(key('Enter', { shiftKey: true }), false, false, backend), newline)
    assert.deepEqual(terminalKeyDecision(key('Enter', { ctrlKey: true }), false, false, backend), newline)
    assert.deepEqual(terminalKeyDecision(key('Enter', { ctrlKey: true, shiftKey: true }), false, false, backend), newline)
    // A selection must not turn the newline chord into a copy, and keyup never fires input.
    assert.deepEqual(terminalKeyDecision(key('Enter', { ctrlKey: true }), false, true, backend), newline)
    assert.deepEqual(terminalKeyDecision({ ...key('Enter', { shiftKey: true }), type: 'keyup' }, false, false, backend), { kind: 'pass' })
  })

  test(`${backend}: Alt or Meta held keeps xterm's own Enter encoding`, () => {
    assert.deepEqual(terminalKeyDecision(key('Enter', { shiftKey: true, altKey: true }), false, false, backend), { kind: 'pass' })
    assert.deepEqual(terminalKeyDecision(key('Enter', { ctrlKey: true, metaKey: true }), false, false, backend), { kind: 'pass' })
  })
}

test('shell panes keep Shift+Enter and Ctrl+Enter as a submit', () => {
  assert.deepEqual(terminalKeyDecision(key('Enter', { shiftKey: true }), false, false, 'shell'), { kind: 'pass' })
  assert.deepEqual(terminalKeyDecision(key('Enter', { ctrlKey: true }), false, false, 'shell'), { kind: 'pass' })
  assert.deepEqual(terminalKeyDecision(key('Enter', { shiftKey: true }), false, false), { kind: 'pass' })
})

test('a claimed chord is swallowed and an unclaimed one reaches the PTY', () => {
  // `claimed` is a boolean rather than a command id since 2026-08-30: App's window
  // handler owns dispatch, and this decides only whether xterm may have the key.
  assert.deepEqual(terminalKeyDecision(key('f', { ctrlKey: true, shiftKey: true }), true, false), { kind: 'claimed' })
  assert.deepEqual(terminalKeyDecision(key('Tab', { ctrlKey: true }), true, false), { kind: 'claimed' })
  assert.deepEqual(terminalKeyDecision(key('x', { altKey: true }), false, false), { kind: 'pass' })
  // Claiming outranks every later rule, including the ones that would have sent a
  // newline or copied - a leader that happens to be Ctrl+Enter must arm, not submit.
  assert.deepEqual(terminalKeyDecision(key('Enter', { ctrlKey: true }), true, false, 'claude'), { kind: 'claimed' })
  assert.deepEqual(terminalKeyDecision(key('c', { ctrlKey: true }), true, true), { kind: 'claimed' })
})
