import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { api } from './api'
import type { Session, Project } from './types'
import { useModalFocus } from './modalFocus'
import { buildProcessTree, type ProcessTreeNode } from './processTree'
import { buildProjectGroups } from './projectGroups'
import {
  commandTail, isAbnormalState, memoryLabel, processDetails, processMetrics, processRowKey,
  processState, rollupLabel, sessionRollup,
} from './processRows'
import { combinedResourceTotals } from './resourceTotals'

type Listener = {host:string;port:number;loopback:boolean;url:string}
type Connection = {local_host:string;local_port:number;remote_host:string;remote_port:number}
export type ProcessItem = {
  pid:number;parent_pid?:number;executable:string;command:string;started_at?:number
  exited_at?:number;cpu_pct:number;memory_bytes:number;memory_unique_bytes?:number|null;listeners:Listener[];connections:Connection[];conditions:string[]
  identity_id?:string;command_hash?:string;parent_lineage?:Array<{pid:number;creation_time?:number}>
  job_assignment?:string;evidence_state?:'active'|'exited'|'escaped'|'suspected_orphan'|'stale'|'inaccessible'
  evidence_reason?:string;confidence?:'high'|'medium'|'low';first_seen?:number;last_seen?:number
  last_verified_at?:number;exit_evidence?:string;startup_revalidated?:boolean
  attribution_version?:number;attribution_source?:'session_root'|'parent_walk'|'job_membership'|'legacy'|'unknown'
  last_attributed_at?:number;last_job_confirmed_at?:number;server_eligible?:boolean
}
export type SessionProcesses = {session_id:string;project_id:string;processes:ProcessItem[]}
type OwnershipDiagnostic = {
  ts:number;kind:string;pid?:number;session_id?:string;other_session_id?:string
  parent_pid?:number;reason?:string
}
export type DaemonProcesses = {
  pid:number;processes:number;cpu_pct:number;memory_bytes:number;memory_unique_bytes?:number|null
  listeners?:number;connections?:number;members?:ProcessItem[]
}
export type FleetSnapshot = {
  available:boolean;diagnostic?:string;sessions:SessionProcesses[]
  system_cpu_pct?:number|null
  daemon?:DaemonProcesses
  ownership_diagnostics?:OwnershipDiagnostic[]
  totals:{processes:number;cpu_pct:number;memory_bytes:number;memory_unique_bytes?:number|null;listeners:number;connections:number}
}
type SessionSnapshot = {
  available:boolean;diagnostic?:string;session_id:string;processes:ProcessItem[]
  ownership_diagnostics?:OwnershipDiagnostic[]
}
type PreviewResult = {preview:Preview;project:Project}
type PreviewList = {items:Preview[]}
export type Preview = {id:string;session_id:string;project_id:string;url:string;host:string;port:number;source:string;viewport:string;listed?:boolean}

type Props={
  initialSessionId?:string|null; initialProjectId?:string|null
  sessions:Session[];projects:Project[];onClose:()=>void
  onAttached:(preview:Preview,project:Project)=>void
}


const normalizeProcesses=(processes:ProcessItem[])=>(processes||[]).map(item=>({
  ...item,
  listeners:item.listeners||[],
  connections:item.connections||[],
  conditions:item.conditions||[],
}))

const processTotals=(processes:ProcessItem[])=>({
  processes:processes.length,
  cpu_pct:processes.reduce((total,item)=>total+item.cpu_pct,0),
  memory_bytes:processes.reduce((total,item)=>total+item.memory_bytes,0),
  listeners:processes.reduce((total,item)=>total+item.listeners.length,0),
  connections:processes.reduce((total,item)=>total+item.connections.length,0),
})

const normalizeSnapshot=(snapshot:FleetSnapshot|SessionSnapshot,sessions:Session[]):FleetSnapshot=>{
  if('sessions' in snapshot){
    const groups=(snapshot.sessions||[]).map(group=>({...group,processes:normalizeProcesses(group.processes)}))
    const processes=groups.flatMap(group=>group.processes)
    const daemon=snapshot.daemon
      ?{...snapshot.daemon,members:normalizeProcesses(snapshot.daemon.members||[])}
      :undefined
    return {...snapshot,daemon,sessions:groups,totals:snapshot.totals||processTotals(processes)}
  }
  const processes=normalizeProcesses(snapshot.processes)
  return {
    available:snapshot.available,
    diagnostic:snapshot.diagnostic,
    ownership_diagnostics:snapshot.ownership_diagnostics,
    sessions:[{
      session_id:snapshot.session_id,
      project_id:sessions.find(item=>item.id===snapshot.session_id)?.project_id||'',
      processes,
    }],
    totals:processTotals(processes),
  }
}

