import { useEffect, useRef, useState } from 'preact/hooks'
import type { Session, Project } from './types'
import { useModalFocus } from './modalFocus'
import { ProcessFleetView } from './ProcessFleetView'
import type { Preview } from './processFleet'
import { sessionDisplayName } from './sessionNames'

// The modal shell around process inspection.
//
// The surface itself is `ProcessFleetView`, shared with the drawer's Processes tab: the two
// differ in chrome and default scope and in nothing else. What is left here is what only a
// modal has — the dialog, its focus trap, the scrim, and a header that names what is being
// inspected — plus the drill-down state that header reads.
//
// Types are re-exported because the fleet snapshot is also what the sidebar's resource summary
// and the Preview surfaces read, and they have imported it from here since before the split.
export type {
  DaemonProcesses, FleetSnapshot, Preview, ProcessItem, SessionProcesses,
} from './processFleet'

type Props = {
  initialSessionId?: string | null
  initialProjectId?: string | null
  sessions: Session[]
  projects: Project[]
  onClose: () => void
  onAttached: (preview: Preview, project: Project) => void
}

export function ProcessPanel({
  initialSessionId = null, initialProjectId = null, sessions, projects, onClose, onAttached,
}: Props) {
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(initialSessionId)
  // Set when opened from a Project row; clearable so the fleet stays browsable.
  const [projectScope, setProjectScope] = useState(initialProjectId || '')
  const panel = useRef<HTMLElement>(null)
  useModalFocus(panel, onClose)
  useEffect(() => setSelectedSessionId(initialSessionId), [initialSessionId])

  const selectedSession = sessions.find(item => item.id === selectedSessionId) || null
  const scopedProject = projects.find(item => item.id === projectScope)
  const heading = selectedSession
    ? `${projects.find(item => item.id === selectedSession.project_id)?.name || 'project'} :: ${sessionDisplayName(selectedSession)} · PID ${selectedSession.pid}`
    : scopedProject
      ? `${scopedProject.name} — every session, listener, and process tree`
      : 'All projects, sessions, and swe-mux infrastructure'

  return <div class="process-layer" onPointerDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <section
      ref={panel}
      class="process-panel unified"
      role="dialog"
      aria-modal="true"
      aria-label={selectedSession ? `Processes for ${sessionDisplayName(selectedSession)}` : 'All processes'}
    >
      <header>
        <div>
          {selectedSessionId && <button class="process-back" onClick={() => setSelectedSessionId(null)}>← all processes</button>}
          <span>{selectedSessionId ? 'SESSION PROCESSES' : 'PROCESS FLEET'}</span>
          <strong>{heading}</strong>
        </div>
        <button aria-label="Close process inspector" onClick={onClose}>×</button>
      </header>
      <ProcessFleetView
        sessions={sessions}
        projects={projects}
        variant="panel"
        projectScope={projectScope}
        onProjectScope={setProjectScope}
        selectedSessionId={selectedSessionId}
        onSelectedSessionId={setSelectedSessionId}
        // Registering a preview attaches it to the layout behind this dialog, so the dialog
        // has nothing left to show.
        onAttached={(preview, project) => { onAttached(preview, project); onClose() }}
      />
    </section>
  </div>
}
