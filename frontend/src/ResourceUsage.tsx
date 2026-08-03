import { createPortal } from 'preact/compat'
import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import type { FleetSnapshot } from './ProcessPanel'
import { memoryLabel } from './ProcessPanel'
import type { Project, Session } from './types'
import { anchoredPopoverStyle } from './providerAccountDisplay'
import { combinedResourceTotals, projectResourceTotals } from './resourceTotals'
import { duplicateToolingGroups } from './resourceTooling'

/** RAM at rail width: `1.4G` / `512M`, since `1.4 GiB` does not fit. */
export const compactMemoryLabel=(bytes:number)=>
  bytes>=1073741824?`${(bytes/1073741824).toFixed(1)}G`:`${Math.round(bytes/1048576)}M`

const totalLabel=(snapshot:FleetSnapshot|null)=>{
  if(!snapshot)return 'loading resources…'
  if(!snapshot.available)return 'resource data unavailable'
  const total=combinedResourceTotals(snapshot)
  return `cpu ${total.cpu_pct.toFixed(1)}% · working set ${memoryLabel(total.memory_bytes)}`
}

export function ResourceUsageSummary({snapshot,sessions,projects,compact=false,onRefresh,onOpenFleet}:{
  snapshot:FleetSnapshot|null;sessions:Session[];projects:Project[];compact?:boolean
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
  const projectTotals=projectResourceTotals(shown,sessions,projects)
  const tooling=duplicateToolingGroups(shown)
  const daemon=shown?.daemon
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
    <header><div><strong>OWNED RESOURCES</strong><span>daemon + session process trees</span></div><button aria-label="Close resource usage" onClick={()=>setOpen(false)}>×</button></header>
    {!snapshot&&<p class="resource-usage-empty">Loading owned process usage…</p>}
    {snapshot&&!snapshot.available&&<p class="resource-usage-empty">{snapshot.diagnostic||'Process resource inspection is unavailable.'}</p>}
    {snapshot?.available&&<>
      <section class="resource-usage-total">
        <article><span>CPU</span><strong>{combined.cpu_pct.toFixed(1)}%</strong></article>
        <article><span>{typeof combined.memory_unique_bytes==='number'?'UNIQUE RAM':'WORKING SET'}</span><strong>{memoryLabel(typeof combined.memory_unique_bytes==='number'?combined.memory_unique_bytes:combined.memory_bytes)}</strong></article>
        <article><span>PROC</span><strong>{combined.processes}</strong></article>
      </section>
      {typeof combined.memory_unique_bytes==='number'&&<p class="resource-usage-note">Working set totals {memoryLabel(combined.memory_bytes)}; the difference is shared pages counted once per process. Unique RAM is what ending these processes would actually return.</p>}
      <section class="resource-daemon-usage"><h4>daemon + infrastructure</h4><article><div><strong>swe-mux daemon</strong><small>PID {daemon?.pid||'—'} · {daemon?.processes||0} process{daemon?.processes===1?'':'es'}</small></div><span>CPU <b>{(daemon?.cpu_pct||0).toFixed(1)}%</b></span><span>RAM <b>{memoryLabel(daemon?.memory_bytes||0)}</b></span></article></section>
      <section class="resource-project-list"><h4>by project</h4>{projectTotals.map(project=><article key={project.project_id}><div><strong>{project.label}</strong><small>{project.processes} process{project.processes===1?'':'es'}</small></div><span>CPU <b>{project.cpu_pct.toFixed(1)}%</b></span><span>RAM <b>{memoryLabel(typeof project.memory_unique_bytes==='number'?project.memory_unique_bytes:project.memory_bytes)}</b></span></article>)}{!projectTotals.length&&<p class="resource-usage-empty">No live project-owned processes.</p>}</section>
      {tooling.length>0&&<section class="resource-tooling-list">
        <h4>duplicated per-session tooling</h4>
        {tooling.map(group=><article key={group.tool}><div><strong>{group.tool}</strong><small>{group.instances} process{group.instances===1?'':'es'} across {group.sessions} sessions</small></div><span>RAM <b>{memoryLabel(group.memory_bytes)}</b></span></article>)}
        <p class="resource-usage-note">Each session runs its own language servers, so concurrent sessions on one repo index it more than once. About {memoryLabel(tooling.reduce((sum,group)=>sum+group.duplicate_bytes,0))} here is the duplication itself.</p>
      </section>}
    </>}
    <footer><button onClick={onRefresh}>refresh</button><button onClick={()=>{setOpen(false);onOpenFleet()}}>open process fleet…</button></footer>
  </div>
  return <div ref={root} class={`resource-usage-control ${compact?'compact':''}`}>{compact
    ? <button class="resource-usage-compact" onClick={toggle} aria-expanded={open} aria-label={`Swe-mux owned process resources: ${totalLabel(snapshot)}`} title={`resources · ${totalLabel(snapshot)}`}>
        <span aria-hidden="true">ws</span><strong>{snapshot?.available?compactMemoryLabel(combined.memory_bytes):'—'}</strong>
      </button>
    : <button class="resource-usage-summary" onClick={toggle} aria-expanded={open} aria-label={`Swe-mux owned process resources: ${totalLabel(snapshot)}`} title="Swe-mux daemon and owned process resources">
        <div class="resource-usage-head"><span>resources</span><small>{snapshot?.available?`· ${combined.processes} proc`:'· open details'}</small></div><strong>{totalLabel(snapshot)}</strong>
      </button>}{popup&&createPortal(popup,document.body)}</div>
}