const combineSessionSnapshots=(snapshots:SessionSnapshot[],sessions:Session[]):FleetSnapshot=>{
  const normalized=snapshots.map(snapshot=>normalizeSnapshot(snapshot,sessions))
  const groups=normalized.flatMap(snapshot=>snapshot.sessions)
  const processes=groups.flatMap(group=>group.processes)
  return {
    available:normalized.every(snapshot=>snapshot.available),
    diagnostic:normalized.find(snapshot=>snapshot.diagnostic)?.diagnostic,
    ownership_diagnostics:normalized.flatMap(snapshot=>snapshot.ownership_diagnostics||[]),
    sessions:groups,
    totals:processTotals(processes),
  }
}

export function ProcessPanel({initialSessionId=null,initialProjectId=null,sessions,projects,onClose,onAttached}:Props) {
  const [snapshot,setSnapshot] = useState<FleetSnapshot | null>(null)
  const [registered,setRegistered] = useState<Preview[]>([])
  const [selectedSessionId,setSelectedSessionId]=useState<string|null>(initialSessionId)
  const [error,setError] = useState('')
  // Empty rather than a seeded port: a seeded 3000 read as an assumed dev-server
  // port. The placeholder shows the shape without implying a running server.
  const [customUrl,setCustomUrl] = useState('')
  const [confirm,setConfirm] = useState('')
  // Ended processes are opt-in: nothing can be done to them and they are already
  // excluded from every total, so they are history rather than fleet state.
  const [includeEnded,setIncludeEnded] = useState(false)
  // Set when opened from a Project row; clearable so the fleet stays browsable.
  const [projectScope,setProjectScope] = useState(initialProjectId||'')
  // Keyed by durable process identity, not PID, so a row survives the 2s refresh that
  // replaces every object in the snapshot.
  const [expanded,setExpanded] = useState<ReadonlySet<string>>(new Set())
  const panel = useRef<HTMLElement>(null)
  const sessionsRef=useRef(sessions)
  sessionsRef.current=sessions
  useModalFocus(panel,onClose)
  useEffect(()=>setSelectedSessionId(initialSessionId),[initialSessionId])
  const load = async () => {
    try {
      const sessionId=selectedSessionId
      const currentSessions=sessionsRef.current
      const ended=includeEnded?'include_ended=1':''
      const processPath=sessionId
        ?`/api/processes?session=${encodeURIComponent(sessionId)}${ended?`&${ended}`:''}`
        :`/api/processes${ended?`?${ended}`:''}`
      const previewsPromise=api<PreviewList>('GET','/api/previews')
      let processes:FleetSnapshot
      try {
        processes=normalizeSnapshot(await api<FleetSnapshot|SessionSnapshot>('GET',processPath),currentSessions)
      } catch(cause) {
        if(sessionId||!(cause instanceof Error)||!/session query parameter is required/i.test(cause.message))throw cause
        const liveSessions=currentSessions.filter(item=>!['exited','crashed'].includes(item.state))
        const sessionSnapshots=await Promise.all(liveSessions.map(async session=>{
          try {return await api<SessionSnapshot>('GET',`/api/processes?session=${encodeURIComponent(session.id)}${ended?`&${ended}`:''}`)}
          catch(error) {
            if((error as Error&{status?:number}).status===404)return null
            throw error
          }
        }))
        processes=combineSessionSnapshots(sessionSnapshots.filter((item):item is SessionSnapshot=>item!==null),currentSessions)
      }
      const previews=await previewsPromise
      setSnapshot(processes);setRegistered(previews.items);setError('')
    } catch(cause) { setError(cause instanceof Error?cause.message:String(cause)) }
  }
  useEffect(()=>{setSnapshot(null);setError('');void load();const tick=()=>{if(!document.hidden)void load()};const timer=window.setInterval(tick,2000);const onVisible=()=>{if(!document.hidden)void load()};document.addEventListener('visibilitychange',onVisible);return()=>{clearInterval(timer);document.removeEventListener('visibilitychange',onVisible)}},[selectedSessionId,includeEnded])
  useEffect(()=>{if(!confirm)return;const timer=window.setTimeout(()=>setConfirm(''),2000);return()=>clearTimeout(timer)},[confirm])

  const selectedSession=sessions.find(item=>item.id===selectedSessionId)||null
  const sessionGroups=useMemo(()=>{
    const groups=snapshot?.sessions||[]
    const scoped=projectScope
      ? groups.filter(group=>
          (sessions.find(item=>item.id===group.session_id)?.project_id||group.project_id)===projectScope)
      : groups
    return selectedSessionId?scoped.filter(group=>group.session_id===selectedSessionId):scoped
  },[snapshot,selectedSessionId,projectScope,sessions])
  const projectProcessGroups=useMemo(()=>buildProjectGroups(sessionGroups,sessions,projects),[sessionGroups,sessions,projects])
  const fleetTotals=combinedResourceTotals(snapshot)
  const fleetListeners=(snapshot?.totals.listeners||0)+(snapshot?.daemon?.listeners||0)
  const fleetConnections=(snapshot?.totals.connections||0)+(snapshot?.daemon?.connections||0)
  const ownershipDiagnostics=(snapshot?.ownership_diagnostics||[]).slice(-10).reverse()
  const act = async (sessionId:string,process:ProcessItem,action:'interrupt'|'terminate'|'terminate_tree') => {
    const key=`${sessionId}:${process.pid}:${action}`
    if(action!=='interrupt'&&confirm!==key){setConfirm(key);return}
    try {await api('POST','/api/processes/action',{session_id:sessionId,pid:process.pid,identity_id:process.identity_id,action});setConfirm('');await load()} catch(cause){setError((cause as Error).message)}
  }
  const toggleExpanded = (key:string) => setExpanded(current=>{
    const next=new Set(current)
    if(!next.delete(key))next.add(key)
    return next
  })
  const attach = async (sessionId:string,url:string,approved=false) => {
    try {const result=await api<PreviewResult>('POST','/api/previews',{session_id:sessionId,url,approved,attach:true,target_session_id:sessionId,direction:'horizontal'});onAttached(result.preview,result.project);onClose()} catch(cause){setError((cause as Error).message)}
  }

  const renderGroup=(group:SessionProcesses)=>{
    const session=sessions.find(item=>item.id===group.session_id)
    const project=projects.find(item=>item.id===(session?.project_id||group.project_id))
    const previews=registered.filter(item=>item.session_id===group.session_id)
    const listeners=group.processes.flatMap(process=>process.listeners.map(listener=>({process,listener})))
    const renderProcessNode=(node:ProcessTreeNode<ProcessItem>):JSX.Element=>{
      const process=node.process
      const terminateKey=`${group.session_id}:${process.pid}:terminate`
      const treeKey=`${group.session_id}:${process.pid}:terminate_tree`
      const state=processState(process)
      const key=processRowKey(process)
      const open=expanded.has(key)
      return <li class={`${process.exited_at?'ended ':''}evidence-${state}${open?' open':''}`} key={key}>
        <button class="process-row" aria-expanded={open} title={process.command||undefined} onClick={()=>toggleExpanded(key)}>
          <i class="process-caret" aria-hidden="true"/>
          <i class={`process-state-dot ${state}`} title={`evidence ${state.replace('_',' ')}`}/>
          <strong>{process.executable}</strong>
          <span class="process-pid">{process.pid}</span>
          <span class="process-args">{commandTail(process)}</span>
          {process.conditions.length>0&&<em class="process-warn" title={process.conditions.join(' · ')}>⚠ {process.conditions.length}</em>}
          {isAbnormalState(state)&&<b class={`process-evidence ${state}`}>{state.replace('_',' ')}</b>}
          <span class="process-metrics">{processMetrics(process)}</span>
        </button>
        {open&&<div class="process-detail">
          <dl>{processDetails(process).map(detail=><div key={detail.label}><dt>{detail.label}</dt><dd>{detail.value}</dd></div>)}</dl>
          <div class="process-actions"><button onClick={()=>void navigator.clipboard.writeText(String(process.pid))}>Copy PID</button><button onClick={()=>void navigator.clipboard.writeText(process.command||String(process.pid))}>Copy command</button><button disabled={!!process.exited_at} title="Re-checks PID, creation time, and ownership before acting" onClick={()=>void act(group.session_id,process,'interrupt')}>Interrupt</button><button disabled={!!process.exited_at} title="Re-checks the durable process fingerprint before acting" class={confirm===terminateKey?'confirming':''} onClick={()=>void act(group.session_id,process,'terminate')}>{confirm===terminateKey?'✓':'Terminate'}</button><button disabled={!!process.exited_at} title="Re-checks this process and every attributable child before acting" class={confirm===treeKey?'confirming':''} onClick={()=>void act(group.session_id,process,'terminate_tree')}>{confirm===treeKey?'✓':'Terminate tree'}</button></div>
        </div>}
        {node.children.length>0&&<ul>{node.children.map(renderProcessNode)}</ul>}
      </li>
    }
    // The Project heading directly above already names the Project, and the rollup only
    // earns its place once it aggregates more than the single row below it.
    const rollup=sessionRollup(group.processes)
    return <section class="process-session-group" key={group.session_id}>
      <button class="process-session-heading" title={`${project?.name||'unknown project'} :: ${session?.name||group.session_id}`} onClick={()=>setSelectedSessionId(group.session_id)}>
        <span><i class={`state-dot ${session?.state||'running'}`}/><strong>{session?.name||group.session_id}</strong></span>
        {rollup&&<small>{rollupLabel(rollup)}</small>}
      </button>
      {/* One line each, for the same reason the process rows are: the URL and its owner are
          the whole content, and a heading plus a stacked button pair cost four rows to say it. */}
      {previews.map(preview=><div class="process-link-row" key={preview.id}>
        <i class="process-link-mark registered" title="Registered preview">▣</i>
        <strong>{preview.url}</strong>
        <span>{preview.source} · port {preview.port}</span>
        <button onClick={()=>void navigator.clipboard.writeText(preview.url)}>copy</button>
      </div>)}
      {listeners.map(({process,listener})=><div class="process-link-row" key={`${process.pid}:${listener.url}`}>
        <i class="process-link-mark" title="Loopback listener">⇢</i>
        <strong>{listener.url}</strong>
        <span>{process.executable} {process.pid}</span>
        <button title={`Open ${listener.url} as a preview beside this session`} onClick={()=>void attach(group.session_id,listener.url)}>preview</button>
        <button onClick={()=>void navigator.clipboard.writeText(listener.url)}>copy</button>
      </div>)}
      <ul class="process-list process-tree">{buildProcessTree(group.processes).map(renderProcessNode)}</ul>
    </section>
  }

  const renderDaemonGroup=()=>{
    const daemon=snapshot?.daemon
    if(!daemon||selectedSessionId)return null
    const members=daemon.members||[]
    const renderDaemonNode=(node:ProcessTreeNode<ProcessItem>):JSX.Element=>{
      const process=node.process
      const role=process.pid===daemon.pid?'daemon':'infrastructure'
      const key=processRowKey(process)
      const open=expanded.has(key)
      // Runtime members carry no session attribution or evidence chain, so they get the
      // observational subset rather than blank rows where those would be.
      const details=[
        {label:'command',value:process.command||'command line unavailable'},
        {label:'parent',value:process.parent_pid?`PID ${process.parent_pid}`:'none observed'},
        {label:'role',value:role==='daemon'?'swe-mux server process':'daemon-owned child not attributed to a terminal session'},
        ...processDetails(process).filter(detail=>detail.label==='network'||detail.label==='warnings'),
      ]
      return <li class={open?'open':''} key={key}>
        <button class="process-row" aria-expanded={open} title={process.command||undefined} onClick={()=>toggleExpanded(key)}>
          <i class="process-caret" aria-hidden="true"/>
          <i class={`process-state-dot ${role==='daemon'?'active':''}`} title={role}/>
          <strong>{process.executable}</strong>
          <span class="process-pid">{process.pid}</span>
          <span class="process-args">{commandTail(process)}</span>
          {process.conditions.length>0&&<em class="process-warn" title={process.conditions.join(' · ')}>⚠ {process.conditions.length}</em>}
          {role==='daemon'&&<b class="process-evidence active">daemon</b>}
          <span class="process-metrics">{processMetrics(process)}</span>
        </button>
        {open&&<div class="process-detail">
          <dl>{details.map(detail=><div key={detail.label}><dt>{detail.label}</dt><dd>{detail.value}</dd></div>)}</dl>
          {/* Observational only: the runtime is never interrupt/terminate territory. */}
          <div class="process-actions"><button onClick={()=>void navigator.clipboard.writeText(String(process.pid))}>Copy PID</button><button onClick={()=>void navigator.clipboard.writeText(process.command||String(process.pid))}>Copy command</button></div>
        </div>}
        {node.children.length>0&&<ul>{node.children.map(renderDaemonNode)}</ul>}
      </li>
    }
    return <section class="process-project-group process-daemon-group">
      <h2>daemon + infrastructure</h2>
      <section class="process-session-group">
        <div class="process-session-heading process-daemon-heading"><span><i class="state-dot running"/><strong>swe-mux runtime</strong></span><small>{daemon.processes} proc · CPU {daemon.cpu_pct.toFixed(1)}% · {memoryLabel(daemon.memory_bytes)}{daemon.listeners||daemon.connections?` · ${daemon.listeners||0}L/${daemon.connections||0}C`:''}</small></div>
        {members.length>0?<ul class="process-list process-tree">{buildProcessTree(members).map(renderDaemonNode)}</ul>:<p class="process-empty">Detailed daemon members are unavailable from the running daemon.</p>}
      </section>
    </section>
  }

  const endedToggle=<button
    class={`process-ended-toggle ${includeEnded?'active':''}`}
    aria-pressed={includeEnded}
    title={includeEnded
      ?'Showing ended processes recorded during this daemon run. They support no actions and are excluded from totals.'
      :'Show processes that have already exited, for this daemon run only.'}
    onClick={()=>setIncludeEnded(current=>!current)}
  >{includeEnded?'✓ ended':'ended'}</button>

  // Opening from a Project row prefilters the fleet, but the scope stays a
  // visible, clearable control rather than a hidden mode.
  const scopeSelect=<select
    class="process-scope-select"
    aria-label="Filter processes by project"
    value={projectScope}
    onChange={event=>{setProjectScope(event.currentTarget.value);setSelectedSessionId(null)}}
  ><option value="">All projects</option>{projects.map(project=><option value={project.id}>{project.name}</option>)}</select>

  return <div class="process-layer" onPointerDown={event=>{if(event.target===event.currentTarget)onClose()}}><section ref={panel} class="process-panel unified" role="dialog" aria-modal="true" aria-label={selectedSession?`Processes for ${selectedSession.name}`:'All processes'}>
    <header><div>{selectedSessionId&&<button class="process-back" onClick={()=>setSelectedSessionId(null)}>← all processes</button>}<span>{selectedSessionId?'SESSION PROCESSES':'PROCESS FLEET'}</span><strong>{selectedSession?`${projects.find(item=>item.id===selectedSession.project_id)?.name||'project'} :: ${selectedSession.name} · PID ${selectedSession.pid}`:'All projects, sessions, and swe-mux infrastructure'}</strong></div><button aria-label="Close process inspector" onClick={onClose}>×</button></header>
    {selectedSession?<div class="process-toolbar"><input value={customUrl} aria-label="Loopback preview URL" placeholder="http://127.0.0.1:3000/" onInput={event=>setCustomUrl(event.currentTarget.value)} /><button disabled={!customUrl.trim()} title="Register a loopback URL you vouch for. Use this when the server is not attributable to this session, such as one running in WSL or Docker or started outside it." onClick={()=>void attach(selectedSession.id,customUrl,true)}>Add preview by URL</button>{endedToggle}<button onClick={()=>void load()}>Refresh</button></div>:<div class="process-fleet-summary">{scopeSelect}<span>{fleetTotals.processes} processes</span><span>CPU {fleetTotals.cpu_pct.toFixed(1)}%</span><span>memory {memoryLabel(fleetTotals.memory_bytes)}</span><span>network {fleetListeners} listeners · {fleetConnections} connections</span>{endedToggle}<button onClick={()=>void load()}>Refresh</button></div>}
    <div class="process-fleet-list">{error&&<p class="process-error" aria-live="assertive">{error}</p>}{!snapshot&&!error&&<p class="process-empty">Loading process trees…</p>}{snapshot&&!snapshot.available&&<p class="process-empty">{snapshot.diagnostic}</p>}{ownershipDiagnostics.length>0&&<details class="process-ownership-diagnostics"><summary>Ownership diagnostics ({ownershipDiagnostics.length} recent)</summary><ul>{ownershipDiagnostics.map((item,index)=><li key={`${item.ts}:${item.kind}:${item.pid||0}:${index}`}><strong>{item.kind.replaceAll('_',' ')}</strong><span>{item.pid?`PID ${item.pid}`:'process'}{item.session_id?` · session ${item.session_id}`:''}{item.other_session_id?` · conflicting session ${item.other_session_id}`:''}{item.parent_pid?` · parent PID ${item.parent_pid}`:''}{item.reason?` · ${item.reason}`:''} · {new Date(item.ts*1000).toLocaleString()}</span></li>)}</ul></details>}{renderDaemonGroup()}{projectProcessGroups.map(group=><section class="process-project-group" key={group.id}><h2>project::{group.label}</h2>{group.groups.map(renderGroup)}</section>)}{snapshot?.available&&!sessionGroups.length&&<p class="process-empty">No matching live sessions.</p>}</div>
  </section></div>
}
