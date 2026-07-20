import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import type { Project, ProjectAction, ProjectActionCatalog, ProjectBackend, Session } from './types'

type Anchor={x:number;y:number}
type Props={
  project:Project
  anchor:Anchor
  onClose:()=>void
  onLaunch:(backend:ProjectBackend)=>void
  onCustom:()=>void
  onSessions:(sessions:Session[])=>void
  onError:(message:string)=>void
}

const sourceLabel:Record<ProjectAction['source'],string>={
  vscode:'VS Code tasks',package:'package scripts',native:'Project Actions',
}

export function ProjectRunMenu({project,anchor,onClose,onLaunch,onCustom,onSessions,onError}:Props){
  const [catalog,setCatalog]=useState<ProjectActionCatalog|null>(null)
  const [loading,setLoading]=useState(true)
  const [busy,setBusy]=useState('')
  const [pending,setPending]=useState<ProjectAction|null>(null)
  const load=()=>{
    setLoading(true)
    void api<ProjectActionCatalog>('GET',`/api/projects/${project.id}/actions`)
      .then(setCatalog).catch(cause=>onError(cause instanceof Error?cause.message:String(cause)))
      .finally(()=>setLoading(false))
  }
  useEffect(load,[project.id])
  useEffect(()=>{
    const close=(event:KeyboardEvent)=>event.key==='Escape'&&(pending?setPending(null):onClose())
    window.addEventListener('keydown',close)
    return()=>window.removeEventListener('keydown',close)
  },[pending,onClose])

  const execute=async(action:ProjectAction)=>{
    if(!catalog?.trusted){setPending(action);return}
    setBusy(action.id)
    try{
      const result=await api<{sessions:Session[];errors:Array<{step:string;error:string}>}>('POST',`/api/projects/${project.id}/actions/run`,{action_id:action.id})
      if(result.sessions.length)onSessions(result.sessions)
      if(result.errors.length)onError(result.errors.map(item=>`${item.step}: ${item.error}`).join('\n'))
      onClose()
    }catch(cause){onError(cause instanceof Error?cause.message:String(cause));load()}
    finally{setBusy('')}
  }
  const trustAndRun=async()=>{
    if(!catalog||!pending)return
    setBusy(pending.id)
    try{
      const trusted=await api<ProjectActionCatalog>('POST',`/api/projects/${project.id}/actions/trust`,{fingerprint:catalog.fingerprint})
      setCatalog(trusted)
      const action=pending
      setPending(null)
      const result=await api<{sessions:Session[];errors:Array<{step:string;error:string}>}>('POST',`/api/projects/${project.id}/actions/run`,{action_id:action.id})
      if(result.sessions.length)onSessions(result.sessions)
      if(result.errors.length)onError(result.errors.map(item=>`${item.step}: ${item.error}`).join('\n'))
      onClose()
    }catch(cause){onError(cause instanceof Error?cause.message:String(cause));setPending(null);load()}
    finally{setBusy('')}
  }
  const groups=(['native','vscode','package'] as const).map(source=>({source,items:catalog?.actions.filter(item=>item.source===source)||[]})).filter(group=>group.items.length)
  const left=Math.min(anchor.x,Math.max(6,window.innerWidth-306))
  const top=Math.min(anchor.y,Math.max(6,window.innerHeight-460))
  return <>
    <div class="run-menu-scrim" onPointerDown={onClose}/>
    <section class="project-run-menu" role="menu" aria-label={`Run in ${project.name}`} style={{left,top}}>
      <header><div><span>RUN</span><strong>{project.name}</strong></div><button aria-label="Close Run menu" onClick={onClose}>×</button></header>
      <div class="run-menu-section"><small>NEW SESSION</small>
        <button role="menuitem" disabled={!!busy} onClick={()=>onLaunch('claude')}><span>[claude]</span> Claude</button>
        <button role="menuitem" disabled={!!busy} onClick={()=>onLaunch('codex')}><span>[codex]</span> Codex</button>
        <button role="menuitem" disabled={!!busy} onClick={()=>onLaunch('shell')}><span>&gt;_</span> Shell</button>
        <button role="menuitem" disabled={!!busy} onClick={onCustom}><span>⋯</span> Custom terminal…</button>
      </div>
      {loading?<p>Reading Project tasks…</p>:groups.map(group=><div class="run-menu-section" key={group.source}><small>{sourceLabel[group.source]}</small>{group.items.map(action=><button role="menuitem" key={action.id} disabled={!!busy} title={action.steps.map(step=>step.command).join('\n')} onClick={()=>void execute(action)}><span>{busy===action.id?'…':'▶'}</span><div><strong>{action.label}</strong>{action.steps.length>1&&<em>{action.steps.length} terminals</em>}</div></button>)}</div>)}
      {!loading&&groups.length===0&&<p>No Project tasks found.</p>}
      {!!catalog?.diagnostics.length&&<details><summary>Import diagnostics ({catalog.diagnostics.length})</summary>{catalog.diagnostics.map((item,index)=><p key={`${index}:${item}`}>{item}</p>)}</details>}
    </section>
    {pending&&catalog&&<div class="modal-layer project-action-trust-layer"><section class="modal project-action-trust" role="dialog" aria-modal="true" aria-label="Trust Project tasks">
      <div class="modal-heading"><div><span>PROJECT TASK TRUST</span><h2>Trust these task files?</h2></div><button onClick={()=>setPending(null)}>×</button></div>
      <div class="project-action-trust-body"><p>Running <strong>{pending.label}</strong> executes repository-provided commands. Trust applies only to the current exact contents; editing any task file requires approval again.</p><ul>{catalog.sources.map(source=><li key={source}>{source}</li>)}</ul><pre>{pending.steps.map(step=>`${step.name}: ${step.command}`).join('\n')}</pre></div>
      <div class="modal-footer"><button onClick={()=>setPending(null)}>Cancel</button><button class="primary" disabled={!!busy} onClick={()=>void trustAndRun()}>{busy?'Starting…':'Trust and run'}</button></div>
    </section></div>}
  </>
}
