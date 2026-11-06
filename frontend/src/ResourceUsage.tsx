import { isComposingKey } from './inputComposition.ts'
import { t } from './localization/index.ts'
import { createPortal } from 'preact/compat'
import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import type { FleetSnapshot } from './processFleet'
import { memoryLabel } from './processRows'
import type { Session } from './types'
import { anchoredPopoverStyle } from './providerAccountDisplay'
import { liveSessionCount } from './sessionAttention'
import { combinedResourceTotals } from './resourceTotals'

/** RAM at rail width: `1.4G` / `512M`, since `1.4 GiB` does not fit. */
export const compactMemoryLabel=(bytes:number)=>
  bytes>=1073741824?t("ResourceUsage.20f1a166fc", { value1: (bytes/1073741824).toFixed(1) }):t("ResourceUsage.cf0d24d173", { value1: Math.round(bytes/1048576) })

// The session count leads because it is the one figure here that is always knowable:
// it is counted from the fleet the sidebar is already rendering, while every other
// figure comes from process inspection that can be unavailable outright.
const resourceSummaryLabel=(snapshot:FleetSnapshot|null,liveSessions:number)=>{
  const sessions=t("ResourceUsage.c740adf8dd", { value1: liveSessions, value2: (liveSessions===1) ? 'yes' : 'no' })
  if(!snapshot)return t("ResourceUsage.dbc4af4281", { sessions: sessions })
  if(!snapshot.available)return t("ResourceUsage.a3e21dd4e1", { sessions: sessions })
  const total=combinedResourceTotals(snapshot)
  const cpu=typeof snapshot.system_cpu_pct==='number'?`${Math.round(snapshot.system_cpu_pct)}% cpu`:t("ResourceUsage.8a1b73005f")
  return t("ResourceUsage.28077e3041", { sessions: sessions, cpu: cpu, value3: memoryLabel(total.memory_bytes) })
}

const CpuIcon=()=>
  <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <rect x="6" y="6" width="12" height="12" rx="1"/><rect x="9" y="9" width="6" height="6"/>
    <path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/>
  </svg>

/** A terminal window with a shell prompt in it. The sessions this counts are panes the
 *  operator opens and types into, so the glyph is the pane rather than an abstract
 *  node diagram. */
const SessionsIcon=()=>
  <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <rect x="2.5" y="4" width="19" height="16" rx="1.5"/>
    <path d="M6.5 10.5 9 13l-2.5 2.5M12 15.5h5.5"/>
  </svg>

const RamIcon=()=>
  <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <rect x="2" y="5" width="20" height="12" rx="1"/><path d="M6 8v6m4-6v6m4-6v6m4-6v6M5 17v3m4-3v3m6-3v3m4-3v3"/>
  </svg>

