export type AgentEnvironmentScope = 'built_in' | 'managed' | 'user' | 'project' | 'local' | 'session' | 'unknown'

export interface AgentEnvironmentMeta {
  label: string
  value: string
}

export interface AgentEnvironmentItem {
  id: string
  kind: string
  name: string
  description: string
  scope: AgentEnvironmentScope
  origin: string
  state: string
  source_id: string | null
  source_label: string | null
  changed_after_start: boolean
  meta: AgentEnvironmentMeta[]
}

export interface AgentEnvironmentSection {
  id: string
  label: string
  completeness: string
  items: AgentEnvironmentItem[]
  total: number
  truncated: boolean
  note: string
}

export interface AgentEnvironmentSource {
  id: string
  label: string
  scope: AgentEnvironmentScope
  format: string
  status: string
  mtime: number | null
  changed_after_start: boolean
}

export interface AgentEnvironmentInventory {
  schema_version: number
  backend: 'claude' | 'codex'
  cwd: string
  generated_at: number
  runtime: {
    executable: string
    version: string | null
    model: string | null
    loaded_at: number
    run_started_at: number | null
    options: AgentEnvironmentMeta[]
    modes: string[]
  }
  sources: AgentEnvironmentSource[]
  sections: AgentEnvironmentSection[]
  diagnostics: Array<{ kind: string; source_id: string | null; message: string }>
}

const SCOPE_LABELS: Record<AgentEnvironmentScope, string> = {
  built_in: 'Built in',
  managed: 'Managed',
  user: 'Global',
  project: 'Project',
  local: 'Local',
  session: 'Session',
  unknown: 'Other',
}

export function agentScopeLabel(scope: AgentEnvironmentScope): string {
  return SCOPE_LABELS[scope] || scope
}

export function agentStateLabel(state: string): string {
  return state.replaceAll('_', ' ')
}

export function agentCompletenessLabel(completeness: string): string {
  const labels: Record<string, string> = {
    documented_catalog: 'Documented catalog',
    runtime_dependent: 'Runtime dependent',
    current_filesystem: 'Current files',
    configured_only: 'Configured only',
    installed_config: 'Installed + configured',
    documented_and_configured: 'Documented + configured',
    resolved_known_keys: 'Resolved keys',
  }
  return labels[completeness] || agentStateLabel(completeness)
}

export function filterAgentEnvironmentSections(
  sections: AgentEnvironmentSection[],
  query: string,
): AgentEnvironmentSection[] {
  const needle = query.trim().toLowerCase()
  if (!needle) return sections
  return sections.map(section => ({
    ...section,
    items: section.items.filter(item =>
      [
        item.name,
        item.description,
        item.kind,
        item.origin,
        item.scope,
        agentScopeLabel(item.scope),
        item.state,
        ...item.meta.flatMap(meta => [meta.label, meta.value]),
      ].join(' ').toLowerCase().includes(needle),
    ),
  })).filter(section => section.items.length > 0)
}
