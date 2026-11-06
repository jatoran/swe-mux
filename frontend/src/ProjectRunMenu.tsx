import { isComposingKey } from './inputComposition.ts'
import { codeLabel } from './localization/codeLabels.ts'
import { rich, t } from './localization/index.ts'
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
  /** Runs the whole worktree launch - `git worktree add`, then bootstrap and spawn behind an
   *  optimistic pane. Resolves to the creation failure's message, which belongs in this form
   *  because it is the one the operator can fix here, or `null` once the launch is under way. */
  onWorktreeLaunch:(draft:WorktreeLaunch)=>Promise<string|null>
  plugins:InstalledPlugin[]
  onPluginPane:(pluginId:string,paneId:string)=>void
  onError:(message:string)=>void
  /** Opened out of the Project menu by hover. No scrim - the menu underneath stays live -
   *  and the pointer entering and leaving this panel is reported so the host can hold the
   *  flyout open while the pointer is here and fold it after the pointer has gone. Leaving
   *  is *not* reported while a trust prompt, an input prompt, the actions editor or the
   *  worktree form is up: those are the operator's, and a pointer drifting off a form must
   *  not take the form with it. */
  flyout?:boolean
  onHoverEnter?:()=>void
  onHoverLeave?:()=>void
}

const sourceLabel:Record<ProjectAction['source'],string>={
  get vscode() { return t("ProjectRunMenu.6e6024283c") },get package() { return t("ProjectRunMenu.b6d7e44079") },get native() { return t("ProjectRunMenu.4886862276") },
}

type WorktreeDraft={backend:ProjectBackend;branch:string;startPoint:string;path:string;pathEdited:boolean}
export type WorktreeLaunch={path:string;branch:string;startPoint:string;backend:ProjectBackend}
type ActionsSource={path:string;exists:boolean;text:string;revision:string;starter:boolean}
type ActionsSaved=ActionsSource&{diagnostics:string[];catalog:ProjectActionCatalog}

