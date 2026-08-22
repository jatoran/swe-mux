/**
 * The snapshot model both process-inspection surfaces read.
 *
 * There is one inspection surface, rendered twice: the modal `Process fleet` and the drawer's
 * `Processes` tab. They differ in chrome and default scope, never in what they can show or do,
 * so everything that decides *what is on screen* — normalization, the older-daemon fallback,
 * scoping, and the totals line — lives here rather than in either renderer.
 *
 * Pure, and free of `.tsx` imports, so the node test runner can load it.
 */

export type RowListener = { host: string; port: number; loopback: boolean; url: string }
export type RowConnection = {
  local_host: string; local_port: number; remote_host: string; remote_port: number
}

export type ProcessItem = {
  pid: number; parent_pid?: number; executable: string; command: string; started_at?: number
  exited_at?: number; cpu_pct: number; memory_bytes: number; memory_unique_bytes?: number | null
  listeners: RowListener[]; connections: RowConnection[]; conditions: string[]
  identity_id?: string; command_hash?: string
  parent_lineage?: Array<{ pid: number; creation_time?: number }>
  job_assignment?: string
  evidence_state?: 'active' | 'exited' | 'escaped' | 'suspected_orphan' | 'stale' | 'inaccessible'
  evidence_reason?: string; confidence?: 'high' | 'medium' | 'low'
  first_seen?: number; last_seen?: number; last_verified_at?: number; exit_evidence?: string
  startup_revalidated?: boolean; attribution_version?: number
  attribution_source?: 'session_root' | 'parent_walk' | 'job_membership' | 'legacy' | 'unknown'
  last_attributed_at?: number; last_job_confirmed_at?: number; server_eligible?: boolean
}

export type SessionProcesses = {
  session_id: string; project_id: string; processes: ProcessItem[]
}

export type OwnershipDiagnostic = {
  ts: number; kind: string; pid?: number; session_id?: string; other_session_id?: string
  parent_pid?: number; reason?: string
}

export type DaemonProcesses = {
  pid: number; processes: number; cpu_pct: number; memory_bytes: number
  memory_unique_bytes?: number | null
  listeners?: number; connections?: number; members?: ProcessItem[]
}

export type FleetTotals = {
  processes: number; cpu_pct: number; memory_bytes: number
  memory_unique_bytes?: number | null; listeners: number; connections: number
}

export type FleetSnapshot = {
  available: boolean; diagnostic?: string; sessions: SessionProcesses[]
  system_cpu_pct?: number | null
  daemon?: DaemonProcesses
  ownership_diagnostics?: OwnershipDiagnostic[]
  totals: FleetTotals
}

export type SessionSnapshot = {
  available: boolean; diagnostic?: string; session_id: string; processes: ProcessItem[]
  ownership_diagnostics?: OwnershipDiagnostic[]
}

export type Preview = {
  id: string; session_id: string; project_id: string; url: string; host: string; port: number
  source: string; viewport: string; listed?: boolean
  /** 'loopback' proxies a session-owned dev server; 'static' is a document in the
   *  Project checkout the daemon serves itself. Absent on an older daemon, which
   *  only ever had the first kind. */
  kind?: 'loopback'|'static'
  /** Static only: the file name to draw, the served directory, the entry within
   *  it, that directory relative to the checkout root (what the file watcher
   *  speaks in), and the exact worktree it came from ('' for the Project root). */
  label?: string; doc_root?: string; entry?: string; doc_root_relative?: string; worktree?: string
}

/** What a Preview is called on a tab, a sidebar row, and its own header.
 *  A loopback preview is known by its port; a static one has none, so its file
 *  name is the only thing that identifies it. */
export const previewLabel = (preview: Preview): string =>
  preview.kind === 'static' ? (preview.label || preview.entry || preview.id) : `:${preview.port}`

export const isStaticPreview = (preview: Preview): boolean => preview.kind === 'static'

/** '' means every Project; anything else scopes a surface to that Project. */
export type ProjectScope = string

/**
 * The Project scope a docked surface should draw, given what the user last chose.
 *
 * `null` and `''` are deliberately not the same thing. `null` is "never scoped", which means
 * the Project the drawer is sitting beside — so the tab follows a Project switch instead of
 * pinning whichever Project was active when it first opened. `''` is the user having asked for
 * every Project, and it has to survive, because collapsing the two made `All projects`
 * unselectable: choosing it stored a falsy value that read as "never scoped" and snapped
 * straight back to the active Project.
 *
 * A stored id whose Project has since been deleted falls back the same way `null` does.
 */
export function resolveProjectScope(
  stored: ProjectScope | null,
  activeProjectId: string,
  projects: Array<{ id: string }>,
): ProjectScope {
  if (stored === null) return activeProjectId
  if (stored === '') return ''
  return projects.some(project => project.id === stored) ? stored : activeProjectId
}

/** Structural, so both a `Session` and a test fixture satisfy it. */
type ScopeSession = { id: string; project_id: string }

