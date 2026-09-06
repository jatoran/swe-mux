import { useRef, useState } from 'preact/hooks'
import { AnalyticsNav } from './AnalyticsPrimitives'
import { AutomationSpendView } from './AutomationSpendView'
import { FleetActivityView } from './FleetActivityView'
import { useModalFocus } from './modalFocus'
import { QuotaAnalytics } from './QuotaAnalytics'
import { UsageAgentsView } from './UsageDashboardView'
import { UsageOverview } from './UsageOverview'
import { USAGE_SEGMENTS, type UsageSegment } from './usageSegments'
import type { Project, Session } from './types'
import './analytics.css'

type Props = {
  initial?:UsageSegment; onClose:()=>void; onConfigure:()=>void; onOpenAutomation?:()=>void
  onManageAccounts?:()=>void; sessions?:Session[]; projects?:Project[]
  onOpenSession?:(id:string)=>void; onOpenHistory?:(id:string)=>void
}

export function UsageModal({initial='overview',onClose,onConfigure,onOpenAutomation,onManageAccounts,sessions=[],projects=[],onOpenSession,onOpenHistory}:Props) {
  const [segment,setSegment]=useState<UsageSegment>(initial)
  const panel=useRef<HTMLElement>(null)
  useModalFocus(panel,onClose)
  const active=USAGE_SEGMENTS.find(item=>item.id===segment)!
  return <div class={`usage-layer resources-layer usage-dialog usage-${segment}`} role="dialog" aria-modal="true" aria-label="Usage & activity" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}>
    <section class="usage-panel resources-panel analytics-panel" ref={panel}>
      <header><div><span>Usage &amp; activity</span><strong>{active.title}</strong></div><div class="usage-header-actions"><button onClick={onConfigure} aria-label="Usage settings">Settings</button><button aria-label="Close usage" onClick={onClose}>×</button></div></header>
      <AnalyticsNav primary label="Usage & activity sections" value={segment} onChange={setSegment} items={USAGE_SEGMENTS}/>
      {segment==='overview'&&<UsageOverview onOpen={setSegment} sessions={sessions}/>}
      {segment==='agents'&&<UsageAgentsView onConfigure={onConfigure}/>}
      {segment==='quota'&&<QuotaAnalytics onManage={onManageAccounts}/>}
      {segment==='activity'&&<FleetActivityView sessions={sessions} projects={projects} onOpenSession={onOpenSession} onOpenHistory={onOpenHistory}/>}
      {segment==='automation'&&<>{onOpenAutomation&&<div class="analytics-toolbar"><button onClick={onOpenAutomation}>Manage automation</button></div>}<main class="analytics-content"><AutomationSpendView/></main></>}
      <footer>{active.footer}</footer>
    </section>
  </div>
}
