import assert from 'node:assert/strict'
import test from 'node:test'
import { terminalKeyDecision, type TerminalKey } from '../src/terminalKeys.ts'

const key = (value: string, overrides: Partial<TerminalKey> = {}): TerminalKey => ({
  type: 'keydown', key: value, ctrlKey: false, shiftKey: false, altKey: false, metaKey: false,
  ...overrides,
})

for (const backend of ['PowerShell', 'CMD', 'pwsh', 'WSL', 'Claude', 'Codex']) {
  test(`${backend}: ordinary input and disabled copy reach the PTY`, () => {
    assert.deepEqual(terminalKeyDecision(key('a'), undefined, false), { kind: 'pass' })
    assert.deepEqual(terminalKeyDecision(key('c', { ctrlKey: true }), undefined, false), { kind: 'pass' })
    assert.deepEqual(terminalKeyDecision(key('c', { ctrlKey: true, shiftKey: true }), 'terminal.copy', false), { kind: 'pass' })
  })

  test(`${backend}: copy and bracketed-paste paths stay distinct`, () => {
    assert.deepEqual(terminalKeyDecision(key('c', { ctrlKey: true }), undefined, true), { kind: 'copySelection' })
    assert.deepEqual(terminalKeyDecision(key('v', { ctrlKey: true }), undefined, false), { kind: 'pasteText' })
    assert.deepEqual(terminalKeyDecision(key('v', { ctrlKey: true, shiftKey: true }), undefined, false), { kind: 'pasteText' })
  })
}

test('configured allowlisted commands are routed and unmatched chords pass', () => {
  assert.deepEqual(terminalKeyDecision(key('f', { ctrlKey: true, shiftKey: true }), 'terminal.find', false), { kind: 'command', command: 'terminal.find' })
  assert.deepEqual(terminalKeyDecision(key('x', { altKey: true }), undefined, false), { kind: 'pass' })
  assert.deepEqual(terminalKeyDecision(key('x', { ctrlKey: true }), 'unknown.disabled', false), { kind: 'command', command: 'unknown.disabled' })
})
