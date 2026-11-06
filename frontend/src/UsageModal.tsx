import { t } from './localization/index.ts'
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
  return <div data-dialog-id="usage" class={`usage-layer resources-layer usage-dialog usage-${segment}`} role="dialog" aria-modal="true" aria-label={t("UsageModal.d96a9b9cc5")} onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}>
    <section class="usage-panel resources-panel analytics-panel" ref={panel}>
      <header><div><span>{t("UsageModal.d96a9b9cc5")}</span><strong>{active.title}</strong></div><div class="usage-header-actions"><button onClick={onConfigure} aria-label={t("UsageModal.5b4ca7d0d0")}>{t("UsageModal.74a883a037")}</button><button aria-label={t("UsageModal.379788c76b")} onClick={onClose}>×</button></div></header>
      <AnalyticsNav primary label={t("UsageModal.278e411480")} value={segment} onChange={setSegment} items={USAGE_SEGMENTS}/>
      {segment==='overview' && <UsageOverview onOpen={setSegment} sessions={sessions}/>}
      {segment==='agents' && <UsageAgentsView onConfigure={onConfigure}/>}
      {segment==='quota' && <QuotaAnalytics onManage={onManageAccounts}/>}
      {segment==='activity' && <FleetActivityView sessions={sessions} projects={projects} onOpenSession={onOpenSession} onOpenHistory={onOpenHistory}/>}
      {segment==='automation' && <>{onOpenAutomation && <div class="analytics-toolbar"><button onClick={onOpenAutomation}>{t("UsageModal.a06de73c5a")}</button></div>}<main class="analytics-content"><AutomationSpendView/></main></>}
      <footer>{active.footer}</footer>
    </section>
  </div>
}
