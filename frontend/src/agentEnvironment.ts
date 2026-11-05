import { t } from './localization/index.ts'
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
  /** Differs from the snapshot taken when the CLI loaded. Always false when `config_baseline` is `unavailable`. */
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
  /**
   * Whether "changed since load" is answerable at all. `unavailable` means no
   * load-time snapshot exists for this CLI generation (it started before the
   * daemon began taking them, or was recovered cold), so every source reads
   * unchanged because nothing is known — not because nothing moved.
   */
  config_baseline: 'captured' | 'unavailable'
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

/**
 * How a tool catalog was obtained. Never collapsed into "connected"/"available":
 * the whole point of the tiers is that a sidecar's health is not the running
 * CLI's, and a reader must be able to tell which one they are looking at.
 */
export type McpToolEvidence = 'swe_mux_owned' | 'live_process' | 'parallel_probe' | 'not_supported'

export type McpToolStatus = 'ok' | 'auth_required' | 'unsupported' | 'unavailable' | 'error'

export interface McpToolCatalog {
  server: string
  backend: string
  evidence: McpToolEvidence
  status: McpToolStatus
  tools: Array<{ name: string; description: string; read_only?: boolean }>
  total: number
  truncated: boolean
  note: string
  diagnostic: string
  observed_at: number
  ttl_ms: number
  cache_scope: 'public' | 'private'
  server_version: string
  fingerprint: string
  cached: boolean
}

const EVIDENCE_LABELS: Record<McpToolEvidence, string> = {
  get swe_mux_owned() { return t("agentEnvironment.60257385ed") },
  get live_process() { return t("agentEnvironment.94f506b7d3") },
  get parallel_probe() { return t("agentEnvironment.b00ddded31") },
  get not_supported() { return t("agentEnvironment.15f6993e8b") },
}

export function mcpEvidenceLabel(evidence: McpToolEvidence): string {
  return EVIDENCE_LABELS[evidence] || evidence
}

const STATUS_LABELS: Record<McpToolStatus, string> = {
  ok: '',
  get auth_required() { return t("agentEnvironment.e0255ee725") },
  get unsupported() { return t("agentEnvironment.366c030ffe") },
  get unavailable() { return t("agentEnvironment.2087cb5f8b") },
  get error() { return t("agentEnvironment.5a414e1bab") },
}

export function mcpStatusLabel(status: McpToolStatus): string {
  return STATUS_LABELS[status] ?? status
}

const SCOPE_LABELS: Record<AgentEnvironmentScope, string> = {
  get built_in() { return t("agentEnvironment.78a6a8fe9b") },
  get managed() { return t("agentEnvironment.8f2de600bf") },
  get user() { return t("agentEnvironment.a258b30f88") },
  get project() { return t("agentEnvironment.9859597853") },
  get local() { return t("agentEnvironment.8c31e6e722") },
  get session() { return t("agentEnvironment.6959b41595") },
  get unknown() { return t("agentEnvironment.f97e9da0e3") },
}

export function agentScopeLabel(scope: AgentEnvironmentScope): string {
  return SCOPE_LABELS[scope] || scope
}

export function agentStateLabel(state: string): string {
  return state.replaceAll('_', ' ')
}

const OWNER_LABELS: Record<string, string> = { get swe_mux() { return t("agentEnvironment.142caaf1c5") } }

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
    get documented_catalog() { return t("agentEnvironment.ec9551cc75") },
    get runtime_dependent() { return t("agentEnvironment.cf3123840b") },
    get current_filesystem() { return t("agentEnvironment.eef7a2320b") },
    get configured_only() { return t("agentEnvironment.0752fa4725") },
    installed_config: 'Installed + configured',
    documented_and_configured: 'Documented + configured',
    get resolved_known_keys() { return t("agentEnvironment.45a3aa1edc") },
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