export function ProjectRunMenu({project,profiles,anchor,onClose,onLaunch,onCustom,onSessions,onWorktreeLaunch,plugins,onPluginPane,onError,flyout=false,onHoverEnter,onHoverLeave}:Props){
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
      setWorktreeConfigError(root?'':t("ProjectRunMenu.89acfcfd7a"))
      setWorktree(current=>current.pathEdited?current:{...current,path:worktreePathForBranch(root,project.name,project.id,current.branch)})
    }).catch(cause=>{
      if(active)setWorktreeConfigError(t("ProjectRunMenu.7056d5c1df", { value1: cause instanceof Error?cause.message:String(cause) }))
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
    const close=(event:KeyboardEvent)=>{
      if (isComposingKey(event)) return
if(event.key==='Escape')dismissStack.pop()}
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
    if(!branch){setWorktreeError(t("ProjectRunMenu.d05812e94e"));return}
    if(!isAbsolutePath(path)){setWorktreeError(t("ProjectRunMenu.c26e104eb3"));return}
    setBusy('__worktree__');setWorktreeError('')
    // The pane is already on screen behind this menu, so the form is open only for as long as
    // `git worktree add` runs - and stays open *on its error*, which is the one failure the
    // operator can fix from here. Bootstrap and spawn continue after it closes.
    const failure=await onWorktreeLaunch({path,branch,startPoint:worktree.startPoint.trim(),backend:worktree.backend})
    setBusy('')
    if(failure){setWorktreeError(failure);return}
    onClose()
  }
  const groups=(['native','vscode','package'] as const).map(source=>({source,items:catalog?.actions.filter(item=>item.source===source)||[]})).filter(group=>group.items.length)
  const pluginPanes=projectPluginPanes(plugins)
  const left=Math.min(anchor.x,Math.max(6,window.innerWidth-306))
  const top=Math.min(anchor.y,Math.max(6,window.innerHeight-460))
  // A sub-surface pins a flyout: the pointer is on a form or a dialog, not browsing rows.
  const hoverLocked=worktreeOpen||!!pending||!!prompting||!!source
  return <>
    {!flyout && <div class="run-menu-scrim" onPointerDown={onClose}/>}
    <section ref={el=>{panel.current=el;fitScrollingMenuInViewport(el)}} class={`project-run-menu${flyout?' run-menu-flyout':''}`} role="menu" aria-label={t("ProjectRunMenu.672398143e", { name: project.name })} style={{left,top}}
      onPointerEnter={event=>{if(flyout&&event.pointerType!=='touch')onHoverEnter?.()}}
      onPointerLeave={event=>{if(flyout&&!hoverLocked&&event.pointerType!=='touch')onHoverLeave?.()}}>
      <header><div><span>{t("ProjectRunMenu.ddeaded8ad")}</span><strong>{project.name}</strong></div><button aria-label={t("ProjectRunMenu.25bbfc5959")} onClick={onClose}>×</button></header>
      {worktreeOpen ? <form class="run-worktree-form" onSubmit={event=>void launchWorktree(event)}>
        <small>{t("ProjectRunMenu.8304bf9bd5")}</small>
        <label>{rich("ProjectRunMenu.7d8e5ab667", { value1: <Dropdown value={worktree.backend} disabled={!!busy} onChange={value=>setWorktree(current=>({...current,backend:value}))} options={[...harnesses.map(harness=>({value:harness.name,label:harness.display_name})),{value:'shell',get label() { return t("ProjectRunMenu.a733285486") }}]}/> })}</label>
        <label>{rich("ProjectRunMenu.32e5bfbc84", { value1: <input autofocus value={worktree.branch} disabled={!!busy} placeholder={t("ProjectRunMenu.7fe9a60fbf")} spellcheck={false} onInput={event=>changeBranch(event.currentTarget.value)}/> })}</label>
        <label>{rich("ProjectRunMenu.f974f23242", { value1: <em>{t("ProjectRunMenu.ec91fdd925")}</em>, value2: <input value={worktree.startPoint} disabled={!!busy} placeholder={t("ProjectRunMenu.b518022316")} onInput={event=>setWorktree(current=>({...current,startPoint:event.currentTarget.value}))}/> })}</label>
        <label>{rich("ProjectRunMenu.d29a6c1b66", { value1: <input value={worktree.path} disabled={!!busy} onInput={event=>setWorktree(current=>({...current,path:event.currentTarget.value,pathEdited:true}))}/> })}</label>
        {worktreeError && <p class="error" role="alert">{worktreeError}</p>}
        <div><button type="button" disabled={!!busy} onClick={()=>setWorktreeOpen(false)}>{t("ProjectRunMenu.76900f1bfd")}</button><button class="primary" type="submit" disabled={!!busy}>{busy ? t("ProjectRunMenu.c79ed9492e") : t("ProjectRunMenu.6c90567a27")}</button></div>
      </form> : <>
      <div class="run-menu-section"><small>{t("ProjectRunMenu.0473538890")}</small>
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
        {!harnesses.length && <p class="run-menu-empty">{allHarnessesIncludingDisabled().length ? <>{rich("ProjectRunMenu.09a0a7a154", { value1: <SettingLink target="harnesses.enabled" variant="link">{t("ProjectRunMenu.7bfb00b315")}</SettingLink> })}</> : <>{t("ProjectRunMenu.7fc44c513e")}</>}</p>}
        {harnesses.map(harness=><Fragment key={harness.name}>
          <button role="menuitem" aria-label={t("ProjectRunMenu.8ebcadc457", { display_name: harness.display_name })} disabled={!!busy} onClick={()=>onLaunch(harness.name)}><span class="run-menu-mark" aria-hidden="true">{harnessMark(harness.name)}</span><div><strong>{harness.display_name}</strong>{defaultProfileLabel(harness.name) && <em>{defaultProfileLabel(harness.name)}</em>}</div></button>
          {profilesFor(harness.name).map(profile=><button role="menuitem" key={profile.id} aria-label={t("ProjectRunMenu.335ab75aa6", { display_name: harness.display_name, label: profile.label })} disabled={!!busy} title={profile.args.join(' ')} onClick={()=>onLaunch(harness.name,profile.id)}><span class="run-menu-mark" aria-hidden="true">{harnessMark(harness.name)}</span><div><strong>{profile.label}</strong><em>{profile.args.join(' ') || harness.display_name}</em></div></button>)}
        </Fragment>)}
        <button data-tutorial="run-choice-shell" role="menuitem" aria-label={t("ProjectRunMenu.77465d308f")} disabled={!!busy} onClick={()=>onLaunch('shell')}><span aria-hidden="true">&gt;_</span><div><strong>{t("ProjectRunMenu.a733285486")}</strong></div></button>
        <button role="menuitem" aria-label={t("ProjectRunMenu.ff6c5a520e")} disabled={!!busy} onClick={onCustom}><span aria-hidden="true">⋯</span><div><strong>{t("ProjectRunMenu.98c7e7f7cd")}</strong></div></button>
      </div>
      <div class="run-menu-section"><small>{t("ProjectRunMenu.b7de186b43")}</small><button role="menuitem" aria-label={t("ProjectRunMenu.2f7b1c25b7")} disabled={!!busy} onClick={openWorktree}><span aria-hidden="true">⑂</span><div><strong>{t("ProjectRunMenu.0669d1c030")}</strong></div></button></div>
      {!!pluginPanes.length && <div class="run-menu-section"><small>{t("ProjectRunMenu.1622f1e456")}</small>{pluginPanes.map(item=><button role="menuitem" key={`${item.pluginId}:${item.pane.id}`} disabled={!!busy} title={item.pane.description} onClick={()=>{onClose();onPluginPane(item.pluginId,item.pane.id)}}><span aria-hidden="true">◇</span><div><strong>{item.pane.title}</strong><em>{item.pluginName}</em></div></button>)}</div>}
      {loading ? <p>{t("ProjectRunMenu.5b3dad4f28")}</p> : groups.map(group=><div class="run-menu-section" key={group.source}><small>{sourceLabel[group.source]}</small>{group.items.map(action=>{
    /* Only the run shape sits beside the title. The description is agent-facing prose
       and it is laid out `flex:none`, so it took the whole row and ellipsised the name
       a human picks by - on package scripts, where the description is the script body,
       the name vanished entirely. It stays on the row's tooltip, in the trust prompt,
       and above the inputs form. */
    const hint=[action.steps.length>1?`${action.steps.length} terminals`:'',action.inputs?.length?t("ProjectRunMenu.1ff9591981"):''].filter(Boolean).join(' · ')
    /* Hollow triangle for an action whose file is not approved, filled once it is:
       monospace-safe unlike an emoji, and it reads as "not armed". Deliberately the
       only trust hint in the row; the trust prompt is where the state gets explained,
       in full, with a diff. */
    return <button role="menuitem" key={action.id} disabled={!!busy} title={[action.description,...action.steps.map(step=>step.command)].filter(Boolean).join('\n')} onClick={()=>execute(action)}><span>{busy===action.id ? '…' : isTrusted(action) ? '▶' : '▷'}</span><div><strong>{action.label}</strong>{hint && <em>{hint}</em>}</div></button>
  })}</div>)}
      {!loading&&groups.length===0 && <p>{t("ProjectRunMenu.df343707ff")}</p>}
      <div class="run-menu-section"><small>{t("ProjectRunMenu.bb612d263d")}</small><button role="menuitem" aria-label={t("ProjectRunMenu.fa515ecd51")} disabled={!!busy} onClick={openSource}><span aria-hidden="true">✎</span><div><strong>{catalog?.files?.find(item=>item.path==='.swe-mux/actions.toml')?.present ? t("ProjectRunMenu.ad106b6347") : t("ProjectRunMenu.b4d0d6fca9")}</strong><em>{t("ProjectRunMenu.566451c57f")}</em></div></button></div>
      {!!catalog?.diagnostics.length && <details><summary>{rich("ProjectRunMenu.14c88bcf98", { length: catalog.diagnostics.length })}</summary>{catalog.diagnostics.map((item,index)=><p key={`${index}:${item}`}>{item}</p>)}</details>}
      </>}
    </section>
    {pending&&catalog && <div class="modal-layer project-action-trust-layer"><section class="modal project-action-trust" role="dialog" aria-modal="true" aria-label={t("ProjectRunMenu.b3cc2ac7be")}>
      <div class="modal-heading"><div><span>{t("ProjectRunMenu.59430746e0")}</span><h2>{rich("ProjectRunMenu.72449dfb72", { value1: fileFor(pending)?.path || t("ProjectRunMenu.8159030727") })}</h2></div><button onClick={()=>{setPending(null);setDiffs(null)}}>×</button></div>
      <div class="project-action-trust-body">
        <p>{rich("ProjectRunMenu.4d6b69626d", { value1: <strong>{pending.label}</strong> })}</p>
        <ul>{(fileFor(pending)?[fileFor(pending)!.path]:catalog.sources).map(source=><li key={source}>{source}</li>)}</ul>
        <pre>{pending.steps.map(step=>`${step.name}: ${step.command}`).join('\n')}</pre>
        {diffs===null ? <p>{t("ProjectRunMenu.800e546f00")}</p> : changedDiffs(diffs,pending).map(item=><details key={item.path} open>
            <summary>{item.path} · {codeLabel(item.status)}</summary>
            {item.diff ? <pre class="project-action-diff">{item.diff}</pre> : <p>{t("ProjectRunMenu.d2a815d99e")}</p>}
          </details>)}
      </div>
      <div class="modal-footer"><button onClick={()=>{setPending(null);setDiffs(null)}}>{t("ProjectRunMenu.19766ed6cc")}</button><button class="primary" disabled={!!busy} onClick={()=>void trustAndContinue()}>{busy ? t("ProjectRunMenu.bbe5fc3b9e") : t("ProjectRunMenu.af9f197158")}</button></div>
    </section></div>}
    {source && <div class="modal-layer project-action-source-layer"><section class="modal project-action-source" role="dialog" aria-modal="true" aria-label={t("ProjectRunMenu.ef3ec728c5")}>
      <div class="modal-heading"><div><span>{t("ProjectRunMenu.1f30dc4bf2")}</span><h2>{source.path}</h2></div><button onClick={()=>setSource(null)}>×</button></div>
      <div class="project-action-source-body">
        <p>{source.starter ? t("ProjectRunMenu.899e9627a8") : t("ProjectRunMenu.43b0c82d4c")}</p>
        <button class="link" onClick={()=>setSourceHelp(!sourceHelp)}>{sourceHelp ? t("ProjectRunMenu.c3a0833c41") : t("ProjectRunMenu.4a59edbd04")}</button>
        {sourceHelp && <div class="project-action-help">
          <p>{rich("ProjectRunMenu.d39890fde0", { value1: <strong>{t("ProjectRunMenu.6b8d828fe7")}</strong>, value2: <code>{t("ProjectRunMenu.19ed40bf62")}</code> })}</p>
          <ul>
            <li>{rich("ProjectRunMenu.c8247b66b8", { value1: <code>{t("ProjectRunMenu.a56145270c")}</code>, value2: <code>{t("ProjectRunMenu.1aca80e8b5")}</code>, value3: <code>{t("ProjectRunMenu.c9046f7a37")}</code> })}</li>
            <li>{rich("ProjectRunMenu.83f22e8971", { value1: <code>{t("ProjectRunMenu.1303c06b0b")}</code>, value2: <code>{t("ProjectRunMenu.19ed40bf62")}</code>, value3: <code>{t("ProjectRunMenu.ce635c4eab")}</code> })}</li>
            <li>{rich("ProjectRunMenu.073d304822", { value1: <code>{t("ProjectRunMenu.5d347fd948")}</code>, value2: <code>{t("ProjectRunMenu.090772cf40")}</code>, value3: <code>{t("ProjectRunMenu.6f2f7e6c65")}</code>, value4: <code>{t("ProjectRunMenu.b77349bf1f")}</code> })}</li>
            <li>{rich("ProjectRunMenu.4cfee58fed", { value1: <code>{t("ProjectRunMenu.0684f6b9c8")}</code>, value2: <code>{t("ProjectRunMenu.340d600392")}</code>, value3: <code>{t("ProjectRunMenu.caf90169ee")}</code>, value4: <code>{t("ProjectRunMenu.26ce1a1580")}</code>, value5: <code>{t("ProjectRunMenu.9cf70d8a9b")}</code>, value6: <code>{t("ProjectRunMenu.3ee15b8a77")}</code> })}</li>
            <li>{rich("ProjectRunMenu.073421b027", { value1: <code>{t("ProjectRunMenu.2553146d02")}</code> })}</li>
            <li>{rich("ProjectRunMenu.6767d1fef9", { value1: <code>{t("ProjectRunMenu.b40eb9093f")}</code>, value2: <code>{t("ProjectRunMenu.69f86b0cef")}</code>, value3: <code>{t("ProjectRunMenu.ce635c4eab")}</code> })}</li>
            <li>{rich("ProjectRunMenu.0314f01e64", { value1: <code>{t("ProjectRunMenu.ad6f6fd6d1")}</code>, value2: <code>{t("ProjectRunMenu.93e6eb88da")}</code>, value3: <code>{t("ProjectRunMenu.826c0c6a39")}</code>, value4: <code>{t("ProjectRunMenu.ae77b7f8a0")}</code> })}</li>
          </ul>
          <p>{rich("ProjectRunMenu.4e0509062d", { value1: <code>{t("ProjectRunMenu.d397a48f39")}</code>, value2: <code>{t("ProjectRunMenu.0ae4b8386b")}</code> })}</p>
        </div>}
        <textarea class="project-action-source-text" spellcheck={false} value={sourceText} onInput={event=>setSourceText(event.currentTarget.value)}/>
        {sourceError && <pre class="error" role="alert">{sourceError}</pre>}
      </div>
      <div class="modal-footer"><button onClick={()=>setSource(null)}>{t("ProjectRunMenu.19766ed6cc")}</button><button class="primary" disabled={busy==='__source__'} onClick={()=>void saveSource()}>{busy==='__source__' ? t("ProjectRunMenu.23e39291d6") : t("ProjectRunMenu.1509f561f2")}</button></div>
    </section></div>}
    {prompting && <div class="modal-layer project-action-inputs-layer"><section class="modal project-action-inputs" role="dialog" aria-modal="true" aria-label={t("ProjectRunMenu.67fc4ec3d7", { label: prompting.label })}>
      <div class="modal-heading"><div><span>{t("ProjectRunMenu.2dcc099b98")}</span><h2>{prompting.label}</h2></div><button onClick={()=>setPrompting(null)}>×</button></div>
      <form onSubmit={event=>{event.preventDefault();const action=prompting;setPrompting(null);void start(action,inputValues)}}>
        {prompting.description && <p>{prompting.description}</p>}
        {(prompting.inputs||[]).map(item=><label key={item.id}>{item.label}
          {item.kind==='choice' ? <Dropdown value={inputValues[item.id]??item.default} onChange={value=>setInputValues(current=>({...current,[item.id]:value}))} options={item.options.map(option=>({value:option,label:option}))}/> : <input value={inputValues[item.id]??item.default} spellcheck={false} onInput={event=>setInputValues(current=>({...current,[item.id]:event.currentTarget.value}))}/>}
        </label>)}
        <pre>{prompting.steps.map(step=>`${step.name}: ${step.command}`).join('\n')}</pre>
        <div class="modal-footer"><button type="button" onClick={()=>setPrompting(null)}>{t("ProjectRunMenu.19766ed6cc")}</button><button class="primary" type="submit" disabled={!!busy}>{busy ? t("ProjectRunMenu.bbe5fc3b9e") : t("ProjectRunMenu.00d60e31a4")}</button></div>
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
