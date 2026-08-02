export type AgentContextStatus = 'available' | 'missing' | 'disabled' | 'unsupported' | 'unreadable' | 'too_large'
export type AgentContextComparison = 'in_sync' | 'different' | 'missing'
export type AgentContextDirection = 'claude_to_agents' | 'agents_to_claude'

export interface AgentContextSource {
  id: string
  provider: 'claude' | 'codex'
  kind: 'instructions' | 'memory'
  label: string
  status: AgentContextStatus
  detail?: string
  revision?: string
  size: number
  modified_at: number | null
  line_ending?: 'lf' | 'crlf'
  changed_since_start?: boolean
}

export interface AgentContextProvider {
  id: 'claude' | 'codex'
  label: string
  status: AgentContextStatus
  detail: string
  items: AgentContextSource[]
  truncated: boolean
}

export interface AgentContextBackup {
  id: string
  target: 'CLAUDE.md' | 'AGENTS.md'
  created_at: number
  existed: boolean
  revision: string
  size: number
}

export interface AgentContextInventory {
  project: { id: string; name: string }
  generated_at: number
  instructions: {
    comparison: AgentContextComparison
    items: AgentContextSource[]
  }
  providers: AgentContextProvider[]
  backups: AgentContextBackup[]
}

export interface AgentContextRead {
  source: Omit<AgentContextSource, 'status'>
  text: string
}

export interface AgentContextSyncPreview {
  direction: AgentContextDirection
  source: { label: string; revision: string }
  target: { label: string; revision: string }
  in_sync: boolean
  diff: string
  diff_truncated: boolean
}

export const AGENT_CONTEXT_SYNC_OPTIONS: Array<{
  direction: AgentContextDirection
  source: 'CLAUDE.md' | 'AGENTS.md'
  target: 'CLAUDE.md' | 'AGENTS.md'
}> = [
  { direction: 'claude_to_agents', source: 'CLAUDE.md', target: 'AGENTS.md' },
  { direction: 'agents_to_claude', source: 'AGENTS.md', target: 'CLAUDE.md' },
]

export function comparisonLabel(value: AgentContextComparison): string {
  if (value === 'in_sync') return 'In sync'
  if (value === 'different') return 'Different'
  return 'One or both missing'
}

export function statusLabel(value: AgentContextStatus): string {
  return value === 'too_large' ? 'too large' : value
}

export function backupAction(backup: AgentContextBackup): string {
  return backup.existed ? `Restore ${backup.target}` : `Remove created ${backup.target}`
}
