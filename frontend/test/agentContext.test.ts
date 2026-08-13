import assert from 'node:assert/strict'
import test from 'node:test'
import {
  AGENT_CONTEXT_DESKTOP_MENU_QUERY,
  AGENT_CONTEXT_DISCLOSURE_DEFAULTS,
  agentContextSourceMenuEnabled,
  backupAction,
  comparisonLabel,
  memoryFileCount,
  statusLabel,
  type AgentContextProvider,
  type AgentContextSyncOption,
} from '../src/agentContext.ts'

test('instruction sync accepts descriptor-supplied whole-file directions', () => {
  const options: AgentContextSyncOption[] = [
    { direction: 'instruction:claude->instruction:codex', source_id: 'instruction:claude', source: 'CLAUDE.md', target_id: 'instruction:codex', target: 'AGENTS.md' },
    { direction: 'instruction:codex->instruction:claude', source_id: 'instruction:codex', source: 'AGENTS.md', target_id: 'instruction:claude', target: 'CLAUDE.md' },
  ]
  for (const option of options) {
    assert.notEqual(option.source, option.target)
    assert.match(option.direction, /^instruction:\w+->instruction:\w+$/)
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

test('memory count uses the complete provider inventory count', () => {
  const providers = [
    { item_count: 130, items: new Array(128) },
    { item_count: 0, items: [] },
  ] as unknown as AgentContextProvider[]
  assert.equal(memoryFileCount(providers), 130)
})

test('source reveal menus require a real file and a desktop pointer', () => {
  assert.equal(AGENT_CONTEXT_DESKTOP_MENU_QUERY, '(pointer:fine) and (min-width:761px)')
  assert.equal(agentContextSourceMenuEnabled({ revealable: true }, true), true)
  assert.equal(agentContextSourceMenuEnabled({ revealable: true }, false), false)
  assert.equal(agentContextSourceMenuEnabled({ revealable: false }, true), false)
})

test('only Project instructions are expanded by default', () => {
  assert.deepEqual(AGENT_CONTEXT_DISCLOSURE_DEFAULTS, {
    projectInstructions: true,
    globalInstructions: false,
    memories: false,
  })
})
