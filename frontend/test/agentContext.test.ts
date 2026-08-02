import assert from 'node:assert/strict'
import test from 'node:test'
import {
  AGENT_CONTEXT_SYNC_OPTIONS,
  backupAction,
  comparisonLabel,
  statusLabel,
} from '../src/agentContext.ts'

test('instruction sync exposes only the two explicit whole-file directions', () => {
  assert.deepEqual(AGENT_CONTEXT_SYNC_OPTIONS, [
    { direction: 'claude_to_agents', source: 'CLAUDE.md', target: 'AGENTS.md' },
    { direction: 'agents_to_claude', source: 'AGENTS.md', target: 'CLAUDE.md' },
  ])
  for (const option of AGENT_CONTEXT_SYNC_OPTIONS) {
    assert.notEqual(option.source, option.target)
  }
})

test('inventory states have compact human labels', () => {
  assert.equal(comparisonLabel('in_sync'), 'In sync')
  assert.equal(comparisonLabel('different'), 'Different')
  assert.equal(comparisonLabel('missing'), 'One or both missing')
  assert.equal(statusLabel('too_large'), 'too large')
  assert.equal(statusLabel('unsupported'), 'unsupported')
})

test('restore copy distinguishes a prior file from undoing a newly created one', () => {
  const base = { id: 'backup', created_at: 1, revision: 'missing', size: 0 } as const
  assert.equal(backupAction({ ...base, target: 'AGENTS.md', existed: false }), 'Remove created AGENTS.md')
  assert.equal(backupAction({ ...base, target: 'CLAUDE.md', existed: true }), 'Restore CLAUDE.md')
})
