import assert from 'node:assert/strict'
import test from 'node:test'
import { resumeCommand } from '../src/resumeCommand.ts'

test('claude sessions resume with --resume and are available immediately', () => {
  // Mux passes --session-id at spawn, so native id equals the mux id from the start.
  assert.equal(
    resumeCommand({ id: 'abc-123', backend: 'claude', native_session_id: 'abc-123' }),
    'claude --resume abc-123',
  )
})

test('codex sessions resume with the resume subcommand once the native id is observed', () => {
  assert.equal(
    resumeCommand({ id: 'mux-1', backend: 'codex', native_session_id: '0199-codex-rollout' }),
    'codex resume 0199-codex-rollout',
  )
})

test('codex placeholder ids are not offered as resumable', () => {
  // Before rollout detection the record carries the mux id, which codex would reject.
  assert.equal(resumeCommand({ id: 'mux-1', backend: 'codex', native_session_id: 'mux-1' }), null)
})

test('shell sessions and blank ids yield no command', () => {
  assert.equal(resumeCommand({ id: 's1', backend: 'shell', native_session_id: 's1' }), null)
  assert.equal(resumeCommand({ id: 's2', backend: 'claude', native_session_id: '' }), null)
  assert.equal(resumeCommand({ id: 's3', backend: 'claude', native_session_id: '   ' }), null)
})
