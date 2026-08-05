import assert from 'node:assert/strict'
import test from 'node:test'
import {
  agentCompletenessLabel,
  agentScopeLabel,
  agentStateLabel,
  filterAgentEnvironmentSections,
  type AgentEnvironmentSection,
} from '../src/agentEnvironment.ts'

const sections: AgentEnvironmentSection[] = [{
  id: 'mcp',
  label: 'MCP servers',
  completeness: 'configured_only',
  total: 2,
  truncated: false,
  note: 'Passive inventory.',
  items: [
    {
      id: 'one', kind: 'mcp_server', name: 'github', description: 'Repository access',
      scope: 'project', origin: 'Project MCP', state: 'configured', source_id: 'source:one',
      source_label: 'Project MCP', changed_after_start: false,
      meta: [{ label: 'Transport', value: 'stdio' }, { label: 'Executable', value: 'github-mcp' }],
    },
    {
      id: 'two', kind: 'mcp_server', name: 'browser', description: '',
      scope: 'user', origin: 'User config', state: 'disabled', source_id: 'source:two',
      source_label: 'User config', changed_after_start: true,
      meta: [{ label: 'Transport', value: 'http' }],
    },
  ],
}]

test('environment labels keep scope, state, and completeness as separate axes', () => {
  assert.equal(agentScopeLabel('built_in'), 'Built in')
  assert.equal(agentScopeLabel('session'), 'Session')
  assert.equal(agentStateLabel('restart_required'), 'restart required')
  assert.equal(agentCompletenessLabel('configured_only'), 'Configured only')
})

test('environment filtering searches identity, origin, state, and safe metadata', () => {
  assert.deepEqual(filterAgentEnvironmentSections(sections, 'github-mcp')[0].items.map(item => item.id), ['one'])
  assert.deepEqual(filterAgentEnvironmentSections(sections, 'disabled')[0].items.map(item => item.id), ['two'])
  assert.deepEqual(filterAgentEnvironmentSections(sections, 'global')[0].items.map(item => item.id), ['two'])
  assert.deepEqual(filterAgentEnvironmentSections(sections, 'missing'), [])
  assert.equal(filterAgentEnvironmentSections(sections, '')[0], sections[0])
})
