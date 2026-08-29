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
import { allBackendNames, harnessDisplayName } from './harnessRegistry'
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
  { field: 'session_control_grant', label: 'Interrupt and end sessions', levels: { draft: 'a human approves each', granted: 'acts directly' } },
  { field: 'spawn_grant', label: 'Start new sessions here', levels: { draft: 'a human approves each', granted: 'creates them directly' } },
  { field: 'land_grant', label: 'Land a branch onto the trunk', levels: { draft: 'a human approves each', granted: 'starts the pipeline' } },
  { field: 'interject_grant', label: 'Write into a running turn', levels: { off: 'never', granted: 'may interject' } },
  { field: 'message_envelope', label: 'Metadata on agent messages', levels: { full: 'full trust statement', compact: 'sender and reply route', bare: 'none' } },
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
    <h4 data-setting="agent_authority">Agent authority<em class="project-setting-chip">repo</em></h4>
    <p>Whether an agent still needs a human once the automation above is on. Edited on the
    policy matrix, where the install-wide default and the per-Project override sit side by
    side. A field this repository has not set follows the install default.</p>
    {!config && <p>Loading…</p>}
    {config?.status === 'malformed' && <p class="project-folder-missing">
      This Project’s <code>.swe-mux/config.toml</code> cannot be parsed, so every field below
      resolves to its most restrictive value and inherits nothing. Fix the file to restore them.
    </p>}
    {config && <dl class="project-authority-summary">
      {AUTHORITY_SUMMARY.map(row => {
        const own = config.values[row.field]
        const level = typeof own === 'string' ? own : ''
        return <div key={row.field} data-setting={row.field}>
          <dt>{row.label}</dt>
          <dd>{level ? (row.levels[level] || level) : 'follows global'}</dd>
        </div>
      })}
    </dl>}
    <SettingLink target="project.authority" projectId={projectId}>Edit agent authority</SettingLink>
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

const SCOPE_LABELS:Record<PromptLibraryScope,string>={off:'Off',global:'Global only',project:'Project only',both:'Global + Project'}

