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
  bytes>=1073741824?`${(bytes/1073741824).toFixed(1)}G`:`${Math.round(bytes/1048576)}M`

// The session count leads because it is the one figure here that is always knowable:
// it is counted from the fleet the sidebar is already rendering, while every other
// figure comes from process inspection that can be unavailable outright.
const resourceSummaryLabel=(snapshot:FleetSnapshot|null,liveSessions:number)=>{
  const sessions=`${liveSessions} live session${liveSessions===1?'':'s'}`
  if(!snapshot)return `${sessions}, loading resource usage - click to see usage details`
  if(!snapshot.available)return `${sessions}, resource usage unavailable - click to see usage details`
  const total=combinedResourceTotals(snapshot)
  const cpu=typeof snapshot.system_cpu_pct==='number'?`${Math.round(snapshot.system_cpu_pct)}% cpu`:'cpu sampling'
  return `${sessions}, ${total.processes} process${total.processes===1?'':'es'}, ${cpu}, ${memoryLabel(total.memory_bytes)} ram - click to see usage details`
}

const CpuIcon=()=>
  <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <rect x="6" y="6" width="12" height="12" rx="1"/><rect x="9" y="9" width="6" height="6"/>
    <path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/>
  </svg>

const ProcessesIcon=()=>
  <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <circle cx="12" cy="5" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="18" r="2.5"/>
    <path d="M12 7.5V12M6 15.5V12h12v3.5"/>
  </svg>

/** A terminal window with a shell prompt in it. The sessions this counts are panes the
 *  operator opens and types into, so the glyph is the pane rather than another abstract
 *  node diagram - the process tree next to it already owns that shape. */
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
    const key=(event:KeyboardEvent)=>{if(event.key==='Escape')setOpen(false)}
    window.addEventListener('resize',reposition);window.addEventListener('scroll',reposition,true);window.addEventListener('pointerdown',dismiss);window.addEventListener('keydown',key)
    return()=>{window.removeEventListener('resize',reposition);window.removeEventListener('scroll',reposition,true);window.removeEventListener('pointerdown',dismiss);window.removeEventListener('keydown',key)}
  },[open])
  const popup=open&&<div ref={popover} class="account-popover resource-usage-popover ui-portal" style={popoverStyle} role="dialog" aria-label="Swe-mux resource usage">
    <header><div><strong>RESOURCE USAGE</strong><span>system CPU · swe-mux process tree</span></div><button aria-label="Close resource usage" onClick={()=>setOpen(false)}>×</button></header>
    {!snapshot&&<p class="resource-usage-empty">Loading resource usage…</p>}
    {snapshot&&!snapshot.available&&<p class="resource-usage-empty">{snapshot.diagnostic||'Process resource inspection is unavailable.'}</p>}
    {snapshot?.available&&<section class="resource-usage-total">
      <article><span>SYSTEM CPU</span><strong>{systemCpu}</strong></article>
      {/* One RAM figure for the whole swe-mux tree (daemon + every project's processes):
          the reclaimable (unique-set) reading when the open-panel sample carries it, the
          working set otherwise. */}
      <article><span>RAM</span><strong>{memoryLabel(reclaimableRam??combined.memory_bytes)}</strong></article>
      <article><span>PROCESSES</span><strong>{combined.processes}</strong></article>
    </section>}
    <footer><button onClick={onRefresh}>refresh</button><button onClick={()=>{setOpen(false);onOpenFleet()}}>open process fleet…</button></footer>
  </div>
  return <div ref={root} class={`resource-usage-control ${compact?'compact':''}`}>{compact
    ? <button class="resource-usage-compact" onClick={toggle} aria-expanded={open} aria-label={summaryTitle} title={summaryTitle}>
        <span aria-hidden="true">ws</span><strong>{snapshot?.available?compactMemoryLabel(combined.memory_bytes):'—'}</strong>
      </button>
    : <button class="resource-usage-summary" onClick={toggle} aria-expanded={open} aria-label={summaryTitle} title={summaryTitle}>
        {/* Sessions first, then the processes they add up to: the operator's own unit
            of work leads, and the machine's reading of it follows. It carries no
            em-dash fallback because it never needs one — nothing about it depends on
            the process snapshot having arrived. */}
        <span class="resource-session-count" aria-hidden="true"><SessionsIcon/>{liveSessions}</span>
        <span class="resource-process-count" aria-hidden="true"><ProcessesIcon/>{snapshot?.available?combined.processes:'—'}</span>
        <span class="resource-summary-metric" aria-hidden="true"><CpuIcon/><strong>{summaryCpu}</strong></span>
        <span class="resource-summary-metric" aria-hidden="true"><RamIcon/><strong>{snapshot?.available?memoryLabel(combined.memory_bytes):'—'}</strong></span>
      </button>}{popup&&createPortal(popup,document.body)}</div>
}
