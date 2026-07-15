import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { api } from './api'
import type { Session, Space } from './types'
import { useModalFocus } from './modalFocus'
import { buildProcessTree, type ProcessTreeNode } from './processTree'
import { buildSpaceGroups } from './spaceGroups'

type Listener = {host:string;port:number;loopback:boolean;url:string}
type Connection = {local_host:string;local_port:number;remote_host:string;remote_port:number}
export type ProcessItem = {
  pid:number;parent_pid?:number;executable:string;command:string;started_at?:number
  exited_at?:number;cpu_pct:number;memory_bytes:number;listeners:Listener[];connections:Connection[];conditions:string[]
}
type SessionProcesses = {session_id:string;space_id:string;processes:ProcessItem[]}
type FleetSnapshot = {
  available:boolean;diagnostic?:string;sessions:SessionProcesses[]
  totals:{processes:number;cpu_pct:number;memory_bytes:number;listeners:number;connections:number}
}
type SessionSnapshot = {available:boolean;diagnostic?:string;session_id:string;processes:ProcessItem[]}
type PreviewResult = {preview:Preview;space:Space}
type PreviewList = {items:Preview[]}
export type Preview = {id:string;session_id:string;space_id:string;url:string;host:string;port:number;source:string;viewport:string}

type Props={
  initialSessionId?:string|null; sessions:Session[];spaces:Space[];onClose:()=>void
  onAttached:(preview:Preview,space:Space)=>void
}

