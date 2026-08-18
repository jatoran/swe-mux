import { useEffect, useState } from 'preact/hooks'
import { ProcessFleetView } from './ProcessFleetView'
import type { Preview, ProjectScope } from './processFleet'
import type { Project, Session } from './types'

// The drawer's Processes tab.
//
// It draws `ProcessFleetView` — the same surface as the modal Process fleet inspector, with the
// same trees, evidence, listeners, previews, and interrupt/terminate actions. It differs in two
// ways, and only these two: it opens scoped to the active Project, and it marks and pins the
// focused terminal's session so "what is *this* session running" is answered without a scroll.
//
// This tab used to be a watch-only rollup, on the argument that a destructive confirm in a
// 300 px column is how the wrong tree gets killed. That argument was about layout, and it is
// answered by layout: the column renders the narrow layout the modal already used on a phone,
// and the confirm is the same two-press confirm in both places. What it cost was that the
// surface you have open beside a terminal could not answer the question you opened it with.
//
// It no longer reads the reduced `?summary=1` sample the sidebar rail polls — trees and evidence
// are not in that projection — so it subscribes to the shared full-snapshot feed instead. That
// feed is refcounted: a closed tab polls nothing, and a tab open in both drawer stacks with the
// modal over it is still one request per tick.

type Props = {
  sessions: Session[]
  projects: Project[]
  /** The active Project, which is what an unscoped open means. */
  projectId: string
  /** Marked and pinned first within its Project. */
  focusedSessionId: string | null
  scope: ProjectScope
  onScope: (scope: ProjectScope) => void
  onSelectSession: (sessionId: string) => void
  onAttached: (preview: Preview, project: Project) => void
  onOpenInspector: (projectId: string | null) => void
  onDone: () => void
}

export function ProcessesTab({
  sessions, projects, projectId, focusedSessionId, scope, onScope,
  onSelectSession, onAttached, onOpenInspector, onDone,
}: Props) {
  // Drill-down is the tab's own, not the caller's: it is a momentary "just this session" read,
  // and carrying it across a Project change would scope the tab to a session that is no longer
  // on screen.
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null)
  useEffect(() => setSelectedSessionId(null), [projectId])

  return <div class="processes-tab">
    <ProcessFleetView
      sessions={sessions}
      projects={projects}
      variant="drawer"
      projectScope={scope}
      onProjectScope={onScope}
      selectedSessionId={selectedSessionId}
      onSelectedSessionId={setSelectedSessionId}
      focusedSessionId={focusedSessionId}
      onAttached={(preview, project) => { onAttached(preview, project); onDone() }}
      // Revealing a terminal is acting on something other than the drawer, so on mobile the
      // drawer gets out of the way and shows the pane it just selected.
      onRevealSession={sessionId => { onSelectSession(sessionId); onDone() }}
    />
    <div class="processes-footnote">
      <button
        title="Open the same view as a full-width dialog, at this tab's current scope"
        onClick={() => { onOpenInspector(scope || null); onDone() }}
      >Open full width</button>
    </div>
  </div>
}
