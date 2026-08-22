import { useRef, useState } from 'preact/hooks'
import { useModalFocus } from './modalFocus'
import { NetworkUsageView } from './NetworkUsageModal'
import { StorageUsageView } from './StorageUsageModal'
import { FleetActivityView } from './FleetActivityView'
import { ProcessFleetView } from './ProcessFleetView'
import type { Preview } from './processFleet'
import type { Project, Session } from './types'
import { sessionDisplayName } from './sessionNames'

// One dialog for what the machine and the fleet are doing.
//
// Processes, bandwidth, and disk were three separate modals reached from three separate
// app-menu rows, each with its own layer, focus trap, header, and close control - three
// implementations of one shape, in a menu that was itself the thing people found
// overwhelming. The question behind them is the same: what is this consuming, and is that
// a lot?
//
// A fourth segment used to be **Tokens**, and it is now its own `Usage` dialog. It never
// fit, and the way it did not fit was instructive: three quarters of what it held measured
// no tokens and no money at all, and the quarter that did was three separate currencies
// that must never be summed. Its behavioral half - runs, tool calls, compaction - is
// **Fleet activity** here, where it belongs. Processes says what the fleet is running right
// now; Fleet activity says what it has been doing. Both are live-ish readings of this host,
// both are opened when something looks wrong, and neither is a bill.
//
// What is *not* here: the drawer's Processes tab. This dialog and that tab draw the same
// `ProcessFleetView`, and the tab is not made redundant by it, because a modal covers the
// terminal. "What is this session running" has to be readable beside the session, which is
// the same reason the prompt Queue is a drawer tab and the Fleet Queue is a modal.

export type ResourceSegment = 'processes' | 'network' | 'storage' | 'fleet'

const SEGMENTS: Array<{ id: ResourceSegment; label: string; title: string; heading: string }> = [
  { id: 'processes', label: 'Processes', title: 'Every session, listener, and process tree swe-mux can see', heading: 'PROCESS::FLEET' },
  { id: 'network', label: 'Network', title: 'Bandwidth for this daemon measurement window', heading: 'NETWORK::USAGE' },
  { id: 'storage', label: 'Storage', title: 'Disk swe-mux uses, by area and by project', heading: 'STORAGE::USAGE' },
  { id: 'fleet', label: 'Fleet activity', title: 'What the agents have been doing: runs, tools and skills, context compaction', heading: 'FLEET::ACTIVITY' },
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
  // three measure one fixed thing each and say so in `SEGMENTS`.
  const subtitle = segment !== 'processes' ? active.title
    : selectedSession
      ? `${projects.find(item => item.id === selectedSession.project_id)?.name || 'project'} :: ${sessionDisplayName(selectedSession)} · PID ${selectedSession.pid}`
      : scopedProject
        ? `${scopedProject.name} — every session, listener, and process tree`
        : 'All projects, sessions, and swe-mux infrastructure'

  return <div
    class={`usage-layer resources-layer resources-${segment}`}
    role="dialog"
    aria-modal="true"
    aria-label="Resources"
    onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}
  >
    <section class="usage-panel resources-panel" ref={panel}>
      <header>
        <div>
          {segment === 'processes' && selectedSessionId && <button class="process-back" onClick={() => setSelectedSessionId(null)}>← all processes</button>}
          <span>{segment === 'processes' && selectedSessionId ? 'SESSION PROCESSES' : active.heading}</span>
          <strong>{subtitle}</strong>
        </div>
        <div class="usage-header-actions"><button aria-label="Close resources" onClick={onClose}>×</button></div>
      </header>
      <div class="segmented-tabs resources-segmented" role="tablist" aria-label="Resource">
        {SEGMENTS.map(item => <button
          key={item.id}
          role="tab"
          aria-selected={item.id === segment}
          class={item.id === segment ? 'active' : ''}
          title={item.title}
          onClick={() => setSegment(item.id)}
        >{item.label}</button>)}
      </div>
      {/* Each segment is unmounted when it is not selected, on purpose: two of the four
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
      {segment === 'fleet' && <FleetActivityView />}
    </section>
  </div>
}