const memoryLabel=(bytes:number)=>`${(bytes/1048576).toFixed(1)} MiB`

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
    return {...snapshot,sessions:groups,totals:snapshot.totals||processTotals(processes)}
  }
  const processes=normalizeProcesses(snapshot.processes)
  return {
    available:snapshot.available,
    diagnostic:snapshot.diagnostic,
    sessions:[{
      session_id:snapshot.session_id,
      space_id:sessions.find(item=>item.id===snapshot.session_id)?.space_id||'',
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
    sessions:groups,
    totals:processTotals(processes),
  }
}

export function ProcessPanel({initialSessionId=null,sessions,spaces,onClose,onAttached}:Props) {
  const [snapshot,setSnapshot] = useState<FleetSnapshot | null>(null)
  const [registered,setRegistered] = useState<Preview[]>([])
  const [selectedSessionId,setSelectedSessionId]=useState<string|null>(initialSessionId)
  const [error,setError] = useState('')
  const [customUrl,setCustomUrl] = useState('http://127.0.0.1:3000/')
  const [confirm,setConfirm] = useState('')
  const panel = useRef<HTMLElement>(null)
  const sessionsRef=useRef(sessions)
  sessionsRef.current=sessions
  useModalFocus(panel,onClose)
  useEffect(()=>setSelectedSessionId(initialSessionId),[initialSessionId])
  const load = async () => {
    try {
      const sessionId=selectedSessionId
      const currentSessions=sessionsRef.current
      const processPath=sessionId?`/api/processes?session=${encodeURIComponent(sessionId)}`:'/api/processes'
      const previewsPromise=api<PreviewList>('GET','/api/previews')
      let processes:FleetSnapshot
      try {
        processes=normalizeSnapshot(await api<FleetSnapshot|SessionSnapshot>('GET',processPath),currentSessions)
      } catch(cause) {
        if(sessionId||!(cause instanceof Error)||!/session query parameter is required/i.test(cause.message))throw cause
        const liveSessions=currentSessions.filter(item=>!['exited','crashed'].includes(item.state))
        const sessionSnapshots=await Promise.all(liveSessions.map(async session=>{
          try {return await api<SessionSnapshot>('GET',`/api/processes?session=${encodeURIComponent(session.id)}`)}
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
  useEffect(()=>{setSnapshot(null);setError('');void load();const tick=()=>{if(!document.hidden)void load()};const timer=window.setInterval(tick,2000);const onVisible=()=>{if(!document.hidden)void load()};document.addEventListener('visibilitychange',onVisible);return()=>{clearInterval(timer);document.removeEventListener('visibilitychange',onVisible)}},[selectedSessionId])
  useEffect(()=>{if(!confirm)return;const timer=window.setTimeout(()=>setConfirm(''),2000);return()=>clearTimeout(timer)},[confirm])

  const selectedSession=sessions.find(item=>item.id===selectedSessionId)||null
  const sessionGroups=useMemo(()=>{
    const groups=snapshot?.sessions||[]
    return selectedSessionId?groups.filter(group=>group.session_id===selectedSessionId):groups
  },[snapshot,selectedSessionId])
  const spaceGroups=useMemo(()=>buildSpaceGroups(sessionGroups,sessions,spaces),[sessionGroups,sessions,spaces])
  const act = async (sessionId:string,process:ProcessItem,action:'interrupt'|'terminate'|'terminate_tree') => {
    const key=`${sessionId}:${process.pid}:${action}`
    if(action!=='interrupt'&&confirm!==key){setConfirm(key);return}
    try {await api('POST','/api/processes/action',{session_id:sessionId,pid:process.pid,action});setConfirm('');await load()} catch(cause){setError((cause as Error).message)}
  }
  const attach = async (sessionId:string,url:string,approved=false) => {
    try {const result=await api<PreviewResult>('POST','/api/previews',{session_id:sessionId,url,approved,attach:true,target_session_id:sessionId,direction:'horizontal'});onAttached(result.preview,result.space);onClose()} catch(cause){setError((cause as Error).message)}
  }

  const renderGroup=(group:SessionProcesses)=>{
    const session=sessions.find(item=>item.id===group.session_id)
    const space=spaces.find(item=>item.id===(session?.space_id||group.space_id))
    const previews=registered.filter(item=>item.session_id===group.session_id)
    const listeners=group.processes.flatMap(process=>process.listeners.map(listener=>({process,listener})))
    const live=group.processes.filter(process=>!process.exited_at)
    const renderProcessNode=(node:ProcessTreeNode<ProcessItem>):JSX.Element=>{
      const process=node.process
      const terminateKey=`${group.session_id}:${process.pid}:terminate`
      const treeKey=`${group.session_id}:${process.pid}:terminate_tree`
      return <li class={process.exited_at?'ended':''} key={process.pid}>
        <article><div class="process-copy"><strong>{process.executable} · PID {process.pid}</strong><span>parent {process.parent_pid||'—'} · CPU {process.cpu_pct.toFixed(1)}% · memory {memoryLabel(process.memory_bytes)} · net {process.listeners.length}L/{process.connections.length}C</span><small title={process.command}>{process.command||'command unavailable'}</small>{process.conditions.length>0&&<em>{process.conditions.join(' · ')}</em>}</div><div class="process-actions"><button onClick={()=>void navigator.clipboard.writeText(String(process.pid))}>Copy PID</button><button disabled={!!process.exited_at} onClick={()=>void act(group.session_id,process,'interrupt')}>Interrupt</button><button disabled={!!process.exited_at} class={confirm===terminateKey?'confirming':''} onClick={()=>void act(group.session_id,process,'terminate')}>{confirm===terminateKey?'✓':'Terminate'}</button><button disabled={!!process.exited_at} class={confirm===treeKey?'confirming':''} onClick={()=>void act(group.session_id,process,'terminate_tree')}>{confirm===treeKey?'✓':'Terminate tree'}</button></div></article>
        {node.children.length>0&&<ul>{node.children.map(renderProcessNode)}</ul>}
      </li>
    }
    return <section class="process-session-group" key={group.session_id}>
      <button class="process-session-heading" onClick={()=>setSelectedSessionId(group.session_id)}>
        <span><i class={`state-dot ${session?.state||'running'}`}/><strong>{space?.name||'unknown space'} :: {session?.name||group.session_id}</strong></span>
        <small>{live.length} proc · CPU {live.reduce((total,item)=>total+item.cpu_pct,0).toFixed(1)}% · {memoryLabel(live.reduce((total,item)=>total+item.memory_bytes,0))} · net {listeners.length}L/{live.reduce((total,item)=>total+item.connections.length,0)}C</small>
      </button>
      {previews.length>0&&<div class="registered-preview-list"><h3>Registered previews</h3>{previews.map(preview=><article><div><strong>{preview.url}</strong><span>{preview.source} · port {preview.port}</span></div><button onClick={()=>void navigator.clipboard.writeText(preview.url)}>Copy URL</button></article>)}</div>}
      {listeners.length>0&&<div class="listener-list"><h3>Detected previews</h3>{listeners.map(({process,listener})=><article><div><strong>{listener.url}</strong><span>{process.executable} · PID {process.pid}</span></div><button onClick={()=>void attach(group.session_id,listener.url)}>Open preview</button><button onClick={()=>void navigator.clipboard.writeText(listener.url)}>Copy URL</button></article>)}</div>}
      <ul class="process-list process-tree">{buildProcessTree(group.processes).map(renderProcessNode)}</ul>
    </section>
  }

  return <div class="process-layer" onPointerDown={event=>{if(event.target===event.currentTarget)onClose()}}><section ref={panel} class="process-panel unified" role="dialog" aria-modal="true" aria-label={selectedSession?`Processes for ${selectedSession.name}`:'All processes'}>
    <header><div>{selectedSessionId&&<button class="process-back" onClick={()=>setSelectedSessionId(null)}>← all processes</button>}<span>{selectedSessionId?'SESSION PROCESSES':'PROCESS FLEET'}</span><strong>{selectedSession?`${spaces.find(item=>item.id===selectedSession.space_id)?.name||'space'} :: ${selectedSession.name} · PID ${selectedSession.pid}`:'All spaces and sessions'}</strong></div><button aria-label="Close process inspector" onClick={onClose}>×</button></header>
    {selectedSession?<div class="process-toolbar"><input value={customUrl} aria-label="Loopback preview URL" onInput={event=>setCustomUrl(event.currentTarget.value)} /><button title="Register a loopback URL you vouch for. Use this when the server is not attributable to this session, such as one running in WSL or Docker or started outside it." onClick={()=>void attach(selectedSession.id,customUrl,true)}>Add preview by URL</button><button onClick={()=>void load()}>Refresh</button></div>:<div class="process-fleet-summary"><span>{snapshot?.totals.processes||0} processes</span><span>CPU {(snapshot?.totals.cpu_pct||0).toFixed(1)}%</span><span>memory {memoryLabel(snapshot?.totals.memory_bytes||0)}</span><span>network {snapshot?.totals.listeners||0} listeners · {snapshot?.totals.connections||0} connections</span><button onClick={()=>void load()}>Refresh</button></div>}
    <div class="process-fleet-list">{error&&<p class="process-error" aria-live="assertive">{error}</p>}{!snapshot&&!error&&<p class="process-empty">Loading process trees…</p>}{snapshot&&!snapshot.available&&<p class="process-empty">{snapshot.diagnostic}</p>}{spaceGroups.map(group=><section class="process-space-group" key={group.id}><h2>space::{group.label}</h2>{group.groups.map(renderGroup)}</section>)}{snapshot?.available&&!sessionGroups.length&&<p class="process-empty">No matching live sessions.</p>}</div>
  </section></div>
}
