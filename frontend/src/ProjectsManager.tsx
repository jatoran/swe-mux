import { codeLabel } from './localization/codeLabels.ts'
import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { Dropdown } from './Dropdown'
import { revealSetting } from './settingReveal'
import { SettingLink } from './SettingLink'
import type { LlmReadiness } from './llmProvider'
import { parseIgnorePatternDraft } from './settingsDraft'
import {
  PANEL_CONFIG_FIELDS,
  conflictNotice,
  nextWorktreeTable,
  projectConfigDelta,
  revisionConflict,
  type ProjectConfigChanges,
  type ProjectConfigValues,
  type WorktreeValues,
} from './projectConfig'
import { useProjectConfig, type ProjectConfigStore } from './projectConfigState'
import type { Project, ProjectBackend, ProjectGroup, PromptLibraryScope, Session, LaunchProfile } from './types'
import { allBackendNames, harnessDisplayName, isAgentBackend } from './harnessRegistry'
import { useDismissLevel } from './modalFocus'
import { byProjectName, projectDropdownOptions } from './projectOptions'

// The Projects registry owns a Project's record, defaults, resources, and agent
// authority. Control-plane opt-ins live in the Automation workspace with the graph
// and global policy they modify, and this panel links there instead of duplicating them.

// A Project default can live in either of two layers, and users care about the
// difference: `device` is a database override private to this machine, `repo` is
// `.swe-mux/config.toml` and travels with the checkout. Precedence is
// device > repo > global, so a value is only ever written to one of them.
type Layer = 'device' | 'repo'

/** Field id to the sentence a person reads, for the summary below. */
const AUTHORITY_SUMMARY: { field: string; label: string; levels: Record<string, string> }[] = [
  { field: 'session_control_grant', get label() { return t("ProjectsManager.dea242baeb") }, levels: { get draft() { return t("ProjectsManager.785cda37bb") }, get granted() { return t("ProjectsManager.b87ad6cb87") } } },
  { field: 'spawn_grant', get label() { return t("ProjectsManager.f4db61e77e") }, levels: { get draft() { return t("ProjectsManager.785cda37bb") }, get granted() { return t("ProjectsManager.45cfbcf74d") } } },
  { field: 'land_grant', get label() { return t("ProjectsManager.3675b3e222") }, levels: { get draft() { return t("ProjectsManager.785cda37bb") }, get granted() { return t("ProjectsManager.da8fe1373d") } } },
  { field: 'land_verify_grant', get label() { return t("ProjectsManager.291b4ebfaa") }, levels: { get draft() { return t("ProjectsManager.58a8b05591") }, get granted() { return t("ProjectsManager.741e2c9982") } } },
  { field: 'interject_grant', get label() { return t("ProjectsManager.312048bb88") }, levels: { off: 'never', get granted() { return t("ProjectsManager.9e39165a1f") } } },
  { field: 'message_envelope', get label() { return t("ProjectsManager.17bf57b622") }, levels: { get full() { return t("ProjectsManager.04c9d73c04") }, get compact() { return t("ProjectsManager.cc01313112") }, bare: 'none' } },
]

/**
 * What an agent may do here without asking - shown, not edited.
 *
 * These fields decide the *authority* behind capabilities whose on/off is an automation.
 * Every one of them shipped with no control in any overlay: they were lines in a committed
 * `.swe-mux/config.toml` and nothing else, which made the inert default both impossible to
 * discover and unreachable to change - one of them told the agent to go and edit the file by
 * hand (`agent_messaging.py`). This panel was the fix, and owned them until 2026-08-29.
 *
 * They now live on the Automation dashboard's policy matrix, beside the opt-ins they
 * qualify and beside the install-wide default and ceiling that only exist there. That
 * follows the rule the automation opt-ins already moved under: policy is the control map,
 * the matrix is the one editor, and this registry links to it rather than rendering a
 * second editor over the same file. What stays here is the summary, because this is where
 * somebody configuring a Project looks first and a permission with no trace on that screen
 * is a permission nobody finds.
 *
 * The values shown are this repository's own, before the install layers. "Follows global"
 * is a real state rather than a missing one: an unset field is what the install default is
 * allowed to reach, and collapsing it into a level here is exactly the confusion the third
 * dropdown position on the matrix exists to prevent.
 */
