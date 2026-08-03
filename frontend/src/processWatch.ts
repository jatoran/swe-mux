/**
 * The row model behind the drawer's Processes tab.
 *
 * The tab is the *watch* half of process inspection, and the split from the modal inspector is
 * deliberate. What you want next to a terminal is "which of my sessions are running something,
 * and is that dev server up" — a handful of numbers and a link. What the modal owns is the
 * *act* half: the full tree with parent lineage, evidence state and confidence, and the
 * interrupt/terminate/terminate-tree row. Those need width to read and a visible confirmation
 * step to be safe, and a 300 px column gives them neither. Nothing here can kill anything.
 *
 * Rows are per session, not per process. A session's tree is mostly bookkeeping (`cmd`,
 * `conhost`, the agent CLI), so a column that listed processes would be a wall of noise around
 * the one row that matters; the rollup plus its listeners is the whole answer at this width.
 *
 * Pure, so the ordering and the exited-process rules are testable without a DOM or a daemon.
 */

// Explicit extension: this module is reachable from the node test runner, whose
// type-stripping ESM loader does not resolve extensionless specifiers.
import { detectedServers, type DetectedServer, type ProcessWithListeners } from './sessionProcesses.ts'

export type WatchProcess = ProcessWithListeners & { cpu_pct?: number; memory_bytes?: number }
export type WatchSnapshot = {
  sessions?: Array<{ session_id: string; project_id?: string; processes?: WatchProcess[] }>
} | null
export type WatchSession = { id: string; name?: string; project_id: string; state?: string; created_at?: number }
export type WatchProject = { id: string; name: string; position: number }

export type WatchRow = {
  sessionId: string
  projectId: string
  /** Session name, falling back to its id when the session itself is already gone. */
  label: string
  projectLabel: string
  state: string
  /** The focused terminal's row, pinned first and marked. */
  focused: boolean
  processes: number
  cpu_pct: number
  memory_bytes: number
  /** Loopback servers this session is running, which are the only actionable rows here. */
  servers: DetectedServer[]
}

export type WatchTotals = { sessions: number; processes: number; cpu_pct: number; memory_bytes: number; servers: number }

/** '' means every Project; anything else scopes to that Project. */
export type WatchScope = string

/**
 * Roll the fleet snapshot up into one row per session.
 *
 * Ended processes are dropped rather than shown greyed: they support no action here, they are
 * already excluded from every total in the app, and at column width a row that cannot be acted
 * on is a row that costs a real one its place. A session left with nothing live drops out
 * entirely, which is the same boundary the daemon's own payload draws.
 *
 * The focused session sorts first so "what is *this* session running" is answerable without
 * changing scope or hunting; everything else follows Project order then session age, the same
 * order the sidebar uses, so rows do not move between samples.
 */
export function buildWatchRows(
  snapshot: WatchSnapshot,
  sessions: WatchSession[],
  projects: WatchProject[],
  scope: WatchScope,
  focusedSessionId: string | null,
): WatchRow[] {
  const sessionById = new Map(sessions.map(session => [session.id, session]))
  const projectById = new Map(projects.map(project => [project.id, project]))
  const rows: WatchRow[] = []
  for (const group of snapshot?.sessions || []) {
    const session = sessionById.get(group.session_id)
    const projectId = session?.project_id || group.project_id || ''
    if (scope && projectId !== scope) continue
    const live = (group.processes || []).filter(process => !process.exited_at)
    if (!live.length) continue
    rows.push({
      sessionId: group.session_id,
      projectId,
      label: session?.name || group.session_id,
      projectLabel: projectById.get(projectId)?.name || 'Unknown project',
      state: session?.state || 'running',
      focused: !!focusedSessionId && group.session_id === focusedSessionId,
      processes: live.length,
      cpu_pct: Math.round(live.reduce((sum, process) => sum + (Number(process.cpu_pct) || 0), 0) * 10) / 10,
      memory_bytes: live.reduce((sum, process) => sum + (Number(process.memory_bytes) || 0), 0),
      servers: detectedServers(live),
    })
  }
  return rows.sort((left, right) => {
    if (left.focused !== right.focused) return left.focused ? -1 : 1
    const byProject = (projectById.get(left.projectId)?.position ?? Number.MAX_SAFE_INTEGER)
      - (projectById.get(right.projectId)?.position ?? Number.MAX_SAFE_INTEGER)
    if (byProject) return byProject
    const byAge = (sessionById.get(left.sessionId)?.created_at || 0) - (sessionById.get(right.sessionId)?.created_at || 0)
    return byAge || left.sessionId.localeCompare(right.sessionId)
  })
}

/** Header figures for whatever the rows currently cover, so they always agree with what is
 *  drawn below them rather than with the unscoped fleet. */
export function watchTotals(rows: WatchRow[]): WatchTotals {
  return {
    sessions: rows.length,
    processes: rows.reduce((sum, row) => sum + row.processes, 0),
    cpu_pct: Math.round(rows.reduce((sum, row) => sum + row.cpu_pct, 0) * 10) / 10,
    memory_bytes: rows.reduce((sum, row) => sum + row.memory_bytes, 0),
    servers: rows.reduce((sum, row) => sum + row.servers.length, 0),
  }
}