export function ResourceUsageSummary({snapshot,sessions,compact=false,onRefresh,onOpenFleet}:{
  snapshot:FleetSnapshot|null;sessions:Session[];compact?:boolean
  onRefresh:()=>void;onOpenFleet:()=>void
}) {
  const [open,setOpen]=useState(false)
  const [popoverStyle,setPopoverStyle]=useState<Record<string,string>>({})
  // Unique-set-size sampling walks every process's working set, so it is far too
  // expensive for the background poll that feeds `snapshot`. Only while the panel is
  // actually open does it get requested, and only that copy shows the honest figure.
  const [detailed,setDetailed]=useState<FleetSnapshot|null>(null)
  const root=useRef<HTMLDivElement>(null)
  const popover=useRef<HTMLDivElement>(null)
  const shown=(open&&detailed)||snapshot
  const combined=combinedResourceTotals(shown)
  const systemCpu=typeof shown?.system_cpu_pct==='number'?`${shown.system_cpu_pct.toFixed(1)}%`:'sampling…'
  const summaryCpu=typeof snapshot?.system_cpu_pct==='number'?`${Math.round(snapshot.system_cpu_pct)}%`:'…'
  // Counted from the fleet the sidebar already holds, not from the process snapshot:
  // this stays truthful on a host where process inspection is refused.
  const liveSessions=liveSessionCount(sessions)
  const summaryTitle=resourceSummaryLabel(snapshot,liveSessions)
  const reclaimableRam=typeof combined.memory_unique_bytes==='number'?combined.memory_unique_bytes:null
  const position=()=>{const rect=root.current?.getBoundingClientRect();if(rect)setPopoverStyle(anchoredPopoverStyle(rect,false,{width:window.innerWidth,height:window.innerHeight}))}
  const toggle=()=>{if(!open)position();setOpen(value=>!value)}
  useEffect(()=>{
    if(!open){setDetailed(null);return}
    let cancelled=false
    const load=async()=>{
      try{
        const detail=await api<FleetSnapshot>('GET','/api/processes?unique_memory=1')
        if(!cancelled)setDetailed(detail)
      }catch{ if(!cancelled)setDetailed(null) }
    }
    void load()
    // Refreshed while open so an operator watching the panel is not reading a frozen
    // sample, but never on the rail's own cadence.
    const timer=setInterval(()=>{if(!document.hidden)void load()},10000)
    return()=>{cancelled=true;clearInterval(timer)}
  },[open])
  useEffect(()=>{
    if(!open)return
    position()
    const reposition=()=>position()
    const dismiss=(event:PointerEvent)=>{const target=event.target as Node;if(!root.current?.contains(target)&&!popover.current?.contains(target))setOpen(false)}
    const key=(event:KeyboardEvent)=>{
      if (isComposingKey(event)) return
if(event.key==='Escape')setOpen(false)}
    window.addEventListener('resize',reposition);window.addEventListener('scroll',reposition,true);window.addEventListener('pointerdown',dismiss);window.addEventListener('keydown',key)
    return()=>{window.removeEventListener('resize',reposition);window.removeEventListener('scroll',reposition,true);window.removeEventListener('pointerdown',dismiss);window.removeEventListener('keydown',key)}
  },[open])
  const popup=open&&<div ref={popover} class="account-popover resource-usage-popover ui-portal" style={popoverStyle} role="dialog" aria-label={t("ResourceUsage.a9c0f43076")}>
    <header><div><strong>{t("ResourceUsage.2e399e922e")}</strong><span>{t("ResourceUsage.7f58886ef7")}</span></div><button aria-label={t("ResourceUsage.71a30368ba")} onClick={()=>setOpen(false)}>×</button></header>
    {!snapshot && <p class="resource-usage-empty">{t("ResourceUsage.626ae6db5e")}</p>}
    {snapshot&&!snapshot.available && <p class="resource-usage-empty">{snapshot.diagnostic || t("ResourceUsage.9dd0f9baea")}</p>}
    {snapshot?.available && <section class="resource-usage-total">
      <article><span>{t("ResourceUsage.fa5497bf3e")}</span><strong>{systemCpu}</strong></article>
      {/* One RAM figure for the whole swe-mux tree (daemon + every project's processes):
          the reclaimable (unique-set) reading when the open-panel sample carries it, the
          working set otherwise. */}
      <article><span>{t("ResourceUsage.bf4b0c03c6")}</span><strong>{memoryLabel(reclaimableRam??combined.memory_bytes)}</strong></article>
      <article><span>{t("ResourceUsage.dae2c8cbe1")}</span><strong>{combined.processes}</strong></article>
    </section>}
    <footer><button onClick={onRefresh}>{t("ResourceUsage.d6cc0a088c")}</button><button onClick={()=>{setOpen(false);onOpenFleet()}}>{t("ResourceUsage.39a6f8361b")}</button></footer>
  </div>
  return <div ref={root} class={`resource-usage-control ${compact?'compact':''}`}>{compact ? <button class="resource-usage-compact" onClick={toggle} aria-expanded={open} aria-label={summaryTitle} title={summaryTitle}>
        <span aria-hidden="true">{t("ResourceUsage.50a66195d3")}</span><strong>{snapshot?.available ? compactMemoryLabel(combined.memory_bytes) : '—'}</strong>
      </button> : <button class="resource-usage-summary" onClick={toggle} aria-expanded={open} aria-label={summaryTitle} title={summaryTitle}>
        {/* Sessions first: the operator's own unit of work leads, and the machine's
            reading of it follows. It carries no em-dash fallback because it never needs
            one — nothing about it depends on the process snapshot having arrived. The
            process count is left to the popover, which is where it is worth reading. */}
        <span class="resource-session-count" aria-hidden="true"><SessionsIcon/>{liveSessions}</span>
        <span class="resource-summary-metric" aria-hidden="true"><CpuIcon/><strong>{summaryCpu}</strong></span>
        <span class="resource-summary-metric" aria-hidden="true"><RamIcon/><strong>{snapshot?.available ? memoryLabel(combined.memory_bytes) : '—'}</strong></span>
      </button>}{popup && createPortal(popup,document.body)}</div>
}