function AgentAuthoritySummary({ projectId, store }: {
  projectId: string
  /** The panel's one copy of `.swe-mux/config.toml`. Read only, here. */
  store: ProjectConfigStore
}) {
  const config = store.config
  return <section class="project-setting project-authority">
    <h4 data-setting="agent_authority">{rich("ProjectsManager.a1efb9992d", { value1: <em class="project-setting-chip">{t("ProjectsManager.071ca22277")}</em> })}</h4>
    <p>{t("ProjectsManager.318cb50def")}</p>
    {!config && <p>{t("ProjectsManager.ba3bbbe10d")}</p>}
    {config?.status === 'malformed' && <p class="project-folder-missing">{rich("ProjectsManager.161cfa3fc6", { value1: <code>{t("ProjectsManager.9f98c8f7e7")}</code> })}</p>}
    {config && <dl class="project-authority-summary">
      {AUTHORITY_SUMMARY.map(row => {
        const own = config.values[row.field]
        const level = typeof own === 'string' ? own : ''
        return <div key={row.field} data-setting={row.field}>
          <dt>{row.label}</dt>
          <dd>{level ? (row.levels[level] || level) : t("ProjectsManager.5818a98790")}</dd>
        </div>
      })}
    </dl>}
    <SettingLink target="project.authority" projectId={projectId}>{t("ProjectsManager.884df11c33")}</SettingLink>
  </section>
}

export type ProjectPatch =
  Partial<Pick<Project, 'name' | 'group_id' | 'sidebar_visible'>>
  & {
    default_backend?: ProjectBackend | null
    default_profile_id?: string | null
    default_agent_profiles?: Record<string, string>
  }

type Overrides = {
  default_backend?: ProjectBackend
  default_profile_id?: string
  default_agent_profiles: Record<string, string>
}

type Props = {
  projects:Project[]
  groups:ProjectGroup[]
  sessions:Session[]
  profiles:LaunchProfile[]
  initialProjectId?:string
  /** `data-setting` id of one control to scroll to and flash on arrival (`settingTargets.ts`). */
  initialSetting?:string
  /** Changes per deep-link request, so the same link twice reveals twice. */
  revealToken?:number
  onClose:()=>void
  onAdd:()=>void
  onAddGroup:()=>void
  onOpen:(project:Project)=>void
  onPatch:(project:Project,changes:ProjectPatch)=>Promise<Project>
  onRemove:(project:Project,closeLive:boolean)=>Promise<void>
}

const SCOPE_LABELS:Record<PromptLibraryScope,string>={get off() { return t("ProjectsManager.ca7981b46e") },get global() { return t("ProjectsManager.72cf0bd4a2") },get project() { return t("ProjectsManager.41bcf3115d") },get both() { return t("ProjectsManager.cf1ddbb925") }}

