import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { fitScrollingMenuInViewport } from './menuPosition'
import type { Project, ProjectAction, ProjectActionCatalog, ProjectBackend, Session } from './types'
import { promptDeliveryHarnesses } from './harnessRegistry'
import { isAbsolutePath } from './gitWorktrees'
import { normalizeWorktreeBranchInput, worktreePathForBranch } from './worktreeLaunch'
import { dismissStack } from './dismissStack.ts'
import { useDismissLevel } from './modalFocus'

type Anchor={x:number;y:number}
type Props={
  project:Project
  anchor:Anchor
  onClose:()=>void
  onLaunch:(backend:ProjectBackend)=>void
  onCustom:()=>void
  onSessions:(sessions:Session[])=>void
  onWorktreeCreated:(path:string,backend:ProjectBackend)=>void
  onError:(message:string)=>void
}

const sourceLabel:Record<ProjectAction['source'],string>={
  vscode:'VS Code tasks',package:'package scripts',native:'Project Actions',
}

type WorktreeDraft={backend:ProjectBackend;branch:string;startPoint:string;path:string;pathEdited:boolean}
type WorktreeCreateResult={ok:true;path:string}

export function ProjectRunMenu({project,anchor,onClose,onLaunch,onCustom,onSessions,onWorktreeCreated,onError}:Props){
  const harnesses=promptDeliveryHarnesses()
  const launchBackends=['shell',...harnesses.map(harness=>harness.name)]
  const preferred=project.effective_options?.backend||project.default_backend||harnesses[0]?.name||'shell'
  const [catalog,setCatalog]=useState<ProjectActionCatalog|null>(null)
  const [loading,setLoading]=useState(true)
  const [busy,setBusy]=useState('')
  const [pending,setPending]=useState<ProjectAction|null>(null)
  const [worktreeOpen,setWorktreeOpen]=useState(false)
  const [worktreeError,setWorktreeError]=useState('')
  const [worktreeRoot,setWorktreeRoot]=useState('')
  const [worktreeConfigError,setWorktreeConfigError]=useState('')
  const [worktree,setWorktree]=useState<WorktreeDraft>({
    backend:launchBackends.includes(preferred)?preferred:'shell',branch:'',startPoint:'',
    path:'',pathEdited:false,
  })
  const panel=useRef<HTMLElement|null>(null)
  const load=()=>{
    setLoading(true)
    void api<ProjectActionCatalog>('GET',`/api/projects/${project.id}/actions`)
      .then(setCatalog).catch(cause=>onError(cause instanceof Error?cause.message:String(cause)))
      .finally(()=>setLoading(false))
  }
  useEffect(load,[project.id])
  useEffect(()=>{
    let active=true
    api<{worktree_root?:string}>('GET','/api/config').then(config=>{
      if(!active)return
      const root=typeof config.worktree_root==='string'?config.worktree_root:''
      setWorktreeRoot(root)
      setWorktreeConfigError(root?'':'Worktree root is unavailable. Enter an absolute path.')
      setWorktree(current=>current.pathEdited?current:{...current,path:worktreePathForBranch(root,project.name,project.id,current.branch)})
    }).catch(cause=>{
      if(active)setWorktreeConfigError(`Could not load the worktree root: ${cause instanceof Error?cause.message:String(cause)}`)
    })
    return()=>{active=false}
  },[project.id,project.name])
  // Was a three-rung Escape ternary. The rungs are now independent levels, so back
  // unwinds them in the order they were opened and the same rungs answer the platform
  // back gesture and the mobile back swipe.
  useDismissLevel(onClose,true,'run-menu')
  useDismissLevel(()=>setWorktreeOpen(false),worktreeOpen,'run-menu-worktree')
  useDismissLevel(()=>setPending(null),!!pending,'run-menu-trust')
  useEffect(()=>{
    const close=(event:KeyboardEvent)=>{if(event.key==='Escape')dismissStack.pop()}
    window.addEventListener('keydown',close)
    return()=>window.removeEventListener('keydown',close)
  },[])
  // Rotating the phone (or a soft keyboard opening) changes the viewport under a
  // menu that was already fitted to the old one, so re-fit on resize as well.
  useEffect(()=>{
    const refit=()=>fitScrollingMenuInViewport(panel.current)
    window.addEventListener('resize',refit)
    return()=>window.removeEventListener('resize',refit)
  },[])

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
  const openWorktree=()=>{
    setWorktreeError(worktreeConfigError)
    setWorktree(current=>({...current,branch:'',startPoint:'',path:worktreePathForBranch(worktreeRoot,project.name,project.id,''),pathEdited:false}))
    setWorktreeOpen(true)
  }
  const changeBranch=(value:string)=>{
    const branch=normalizeWorktreeBranchInput(value)
    setWorktree(current=>({
      ...current,branch,path:current.pathEdited?current.path:worktreePathForBranch(worktreeRoot,project.name,project.id,branch),
    }))
  }
  const launchWorktree=async(event:SubmitEvent)=>{
    event.preventDefault()
    const branch=worktree.branch.trim(),path=worktree.path.trim()
    if(!branch){setWorktreeError('New branch is required.');return}
    if(!isAbsolutePath(path)){setWorktreeError('Worktree path must be absolute.');return}
    setBusy('__worktree__');setWorktreeError('')
    try{
      const result=await api<WorktreeCreateResult>('POST','/api/git/worktrees',{
        cwd:project.root,path,branch,start_point:worktree.startPoint.trim()||undefined,
      },{timeoutMs:30000})
      onClose()
      onWorktreeCreated(result.path,worktree.backend)
    }catch(cause){setWorktreeError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy('')}
  }
  const groups=(['native','vscode','package'] as const).map(source=>({source,items:catalog?.actions.filter(item=>item.source===source)||[]})).filter(group=>group.items.length)
  const left=Math.min(anchor.x,Math.max(6,window.innerWidth-306))
  const top=Math.min(anchor.y,Math.max(6,window.innerHeight-460))
  return <>
    <div class="run-menu-scrim" onPointerDown={onClose}/>
    <section ref={el=>{panel.current=el;fitScrollingMenuInViewport(el)}} class="project-run-menu" role="menu" aria-label={`Run in ${project.name}`} style={{left,top}}>
      <header><div><span>RUN</span><strong>{project.name}</strong></div><button aria-label="Close Run menu" onClick={onClose}>×</button></header>
      {worktreeOpen?<form class="run-worktree-form" onSubmit={event=>void launchWorktree(event)}>
        <small>NEW WORKTREE SESSION</small>
        <label>Session<select value={worktree.backend} disabled={!!busy} onChange={event=>setWorktree(current=>({...current,backend:event.currentTarget.value}))}>{harnesses.map(harness=><option value={harness.name}>{harness.display_name}</option>)}<option value="shell">Shell</option></select></label>
        <label>New branch<input autofocus value={worktree.branch} disabled={!!busy} placeholder="worktree-my-change" spellcheck={false} onInput={event=>changeBranch(event.currentTarget.value)}/></label>
        <label>Start point <em>optional</em><input value={worktree.startPoint} disabled={!!busy} placeholder="HEAD" onInput={event=>setWorktree(current=>({...current,startPoint:event.currentTarget.value}))}/></label>
        <label>Absolute path<input value={worktree.path} disabled={!!busy} onInput={event=>setWorktree(current=>({...current,path:event.currentTarget.value,pathEdited:true}))}/></label>
        {worktreeError&&<p class="error" role="alert">{worktreeError}</p>}
        <div><button type="button" disabled={!!busy} onClick={()=>setWorktreeOpen(false)}>Back</button><button class="primary" type="submit" disabled={!!busy}>{busy?'Creating…':'Create and start'}</button></div>
      </form>:<>
      <div class="run-menu-section"><small>NEW SESSION</small>
        {harnesses.map(harness=><button role="menuitem" aria-label={`Start ${harness.display_name} session`} disabled={!!busy} onClick={()=>onLaunch(harness.name)}><span aria-hidden="true">▶</span><div><strong>{harness.display_name}</strong></div></button>)}
        <button data-tutorial="run-choice-shell" role="menuitem" aria-label="Start shell session" disabled={!!busy} onClick={()=>onLaunch('shell')}><span aria-hidden="true">&gt;_</span><div><strong>Shell</strong></div></button>
        <button role="menuitem" aria-label="Open custom terminal launcher" disabled={!!busy} onClick={onCustom}><span aria-hidden="true">⋯</span><div><strong>Custom terminal…</strong></div></button>
      </div>
      <div class="run-menu-section"><small>ISOLATED CHECKOUT</small><button role="menuitem" aria-label="Create a worktree and start a session" disabled={!!busy} onClick={openWorktree}><span aria-hidden="true">⑂</span><div><strong>New worktree session…</strong></div></button></div>
      {loading?<p>Reading Project tasks…</p>:groups.map(group=><div class="run-menu-section" key={group.source}><small>{sourceLabel[group.source]}</small>{group.items.map(action=><button role="menuitem" key={action.id} disabled={!!busy} title={action.steps.map(step=>step.command).join('\n')} onClick={()=>void execute(action)}><span>{busy===action.id?'…':'▶'}</span><div><strong>{action.label}</strong>{action.steps.length>1&&<em>{action.steps.length} terminals</em>}</div></button>)}</div>)}
      {!loading&&groups.length===0&&<p>No Project tasks found.</p>}
      {!!catalog?.diagnostics.length&&<details><summary>Import diagnostics ({catalog.diagnostics.length})</summary>{catalog.diagnostics.map((item,index)=><p key={`${index}:${item}`}>{item}</p>)}</details>}
      </>}
    </section>
    {pending&&catalog&&<div class="modal-layer project-action-trust-layer"><section class="modal project-action-trust" role="dialog" aria-modal="true" aria-label="Trust Project tasks">
      <div class="modal-heading"><div><span>PROJECT TASK TRUST</span><h2>Trust these task files?</h2></div><button onClick={()=>setPending(null)}>×</button></div>
      <div class="project-action-trust-body"><p>Running <strong>{pending.label}</strong> executes repository-provided commands. Trust applies only to the current exact contents; editing any task file requires approval again.</p><ul>{catalog.sources.map(source=><li key={source}>{source}</li>)}</ul><pre>{pending.steps.map(step=>`${step.name}: ${step.command}`).join('\n')}</pre></div>
      <div class="modal-footer"><button onClick={()=>setPending(null)}>Cancel</button><button class="primary" disabled={!!busy} onClick={()=>void trustAndRun()}>{busy?'Starting…':'Trust and run'}</button></div>
    </section></div>}
  </>
}
