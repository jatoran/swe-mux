import { useRef, useState } from 'preact/hooks'
import { useModalFocus } from './modalFocus'
import { NetworkUsageView } from './NetworkUsageModal'
import { StorageUsageView } from './StorageUsageModal'
import { ProcessFleetView } from './ProcessFleetView'
import type { Preview } from './processFleet'
import type { Project, Session } from './types'
import { sessionDisplayName } from './sessionNames'

// Host resources share one frame; agent behavior lives in Usage & activity.

export type ResourceSegment = 'processes' | 'network' | 'storage'

const SEGMENTS: Array<{ id: ResourceSegment; label: string; title: string; heading: string }> = [
  { id: 'processes', label: 'Processes', title: 'Every session, listener, and process tree swe-mux can see', heading: 'PROCESS::FLEET' },
  { id: 'network', label: 'Network', title: 'Bandwidth for this daemon measurement window', heading: 'NETWORK::USAGE' },
  { id: 'storage', label: 'Storage', title: 'Disk swe-mux uses, by area and by project', heading: 'STORAGE::USAGE' },
]

type Props = {
  /** Which segment to open on. A caller that named one has already said what it wants. */
  initial?: ResourceSegment
  /** Processes only: drill straight into one session's trees. */
  initialSessionId?: string | null
  initialProjectId?: string | null
  sessions: Session[]
  projects: Project[]
  onClose: () => void
  onAttached: (preview: Preview, project: Project) => void
}

export function ResourcesModal({
  initial = 'processes', initialSessionId = null, initialProjectId = null,
  sessions, projects, onClose, onAttached,
}: Props) {
  const [segment, setSegment] = useState<ResourceSegment>(initial)
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(initialSessionId)
  const [projectScope, setProjectScope] = useState(initialProjectId || '')
  const panel = useRef<HTMLElement>(null)
  useModalFocus(panel, onClose)

  const active = SEGMENTS.find(item => item.id === segment) || SEGMENTS[0]
  const selectedSession = sessions.find(item => item.id === selectedSessionId) || null
  const scopedProject = projects.find(item => item.id === projectScope)
  // Only Processes has a heading that changes with what is selected inside it; the other
  // two measure one fixed thing each and say so in `SEGMENTS`.
  const subtitle = segment !== 'processes' ? active.title
    : selectedSession
      ? `${projects.find(item => item.id === selectedSession.project_id)?.name || 'project'} :: ${sessionDisplayName(selectedSession)} · PID ${selectedSession.pid}`
      : scopedProject
        ? `${scopedProject.name} - every session, listener, and process tree`
        : 'All projects, sessions, and swe-mux infrastructure'

  return <div
    class={`usage-layer resources-layer resources-${segment}`}
    role="dialog"
    aria-modal="true"
    aria-label="System"
    onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}
  >
    <section class="usage-panel resources-panel" ref={panel}>
      <header>
        <div>
          {segment === 'processes' && selectedSessionId && <button class="process-back" onClick={() => setSelectedSessionId(null)}>← all processes</button>}
          <span>{segment === 'processes' && selectedSessionId ? 'Session processes' : 'System'}</span>
          <strong>{subtitle}</strong>
        </div>
        <div class="usage-header-actions"><button aria-label="Close system" onClick={onClose}>×</button></div>
      </header>
      <div class="segmented-tabs resources-segmented" role="tablist" aria-label="System sections">
        {SEGMENTS.map(item => <button
          key={item.id}
          role="tab"
          aria-selected={item.id === segment}
          class={item.id === segment ? 'active' : ''}
          title={item.title}
          onClick={() => setSegment(item.id)}
        >{item.label}</button>)}
      </div>
      {/* Each segment is unmounted when it is not selected, on purpose: two of the three
          poll (Processes on the shared refcounted snapshot feed, Network every three
          seconds), and a dialog that quietly held live pollers open would cost more than
          the modals it replaced. Selection is cheap to re-enter; the polling is not cheap
          to leave running. */}
      {segment === 'processes' && <ProcessFleetView
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
      />}
      {segment === 'network' && <NetworkUsageView />}
      {segment === 'storage' && <StorageUsageView />}
    </section>
  </div>
}
