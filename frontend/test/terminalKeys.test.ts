import assert from 'node:assert/strict'
import test from 'node:test'
import { AGENT_NEWLINE, terminalKeyDecision, type TerminalKey } from '../src/terminalKeys.ts'

const key = (value: string, overrides: Partial<TerminalKey> = {}): TerminalKey => ({
  type: 'keydown', key: value, ctrlKey: false, shiftKey: false, altKey: false, metaKey: false,
  ...overrides,
})

for (const [label, backend] of [['PowerShell', 'shell'], ['CMD', 'shell'], ['pwsh', 'shell'], ['WSL', 'shell'], ['Claude', 'claude'], ['Codex', 'codex']]) {
  test(`${label}: ordinary input and disabled copy reach the PTY`, () => {
    assert.deepEqual(terminalKeyDecision(key('a'), undefined, false, backend), { kind: 'pass' })
    assert.deepEqual(terminalKeyDecision(key('c', { ctrlKey: true }), undefined, false, backend), { kind: 'pass' })
    assert.deepEqual(terminalKeyDecision(key('c', { ctrlKey: true, shiftKey: true }), 'terminal.copy', false, backend), { kind: 'pass' })
  })

  test(`${label}: copy and bracketed-paste paths stay distinct`, () => {
    assert.deepEqual(terminalKeyDecision(key('c', { ctrlKey: true }), undefined, true, backend), { kind: 'copySelection' })
    assert.deepEqual(terminalKeyDecision(key('v', { ctrlKey: true }), undefined, false, backend), { kind: 'browserPaste' })
    assert.deepEqual(terminalKeyDecision(key('v', { ctrlKey: true, shiftKey: true }), undefined, false, backend), { kind: 'browserPaste' })
  })

  test(`${label}: plain Enter always submits`, () => {
    assert.deepEqual(terminalKeyDecision(key('Enter'), undefined, false, backend), { kind: 'pass' })
    assert.deepEqual(terminalKeyDecision(key('Enter', { altKey: true }), undefined, false, backend), { kind: 'pass' })
  })
}

for (const backend of ['claude', 'codex']) {
  test(`${backend}: Shift+Enter and Ctrl+Enter both insert a newline`, () => {
    const newline = { kind: 'sendInput', data: AGENT_NEWLINE }
    assert.deepEqual(terminalKeyDecision(key('Enter', { shiftKey: true }), undefined, false, backend), newline)
    assert.deepEqual(terminalKeyDecision(key('Enter', { ctrlKey: true }), undefined, false, backend), newline)
    assert.deepEqual(terminalKeyDecision(key('Enter', { ctrlKey: true, shiftKey: true }), undefined, false, backend), newline)
    // A selection must not turn the newline chord into a copy, and keyup never fires input.
    assert.deepEqual(terminalKeyDecision(key('Enter', { ctrlKey: true }), undefined, true, backend), newline)
    assert.deepEqual(terminalKeyDecision({ ...key('Enter', { shiftKey: true }), type: 'keyup' }, undefined, false, backend), { kind: 'pass' })
  })

  test(`${backend}: Alt or Meta held keeps xterm's own Enter encoding`, () => {
    assert.deepEqual(terminalKeyDecision(key('Enter', { shiftKey: true, altKey: true }), undefined, false, backend), { kind: 'pass' })
    assert.deepEqual(terminalKeyDecision(key('Enter', { ctrlKey: true, metaKey: true }), undefined, false, backend), { kind: 'pass' })
  })
}

test('shell panes keep Shift+Enter and Ctrl+Enter as a submit', () => {
  assert.deepEqual(terminalKeyDecision(key('Enter', { shiftKey: true }), undefined, false, 'shell'), { kind: 'pass' })
  assert.deepEqual(terminalKeyDecision(key('Enter', { ctrlKey: true }), undefined, false, 'shell'), { kind: 'pass' })
  assert.deepEqual(terminalKeyDecision(key('Enter', { shiftKey: true }), undefined, false), { kind: 'pass' })
})

test('configured allowlisted commands are routed and unmatched chords pass', () => {
  assert.deepEqual(terminalKeyDecision(key('f', { ctrlKey: true, shiftKey: true }), 'terminal.find', false), { kind: 'command', command: 'terminal.find' })
  assert.deepEqual(terminalKeyDecision(key('x', { altKey: true }), undefined, false), { kind: 'pass' })
  assert.deepEqual(terminalKeyDecision(key('x', { ctrlKey: true }), 'unknown.disabled', false), { kind: 'command', command: 'unknown.disabled' })
})
