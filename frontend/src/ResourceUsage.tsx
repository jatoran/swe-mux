import { createPortal } from 'preact/compat'
import { useEffect, useRef, useState } from 'preact/hooks'
import type { FleetSnapshot } from './ProcessPanel'
import { memoryLabel } from './ProcessPanel'
import type { Project, Session } from './types'
import { anchoredPopoverStyle } from './providerAccountDisplay'
import { combinedResourceTotals, projectResourceTotals } from './resourceTotals'

const totalLabel=(snapshot:FleetSnapshot|null)=>{
  if(!snapshot)return 'loading resources…'
  if(!snapshot.available)return 'resource data unavailable'
  const total=combinedResourceTotals(snapshot)
  return `cpu ${total.cpu_pct.toFixed(1)}% · ram ${memoryLabel(total.memory_bytes)}`
}

export function ResourceUsageSummary({snapshot,sessions,projects,onRefresh,onOpenFleet}:{
  snapshot:FleetSnapshot|null;sessions:Session[];projects:Project[];onRefresh:()=>void;onOpenFleet:()=>void
}) {
  const [open,setOpen]=useState(false)
  const [popoverStyle,setPopoverStyle]=useState<Record<string,string>>({})
  const root=useRef<HTMLDivElement>(null)
  const popover=useRef<HTMLDivElement>(null)
  const combined=combinedResourceTotals(snapshot)
  const projectTotals=projectResourceTotals(snapshot,sessions,projects)
  const daemon=snapshot?.daemon
  const position=()=>{const rect=root.current?.getBoundingClientRect();if(rect)setPopoverStyle(anchoredPopoverStyle(rect,false,{width:window.innerWidth,height:window.innerHeight}))}
  const toggle=()=>{if(!open)position();setOpen(value=>!value)}
  useEffect(()=>{
    if(!open)return
    position()
    const reposition=()=>position()
    const dismiss=(event:PointerEvent)=>{const target=event.target as Node;if(!root.current?.contains(target)&&!popover.current?.contains(target))setOpen(false)}
    const key=(event:KeyboardEvent)=>{if(event.key==='Escape')setOpen(false)}
    window.addEventListener('resize',reposition);window.addEventListener('scroll',reposition,true);window.addEventListener('pointerdown',dismiss);window.addEventListener('keydown',key)
    return()=>{window.removeEventListener('resize',reposition);window.removeEventListener('scroll',reposition,true);window.removeEventListener('pointerdown',dismiss);window.removeEventListener('keydown',key)}
  },[open])
  const popup=open&&<div ref={popover} class="account-popover resource-usage-popover" style={popoverStyle} role="dialog" aria-label="Swe-mux resource usage">
    <header><div><strong>OWNED RESOURCES</strong><span>daemon + session process trees</span></div><button aria-label="Close resource usage" onClick={()=>setOpen(false)}>×</button></header>
    {!snapshot&&<p class="resource-usage-empty">Loading owned process usage…</p>}
    {snapshot&&!snapshot.available&&<p class="resource-usage-empty">{snapshot.diagnostic||'Process resource inspection is unavailable.'}</p>}
    {snapshot?.available&&<>
      <section class="resource-usage-total"><article><span>CPU</span><strong>{combined.cpu_pct.toFixed(1)}%</strong></article><article><span>RAM</span><strong>{memoryLabel(combined.memory_bytes)}</strong></article><article><span>PROC</span><strong>{combined.processes}</strong></article></section>
      <section class="resource-daemon-usage"><h4>daemon + infrastructure</h4><article><div><strong>swe-mux daemon</strong><small>PID {daemon?.pid||'—'} · {daemon?.processes||0} process{daemon?.processes===1?'':'es'}</small></div><span>CPU <b>{(daemon?.cpu_pct||0).toFixed(1)}%</b></span><span>RAM <b>{memoryLabel(daemon?.memory_bytes||0)}</b></span></article></section>
      <section class="resource-project-list"><h4>by project</h4>{projectTotals.map(project=><article key={project.project_id}><div><strong>{project.label}</strong><small>{project.processes} process{project.processes===1?'':'es'}</small></div><span>CPU <b>{project.cpu_pct.toFixed(1)}%</b></span><span>RAM <b>{memoryLabel(project.memory_bytes)}</b></span></article>)}{!projectTotals.length&&<p class="resource-usage-empty">No live project-owned processes.</p>}</section>
    </>}
    <footer><button onClick={onRefresh}>refresh</button><button onClick={()=>{setOpen(false);onOpenFleet()}}>open process fleet…</button></footer>
  </div>
  return <div ref={root} class="resource-usage-control"><button class="resource-usage-summary" onClick={toggle} aria-expanded={open} aria-label={`Swe-mux owned process resources: ${totalLabel(snapshot)}`} title="Swe-mux daemon and owned process resources">
    <span>owned resources</span><strong>{totalLabel(snapshot)}</strong><small>{snapshot?.available?`${combined.processes} processes`:'open details'}</small>
  </button>{popup&&createPortal(popup,document.body)}</div>
}
