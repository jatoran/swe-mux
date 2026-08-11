import { useEffect, useMemo, useState } from 'preact/hooks'
import { api } from './api'
import { normalizeIgnorePatterns, parseIgnorePatternDraft, sameDraftValue } from './settingsDraft'
import type { Project, ProjectBackend, ProjectGroup, PromptLibraryScope, Session, ShellProfile } from './types'
import { allBackendNames } from './harnessRegistry'
import { useDismissLevel } from './modalFocus'

// The Projects registry is the ONLY per-Project editor. Settings holds global
// options exclusively; anything scoped to one Project — its record and its
// defaults — is edited here, reached from this modal or from a Project's context
// menu. Two doors to two overlapping surfaces was the previous arrangement and it
// left users hunting for which modal owned which field.
export type ProjectsManagerTab = 'details' | 'settings'

// A Project default can live in either of two layers, and users care about the
// difference: `device` is a database override private to this machine, `repo` is
// `.swe-mux/config.toml` and travels with the checkout. Precedence is
// device > repo > global, so a value is only ever written to one of them.
type Layer = 'device' | 'repo'

type PortableValues = {
  default_shell_profile?: string
  preferred_backend?: ProjectBackend
  prompt_library_scope?: PromptLibraryScope
  notification_sounds_enabled?: boolean
  ignore_patterns?: string[]
  worktree?: { setup_command?: string }
}
type ProjectConfig = {
  project: { id: string; label: string; root: string }
  path: string; status: string; revision: string; error?: string
  values: PortableValues
}

type AutomationEntry = {
  id: string
  kind: 'substrate' | 'consumer'
  label: string
  requires: string[]
  implemented: boolean
}
type AutomationState = {
  revision: string
  requested: Record<string, boolean>
  enabled: string[]
  blocked: Record<string, string[]>
  automations: AutomationEntry[]
}

/**
 * Per-project control-plane opt-in, rendered as a dependency tree rather than a
 * flat checkbox list.
 *
 * The distinction is the whole point: a consumer is only *effective* when its
 * substrate is enabled too, so a checkbox that reads "on" while the thing it
 * reads from is off would be a lie. Toggling a consumer therefore names what it
 * needs and offers to enable that closure in the same click. Enabling anything
 * here is what makes it run at all — nothing in this layer touches a project
 * that did not opt in.
 */
function AutomationOptIns({ project, busy, onError }: {
  project: Project
  busy: boolean
  onError: (message: string) => void
}) {
  const [state, setState] = useState<AutomationState | null>(null)
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    let stale = false
    setState(null)
    api<AutomationState>('GET', `/api/projects/${project.id}/automations`)
      .then(result => { if (!stale) setState(result) })
      .catch(cause => { if (!stale) onError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { stale = true }
  }, [project.id])

  const write = async (next: Record<string, boolean>) => {
    if (!state) return
    setSaving(true)
    try {
      const result = await api<AutomationState>('PUT', `/api/projects/${project.id}/automations`, {
        automations: next, revision: state.revision,
      })
      setState(result)
    } catch (cause) { onError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setSaving(false) }
  }

  if (!state) return <div class="project-automations"><h4>Control-plane automations</h4><p>Loading…</p></div>
  const byId = new Map(state.automations.map(item => [item.id, item]))
  // Enabling a consumer enables its whole transitive closure: the alternative is
  // a toggle that appears on and silently does nothing.
  const closure = (id: string, seen = new Set<string>()): Set<string> => {
    if (seen.has(id)) return seen
    seen.add(id)
    for (const dependency of byId.get(id)?.requires || []) closure(dependency, seen)
    return seen
  }
  const toggle = (id: string, on: boolean) => {
    const next = { ...state.requested }
    if (on) for (const item of closure(id)) next[item] = true
    else {
      delete next[id]
      // Turning substrate off turns off everything that reads from it, rather
      // than leaving dependents enabled-but-inert.
      for (const item of state.automations) if (closure(item.id).has(id)) delete next[item.id]
    }
    void write(next)
  }
  const row = (item: AutomationEntry) => {
    const on = state.requested[item.id] === true
    const missing = state.blocked[item.id] || []
    const disabled = busy || saving || !item.implemented
    return <li key={item.id} class={item.implemented ? '' : 'unavailable'}>
      <label class="check">
        <span class="project-setting-name">{item.label}
          <em class="project-setting-chip">{item.kind}</em>
          {!item.implemented && <em class="project-setting-chip">not built yet</em>}
        </span>
        <input type="checkbox" disabled={disabled} checked={on}
          onChange={event => toggle(item.id, event.currentTarget.checked)} />
      </label>
      {item.requires.length > 0 && <p class="project-automation-deps">
        needs {item.requires.map(id => byId.get(id)?.label || id).join(' · ')}
        {missing.length > 0 && <strong> — {missing.length} still off</strong>}
      </p>}
    </li>
  }
  const substrate = state.automations.filter(item => item.kind === 'substrate')
  const consumers = state.automations.filter(item => item.kind === 'consumer')
  return <div class="project-automations">
    <h4>Control-plane automations<em class="project-setting-chip">repo</em></h4>
    <p>Per-project opt-in. Substrate records facts and never acts or spends; a consumer reads substrate and needs it enabled to do anything. Nothing here runs on a project that did not opt in.</p>
    <ul class="project-automation-list">{substrate.map(row)}</ul>
    <ul class="project-automation-list">{consumers.map(row)}</ul>
  </div>
}