const normalizeProcesses = (processes: ProcessItem[]): ProcessItem[] =>
  (processes || []).map(item => ({
    ...item,
    listeners: item.listeners || [],
    connections: item.connections || [],
    conditions: item.conditions || [],
  }))

export const processTotals = (processes: ProcessItem[]): FleetTotals => ({
  processes: processes.length,
  cpu_pct: processes.reduce((total, item) => total + item.cpu_pct, 0),
  memory_bytes: processes.reduce((total, item) => total + item.memory_bytes, 0),
  listeners: processes.reduce((total, item) => total + item.listeners.length, 0),
  connections: processes.reduce((total, item) => total + item.connections.length, 0),
})

/** Accept either payload shape the daemon can return, and never hand a renderer a
 *  missing `listeners`/`connections`/`conditions` array to guard against. */
export function normalizeSnapshot(
  snapshot: FleetSnapshot | SessionSnapshot,
  sessions: ScopeSession[],
): FleetSnapshot {
  if ('sessions' in snapshot) {
    const groups = (snapshot.sessions || []).map(group => ({
      ...group,
      processes: normalizeProcesses(group.processes),
    }))
    const processes = groups.flatMap(group => group.processes)
    const daemon = snapshot.daemon
      ? { ...snapshot.daemon, members: normalizeProcesses(snapshot.daemon.members || []) }
      : undefined
    return { ...snapshot, daemon, sessions: groups, totals: snapshot.totals || processTotals(processes) }
  }
  const processes = normalizeProcesses(snapshot.processes)
  return {
    available: snapshot.available,
    diagnostic: snapshot.diagnostic,
    ownership_diagnostics: snapshot.ownership_diagnostics,
    sessions: [{
      session_id: snapshot.session_id,
      project_id: sessions.find(item => item.id === snapshot.session_id)?.project_id || '',
      processes,
    }],
    totals: processTotals(processes),
  }
}

/** The fallback for a daemon too old to serve the coherent all-session snapshot. */
export function combineSessionSnapshots(
  snapshots: SessionSnapshot[],
  sessions: ScopeSession[],
): FleetSnapshot {
  const normalized = snapshots.map(snapshot => normalizeSnapshot(snapshot, sessions))
  const groups = normalized.flatMap(snapshot => snapshot.sessions)
  const processes = groups.flatMap(group => group.processes)
  return {
    available: normalized.every(snapshot => snapshot.available),
    diagnostic: normalized.find(snapshot => snapshot.diagnostic)?.diagnostic,
    ownership_diagnostics: normalized.flatMap(snapshot => snapshot.ownership_diagnostics || []),
    sessions: groups,
    totals: processTotals(processes),
  }
}

/**
 * The session groups a surface should draw, given its Project scope and drill-down.
 *
 * A session's Project is read from the live session first and from the snapshot second, because
 * a session moved between Projects updates in the app before the next sample carries it.
 */
export function scopedSessionGroups(
  snapshot: FleetSnapshot | null,
  sessions: ScopeSession[],
  projectScope: string,
  selectedSessionId: string | null,
): SessionProcesses[] {
  const sessionById = new Map(sessions.map(session => [session.id, session]))
  const groups = snapshot?.sessions || []
  const scoped = projectScope
    ? groups.filter(group =>
        (sessionById.get(group.session_id)?.project_id || group.project_id) === projectScope)
    : groups
  return selectedSessionId ? scoped.filter(group => group.session_id === selectedSessionId) : scoped
}

const roundCpu = (totals: FleetTotals): FleetTotals =>
  ({ ...totals, cpu_pct: Math.round(totals.cpu_pct * 10) / 10 })

/**
 * The summary line for whatever is actually on screen.
 *
 * Unscoped it is the daemon's own totals plus the runtime bucket, which is the one figure that
 * reconciles with the sidebar's resource summary. Scoped it is recomputed from the visible
 * groups: a header that keeps reporting the whole fleet above a single Project's rows is a
 * number that disagrees with everything beneath it.
 */
export function fleetViewTotals(
  snapshot: FleetSnapshot | null,
  groups: SessionProcesses[],
  scoped: boolean,
): FleetTotals & { sessions: number } {
  if (!snapshot) return { sessions: 0, processes: 0, cpu_pct: 0, memory_bytes: 0, listeners: 0, connections: 0 }
  const live = groups.flatMap(group => group.processes).filter(process => !process.exited_at)
  const sessions = groups.filter(group => group.processes.some(process => !process.exited_at)).length
  if (scoped) return { sessions, ...roundCpu(processTotals(live)) }
  const daemon = snapshot.daemon
  return {
    sessions,
    ...roundCpu({
      processes: snapshot.totals.processes + (daemon?.processes || 0),
      cpu_pct: snapshot.totals.cpu_pct + (daemon?.cpu_pct || 0),
      memory_bytes: snapshot.totals.memory_bytes + (daemon?.memory_bytes || 0),
      listeners: snapshot.totals.listeners + (daemon?.listeners || 0),
      connections: snapshot.totals.connections + (daemon?.connections || 0),
    }),
  }
}
