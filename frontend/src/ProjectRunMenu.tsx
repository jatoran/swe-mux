import { Fragment } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { Dropdown } from './Dropdown'
import { fitScrollingMenuInViewport } from './menuPosition'
import type { LaunchProfile, Project, ProjectAction, ProjectActionCatalog, ProjectActionDiff, ProjectBackend, Session } from './types'
import { allHarnessesIncludingDisabled, promptDeliveryHarnesses } from './harnessRegistry'
import { SettingLink } from './SettingLink'
import { harnessMark } from './harnessIcons'
import { isAbsolutePath } from './gitWorktrees'
import { normalizeWorktreeBranchInput, worktreePathForBranch } from './worktreeLaunch'
import { dismissStack } from './dismissStack.ts'
import { useDismissLevel } from './modalFocus'
import { projectPluginPanes, type InstalledPlugin } from './pluginCatalog.ts'

type Anchor={x:number;y:number}
type Props={
  project:Project
  /** Every enabled launch profile, shells included; this menu filters to agents. */
  profiles:LaunchProfile[]
  anchor:Anchor
  onClose:()=>void
  onLaunch:(backend:ProjectBackend,profileId?:string)=>void
  onCustom:()=>void
  onSessions:(sessions:Session[])=>void
  onWorktreeCreated:(path:string,backend:ProjectBackend)=>void
  plugins:InstalledPlugin[]
  onPluginPane:(pluginId:string,paneId:string)=>void
  onError:(message:string)=>void
}

const sourceLabel:Record<ProjectAction['source'],string>={
  vscode:'VS Code tasks',package:'package scripts',native:'Project Actions',
}

type WorktreeDraft={backend:ProjectBackend;branch:string;startPoint:string;path:string;pathEdited:boolean}
type WorktreeCreateResult={ok:true;path:string}
type ActionsSource={path:string;exists:boolean;text:string;revision:string;starter:boolean}
type ActionsSaved=ActionsSource&{diagnostics:string[];catalog:ProjectActionCatalog}