export function ProjectsManager({projects,groups,sessions,profiles,initialProjectId,initialSetting,revealToken,onClose,onAdd,onAddGroup,onOpen,onPatch,onRemove}:Props){
  const isVisible=(project:Project)=>project.sidebar_visible!==false
  // By name, not by sidebar position. Position is the operator's own sidebar arrangement,
  // which says nothing here: this list is searched for a Project by name, and the picker
  // above it is read the same way.
  const ordered=useMemo(()=>byProjectName(projects,project=>project.name),[projects])
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
  },[profiles])
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
    detail:`${project.root}${isVisible(project)?'':' · hidden'}`,
  })),[ordered])

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
    <button class={active==='device'?'active':''} disabled={busy} onClick={()=>set('device')}>this device</button>
    <button class={active==='repo'?'active':''} disabled={busy||!config} title={config?undefined:'The Project’s .swe-mux/config.toml is not readable.'} onClick={()=>set('repo')}>repo</button>
    <span class="project-setting-note">{active==='repo'?'stored in .swe-mux/config.toml, travels with the checkout':'stored in this machine’s database'}</span>
  </div>

  return <div class="modal-layer projects-manager-layer" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="projects-manager" role="dialog" aria-modal="true" aria-label="Manage projects" ref={panel}>
      <header><div><span>PROJECTS::REGISTRY</span><h2>Projects</h2><small>Configured Projects keep their notes, files, settings, and history even when hidden from the sidebar.</small></div><div class="projects-manager-header-actions"><button onClick={onAddGroup}>New group</button><button data-tutorial="add-project" class="primary" onClick={onAdd}>+ Add project</button><button class="icon" aria-label="Close Projects" onClick={onClose}>×</button></div></header>
      <div class="projects-manager-body">
        <aside><div class="projects-manager-filter"><input aria-label="Search projects" placeholder="Search projects…" value={query} onInput={event=>setQuery(event.currentTarget.value)}/><Dropdown ariaLabel="Filter projects" value={filter} onChange={value=>setFilter(value as typeof filter)} options={[{value:'all',label:'All'},{value:'visible',label:'In sidebar'},{value:'hidden',label:'Hidden'}]}/></div>
          <div data-tutorial="project-list" class="projects-manager-list">{shown.map(project=><button class={project.id===selected?.id?'active':''} onClick={()=>selectProject(project.id)}><span class={`project-visibility-dot ${isVisible(project)?'visible':'hidden'}`} aria-hidden="true"/><strong>{project.name}</strong><small>{project.root}</small><em>{isVisible(project)?'sidebar':'hidden'}</em></button>)}{!shown.length&&<p>No Projects match this view.</p>}</div>
        </aside>
        <main>{selected?<>
          <div class="projects-manager-picker"><Dropdown id="projects-manager-picker" class="projects-manager-picker-trigger" ariaLabel="Selected project" listLabel="Configured projects" value={selected.id} options={pickerOptions} placeholder="Select a project…" filter filterPlaceholder="Filter Projects…" onChange={selectProject}/></div>
          {pendingSwitch&&<div class="projects-manager-switch-confirm" role="alertdialog" aria-label="Unsaved changes"><span>Discard unsaved changes to <strong>{selected.name}</strong>?</span><div><button onClick={()=>setPendingSwitch(null)}>Keep editing</button><button class="danger" onClick={()=>{const target=pendingSwitch;setPendingSwitch(null);setSelectedId(target)}}>Discard and switch</button></div></div>}
          <div class="projects-manager-title"><span>PROJECT</span><h3>{selected.name}</h3><div class="projects-manager-title-meta"><small>{liveCount} live session{liveCount===1?'':'s'} · {selected.history_count||0} conversation{selected.history_count===1?'':'s'}{selected.root_available===false?' · folder missing':''}</small><div class="projects-manager-title-actions"><button data-tutorial="open-project" class="primary" disabled={selected.root_available===false} onClick={()=>onOpen(selected)}>Open workspace</button><button class={`sidebar-visibility-toggle ${isVisible(selected)?'active':''}`} disabled={busy} onClick={()=>void toggleVisible()}><span aria-hidden="true">{isVisible(selected)?'◉':'○'}</span>{isVisible(selected)?'Shown in sidebar':'Show in sidebar'}</button></div></div></div>
          <div class="projects-manager-panel">
            <div class="projects-manager-form">
              <h4 class="projects-manager-section">Identity</h4>
              <label>Name<input value={name} onInput={event=>setName(event.currentTarget.value)}/></label>
              <label>Folder<input class={selected.root_available===false?'missing':''} value={selected.root} readOnly/>{selected.root_available===false&&<span class="project-folder-missing">Folder missing. swe-mux will not recreate it.</span>}</label>
              <label>Group<div><Dropdown value={groupId} onChange={setGroupId} options={[{value:'',label:'Ungrouped'},...groups.map(group=>({value:group.id,label:group.name}))]}/><button onClick={onAddGroup}>+</button></div></label>
              <h4 class="projects-manager-section">Defaults</h4>
              <p>Blank inherits the global default. Each value is stored either on this device or in the Project's <code>.swe-mux/config.toml</code>; device wins where both are set.{config?` · .swe-mux/config.toml: ${config.status}${config.error?` · ${config.error}`:''}`:' · reading .swe-mux/config.toml…'}</p>
              <div class="project-setting">
                <label><span class="project-setting-name">Default backend{backendValue&&<em class="project-setting-chip">{backendLayer==='repo'?'repo':'device'}</em>}</span>
                  <Dropdown value={backendValue} disabled={busy} onChange={value=>setBackend(value,backendLayer)} options={[{value:'',label:`Inherit (${effective?.backend||'shell'})`},...allBackendNames().map(backend=>({value:backend,label:backend}))]}/>
                </label>
                {layerRow(backendLayer,layer=>setBackend(backendValue,layer),!!backendValue)}
              </div>
              <div class="project-setting">
                <label><span class="project-setting-name">Shell profile{profileValue&&<em class="project-setting-chip">{profileLayer==='repo'?'repo':'device'}</em>}</span>
                  <Dropdown value={profileValue} disabled={busy} onChange={value=>setProfile(value,profileLayer)} options={[{value:'',label:`Inherit (${effective?.profile_id||'default'})`},...profiles.filter(profile=>profile.backend==='shell').map(profile=>({value:profile.id,label:profile.label}))]}/>
                </label>
                {layerRow(profileLayer,layer=>setProfile(profileValue,layer),!!profileValue)}
              </div>
              {harnessesWithProfiles.map(([backend,harnessProfiles])=><div class="project-setting" key={backend}>
                <label><span class="project-setting-name">{harnessDisplayName(backend)} launch profile{agentProfileValue(backend)&&<em class="project-setting-chip">{agentProfileLayer(backend)==='repo'?'repo':'device'}</em>}</span>
                  <Dropdown value={agentProfileValue(backend)} disabled={busy} onChange={value=>setAgentProfile(backend,value,agentProfileLayer(backend))} options={[{value:'',label:'None (plain launch)'},...harnessProfiles.map(profile=>({value:profile.id,label:profile.label}))]}/>
                </label>
                {layerRow(agentProfileLayer(backend),layer=>setAgentProfile(backend,agentProfileValue(backend),layer),!!agentProfileValue(backend))}
              </div>)}
              <h4 class="projects-manager-section">Repository options</h4>
              <label><span class="project-setting-name">Prompt library scope<em class="project-setting-chip">repo</em></span>
                <Dropdown value={values.prompt_library_scope||''} disabled={busy||!config} onChange={value=>editValues({prompt_library_scope:(value||undefined) as PromptLibraryScope|undefined})} options={[{value:'',label:`Inherit (${SCOPE_LABELS[effective?.prompt_library_scope||'both']})`},...(Object.keys(SCOPE_LABELS) as PromptLibraryScope[]).map(scope=>({value:scope,label:SCOPE_LABELS[scope]}))]}/>
              </label>
              <label class="check"><span class="project-setting-name">Allow device notification sounds<em class="project-setting-chip">repo</em></span><input type="checkbox" disabled={busy||!config} checked={values.notification_sounds_enabled!==false} onChange={event=>editValues({notification_sounds_enabled:event.currentTarget.checked})}/></label>
              <label><span class="project-setting-name">Additional ignore patterns<em class="project-setting-chip">repo</em></span>
                <textarea value={(values.ignore_patterns||[]).join('\n')} disabled={busy||!config} onInput={event=>editValues({ignore_patterns:parseIgnorePatternDraft(event.currentTarget.value)})}/>
              </label>
              <p>One glob per line, added to the global ignore list. A name such as <code>node_modules</code> matches that folder at any depth. These rules affect the file tree and resource watchers, not Git.</p>
              <section class="project-setting">
                <h4>Git and worktrees<em class="project-setting-chip">repo</em></h4>
                <label><span class="project-setting-name">Worktree setup command</span>
                  <input value={values.worktree?.setup_command||''} disabled={busy||!config} placeholder="Use executable .worktree-setup when blank" onInput={event=>setWorktreeSetup(event.currentTarget.value)}/>
                </label>
                <p>Runs only after Run creates a new worktree and before its session starts. Blank uses an executable <code>.worktree-setup</code> in the new checkout. The command is committed in <code>.swe-mux/config.toml</code>, so review changes like other repository code.</p>
              </section>
              <div><button disabled={busy||!config} onClick={resetRepoOptions}>Reset repo options to inherited</button></div>
              <section class="project-setting"><h4>Automation policy<em class="project-setting-chip">repo</em></h4>
                <p>Control-plane opt-ins are edited with the global graph and policy in the Automation workspace.</p>
                <SettingLink target="project.automations" projectId={selected.id}>Open this Project's automation policy</SettingLink>
              </section>
              <AgentAuthoritySummary projectId={selected.id} store={configStore} />
            </div>
          </div>
          {confirmRemove&&<section class="project-removal-summary" aria-label="Remove Project confirmation">
            <h4>Remove {selected.name} from swe-mux?</h4>
            <dl><div><dt>Folder</dt><dd>{selected.root_available===false?'Missing':'Available'} · {selected.root}</dd></div><div><dt>Live sessions</dt><dd>{liveCount}</dd></div><div><dt>Conversation history</dt><dd>{selected.history_count||0} preserved</dd></div></dl>
            <p>The Project leaves the sidebar and registry. Its folder and <code>.swe-mux</code> contents are not changed. Re-adding this folder restores the same Project identity, history, settings, and layout.</p>
            <div><button onClick={()=>setConfirmRemove(false)}>Cancel</button><button class="danger" disabled={busy} onClick={()=>void remove(liveCount>0)}>{liveCount>0?`Close ${liveCount} session${liveCount===1?'':'s'} and remove`:'Remove from swe-mux'}</button></div>
          </section>}
          {(error||configStore.error)&&<p class="projects-manager-error" role="alert">{error||configStore.error}</p>}
          <footer><button class="danger" disabled={busy} onClick={()=>setConfirmRemove(true)}>Remove from swe-mux…</button><span>History is preserved. The folder is never deleted.</span><button class="primary" disabled={!dirty||busy||!name.trim()} onClick={()=>void save()}>{busy?'Saving…':'Save changes'}</button></footer>
        </>:<div class="projects-manager-empty"><strong>No Projects configured</strong><p>Add a folder-backed Project to begin.</p><button data-tutorial="add-project" class="primary" onClick={onAdd}>+ Add project</button></div>}</main>
      </div>
    </section>
  </div>
}
