import { useMemo } from 'preact/hooks'
import { memoryLabel } from './ProcessPanel'
import { buildWatchRows, watchTotals, type WatchScope, type WatchSnapshot } from './processWatch'
import type { Project, Session } from './types'

// The drawer's Processes tab: what is running, and what it is serving.
//
// It reads the snapshot `App` already polls for the sidebar's resource summary and its
// spawned-server rows, and starts no loop of its own. That matters more than it looks: the
// reconcile walk behind that data holds the GIL on Windows, and the one rule this feature must
// not break is that a panel left open all day costs the daemon nothing extra.
//
// Nothing here terminates anything. The rollup answers "is it up", which is the question you
// have while looking at a terminal; killing a tree is a decision that needs the evidence line,
// the confidence, and a confirmation step, all of which need the modal's width. `Full inspector`
// is the one-click way there, prefiltered to whatever this tab is scoped to.

type Props = {
  snapshot: WatchSnapshot
  sessions: Session[]
  projects: Project[]
  /** The active Project, which is what an unscoped open means. */
  projectId: string
  /** Pinned first and marked, so "what is this session running" needs no scope change. */
  focusedSessionId: string | null
  scope: WatchScope
  onScope: (scope: WatchScope) => void
  onSelectSession: (sessionId: string) => void
  onOpenPreview: (sessionId: string, url: string) => void
  onOpenInspector: (projectId: string | null) => void
  onRefresh: () => void
  onDone: () => void
}

export function ProcessesTab({
  snapshot, sessions, projects, projectId, focusedSessionId, scope, onScope,
  onSelectSession, onOpenPreview, onOpenInspector, onRefresh, onDone,
}: Props) {
  const rows = useMemo(
    () => buildWatchRows(snapshot, sessions, projects, scope, focusedSessionId),
    [snapshot, sessions, projects, scope, focusedSessionId],
  )
  const totals = watchTotals(rows)
  const activeProject = projects.find(project => project.id === projectId)

  return <div class="processes-tab">
    <div class="processes-filters">
      <select
        aria-label="Filter processes by project"
        value={scope}
        onChange={event => onScope(event.currentTarget.value)}
      >
        <option value={projectId} disabled={!activeProject}>{activeProject ? activeProject.name : 'No project'}</option>
        <option value="">All projects</option>
      </select>
      <button title="Re-read the latest sample" onClick={onRefresh}>refresh</button>
    </div>
    <p class="processes-totals">
      {snapshot
        ? `${totals.sessions} session${totals.sessions === 1 ? '' : 's'} · ${totals.processes} proc · CPU ${totals.cpu_pct.toFixed(1)}% · ${memoryLabel(totals.memory_bytes)}`
        : 'Process data is unavailable from this daemon.'}
    </p>
    <div class="processes-body">
      {snapshot && !rows.length && <p class="processes-empty">
        {scope ? 'Nothing running in this Project.' : 'Nothing running.'}
      </p>}
      {rows.map(row => <section class={`process-watch-row ${row.focused ? 'focused' : ''}`} key={row.sessionId}>
        <button
          class="process-watch-heading"
          title={row.focused ? 'The focused terminal · click to reveal it' : 'Reveal this terminal'}
          onClick={() => { onSelectSession(row.sessionId); onDone() }}
        >
          <span><i class={`state-dot ${row.state}`} /><strong>{row.label}</strong></span>
          <small>
            {!scope && <em class="process-watch-project">{row.projectLabel}</em>}
            {row.processes} proc · {row.cpu_pct.toFixed(1)}% · {memoryLabel(row.memory_bytes)}
          </small>
        </button>
        {/* Listening on a loopback port is the only signal that a child is a server, and a
            preview is the only thing this tab can do about it. Everything else a session
            spawned stays in the inspector. */}
        {row.servers.map(server => <div class="process-watch-server" key={server.port}>
          <span title={server.url}>:{server.port}</span>
          <button title={`Open ${server.url} as a preview tab beside this session`} onClick={() => { onOpenPreview(row.sessionId, server.url); onDone() }}>preview</button>
          <button title={`Copy ${server.url}`} onClick={() => void navigator.clipboard.writeText(server.url)}>copy</button>
        </div>)}
      </section>)}
    </div>
    <div class="processes-footnote">
      <span>Rollups only. Trees, evidence, and terminate live in the inspector.</span>
      <button onClick={() => { onOpenInspector(scope || null); onDone() }}>Full inspector</button>
    </div>
  </div>
}
