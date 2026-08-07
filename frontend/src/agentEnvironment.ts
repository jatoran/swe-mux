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
  /** In-section heading for a contiguous run of items (hooks: the lifecycle event). */
  group?: string
  /** Who provisioned the entry, when knowable: `swe_mux` for swe-mux's own. */
  owner?: string
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
  backend: string
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

const OWNER_LABELS: Record<string, string> = { swe_mux: 'swe-mux' }

export function agentOwnerLabel(owner: string): string {
  return OWNER_LABELS[owner] || agentStateLabel(owner)
}

/**
 * Split a section's items into the consecutive runs that share a `group`.
 *
 * Runs rather than a keyed map: the server already emits each section in the
 * order it wants read (hooks in lifecycle order), so grouping must preserve that
 * order rather than impose an alphabetical one of its own. Items with no group
 * fall into a single unlabelled run, which is every section but Hooks today.
 */
export function groupAgentEnvironmentItems(
  items: AgentEnvironmentItem[],
): Array<{ key: string; items: AgentEnvironmentItem[] }> {
  const runs: Array<{ key: string; items: AgentEnvironmentItem[] }> = []
  for (const item of items) {
    const key = item.group || ''
    const last = runs[runs.length - 1]
    if (last && last.key === key) last.items.push(item)
    else runs.push({ key, items: [item] })
  }
  return runs
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
        item.group || '',
        item.owner ? agentOwnerLabel(item.owner) : '',
        ...item.meta.flatMap(meta => [meta.label, meta.value]),
      ].join(' ').toLowerCase().includes(needle),
    ),
  })).filter(section => section.items.length > 0)
}
