import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import type { Session, Space } from './types'
import { useModalFocus } from './modalFocus'

type Listener = {host:string;port:number;loopback:boolean;url:string}
type ProcessItem = {
  pid:number;parent_pid?:number;executable:string;command:string;started_at?:number
  exited_at?:number;cpu_pct:number;memory_bytes:number;listeners:Listener[];conditions:string[]
}
type Snapshot = {available:boolean;diagnostic?:string;processes:ProcessItem[]}
type PreviewResult = {preview:Preview;space:Space}
export type Preview = {id:string;session_id:string;space_id:string;url:string;host:string;port:number;source:string;viewport:string}

export function ProcessPanel({session,onClose,onAttached}:{session:Session;onClose:()=>void;onAttached:(preview:Preview,space:Space)=>void}) {
  const [snapshot,setSnapshot] = useState<Snapshot | null>(null)
  const [error,setError] = useState('')
  const [customUrl,setCustomUrl] = useState('http://127.0.0.1:3000/')
  const [confirm,setConfirm] = useState('')
  const panel = useRef<HTMLElement>(null)
  useModalFocus(panel,onClose)
  const load = () => api<Snapshot>('GET',`/api/processes?session=${encodeURIComponent(session.id)}`).then(setSnapshot).catch(cause=>setError(cause.message))
  useEffect(()=>{void load();const timer=window.setInterval(load,2000);return()=>clearInterval(timer)},[session.id])
  useEffect(()=>{if(!confirm)return;const timer=window.setTimeout(()=>setConfirm(''),2000);return()=>clearTimeout(timer)},[confirm])
  const act = async (process:ProcessItem,action:'interrupt'|'terminate'|'terminate_tree') => {
    const key=`${process.pid}:${action}`
    if(action!=='interrupt'&&confirm!==key){setConfirm(key);return}
    try {setSnapshot(await api<Snapshot>('POST','/api/processes/action',{session_id:session.id,pid:process.pid,action}));setConfirm('')} catch(cause){setError((cause as Error).message)}
  }
  const attach = async (url:string,approved=false) => {
    try {const result=await api<PreviewResult>('POST','/api/previews',{session_id:session.id,url,approved,attach:true,target_session_id:session.id,direction:'horizontal'});onAttached(result.preview,result.space);onClose()} catch(cause){setError((cause as Error).message)}
  }
  const listeners = snapshot?.processes.flatMap(process=>process.listeners.map(listener=>({process,listener}))) || []
  return <div class="process-layer" onPointerDown={event=>{if(event.target===event.currentTarget)onClose()}}><section ref={panel} class="process-panel" role="dialog" aria-modal="true" aria-label={`Processes for ${session.name}`}>
    <header><div><span>SESSION PROCESSES</span><strong>{session.name} · PID {session.pid}</strong></div><button aria-label="Close process inspector" onClick={onClose}>×</button></header>
    <div class="process-toolbar"><input value={customUrl} aria-label="Custom loopback preview URL" onInput={event=>setCustomUrl(event.currentTarget.value)} /><button onClick={()=>void attach(customUrl,true)}>Add approved preview</button><button onClick={()=>void load()}>Refresh</button></div>
    {error&&<p class="process-error" aria-live="assertive">{error}</p>}
    {!snapshot&&<p class="process-empty">Loading process tree…</p>}
    {snapshot&&!snapshot.available&&<p class="process-empty">{snapshot.diagnostic}</p>}
    {listeners.length>0&&<section class="listener-list"><h3>Detected previews</h3>{listeners.map(({process,listener})=><article><div><strong>{listener.url}</strong><span>{process.executable} · PID {process.pid}</span></div><button onClick={()=>void attach(listener.url)}>Open preview</button><button onClick={()=>void navigator.clipboard.writeText(listener.url)}>Copy URL</button></article>)}</section>}
    <div class="process-list">{snapshot?.processes.map(process=><article class={process.exited_at?'ended':''}><div class="process-copy"><strong>{process.executable} · PID {process.pid}</strong><span>parent {process.parent_pid||'—'} · CPU {process.cpu_pct.toFixed(1)}% · memory {(process.memory_bytes/1048576).toFixed(1)} MiB</span><small title={process.command}>{process.command||'command unavailable'}</small>{process.conditions.length>0&&<em>{process.conditions.join(' · ')}</em>}</div><div class="process-actions"><button onClick={()=>void navigator.clipboard.writeText(String(process.pid))}>Copy PID</button><button disabled={!!process.exited_at} onClick={()=>void act(process,'interrupt')}>Interrupt</button><button disabled={!!process.exited_at} class={confirm===`${process.pid}:terminate`?'confirming':''} onClick={()=>void act(process,'terminate')}>{confirm===`${process.pid}:terminate`?'✓':'Terminate'}</button><button disabled={!!process.exited_at} class={confirm===`${process.pid}:terminate_tree`?'confirming':''} onClick={()=>void act(process,'terminate_tree')}>{confirm===`${process.pid}:terminate_tree`?'✓':'Terminate tree'}</button></div></article>)}</div>
  </section></div>
}
