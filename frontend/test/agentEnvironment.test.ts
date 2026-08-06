import assert from 'node:assert/strict'
import test from 'node:test'
import {
  agentCompletenessLabel,
  agentOwnerLabel,
  agentScopeLabel,
  agentStateLabel,
  filterAgentEnvironmentSections,
  groupAgentEnvironmentItems,
  type AgentEnvironmentItem,
  type AgentEnvironmentSection,
} from '../src/agentEnvironment.ts'

function hook(name: string, group: string, owner = ''): AgentEnvironmentItem {
  return {
    id: `${group}:${name}`, kind: 'hook', name, description: '', scope: 'user',
    origin: '~/.codex/hooks.json', state: 'configured', group, owner,
    source_id: 'source:hooks', source_label: '~/.codex/hooks.json',
    changed_after_start: false, meta: [{ label: 'Runs', value: `/tools/${name}` }],
  }
}

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

test('hook filtering reaches the lifecycle event and the installing owner', () => {
  const hooks: AgentEnvironmentSection[] = [{
    ...sections[0],
    id: 'hooks',
    items: [hook('state.ps1', 'SessionStart'), hook('hook_client', 'Stop', 'swe_mux')],
  }]
  assert.equal(agentOwnerLabel('swe_mux'), 'swe-mux')
  assert.deepEqual(filterAgentEnvironmentSections(hooks, 'sessionstart')[0].items.map(item => item.name), ['state.ps1'])
  assert.deepEqual(filterAgentEnvironmentSections(hooks, 'swe-mux')[0].items.map(item => item.name), ['hook_client'])
})

test('items group into the consecutive runs the server ordered them in', () => {
  const runs = groupAgentEnvironmentItems([
    hook('state.ps1', 'SessionStart'),
    hook('hook_client', 'SessionStart', 'swe_mux'),
    hook('notify', 'Stop'),
  ])
  assert.deepEqual(runs.map(run => [run.key, run.items.length]), [['SessionStart', 2], ['Stop', 1]])
})

test('a section with no groups stays one unlabelled run', () => {
  const runs = groupAgentEnvironmentItems(sections[0].items)
  assert.deepEqual(runs.map(run => run.key), [''])
  assert.equal(runs[0].items.length, 2)
})