export function ProjectRunMenu({project,profiles,anchor,onClose,onLaunch,onCustom,onSessions,onWorktreeCreated,plugins,onPluginPane,onError}:Props){
  const harnesses=promptDeliveryHarnesses()
  // A harness plus its launch profiles, so "Claude" and "Claude (plan)" sit together
  // rather than in a separate list where the relationship is lost.
  const profilesFor=(backend:string)=>profiles.filter(profile=>profile.backend===backend)
  // The bare harness entry is not "no profile" - it is "however this Project starts
  // this harness", which is the Project default when one is set. Saying so on the
  // entry itself is the only place a reader finds out; the alternative is a menu
  // where two entries silently do the same thing.
  const defaultProfileLabel=(backend:string)=>{
    const selected=project.effective_options?.agent_profile_ids?.[backend]
    if(!selected)return ''
    return profiles.find(profile=>profile.id===selected)?.label||selected
  }
  const launchBackends=['shell',...harnesses.map(harness=>harness.name)]
  const preferred=project.effective_options?.backend||project.default_backend||harnesses[0]?.name||'shell'
  const [catalog,setCatalog]=useState<ProjectActionCatalog|null>(null)
  const [loading,setLoading]=useState(true)
  const [busy,setBusy]=useState('')
  const [pending,setPending]=useState<ProjectAction|null>(null)
  const [diffs,setDiffs]=useState<ProjectActionDiff[]|null>(null)
  // The action waiting for its inputs, and the values typed so far. Separate from
  // `pending` (which is the trust prompt) because an approved action with inputs
  // still needs this step, and an unapproved one needs both in order.
  const [prompting,setPrompting]=useState<ProjectAction|null>(null)
  const [inputValues,setInputValues]=useState<Record<string,string>>({})
  // One editor serves both authoring and editing: the file is the unit a human
  // reasons about and the unit trust is granted over, so a per-action form would
  // have to reassemble it anyway and would always trail the format.
  const [source,setSource]=useState<ActionsSource|null>(null)
  const [sourceText,setSourceText]=useState('')
  const [sourceError,setSourceError]=useState('')
  const [sourceHelp,setSourceHelp]=useState(false)
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
  useDismissLevel(()=>{setPending(null);setDiffs(null)},!!pending,'run-menu-trust')
  useDismissLevel(()=>setPrompting(null),!!prompting,'run-menu-inputs')
  useDismissLevel(()=>setSource(null),!!source,'run-menu-source')
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

  // Trust is per file: an action is runnable when *its own* source file is approved,
  // so editing .swe-mux/actions.toml no longer re-prompts for the package scripts.
  // Falls back to the catalog-wide flag for a daemon that predates per-file trust.
  const isTrusted=(action:ProjectAction)=>action.trusted??!!catalog?.trusted
  const fileFor=(action:ProjectAction)=>catalog?.files?.find(item=>item.path===action.source_path)
  const start=async(action:ProjectAction,inputs:Record<string,string>)=>{
    setBusy(action.id)
    try{
      const result=await api<{sessions:Session[];errors:Array<{step:string;error:string}>}>('POST',`/api/projects/${project.id}/actions/run`,{action_id:action.id,inputs})
      if(result.sessions.length)onSessions(result.sessions)
      if(result.errors.length)onError(result.errors.map(item=>`${item.step}: ${item.error}`).join('\n'))
      onClose()
    }catch(cause){onError(cause instanceof Error?cause.message:String(cause));load()}
    finally{setBusy('')}
  }
  const openSource=()=>{
    setSourceError('');setSourceHelp(false)
    void api<ActionsSource>('GET',`/api/projects/${project.id}/actions/source`)
      .then(result=>{setSource(result);setSourceText(result.text)})
      .catch(cause=>onError(cause instanceof Error?cause.message:String(cause)))
  }
  const saveSource=async()=>{
    if(!source)return
    setBusy('__source__');setSourceError('')
    try{
      const saved=await api<ActionsSaved>('PUT',`/api/projects/${project.id}/actions/source`,{text:sourceText,revision:source.revision})
      setCatalog(saved.catalog)
      setSource(saved);setSourceText(saved.text)
      setSourceError(saved.diagnostics.length?saved.diagnostics.join('\n'):'')
      if(!saved.diagnostics.length)setSource(null)
    }catch(cause){setSourceError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy('')}
  }
  const openInputs=(action:ProjectAction)=>{
    setInputValues(Object.fromEntries((action.inputs||[]).map(item=>[item.id,item.default])))
    setPrompting(action)
  }
  const execute=(action:ProjectAction)=>{
    if(!isTrusted(action)){
      setPending(action)
      // Loaded alongside the prompt, not on demand: "these files changed" cannot
      // separate a renamed label from a new `curl | sh`, and a reader who has to
      // click for the diff usually approves without it.
      setDiffs(null)
      void api<{sources:ProjectActionDiff[]}>('GET',`/api/projects/${project.id}/actions/diff`)
        .then(result=>setDiffs(result.sources)).catch(()=>setDiffs([]))
      return
    }
    if(action.inputs?.length){openInputs(action);return}
    void start(action,{})
  }
  const trustAndContinue=async()=>{
    if(!catalog||!pending)return
    const action=pending
    const file=fileFor(action)
    setBusy(action.id)
    try{
      // Approve only the file this action came from when the daemon reports one.
      const body=file?{source:file.path,fingerprint:file.fingerprint}:{fingerprint:catalog.fingerprint}
      const trusted=await api<ProjectActionCatalog>('POST',`/api/projects/${project.id}/actions/trust`,body)
      setCatalog(trusted)
      setPending(null);setDiffs(null)
      const refreshed=trusted.actions.find(item=>item.id===action.id)||action
      if(refreshed.inputs?.length){setBusy('');openInputs(refreshed);return}
      setBusy('')
      await start(refreshed,{})
    }catch(cause){
      onError(cause instanceof Error?cause.message:String(cause))
      setPending(null);setDiffs(null);load();setBusy('')
    }
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
  const pluginPanes=projectPluginPanes(plugins)
  const left=Math.min(anchor.x,Math.max(6,window.innerWidth-306))
  const top=Math.min(anchor.y,Math.max(6,window.innerHeight-460))
  return <>
    <div class="run-menu-scrim" onPointerDown={onClose}/>
    <section ref={el=>{panel.current=el;fitScrollingMenuInViewport(el)}} class="project-run-menu" role="menu" aria-label={`Run in ${project.name}`} style={{left,top}}>
      <header><div><span>RUN</span><strong>{project.name}</strong></div><button aria-label="Close Run menu" onClick={onClose}>×</button></header>
      {worktreeOpen?<form class="run-worktree-form" onSubmit={event=>void launchWorktree(event)}>
        <small>NEW WORKTREE SESSION</small>
        <label>Session<Dropdown value={worktree.backend} disabled={!!busy} onChange={value=>setWorktree(current=>({...current,backend:value}))} options={[...harnesses.map(harness=>({value:harness.name,label:harness.display_name})),{value:'shell',label:'Shell'}]}/></label>
        <label>New branch<input autofocus value={worktree.branch} disabled={!!busy} placeholder="worktree-my-change" spellcheck={false} onInput={event=>changeBranch(event.currentTarget.value)}/></label>
        <label>Start point <em>optional</em><input value={worktree.startPoint} disabled={!!busy} placeholder="HEAD" onInput={event=>setWorktree(current=>({...current,startPoint:event.currentTarget.value}))}/></label>
        <label>Absolute path<input value={worktree.path} disabled={!!busy} onInput={event=>setWorktree(current=>({...current,path:event.currentTarget.value,pathEdited:true}))}/></label>
        {worktreeError&&<p class="error" role="alert">{worktreeError}</p>}
        <div><button type="button" disabled={!!busy} onClick={()=>setWorktreeOpen(false)}>Back</button><button class="primary" type="submit" disabled={!!busy}>{busy?'Creating…':'Create and start'}</button></div>
      </form>:<>
      <div class="run-menu-section"><small>NEW SESSION</small>
        {/* The harness's own mark, not a play triangle. Every row in this section starts a
            session, so a triangle on all of them distinguished nothing; the one fact that
            separates them is which CLI they start, which is exactly what the mark says. A
            profile row wears its harness's mark for the same reason - "Claude (plan)" is a
            Claude launch, and the label is what tells the two apart. */}
        {/* The 2026-08-20 usability audit's handed-off finding, still open when it was
            re-checked in Phase 16: with no harness enabled this section rendered Shell and
            "Custom terminal…" and nothing else, so a launcher whose whole subject is
            starting an agent silently claimed there were none to start. An empty list must
            say which kind of empty it is - the rule the Agent Environment tab already
            carries - and it needs a route to the switch rather than the name of a tab.
            Registered-but-none-enabled and nothing-registered-at-all are different facts
            and read differently here. */}
        {!harnesses.length&&<p class="run-menu-empty">{allHarnessesIncludingDisabled().length
          ?<>No agent is enabled for launching, so only a shell can start here. Agents follow CLI detection until you choose otherwise. <SettingLink target="harnesses.enabled" variant="link">Choose which agents appear</SettingLink></>
          :<>This daemon reported no agent harnesses at all, which usually means it is still starting. Reload the UI if it persists.</>}</p>}
        {harnesses.map(harness=><Fragment key={harness.name}>
          <button role="menuitem" aria-label={`Start ${harness.display_name} session`} disabled={!!busy} onClick={()=>onLaunch(harness.name)}><span class="run-menu-mark" aria-hidden="true">{harnessMark(harness.name)}</span><div><strong>{harness.display_name}</strong>{defaultProfileLabel(harness.name)&&<em>{defaultProfileLabel(harness.name)}</em>}</div></button>
          {profilesFor(harness.name).map(profile=><button role="menuitem" key={profile.id} aria-label={`Start ${harness.display_name} session using ${profile.label}`} disabled={!!busy} title={profile.args.join(' ')} onClick={()=>onLaunch(harness.name,profile.id)}><span class="run-menu-mark" aria-hidden="true">{harnessMark(harness.name)}</span><div><strong>{profile.label}</strong><em>{profile.args.join(' ')||harness.display_name}</em></div></button>)}
        </Fragment>)}
        <button data-tutorial="run-choice-shell" role="menuitem" aria-label="Start shell session" disabled={!!busy} onClick={()=>onLaunch('shell')}><span aria-hidden="true">&gt;_</span><div><strong>Shell</strong></div></button>
        <button role="menuitem" aria-label="Open custom terminal launcher" disabled={!!busy} onClick={onCustom}><span aria-hidden="true">⋯</span><div><strong>Custom terminal…</strong></div></button>
      </div>
      <div class="run-menu-section"><small>ISOLATED CHECKOUT</small><button role="menuitem" aria-label="Create a worktree and start a session" disabled={!!busy} onClick={openWorktree}><span aria-hidden="true">⑂</span><div><strong>New worktree session…</strong></div></button></div>
      {!!pluginPanes.length&&<div class="run-menu-section"><small>PLUGIN TOOLS</small>{pluginPanes.map(item=><button role="menuitem" key={`${item.pluginId}:${item.pane.id}`} disabled={!!busy} title={item.pane.description} onClick={()=>{onClose();onPluginPane(item.pluginId,item.pane.id)}}><span aria-hidden="true">◇</span><div><strong>{item.pane.title}</strong><em>{item.pluginName}</em></div></button>)}</div>}
      {loading?<p>Reading Project tasks…</p>:groups.map(group=><div class="run-menu-section" key={group.source}><small>{sourceLabel[group.source]}</small>{group.items.map(action=>{
    /* Only the run shape sits beside the title. The description is agent-facing prose
       and it is laid out `flex:none`, so it took the whole row and ellipsised the name
       a human picks by - on package scripts, where the description is the script body,
       the name vanished entirely. It stays on the row's tooltip, in the trust prompt,
       and above the inputs form. */
    const hint=[action.steps.length>1?`${action.steps.length} terminals`:'',action.inputs?.length?'asks for input':''].filter(Boolean).join(' · ')
    /* Hollow triangle for an action whose file is not approved, filled once it is:
       monospace-safe unlike an emoji, and it reads as "not armed". Deliberately the
       only trust hint in the row; the trust prompt is where the state gets explained,
       in full, with a diff. */
    return <button role="menuitem" key={action.id} disabled={!!busy} title={[action.description,...action.steps.map(step=>step.command)].filter(Boolean).join('\n')} onClick={()=>execute(action)}><span>{busy===action.id?'…':isTrusted(action)?'▶':'▷'}</span><div><strong>{action.label}</strong>{hint&&<em>{hint}</em>}</div></button>
  })}</div>)}
      {!loading&&groups.length===0&&<p>No Project tasks found.</p>}
      <div class="run-menu-section"><small>AUTHOR</small><button role="menuitem" aria-label="Edit this Project's actions file" disabled={!!busy} onClick={openSource}><span aria-hidden="true">✎</span><div><strong>{catalog?.files?.find(item=>item.path==='.swe-mux/actions.toml')?.present?'Edit Project Actions…':'New Project Action…'}</strong><em>.swe-mux/actions.toml</em></div></button></div>
      {!!catalog?.diagnostics.length&&<details><summary>Import diagnostics ({catalog.diagnostics.length})</summary>{catalog.diagnostics.map((item,index)=><p key={`${index}:${item}`}>{item}</p>)}</details>}
      </>}
    </section>
    {pending&&catalog&&<div class="modal-layer project-action-trust-layer"><section class="modal project-action-trust" role="dialog" aria-modal="true" aria-label="Trust Project tasks">
      <div class="modal-heading"><div><span>PROJECT TASK TRUST</span><h2>Trust {fileFor(pending)?.path||'these task files'}?</h2></div><button onClick={()=>{setPending(null);setDiffs(null)}}>×</button></div>
      <div class="project-action-trust-body">
        <p>Running <strong>{pending.label}</strong> executes repository-provided commands. Trust applies only to the current exact contents; editing the file requires approval again.</p>
        <ul>{(fileFor(pending)?[fileFor(pending)!.path]:catalog.sources).map(source=><li key={source}>{source}</li>)}</ul>
        <pre>{pending.steps.map(step=>`${step.name}: ${step.command}`).join('\n')}</pre>
        {diffs===null
          ?<p>Comparing with the approved version…</p>
          :changedDiffs(diffs,pending).map(item=><details key={item.path} open>
            <summary>{item.path} · {item.status}</summary>
            {item.diff?<pre class="project-action-diff">{item.diff}</pre>:<p>No previous approval to compare against, so the whole file is new to swe-mux.</p>}
          </details>)}
      </div>
      <div class="modal-footer"><button onClick={()=>{setPending(null);setDiffs(null)}}>Cancel</button><button class="primary" disabled={!!busy} onClick={()=>void trustAndContinue()}>{busy?'Starting…':'Trust and run'}</button></div>
    </section></div>}
    {source&&<div class="modal-layer project-action-source-layer"><section class="modal project-action-source" role="dialog" aria-modal="true" aria-label="Edit Project Actions">
      <div class="modal-heading"><div><span>PROJECT ACTIONS</span><h2>{source.path}</h2></div><button onClick={()=>setSource(null)}>×</button></div>
      <div class="project-action-source-body">
        <p>{source.starter
          ?'This Project has no actions file yet. The template below is a working example: edit it and save.'
          :'Saving changes this file, so its actions ask for approval again before they run. That is the trust boundary working, not a bug: an editor that could write a command and approve it would make the approval meaningless.'}</p>
        <button class="link" onClick={()=>setSourceHelp(!sourceHelp)}>{sourceHelp?'Hide syntax':'Show syntax'}</button>
        {sourceHelp&&<div class="project-action-help">
          <p><strong>An action is a manifest entry, not a program.</strong> No conditionals, no loops. Put logic in a script in the repository and point a <code>process</code> step at it.</p>
          <ul>
            <li><code>id</code> required · <code>label</code> · <code>description</code>, which agents read to tell two actions apart</li>
            <li><code>type</code> is <code>process</code> (no shell, args verbatim, no quoting surprises) or <code>shell</code> (pipes and redirection work)</li>
            <li><code>command</code> required · <code>args</code> · <code>cwd</code>, which must stay inside the Project · <code>env</code></li>
            <li><code>platforms</code> of <code>windows</code>, <code>linux</code>, <code>darwin</code> · <code>timeout_seconds</code> · <code>sequential</code> to start steps in order</li>
            <li><code>[[actions.steps]]</code> for more than one step, up to 32</li>
            <li><code>[[actions.inputs]]</code>, then <code>{'${input:id}'}</code> in args, cwd, or env. Never in a <code>shell</code> command with no args: that string reaches the shell unquoted.</li>
            <li>Variables: <code>{'${workspaceFolder}'}</code>, <code>{'${workspaceFolderBasename}'}</code>, <code>{'${pathSeparator}'}</code>, <code>{'${env:NAME}'}</code></li>
          </ul>
          <p>Full reference: <code>src/swe_mux/assets/project-actions-schema.md</code>, also served to agents by the <code>project_actions</code> MCP tool.</p>
        </div>}
        <textarea class="project-action-source-text" spellcheck={false} value={sourceText} onInput={event=>setSourceText(event.currentTarget.value)}/>
        {sourceError&&<pre class="error" role="alert">{sourceError}</pre>}
      </div>
      <div class="modal-footer"><button onClick={()=>setSource(null)}>Cancel</button><button class="primary" disabled={busy==='__source__'} onClick={()=>void saveSource()}>{busy==='__source__'?'Saving…':'Save'}</button></div>
    </section></div>}
    {prompting&&<div class="modal-layer project-action-inputs-layer"><section class="modal project-action-inputs" role="dialog" aria-modal="true" aria-label={`Inputs for ${prompting.label}`}>
      <div class="modal-heading"><div><span>RUN ACTION</span><h2>{prompting.label}</h2></div><button onClick={()=>setPrompting(null)}>×</button></div>
      <form onSubmit={event=>{event.preventDefault();const action=prompting;setPrompting(null);void start(action,inputValues)}}>
        {prompting.description&&<p>{prompting.description}</p>}
        {(prompting.inputs||[]).map(item=><label key={item.id}>{item.label}
          {item.kind==='choice'
            ?<Dropdown value={inputValues[item.id]??item.default} onChange={value=>setInputValues(current=>({...current,[item.id]:value}))} options={item.options.map(option=>({value:option,label:option}))}/>
            :<input value={inputValues[item.id]??item.default} spellcheck={false} onInput={event=>setInputValues(current=>({...current,[item.id]:event.currentTarget.value}))}/>}
        </label>)}
        <pre>{prompting.steps.map(step=>`${step.name}: ${step.command}`).join('\n')}</pre>
        <div class="modal-footer"><button type="button" onClick={()=>setPrompting(null)}>Cancel</button><button class="primary" type="submit" disabled={!!busy}>{busy?'Starting…':'Run'}</button></div>
      </form>
    </section></div>}
  </>
}

/** The sources worth showing beside a trust prompt: the action's own file first.
 *  An unchanged file is omitted because it contributes no decision. */
function changedDiffs(diffs:ProjectActionDiff[],action:ProjectAction):ProjectActionDiff[]{
  const relevant=diffs.filter(item=>item.present&&item.status!=='unchanged')
  const own=relevant.filter(item=>item.path===action.source_path)
  return own.length?own:relevant
}