export function ProjectsManager({projects,groups,sessions,profiles,initialProjectId,initialSetting,revealToken,onClose,onAdd,onAddGroup,onOpen,onPatch,onRemove}:Props){
  const isVisible=(project:Project)=>project.sidebar_visible!==false
  // By name, not by sidebar position. Position is the operator's own sidebar arrangement,
  // which says nothing here: this list is searched for a Project by name, and the picker
  // above it is read the same way.
  const ordered=useMemo(()=>byProjectName(projects,project=>project.name), [projects, uiLocale.value, catalogRevision.value])
  const [selectedId,setSelectedId]=useState(initialProjectId||ordered[0]?.id||'')
  const panel=useRef<HTMLElement>(null)
  // List and detail sit side by side rather than drilling in, so the registry is one
  // level: back closes it, and there is no inner step to unwind first.
  useDismissLevel(onClose,true,'projects-manager')
  const [query,setQuery]=useState('')
  const [filter,setFilter]=useState<'all'|'visible'|'hidden'>('all')
  const [name,setName]=useState('')
  const [groupId,setGroupId]=useState('')
  const [overrides,setOverrides]=useState<Overrides>({default_agent_profiles:{}})
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [confirmRemove,setConfirmRemove]=useState(false)
  // A pending switch target parked while the current Project has unsaved edits, so
  // a fast pick (the mobile dropdown especially) cannot silently discard them.
  const [pendingSwitch,setPendingSwitch]=useState<string|null>(null)
  const selected=projects.find(project=>project.id===selectedId)||ordered[0]||null
  // One copy of `.swe-mux/config.toml` for the whole panel, refreshed when anything
  // writes it. The three sections below used to hold three, which is why editing any
  // one of them made the next edit answer "changed externally".
  const configStore=useProjectConfig(selected)
  const config=configStore.config
  // Only the fields the operator has actually touched. An overlay rather than a copy
  // of the file, so a refresh moves what nobody is editing and cannot discard a draft.
  const [draft,setDraft]=useState<ProjectConfigChanges>({})
  const shown=ordered.filter(project=>{
    if(filter==='visible'&&!isVisible(project))return false
    if(filter==='hidden'&&isVisible(project))return false
    const needle=query.trim().toLowerCase()
    return !needle||project.name.toLowerCase().includes(needle)||project.root.toLowerCase().includes(needle)
  })
  useEffect(()=>{if(!selected&&ordered[0])setSelectedId(ordered[0].id)},[selected,ordered])
  useEffect(()=>{
    setName(selected?.name||'');setGroupId(selected?.group_id||'')
    setOverrides({default_backend:selected?.default_backend,default_profile_id:selected?.default_profile_id,default_agent_profiles:{...(selected?.default_agent_profiles||{})}})
    setConfirmRemove(false);setError('')
  },[selected?.id,selected?.name,selected?.group_id,selected?.default_backend,selected?.default_profile_id,JSON.stringify(selected?.default_agent_profiles||{})])

  // Reveal a control a gated surface deep-linked to. The opt-in list this usually points at
  // is two fetches deep (the registry, then that Project's automation state), and switching
  // Projects re-runs the second one, so the reveal waits for its control instead of firing
  // once into a panel that is still loading. Re-armed on `revealToken` so a second click on
  // the same link flashes again, and on `selected?.id` because a Project switch replaces the
  // very rows it points at.
  useEffect(()=>{
    if(!initialSetting)return
    const root=panel.current
    if(!root)return
    return revealSetting(root,initialSetting)
  },[initialSetting,revealToken,selected?.id])

  // Switching Projects abandons the overlay; the shared copy re-reads itself.
  useEffect(()=>{setDraft({})},[selected?.id,selected?.root])

  // What the form draws: the file, with the operator's unsaved edits on top. Written
  // back as the difference between the two, never as this whole map - it holds fields
  // this form does not draw (the opt-ins, the authority table, the approval posture,
  // the land queue's verify command), and writing it back whole is how a stale copy
  // silently reverts them.
  const values:ProjectConfigValues={...(config?.values||{}),...draft}
  // One field's current worth: the operator's edit where they have made one, the file
  // otherwise. Read inside the state updater so two edits in one tick compose.
  const fieldOf=(current:ProjectConfigChanges,key:string):unknown=>
    key in current?current[key]:config?.values[key]
  const editValues=(patch:ProjectConfigChanges)=>setDraft(current=>({...current,...patch}))

  const effective=selected?.effective_options
  const backendLayer:Layer=overrides.default_backend?'device':values.preferred_backend?'repo':'device'
  const backendValue=overrides.default_backend||values.preferred_backend||''
  const profileLayer:Layer=overrides.default_profile_id?'device':values.default_shell_profile?'repo':'device'
  const profileValue=overrides.default_profile_id||values.default_shell_profile||''

  // Writing a value always clears the other layer: leaving a stale override behind
  // would silently win over the value the user just chose.
  const setBackend=(value:string,layer:Layer)=>{
    setOverrides(current=>({...current,default_backend:layer==='device'&&value?value as ProjectBackend:undefined}))
    editValues({preferred_backend:layer==='repo'&&value?value as ProjectBackend:undefined})
  }
  const setProfile=(value:string,layer:Layer)=>{
    setOverrides(current=>({...current,default_profile_id:layer==='device'&&value?value:undefined}))
    editValues({default_shell_profile:layer==='repo'&&value?value:undefined})
  }
  // One selection per harness, in the same two layers. The repo layer stores an id
  // and never argv, so a checkout can say which locally-defined profile to use
  // without carrying arguments of its own.
  const agentProfileLayer=(backend:string):Layer=>
    overrides.default_agent_profiles[backend]?'device':values.default_agent_profiles?.[backend]?'repo':'device'
  const agentProfileValue=(backend:string)=>
    overrides.default_agent_profiles[backend]||values.default_agent_profiles?.[backend]||''
  // Only harnesses that actually have a profile get a row. A selector whose only
  // option is "none" teaches nothing and makes the panel longer for every harness
  // the user has never configured.
  const harnessesWithProfiles=useMemo(()=>{
    const grouped=new Map<string,LaunchProfile[]>()
    for(const profile of profiles){
      if(profile.backend==='shell')continue
      grouped.set(profile.backend,[...(grouped.get(profile.backend)||[]),profile])
    }
    return [...grouped.entries()]
  }, [profiles, uiLocale.value, catalogRevision.value])
  const setAgentProfile=(backend:string,value:string,layer:Layer)=>{
    const without=(map:Record<string,string>)=>{const next={...map};delete next[backend];return next}
    setOverrides(current=>({...current,default_agent_profiles:layer==='device'&&value
      ?{...current.default_agent_profiles,[backend]:value}
      :without(current.default_agent_profiles)}))
    setDraft(current=>{
      const map=(fieldOf(current,'default_agent_profiles')||{}) as Record<string,string>
      const next=layer==='repo'&&value?{...map,[backend]:value}:without(map)
      return {...current,default_agent_profiles:Object.keys(next).length?next:undefined}
    })
  }
  // Repo-only, one text input per agent harness: a model default is a request in
  // the CLI's own spelling, not an authority, so there is no device layer to
  // arbitrate - and a stale name degrades at spawn time rather than refusing here,
  // which is why free text is honest where a picker would imply a validated list.
  const setAgentModel=(backend:string,value:string)=>setDraft(current=>{
    const map={...((fieldOf(current,'default_agent_models')||{}) as Record<string,string>)}
    if(value.trim())map[backend]=value.trim();else delete map[backend]
    return {...current,default_agent_models:Object.keys(map).length?map:undefined}
  })
  const setWorktreeSetup=(command:string)=>setDraft(current=>({
    ...current,
    // Only this field of the table. The land queue owns `verify_command` in the same
    // table, and replacing the table wholesale used to delete the approved
    // verification command whenever someone cleared the setup command.
    worktree:nextWorktreeTable(fieldOf(current,'worktree') as WorktreeValues|undefined,'setup_command',command),
  }))
  // "Inherited" means the fields this form draws, not the whole file: the button once
  // wrote an empty document, taking the automation opt-ins, the authority table, the
  // approval rules and the verify command with it.
  const resetRepoOptions=()=>setDraft(current=>{
    const next:ProjectConfigChanges={...current}
    for(const field of PANEL_CONFIG_FIELDS)next[field]=undefined
    next.worktree=nextWorktreeTable(fieldOf(current,'worktree') as WorktreeValues|undefined,'setup_command','')
    return next
  })

  const detailsDirty=!!selected&&(name.trim()!==selected.name||(groupId||null)!==(selected.group_id||null))
  const overridesDirty=!!selected&&(
    (overrides.default_backend||null)!==(selected.default_backend||null)
    ||(overrides.default_profile_id||null)!==(selected.default_profile_id||null)
    ||JSON.stringify(overrides.default_agent_profiles)!==JSON.stringify(selected.default_agent_profiles||{})
  )
  const portableChanges=projectConfigDelta(draft,config?.values)
  const portableDirty=Object.keys(portableChanges).length>0
  const dirty=detailsDirty||overridesDirty||portableDirty

  // Switching Projects re-seeds every draft field, so an unsaved edit would be lost.
  // Guard the switch when dirty; a clean switch is immediate.
  const selectProject=(id:string)=>{
    if(id===selectedId)return
    if(dirty){setPendingSwitch(id);return}
    setSelectedId(id)
  }
  useEffect(()=>{setPendingSwitch(null)},[selectedId])
  // The root is the `detail` line rather than a second field, and it is what the filter
  // searches beyond the name: two checkouts of one repo are two Projects with the same name,
  // and the path is the only thing that tells them apart. Sidebar visibility rides the same
  // line, because it was a coloured dot the shared control has no room for.
  const pickerOptions=useMemo(()=>projectDropdownOptions(ordered,project=>({
    value:project.id,
    label:project.name,
    get detail() { return t("ProjectsManager.3b07babd51", { root: project.root, value2: (isVisible(project)) ? 'yes' : 'no' }) },
  })), [ordered, uiLocale.value, catalogRevision.value])

  const save=async()=>{
    if(!selected||!name.trim())return
    setBusy(true);setError('')
    try{
      if(detailsDirty||overridesDirty){
        await onPatch(selected,{
          name:name.trim(),
          group_id:groupId||null,
          default_backend:overrides.default_backend||null,
          default_profile_id:overrides.default_profile_id||null,
          default_agent_profiles:overrides.default_agent_profiles,
        })
      }
      if(config&&portableDirty){
        await configStore.commit(portableChanges)
        // The shared copy now holds what the daemon wrote, so the overlay has nothing
        // left to say; keeping it would re-send the same fields on the next save.
        setDraft({})
      }
    }
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const toggleVisible=async()=>{
    if(!selected)return
    setBusy(true);setError('')
    try{await onPatch(selected,{sidebar_visible:!isVisible(selected)})}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const remove=async(closeLive:boolean)=>{
    if(!selected)return
    setBusy(true);setError('')
    try{await onRemove(selected,closeLive);setSelectedId(ordered.find(project=>project.id!==selected.id)?.id||'')}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause));setConfirmRemove(false)}
    finally{setBusy(false)}
  }
  const liveCount=selected?sessions.filter(session=>session.project_id===selected.id&&!['exited','crashed'].includes(session.state)).length:0

  const layerRow=(active:Layer,set:(layer:Layer)=>void,shownWhen:boolean)=>shownWhen&&<div class="project-setting-layer">
    <button class={active==='device'?'active':''} disabled={busy} onClick={()=>set('device')}>{t("ProjectsManager.cf3cc23e5a")}</button>
    <button class={active==='repo'?'active':''} disabled={busy||!config} title={config ? undefined : t("ProjectsManager.72b964f024")} onClick={()=>set('repo')}>{t("ProjectsManager.071ca22277")}</button>
    <span class="project-setting-note">{active==='repo' ? t("ProjectsManager.bc60fc9699") : t("ProjectsManager.ac1b22cb83")}</span>
  </div>

  return <div class="modal-layer projects-manager-layer" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="projects-manager" role="dialog" aria-modal="true" aria-label={t("ProjectsManager.7e01880f87")} ref={panel}>
      <header><div><span>{t("ProjectsManager.9f309c4aa2")}</span><h2 data-heading-message="ProjectsManager.04e2a9728a">{t("ProjectsManager.04e2a9728a")}</h2><small>{t("ProjectsManager.f9b1e7e32b")}</small></div><div class="projects-manager-header-actions"><button onClick={onAddGroup}>{t("ProjectsManager.df796c655f")}</button><button data-tutorial="add-project" class="primary" onClick={onAdd}>{t("ProjectsManager.cf77ef5338")}</button><button class="icon" aria-label={t("ProjectsManager.d9523c9f8b")} onClick={onClose}>×</button></div></header>
      <div class="projects-manager-body">
        <aside><div class="projects-manager-filter"><input aria-label={t("ProjectsManager.9e079c7df9")} placeholder={t("ProjectsManager.b0c5facf52")} value={query} onInput={event=>setQuery(event.currentTarget.value)}/><Dropdown ariaLabel={t("ProjectsManager.8fd82c567a")} value={filter} onChange={value=>setFilter(value as typeof filter)} options={[{value:'all',get label() { return t("ProjectsManager.a52ace420f") }},{value:'visible',get label() { return t("ProjectsManager.479c830779") }},{value:'hidden',get label() { return t("ProjectsManager.7e6fefff0f") }}]}/></div>
          <div data-tutorial="project-list" class="projects-manager-list">{shown.map(project=><button class={project.id===selected?.id?'active':''} onClick={()=>selectProject(project.id)}><span class={`project-visibility-dot ${isVisible(project)?'visible':'hidden'}`} aria-hidden="true"/><strong>{project.name}</strong><small>{project.root}</small><em>{isVisible(project) ? t("ProjectsManager.2aca0c68ff") : t("ProjectsManager.e564b4081d")}</em></button>)}{!shown.length && <p>{t("ProjectsManager.ee4db6ac7b")}</p>}</div>
        </aside>
        <main>{selected ? <>
          <div class="projects-manager-picker"><Dropdown id="projects-manager-picker" class="projects-manager-picker-trigger" ariaLabel={t("ProjectsManager.dcdc012563")} listLabel={t("ProjectsManager.9656e68cbb")} value={selected.id} options={pickerOptions} placeholder={t("ProjectsManager.45e376ca22")} filter filterPlaceholder={t("ProjectsManager.2e0060c45e")} onChange={selectProject}/></div>
          {pendingSwitch && <div class="projects-manager-switch-confirm" role="alertdialog" aria-label={t("ProjectsManager.a710c2b909")}><span>{rich("ProjectsManager.5c6d72ff1b", { value1: <strong>{selected.name}</strong> })}</span><div><button onClick={()=>setPendingSwitch(null)}>{t("ProjectsManager.e76fd2add0")}</button><button class="danger" onClick={()=>{const target=pendingSwitch;setPendingSwitch(null);setSelectedId(target)}}>{t("ProjectsManager.2c900860e8")}</button></div></div>}
          <div class="projects-manager-title"><span>{t("ProjectsManager.d1fb402f12")}</span><h3>{selected.name}</h3><div class="projects-manager-title-meta"><small>{rich("ProjectsManager.3b246675ac", { liveCount: liveCount, value2: (liveCount===1) ? 'yes' : 'no', value3: selected.history_count || 0, value4: (selected.history_count===1) ? 'yes' : 'no', value5: (selected.root_available===false) ? 'yes' : 'no' })}</small><div class="projects-manager-title-actions"><button data-tutorial="open-project" class="primary" disabled={selected.root_available===false} onClick={()=>onOpen(selected)}>{t("ProjectsManager.b3e34b185d")}</button><button class={`sidebar-visibility-toggle ${isVisible(selected)?'active':''}`} disabled={busy} onClick={()=>void toggleVisible()}><span aria-hidden="true">{isVisible(selected) ? '◉' : '○'}</span>{isVisible(selected) ? t("ProjectsManager.83fa7a6b4a") : t("ProjectsManager.333bbfedde")}</button></div></div></div>
          <div class="projects-manager-panel">
            <div class="projects-manager-form">
              <h4 class="projects-manager-section" data-heading-message="ProjectsManager.999f23fcd7">{t("ProjectsManager.999f23fcd7")}</h4>
              <label>{rich("ProjectsManager.5b326d4444", { value1: <input value={name} onInput={event=>setName(event.currentTarget.value)}/> })}</label>
              <label>{rich("ProjectsManager.926081f8f4", { value1: <input class={selected.root_available===false?'missing':''} value={selected.root} readOnly/>, value2: selected.root_available===false && <span class="project-folder-missing">{t("ProjectsManager.b1d68a1b10")}</span> })}</label>
              <label>{rich("ProjectsManager.bd64d0e1c9", { value1: <div><Dropdown value={groupId} onChange={setGroupId} options={[{value:'',get label() { return t("ProjectsManager.674b38cae7") }},...groups.map(group=>({value:group.id,label:group.name}))]}/><button onClick={onAddGroup}>+</button></div> })}</label>
              <h4 class="projects-manager-section" data-heading-message="ProjectsManager.610b7468ce">{t("ProjectsManager.610b7468ce")}</h4>
              <p>{rich("ProjectsManager.e5a1623a8b", { value1: <code>{t("ProjectsManager.9f98c8f7e7")}</code>, value2: config ? t("ProjectsManager.ac625c1545", { status: codeLabel(config.status), value2: config.error?` · ${config.error}`:'' }) : t("ProjectsManager.0da8662f96") })}</p>
              <div class="project-setting">
                <label><span class="project-setting-name">{rich("ProjectsManager.52c56b5713", { value1: backendValue && <em class="project-setting-chip">{backendLayer==='repo' ? t("ProjectsManager.071ca22277") : t("ProjectsManager.263a4dbe41")}</em> })}</span>
                  <Dropdown value={backendValue} disabled={busy} onChange={value=>setBackend(value,backendLayer)} options={[{value:'',get label() { return t("ProjectsManager.853d3c0d46", { value1: effective?.backend||'shell' }) }},...allBackendNames().map(backend=>({value:backend,label:backend}))]}/>
                </label>
                {layerRow(backendLayer,layer=>setBackend(backendValue,layer),!!backendValue)}
              </div>
              <div class="project-setting">
                <label><span class="project-setting-name">{rich("ProjectsManager.4c5867d9ef", { value1: profileValue && <em class="project-setting-chip">{profileLayer==='repo' ? t("ProjectsManager.071ca22277") : t("ProjectsManager.263a4dbe41")}</em> })}</span>
                  <Dropdown value={profileValue} disabled={busy} onChange={value=>setProfile(value,profileLayer)} options={[{value:'',get label() { return t("ProjectsManager.853d3c0d46", { value1: effective?.profile_id||'default' }) }},...profiles.filter(profile=>profile.backend==='shell').map(profile=>({value:profile.id,label:profile.label}))]}/>
                </label>
                {layerRow(profileLayer,layer=>setProfile(profileValue,layer),!!profileValue)}
              </div>
              {harnessesWithProfiles.map(([backend,harnessProfiles])=><div class="project-setting" key={backend}>
                <label><span class="project-setting-name">{rich("ProjectsManager.4544ea28ff", { value1: harnessDisplayName(backend), value2: agentProfileValue(backend) && <em class="project-setting-chip">{agentProfileLayer(backend)==='repo' ? t("ProjectsManager.071ca22277") : t("ProjectsManager.263a4dbe41")}</em> })}</span>
                  <Dropdown value={agentProfileValue(backend)} disabled={busy} onChange={value=>setAgentProfile(backend,value,agentProfileLayer(backend))} options={[{value:'',get label() { return t("ProjectsManager.78b2c2f3cd") }},...harnessProfiles.map(profile=>({value:profile.id,label:profile.label}))]}/>
                </label>
                {layerRow(agentProfileLayer(backend),layer=>setAgentProfile(backend,agentProfileValue(backend),layer),!!agentProfileValue(backend))}
              </div>)}
              <h4 class="projects-manager-section" data-heading-message="ProjectsManager.6ae8f3a07c">{t("ProjectsManager.6ae8f3a07c")}</h4>
              <label><span class="project-setting-name">{rich("ProjectsManager.5484db6bc4", { value1: <em class="project-setting-chip">{t("ProjectsManager.071ca22277")}</em> })}</span>
                <Dropdown value={values.prompt_library_scope||''} disabled={busy||!config} onChange={value=>editValues({prompt_library_scope:(value||undefined) as PromptLibraryScope|undefined})} options={[{value:'',get label() { return t("ProjectsManager.853d3c0d46", { value1: SCOPE_LABELS[effective?.prompt_library_scope||'both'] }) }},...(Object.keys(SCOPE_LABELS) as PromptLibraryScope[]).map(scope=>({value:scope,label:SCOPE_LABELS[scope]}))]}/>
              </label>
              <label class="check"><span class="project-setting-name">{rich("ProjectsManager.6be1540660", { value1: <em class="project-setting-chip">{t("ProjectsManager.071ca22277")}</em> })}</span><input type="checkbox" disabled={busy||!config} checked={values.notification_sounds_enabled!==false} onChange={event=>editValues({notification_sounds_enabled:event.currentTarget.checked})}/></label>
              <section class="project-setting">
                <h4>{rich("ProjectsManager.bc5eeb117d", { value1: <em class="project-setting-chip">{t("ProjectsManager.071ca22277")}</em> })}</h4>
                <p>{rich("ProjectsManager.f60289f0d5", { value1: <code>{t("ProjectsManager.e12ce8285e")}</code>, value2: <code>{t("ProjectsManager.c5f9e7e936")}</code> })}</p>
                {allBackendNames().filter(isAgentBackend).map(backend=><label key={backend}><span class="project-setting-name">{rich("ProjectsManager.50cefda907", { value1: harnessDisplayName(backend) })}</span>
                  <input value={((values.default_agent_models||{})[backend])||''} disabled={busy||!config} placeholder={t("ProjectsManager.c0eba39ea3")} onInput={event=>setAgentModel(backend,event.currentTarget.value)}/>
                </label>)}
              </section>
              <label><span class="project-setting-name">{rich("ProjectsManager.d05ed0eb75", { value1: <em class="project-setting-chip">{t("ProjectsManager.071ca22277")}</em> })}</span>
                <textarea value={(values.ignore_patterns||[]).join('\n')} disabled={busy||!config} onInput={event=>editValues({ignore_patterns:parseIgnorePatternDraft(event.currentTarget.value)})}/>
              </label>
              <p>{rich("ProjectsManager.510dbbbb36", { value1: <code>{t("ProjectsManager.dba27c31aa")}</code> })}</p>
              <section class="project-setting">
                <h4>{rich("ProjectsManager.47a2268708", { value1: <em class="project-setting-chip">{t("ProjectsManager.071ca22277")}</em> })}</h4>
                <label><span class="project-setting-name">{t("ProjectsManager.1f53e75aa0")}</span>
                  <input value={values.worktree?.setup_command||''} disabled={busy||!config} placeholder={t("ProjectsManager.bffb38bb5e")} onInput={event=>setWorktreeSetup(event.currentTarget.value)}/>
                </label>
                <p>{rich("ProjectsManager.c3cd816e1e", { value1: <code>{t("ProjectsManager.c8a0800a85")}</code>, value2: <code>{t("ProjectsManager.9f98c8f7e7")}</code> })}</p>
              </section>
              <div><button disabled={busy||!config} onClick={resetRepoOptions}>{t("ProjectsManager.6494d9a493")}</button></div>
              <section class="project-setting"><h4>{rich("ProjectsManager.675b7ef936", { value1: <em class="project-setting-chip">{t("ProjectsManager.071ca22277")}</em> })}</h4>
                <p>{t("ProjectsManager.eccb1b48e4")}</p>
                <SettingLink target="project.automations" projectId={selected.id}>{t("ProjectsManager.4b743e7025")}</SettingLink>
              </section>
              <AgentAuthoritySummary projectId={selected.id} store={configStore} />
            </div>
          </div>
          {confirmRemove && <section class="project-removal-summary" aria-label={t("ProjectsManager.88178d6394")}>
            <h4>{rich("ProjectsManager.c744b3f4df", { name: selected.name })}</h4>
            <dl><div><dt>{t("ProjectsManager.74ccd43303")}</dt><dd>{selected.root_available===false ? t("ProjectsManager.6be36ca49e") : t("ProjectsManager.e674447337")} · {selected.root}</dd></div><div><dt>{t("ProjectsManager.366487a11e")}</dt><dd>{liveCount}</dd></div><div><dt>{t("ProjectsManager.80c250f3db")}</dt><dd>{rich("ProjectsManager.19b23e4ff8", { value1: selected.history_count || 0 })}</dd></div></dl>
            <p>{rich("ProjectsManager.a6f04ccb8e", { value1: <code>{t("ProjectsManager.64f9020bac")}</code> })}</p>
            <div><button onClick={()=>setConfirmRemove(false)}>{t("ProjectsManager.19766ed6cc")}</button><button class="danger" disabled={busy} onClick={()=>void remove(liveCount>0)}>{liveCount>0 ? t("ProjectsManager.93471ae7f8", { liveCount: liveCount, value2: (liveCount===1) ? 'yes' : 'no' }) : t("ProjectsManager.6ed5c06df7")}</button></div>
          </section>}
          {(error||configStore.error) && <p class="projects-manager-error" role="alert">{error || configStore.error}</p>}
          <footer><button class="danger" disabled={busy} onClick={()=>setConfirmRemove(true)}>{t("ProjectsManager.1e0492316d")}</button><span>{t("ProjectsManager.4c12251254")}</span><button class="primary" disabled={!dirty||busy||!name.trim()} onClick={()=>void save()}>{busy ? t("ProjectsManager.23e39291d6") : t("ProjectsManager.dd0ae7a5cb")}</button></footer>
        </> : <div class="projects-manager-empty"><strong>{t("ProjectsManager.33564c9af2")}</strong><p>{t("ProjectsManager.d59f0506f8")}</p><button data-tutorial="add-project" class="primary" onClick={onAdd}>{t("ProjectsManager.cf77ef5338")}</button></div>}</main>
      </div>
    </section>
  </div>
}
