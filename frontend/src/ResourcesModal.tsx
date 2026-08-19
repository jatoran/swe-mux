import { useRef, useState } from 'preact/hooks'
import { useModalFocus } from './modalFocus'
import { NetworkUsageView } from './NetworkUsageModal'
import { StorageUsageView } from './StorageUsageModal'
import { UsageTokensView } from './UsageDashboardView'
import { ProcessFleetView } from './ProcessFleetView'
import type { Preview } from './processFleet'
import type { Project, Session } from './types'
import { sessionDisplayName } from './sessionNames'

// One dialog for everything metered.
//
// Processes, bandwidth, disk, and tokens were four separate modals reached from four
// separate app-menu rows, each with its own layer, focus trap, header, and close control —
// four implementations of one shape, and four rows of a menu that was itself the thing
// people found overwhelming. The question behind all four is the same: what is this
// consuming right now, and is that a lot?
//
// Tokens is the odd one and belongs anyway. Three of these are machine resources and one is
// money, but "how much am I burning" is asked about all four in the same breath, and the
// alternative — a Resources dialog that pointedly excludes the most expensive resource — is
// a taxonomy nobody asked for. The segments are named for the concrete thing each measures
// (Processes / Network / Storage / Tokens) rather than leaning on the dialog title to
// explain the grouping.
//
// What is *not* here: the drawer's Processes tab. This dialog and that tab draw the same
// `ProcessFleetView`, and the tab is not made redundant by it, because a modal covers the
// terminal. "What is this session running" has to be readable beside the session, which is
// the same reason the prompt Queue is a drawer tab and the Fleet Queue is a modal.

export type ResourceSegment = 'processes' | 'network' | 'storage' | 'tokens'

const SEGMENTS: Array<{ id: ResourceSegment; label: string; title: string; heading: string }> = [
  { id: 'processes', label: 'Processes', title: 'Every session, listener, and process tree swe-mux can see', heading: 'PROCESS::FLEET' },
  { id: 'network', label: 'Network', title: 'Bandwidth for this daemon measurement window', heading: 'NETWORK::USAGE' },
  { id: 'storage', label: 'Storage', title: 'Disk swe-mux uses, by area and by project', heading: 'STORAGE::USAGE' },
  { id: 'tokens', label: 'Tokens', title: 'Historical model spend, quota windows, tools, and context evidence', heading: 'USAGE::TELEMETRY' },
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
  /** Tokens: the collector and its retention live in Settings. */
  onConfigureUsage: () => void
}

export function ResourcesModal({
  initial = 'processes', initialSessionId = null, initialProjectId = null,
  sessions, projects, onClose, onAttached, onConfigureUsage,
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
      <div class="resources-segmented" role="tablist" aria-label="Resource">
        {SEGMENTS.map(item => <button
          key={item.id}
          role="tab"
          aria-selected={item.id === segment}
          class={item.id === segment ? 'active' : ''}
          title={item.title}
          onClick={() => setSegment(item.id)}
        >{item.label}</button>)}
      </div>
      {/* Each segment is unmounted when it is not selected, on purpose: three of the four
          poll (Processes on the shared refcounted snapshot feed, Network every three
          seconds), and a dialog that quietly held four live pollers open would cost more
          than the four modals it replaced. Selection is cheap to re-enter; the polling is
          not cheap to leave running. */}
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
      {segment === 'tokens' && <UsageTokensView onConfigure={onConfigureUsage} />}
    </section>
  </div>
}
