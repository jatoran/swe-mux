import { useState } from 'preact/hooks'
import { ScanTimelineTab } from './ScanTimelineTab'
import { FindingsPane } from './FindingsPane'
import { hasHarnessTranscript } from './harnessRegistry'
import type { Project, Session } from './types'

// The Insight drawer tab (Phase 7.10) that replaced the standalone Timeline tab.
// A segmented control over two session/Project read surfaces: the scan Timeline
// (unchanged) and the deterministic Findings pane. The Timeline segment needs a
// harness transcript; the Findings pane does not, so the gate is on the segment,
// not the tab — a shell session still reaches its Project findings here.

type Props = {
  session: Session | null
  project?: Project
  onOpenProjectSettings: (projectId: string) => void
  onOpenAutomationDashboard: () => void
}

export function InsightTab({ session, project, onOpenProjectSettings, onOpenAutomationDashboard }: Props) {
  const timelineAvailable = hasHarnessTranscript(session?.backend)
  const [view, setView] = useState<'timeline' | 'findings'>(timelineAvailable ? 'timeline' : 'findings')
  const active = view === 'timeline' && !timelineAvailable ? 'findings' : view
  return <div class="insight-tab">
    <div class="insight-segmented" role="tablist" aria-label="Insight view">
      <button role="tab" aria-selected={active === 'timeline'} class={active === 'timeline' ? 'active' : ''}
        disabled={!timelineAvailable}
        title={timelineAvailable ? undefined : 'This session has no scan timeline.'}
        onClick={() => setView('timeline')}>Timeline</button>
      <button role="tab" aria-selected={active === 'findings'} class={active === 'findings' ? 'active' : ''}
        onClick={() => setView('findings')}>Findings</button>
    </div>
    {active === 'timeline'
      ? <ScanTimelineTab session={session} onOpenProjectSettings={onOpenProjectSettings} />
      : <FindingsPane session={session} project={project} onOpenAutomationDashboard={onOpenAutomationDashboard} />}
  </div>
}