export type ProjectPatch =
  Partial<Pick<Project, 'name' | 'group_id' | 'sidebar_visible'>>
  & { default_backend?: ProjectBackend | null; default_profile_id?: string | null }

type Overrides = { default_backend?: ProjectBackend; default_profile_id?: string }

type Props = {
  projects:Project[]
  groups:ProjectGroup[]
  sessions:Session[]
  profiles:ShellProfile[]
  initialProjectId?:string
  initialTab?:ProjectsManagerTab
  onClose:()=>void
  onAdd:()=>void
  onAddGroup:()=>void
  onOpen:(project:Project)=>void
  onNotes:(project:Project)=>void
  onFiles:(project:Project)=>void
  onPatch:(project:Project,changes:ProjectPatch)=>Promise<Project>
  onDelete:(project:Project)=>Promise<void>
}

const SCOPE_LABELS:Record<PromptLibraryScope,string>={off:'Off',global:'Global only',project:'Project only',both:'Global + Project'}

export function ProjectsManager({projects,groups,sessions,profiles,initialProjectId,initialTab,onClose,onAdd,onAddGroup,onOpen,onNotes,onFiles,onPatch,onDelete}:Props){
  const isVisible=(project:Project)=>project.sidebar_visible!==false
  const ordered=useMemo(()=>[...projects].sort((a,b)=>a.position-b.position||a.name.localeCompare(b.name)),[projects])
  const [selectedId,setSelectedId]=useState(initialProjectId||ordered[0]?.id||'')
  const [tab,setTab]=useState<ProjectsManagerTab>(initialTab||'details')
  // List and detail sit side by side rather than drilling in, so the registry is one
  // level: back closes it, and there is no inner step to unwind first.
  useDismissLevel(onClose,true,'projects-manager')
  const [query,setQuery]=useState('')
  const [filter,setFilter]=useState<'all'|'visible'|'hidden'>('all')
  const [name,setName]=useState('')
  const [groupId,setGroupId]=useState('')
  const [overrides,setOverrides]=useState<Overrides>({})
  const [config,setConfig]=useState<ProjectConfig|null>(null)
  const [values,setValues]=useState<PortableValues>({})
  const [savedValues,setSavedValues]=useState<PortableValues>({})
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [confirmDelete,setConfirmDelete]=useState(false)
  const selected=projects.find(project=>project.id===selectedId)||ordered[0]||null
  const shown=ordered.filter(project=>{
    if(filter==='visible'&&!isVisible(project))return false
    if(filter==='hidden'&&isVisible(project))return false
    const needle=query.trim().toLowerCase()
    return !needle||project.name.toLowerCase().includes(needle)||project.root.toLowerCase().includes(needle)
  })
  useEffect(()=>{if(!selected&&ordered[0])setSelectedId(ordered[0].id)},[selected,ordered])
  useEffect(()=>{
    setName(selected?.name||'');setGroupId(selected?.group_id||'')
    setOverrides({default_backend:selected?.default_backend,default_profile_id:selected?.default_profile_id})
    setConfirmDelete(false);setError('')
  },[selected?.id,selected?.name,selected?.group_id,selected?.default_backend,selected?.default_profile_id])

  // The portable layer lives in the checkout, so it is read per Project rather than
  // taken from the registry payload — its revision is what guards the write.
  useEffect(()=>{
    if(!selected){setConfig(null);setValues({});setSavedValues({});return}
    let stale=false
    setConfig(null);setValues({});setSavedValues({})
    // project_id names the registered Project explicitly: without it the daemon
    // re-resolves the root through Git, and a Project registered inside a larger
    // worktree edits the enclosing worktree's config instead of its own.
    api<ProjectConfig>('GET',`/api/project/config?cwd=${encodeURIComponent(selected.root)}&project_id=${encodeURIComponent(selected.id)}`)
      .then(result=>{if(stale)return;setConfig(result);setValues(result.values);setSavedValues(result.values)})
      .catch(cause=>{if(!stale)setError(cause instanceof Error?cause.message:String(cause))})
    return ()=>{stale=true}
  },[selected?.id,selected?.root])

  const effective=selected?.effective_options
  const backendLayer:Layer=overrides.default_backend?'device':values.preferred_backend?'repo':'device'
  const backendValue=overrides.default_backend||values.preferred_backend||''
  const profileLayer:Layer=overrides.default_profile_id?'device':values.default_shell_profile?'repo':'device'
  const profileValue=overrides.default_profile_id||values.default_shell_profile||''

  // Writing a value always clears the other layer: leaving a stale override behind
  // would silently win over the value the user just chose.
  const setBackend=(value:string,layer:Layer)=>{
    setOverrides(current=>({...current,default_backend:layer==='device'&&value?value as ProjectBackend:undefined}))
    setValues(current=>({...current,preferred_backend:layer==='repo'&&value?value as ProjectBackend:undefined}))
  }
  const setProfile=(value:string,layer:Layer)=>{
    setOverrides(current=>({...current,default_profile_id:layer==='device'&&value?value:undefined}))
    setValues(current=>({...current,default_shell_profile:layer==='repo'&&value?value:undefined}))
  }

  const detailsDirty=!!selected&&(name.trim()!==selected.name||(groupId||null)!==(selected.group_id||null))
  const overridesDirty=!!selected&&(
    (overrides.default_backend||null)!==(selected.default_backend||null)
    ||(overrides.default_profile_id||null)!==(selected.default_profile_id||null)
  )
  const portableDirty=!sameDraftValue(values,savedValues)
  const dirty=detailsDirty||overridesDirty||portableDirty

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
        })
      }
      if(config&&portableDirty){
        const payload:PortableValues={
          ...values,
          ...(values.ignore_patterns?{ignore_patterns:normalizeIgnorePatterns(values.ignore_patterns)}:{}),
        }
        const result=await api<ProjectConfig>('PUT','/api/project/config',{cwd:selected.root,project_id:selected.id,values:payload,revision:config.revision})
        setConfig(result);setValues(result.values);setSavedValues(result.values)
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
  const remove=async()=>{
    if(!selected)return
    if(!confirmDelete){setConfirmDelete(true);return}
    setBusy(true);setError('')
    try{await onDelete(selected);setSelectedId(ordered.find(project=>project.id!==selected.id)?.id||'')}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause));setConfirmDelete(false)}
    finally{setBusy(false)}
  }
  const liveCount=selected?sessions.filter(session=>session.project_id===selected.id&&!['exited','crashed'].includes(session.state)).length:0

  const layerRow=(active:Layer,set:(layer:Layer)=>void,shownWhen:boolean)=>shownWhen&&<div class="project-setting-layer">
    <button class={active==='device'?'active':''} disabled={busy} onClick={()=>set('device')}>this device</button>
    <button class={active==='repo'?'active':''} disabled={busy||!config} title={config?undefined:'The Project’s .swe-mux/config.toml is not readable.'} onClick={()=>set('repo')}>repo</button>
    <span class="project-setting-note">{active==='repo'?'stored in .swe-mux/config.toml, travels with the checkout':'stored in this machine’s database'}</span>
  </div>

  return <div class="modal-layer projects-manager-layer" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="projects-manager" role="dialog" aria-modal="true" aria-label="Manage projects">
      <header><div><span>PROJECTS::REGISTRY</span><h2>Projects</h2><small>Configured Projects keep their notes, files, settings, and history even when hidden from the sidebar.</small></div><div class="projects-manager-header-actions"><button onClick={onAddGroup}>New group</button><button data-tutorial="add-project" class="primary" onClick={onAdd}>+ Add project</button><button class="icon" aria-label="Close Projects" onClick={onClose}>×</button></div></header>
      <div class="projects-manager-body">
        <aside><div class="projects-manager-filter"><input aria-label="Search projects" placeholder="Search projects…" value={query} onInput={event=>setQuery(event.currentTarget.value)}/><select aria-label="Filter projects" value={filter} onChange={event=>setFilter(event.currentTarget.value as typeof filter)}><option value="all">All</option><option value="visible">In sidebar</option><option value="hidden">Hidden</option></select></div>
          <div data-tutorial="project-list" class="projects-manager-list">{shown.map(project=><button class={project.id===selected?.id?'active':''} onClick={()=>setSelectedId(project.id)}><span class={`project-visibility-dot ${isVisible(project)?'visible':'hidden'}`} aria-hidden="true"/><strong>{project.name}</strong><small>{project.root}</small><em>{isVisible(project)?'sidebar':'hidden'}</em></button>)}{!shown.length&&<p>No Projects match this view.</p>}</div>
        </aside>
        <main>{selected?<>
          <div class="projects-manager-title"><div><span>PROJECT::{selected.id.slice(0,8)}</span><h3>{selected.name}</h3><small>{liveCount} live session{liveCount===1?'':'s'} · {isVisible(selected)?'shown in sidebar':'configured, hidden from sidebar'}</small></div><button class={`sidebar-visibility-toggle ${isVisible(selected)?'active':''}`} disabled={busy} onClick={()=>void toggleVisible()}><span aria-hidden="true">{isVisible(selected)?'◉':'○'}</span>{isVisible(selected)?'Shown in sidebar':'Show in sidebar'}</button></div>
          <div class="projects-manager-actions"><button data-tutorial="open-project" class="primary" onClick={()=>onOpen(selected)}>Open workspace</button><button onClick={()=>onNotes(selected)}>Notes</button><button onClick={()=>onFiles(selected)}>Files</button></div>
          <div class="projects-manager-tabs" role="tablist" aria-label="Project record and settings">
            <button role="tab" aria-selected={tab==='details'} class={tab==='details'?'active':''} onClick={()=>setTab('details')}>Details</button>
            <button role="tab" aria-selected={tab==='settings'} class={tab==='settings'?'active':''} onClick={()=>setTab('settings')}>Settings</button>
          </div>
          <div class="projects-manager-panel">
            {tab==='details'&&<div class="projects-manager-form"><label>Name<input value={name} onInput={event=>setName(event.currentTarget.value)}/></label><label>Folder<input value={selected.root} readOnly/></label><label>Group<div><select value={groupId} onChange={event=>setGroupId(event.currentTarget.value)}><option value="">Ungrouped</option>{groups.map(group=><option value={group.id}>{group.name}</option>)}</select><button onClick={onAddGroup}>+</button></div></label></div>}
            {tab==='settings'&&<div class="projects-manager-form">
              <p>Blank inherits the global default. Each value is stored either on this device or in the Project's <code>.swe-mux/config.toml</code>; device wins where both are set.{config?` · .swe-mux/config.toml: ${config.status}${config.error?` · ${config.error}`:''}`:' · reading .swe-mux/config.toml…'}</p>
              <div class="project-setting">
                <label><span class="project-setting-name">Default backend{backendValue&&<em class="project-setting-chip">{backendLayer==='repo'?'repo':'device'}</em>}</span>
                  <select value={backendValue} disabled={busy} onChange={event=>setBackend(event.currentTarget.value,backendLayer)}><option value="">Inherit ({effective?.backend||'shell'})</option>{allBackendNames().map(backend=><option value={backend}>{backend}</option>)}</select>
                </label>
                {layerRow(backendLayer,layer=>setBackend(backendValue,layer),!!backendValue)}
              </div>
              <div class="project-setting">
                <label><span class="project-setting-name">Shell profile{profileValue&&<em class="project-setting-chip">{profileLayer==='repo'?'repo':'device'}</em>}</span>
                  <select value={profileValue} disabled={busy} onChange={event=>setProfile(event.currentTarget.value,profileLayer)}><option value="">Inherit ({effective?.profile_id||'default'})</option>{profiles.map(profile=><option value={profile.id}>{profile.label}</option>)}</select>
                </label>
                {layerRow(profileLayer,layer=>setProfile(profileValue,layer),!!profileValue)}
              </div>
              <label><span class="project-setting-name">Prompt library scope<em class="project-setting-chip">repo</em></span>
                <select value={values.prompt_library_scope||''} disabled={busy||!config} onChange={event=>setValues(current=>({...current,prompt_library_scope:(event.currentTarget.value||undefined) as PromptLibraryScope|undefined}))}><option value="">Inherit ({SCOPE_LABELS[effective?.prompt_library_scope||'both']})</option>{(Object.keys(SCOPE_LABELS) as PromptLibraryScope[]).map(scope=><option value={scope}>{SCOPE_LABELS[scope]}</option>)}</select>
              </label>
              <label class="check"><span class="project-setting-name">Allow device notification sounds<em class="project-setting-chip">repo</em></span><input type="checkbox" disabled={busy||!config} checked={values.notification_sounds_enabled!==false} onChange={event=>setValues(current=>({...current,notification_sounds_enabled:event.currentTarget.checked}))}/></label>
              <label><span class="project-setting-name">Additional ignore patterns<em class="project-setting-chip">repo</em></span>
                <textarea value={(values.ignore_patterns||[]).join('\n')} disabled={busy||!config} onInput={event=>setValues(current=>({...current,ignore_patterns:parseIgnorePatternDraft(event.currentTarget.value)}))}/>
              </label>
              <p>One glob per line, added to the global ignore list. A name such as <code>node_modules</code> matches that folder at any depth. These rules affect the file tree and resource watchers, not Git.</p>
              <section class="project-setting">
                <h4>Git and worktrees<em class="project-setting-chip">repo</em></h4>
                <label><span class="project-setting-name">Worktree setup command</span>
                  <input value={values.worktree?.setup_command||''} disabled={busy||!config} placeholder="Use executable .worktree-setup when blank" onInput={event=>setValues(current=>({...current,worktree:event.currentTarget.value?{...current.worktree,setup_command:event.currentTarget.value}:undefined}))}/>
                </label>
                <p>Runs only after Run creates a new worktree and before its session starts. Blank uses an executable <code>.worktree-setup</code> in the new checkout. The command is committed in <code>.swe-mux/config.toml</code>, so review changes like other repository code.</p>
              </section>
              <div><button disabled={busy||!config} onClick={()=>setValues({})}>Reset repo options to inherited</button></div>
              <AutomationOptIns project={selected} busy={busy} onError={setError} />
            </div>}
          </div>
          {error&&<p class="projects-manager-error" role="alert">{error}</p>}
          <footer><button class={confirmDelete?'danger confirming':'danger'} disabled={busy||liveCount>0} title={liveCount>0?'Stop this Project’s live sessions before deleting it.':undefined} onClick={()=>void remove()}>{confirmDelete?'Confirm delete':'Delete project'}</button><span>{liveCount>0?'Deletion is locked while sessions are live.':'Hiding is non-destructive; deletion may also be blocked by history.'}</span><button class="primary" disabled={!dirty||busy||!name.trim()} onClick={()=>void save()}>{busy?'Saving…':'Save changes'}</button></footer>
        </>:<div class="projects-manager-empty"><strong>No Projects configured</strong><p>Add a folder-backed Project to begin.</p><button data-tutorial="add-project" class="primary" onClick={onAdd}>+ Add project</button></div>}</main>
      </div>
    </section>
  </div>
}
