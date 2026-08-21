import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { ProjectContextEditor } from './ProjectContextEditor'
import { revealSetting } from './settingReveal'
import { SettingLink } from './SettingLink'
import { automationSetting } from './settingTargets'
import type { LlmReadiness } from './llmProvider'
import { normalizeIgnorePatterns, parseIgnorePatternDraft, sameDraftValue } from './settingsDraft'
import type { Project, ProjectBackend, ProjectGroup, PromptLibraryScope, Session, LaunchProfile } from './types'
import { allBackendNames, harnessDisplayName } from './harnessRegistry'
import { useDismissLevel } from './modalFocus'
import { ProjectPicker, type ProjectPickerOption } from './ProjectPicker'

// The Projects registry is the ONLY per-Project editor. Settings holds global
// options exclusively; anything scoped to one Project — its record and its
// defaults — is edited here, reached from this modal or from a Project's context
// menu. Two doors to two overlapping surfaces was the previous arrangement and it
// left users hunting for which modal owned which field. The record fields and the
// defaults share one scrolling settings surface — there is no Details/Settings
// split, because it is all settings for the same Project.

// A Project default can live in either of two layers, and users care about the
// difference: `device` is a database override private to this machine, `repo` is
// `.swe-mux/config.toml` and travels with the checkout. Precedence is
// device > repo > global, so a value is only ever written to one of them.
type Layer = 'device' | 'repo'

type PortableValues = {
  default_shell_profile?: string
  /** Backend name to launch profile id. A selection only; the profile itself is
   *  defined on the device, because argv for an agent CLI is an authority field. */
  default_agent_profiles?: Record<string, string>
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
  /** Whether switching it on can cost money. Straight from the registry, so the editor
   *  and every gate that offers the same switch say the same thing about it. */
  spends: boolean
  /** Whether it is inert without a proven model provider. Separate from `spends`,
   *  because a model on the operator's own machine is a dependency with no bill. */
  needs_llm?: boolean
}
type AutomationState = {
  revision: string
  requested: Record<string, boolean>
  enabled: string[]
  blocked: Record<string, string[]>
  /** Opted in, dependencies met, and held back by an unproven model provider. */
  unverified?: string[]
  llm?: LlmReadiness | null
  automations: AutomationEntry[]
  scan_timeline_auto_enable: boolean
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

  const write = async (next: Record<string, boolean>, autoEnable?: boolean) => {
    if (!state) return
    setSaving(true)
    try {
      setState(await api<AutomationState>('PUT', `/api/projects/${project.id}/automations`, {
        automations: next,
        scan_timeline_auto_enable: autoEnable ?? state.scan_timeline_auto_enable,
        revision: state.revision,
      }))
    } catch (cause) { onError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setSaving(false) }
  }

  if (!state) return <div class="project-automations"><h4>Automations</h4><p>Loading…</p></div>
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
  const unverified = new Set(state.unverified || [])
  const row = (item: AutomationEntry) => {
    const on = state.requested[item.id] === true
    const missing = state.blocked[item.id] || []
    const disabled = busy || saving || !item.implemented
    return <li key={item.id} class={item.implemented ? '' : 'unavailable'} data-setting={automationSetting(item.id)}>
      <label class="check">
        <span class="project-setting-name">{item.label}
          <em class="project-setting-chip">{item.kind}</em>
          {/* The one fact a reader most needs before ticking twenty checkboxes, and it
              lived only in a Python comment until now. Marked rather than unmarked,
              because most of this list is free and a chip on every row says nothing. */}
          {item.spends && <em class="project-setting-chip spends">spends</em>}
          {!item.implemented && <em class="project-setting-chip">not built yet</em>}
        </span>
        <input type="checkbox" disabled={disabled} checked={on}
          onChange={event => toggle(item.id, event.currentTarget.checked)} />
      </label>
      {item.requires.length > 0 && <p class="project-automation-deps">
        needs {item.requires.map(id => byId.get(id)?.label || id).join(' · ')}
        {missing.length > 0 && <strong> — {missing.length} still off</strong>}
      </p>}
      {/* The checkbox stays on and the automation does not run. Saying so here is the
          whole point: the permission is real and the thing it permits has no model to
          call, and a row that showed only a tick would be the silent downstream failure
          this gate exists to replace. The provider is a value rather than a switch, so
          this links rather than granting - see `settingTargets.ts`. */}
      {unverified.has(item.id) && <p class="project-automation-deps">
        <strong>On, and waiting on a model provider.</strong>{' '}
        {state.llm?.reason}{' '}
        <SettingLink target="accounts.llmProvider" variant="link">Choose or verify one</SettingLink>
      </p>}
    </li>
  }
  const substrate = state.automations.filter(item => item.kind === 'substrate')
  // Scan timeline gets its own block below, with the two settings that only
  // make sense next to its permission. Listing it twice would mean two
  // checkboxes for one thing.
  const consumers = state.automations.filter(item => item.kind === 'consumer' && item.id !== 'scan_timeline')
  const scanOn = state.requested.scan_timeline === true
  const scanBlocked = state.blocked.scan_timeline || []
  return <div class="project-automations">
    <h4 data-setting="automations">Automations<em class="project-setting-chip">repo</em></h4>
    <p>Per-project opt-in. Substrate records facts and never acts. Nothing here runs on a project that did not opt in. Spending limits are global, in <SettingLink target="automation.budgets" variant="link">Settings → Automation</SettingLink>.</p>
    <ul class="project-automation-list">{substrate.map(row)}</ul>
    <ul class="project-automation-list">{consumers.map(row)}</ul>

    <h4>Scan timeline<em class="project-setting-chip">repo</em></h4>
    <p>A readable behavioural history of each conversation in this Project. Permitting it here also enables the substrate it reads from. The Timeline drawer tab shows the result; everything that applies to the whole Project is set here.</p>
    <ul class="project-automation-list">
      <li data-setting={automationSetting('scan_timeline')}>
        <label class="check">
          <span class="project-setting-name">Permitted for this Project</span>
          <input type="checkbox" disabled={busy || saving} checked={scanOn}
            onChange={event => toggle('scan_timeline', event.currentTarget.checked)} />
        </label>
        {scanBlocked.length > 0 && <p class="project-automation-deps">
          needs {scanBlocked.join(' · ')} — enabled with it
        </p>}
        {/* Scan timeline has its own block above the consumer list, so it never passes
            through `row()` and would otherwise be the one model-backed switch that could
            sit on and inert with nothing said. */}
        {unverified.has('scan_timeline') && <p class="project-automation-deps">
          <strong>Permitted, and waiting on a model provider.</strong>{' '}
          {state.llm?.reason}{' '}
          <SettingLink target="accounts.llmProvider" variant="link">Choose or verify one</SettingLink>
        </p>}
      </li>
      <li class={scanOn ? '' : 'unavailable'} data-setting="scan_timeline_auto_enable">
        <label class="check">
          <span class="project-setting-name">Arm every new conversation</span>
          <input type="checkbox" disabled={busy || saving || !scanOn}
            checked={scanOn && state.scan_timeline_auto_enable}
            onChange={event => void write(state.requested, event.currentTarget.checked)} />
        </label>
        {/* The grant belongs to one provider conversation, so it resets on
            /clear, /new, a rollover and every new session. Without this you
            re-arm by hand every time. */}
        <p class="project-automation-deps">
          Off, each conversation starts unscanned and you arm it in the Timeline tab. On, a
          conversation with no decision yet arms itself on its first turn — one you switched
          off stays off.
        </p>
      </li>
    </ul>
    {scanOn && <ProjectContextEditor projectId={project.id} busy={busy || saving} onError={onError} />}
  </div>
}

/**
 * What an agent may do here without asking, and what it must draft for a human first.
 *
 * These four fields decide the *authority* behind four capabilities whose on/off is an
 * automation above. Every one of them shipped with no control in any overlay: they were
 * lines in a committed `.swe-mux/config.toml` and nothing else, which made the inert
 * default both impossible to discover and unreachable to change - one of them told the
 * agent to go and edit the file by hand (`agent_messaging.py`).
 *
 * They live here, beside the opt-ins they qualify, because this is the Project's editor
 * and only an editor may take a permission away. A gate elsewhere may raise one; nothing
 * but this can lower it.
 */
function AgentAuthority({ project, busy, onError }: {
  project: Project
  busy: boolean
  onError: (message: string) => void
}) {
  const [config, setConfig] = useState<ProjectConfig | null>(null)
  const [saving, setSaving] = useState(false)
  const read = useCallback(() => {
    api<ProjectConfig>('GET', `/api/project/config?cwd=${encodeURIComponent(project.root)}&project_id=${encodeURIComponent(project.id)}`)
      .then(setConfig)
      .catch(cause => onError(cause instanceof Error ? cause.message : String(cause)))
  }, [project.id, project.root])
  useEffect(() => { setConfig(null); read() }, [read])

  const write = async (field: string, value: string) => {
    if (!config) return
    setSaving(true)
    try {
      // Written straight through rather than into the panel's Save draft: an authority
      // change is a decision of its own, and staging it behind a button that also
      // renames the Project would leave the other rows describing a state the daemon
      // does not have yet.
      setConfig(await api<ProjectConfig>('PUT', '/api/project/config', {
        cwd: project.root,
        project_id: project.id,
        values: { ...config.values, [field]: value },
        revision: config.revision,
      }))
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : String(cause))
      read()
    } finally { setSaving(false) }
  }

  const rows: { field: string; setting: string; label: string; draft: string; granted: string; note: string }[] = [
    {
      field: 'session_control_grant', setting: 'session_control_grant',
      label: 'Interrupt and end sessions',
      draft: 'A human approves each one', granted: 'Acts directly',
      note: 'Needs the Agent session control opt-in above. Draft writes an inert request into the Fleet Queue.',
    },
    {
      field: 'spawn_grant', setting: 'spawn_grant',
      label: 'Start new sessions here',
      draft: 'A human approves each one', granted: 'Creates them directly',
      note: 'Also gated by Agent session control. Granted still bounds an agent to a per-origin budget.',
    },
    {
      field: 'land_grant', setting: 'land_grant',
      label: 'Land a branch onto the trunk',
      draft: 'A human approves each one', granted: 'Starts the pipeline directly',
      note: 'Needs the Land queue opt-in above. Either way the pipeline is fast-forward-only and runs only the verification command you approved.',
    },
    {
      field: 'interject_grant', setting: 'interject_grant',
      label: 'Write into a running turn',
      draft: 'Never (waits for the queue)', granted: 'May interject',
      note: 'Off by default. Granted still requires the receiving session to be interruptible and not opted out for its run.',
    },
  ]
  const inertFor = (field: string) => field === 'interject_grant' ? 'off' : 'draft'
  const value = (field: string) =>
    String((config?.values as Record<string, unknown> | undefined)?.[field] || inertFor(field))

  return <div class="project-automations project-authority">
    <h4 data-setting="agent_authority">Agent authority<em class="project-setting-chip">repo</em></h4>
    <p>The opt-ins above decide whether an agent may <em>ask</em>. These decide whether you
    still approve each time. Every one starts at the inert setting, and the whole table
    is meaningless until its automation is on.</p>
    {!config && <p>Loading…</p>}
    {config && <ul class="project-automation-list">
      {rows.map(row => <li key={row.field} data-setting={row.setting}>
        <label class="check">
          <span class="project-setting-name">{row.label}</span>
          <select disabled={busy || saving || config.status !== 'ready'}
            value={value(row.field)}
            onChange={event => void write(row.field, event.currentTarget.value)}>
            <option value={inertFor(row.field)}>{row.draft}</option>
            <option value="granted">{row.granted}</option>
          </select>
        </label>
        <p class="project-automation-deps">{row.note}</p>
      </li>)}
    </ul>}
  </div>
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
  const ordered=useMemo(()=>[...projects].sort((a,b)=>a.position-b.position||a.name.localeCompare(b.name)),[projects])
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
  const [config,setConfig]=useState<ProjectConfig|null>(null)
  const [values,setValues]=useState<PortableValues>({})
  const [savedValues,setSavedValues]=useState<PortableValues>({})
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [confirmRemove,setConfirmRemove]=useState(false)
  // A pending switch target parked while the current Project has unsaved edits, so
  // a fast pick (the mobile dropdown especially) cannot silently discard them.
  const [pendingSwitch,setPendingSwitch]=useState<string|null>(null)
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

  // The portable layer lives in the checkout, so it is read per Project rather than
  // taken from the registry payload — its revision is what guards the write.
  useEffect(()=>{
    if(!selected||selected.root_available===false){setConfig(null);setValues({});setSavedValues({});return}
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
    setValues(current=>{
      const next=layer==='repo'&&value
        ?{...(current.default_agent_profiles||{}),[backend]:value}
        :without(current.default_agent_profiles||{})
      return {...current,default_agent_profiles:Object.keys(next).length?next:undefined}
    })
  }

  const detailsDirty=!!selected&&(name.trim()!==selected.name||(groupId||null)!==(selected.group_id||null))
  const overridesDirty=!!selected&&(
    (overrides.default_backend||null)!==(selected.default_backend||null)
    ||(overrides.default_profile_id||null)!==(selected.default_profile_id||null)
    ||JSON.stringify(overrides.default_agent_profiles)!==JSON.stringify(selected.default_agent_profiles||{})
  )
  const portableDirty=!sameDraftValue(values,savedValues)
  const dirty=detailsDirty||overridesDirty||portableDirty

  // Switching Projects re-seeds every draft field, so an unsaved edit would be lost.
  // Guard the switch when dirty; a clean switch is immediate.
  const selectProject=(id:string)=>{
    if(id===selectedId)return
    if(dirty){setPendingSwitch(id);return}
    setSelectedId(id)
  }
  useEffect(()=>{setPendingSwitch(null)},[selectedId])
  const pickerOptions=useMemo<ProjectPickerOption[]>(()=>ordered.map(project=>({
    id:project.id,name:project.name,hint:project.root,visible:isVisible(project),
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
        <aside><div class="projects-manager-filter"><input aria-label="Search projects" placeholder="Search projects…" value={query} onInput={event=>setQuery(event.currentTarget.value)}/><select aria-label="Filter projects" value={filter} onChange={event=>setFilter(event.currentTarget.value as typeof filter)}><option value="all">All</option><option value="visible">In sidebar</option><option value="hidden">Hidden</option></select></div>
          <div data-tutorial="project-list" class="projects-manager-list">{shown.map(project=><button class={project.id===selected?.id?'active':''} onClick={()=>selectProject(project.id)}><span class={`project-visibility-dot ${isVisible(project)?'visible':'hidden'}`} aria-hidden="true"/><strong>{project.name}</strong><small>{project.root}</small><em>{isVisible(project)?'sidebar':'hidden'}</em></button>)}{!shown.length&&<p>No Projects match this view.</p>}</div>
        </aside>
        <main>{selected?<>
          <div class="projects-manager-picker"><ProjectPicker id="projects-manager-picker" value={selected.id} options={pickerOptions} placeholder="Select a project…" onChange={selectProject}/></div>
          {pendingSwitch&&<div class="projects-manager-switch-confirm" role="alertdialog" aria-label="Unsaved changes"><span>Discard unsaved changes to <strong>{selected.name}</strong>?</span><div><button onClick={()=>setPendingSwitch(null)}>Keep editing</button><button class="danger" onClick={()=>{const target=pendingSwitch;setPendingSwitch(null);setSelectedId(target)}}>Discard and switch</button></div></div>}
          <div class="projects-manager-title"><span>PROJECT</span><h3>{selected.name}</h3><div class="projects-manager-title-meta"><small>{liveCount} live session{liveCount===1?'':'s'} · {selected.history_count||0} conversation{selected.history_count===1?'':'s'}{selected.root_available===false?' · folder missing':''}</small><div class="projects-manager-title-actions"><button data-tutorial="open-project" class="primary" disabled={selected.root_available===false} onClick={()=>onOpen(selected)}>Open workspace</button><button class={`sidebar-visibility-toggle ${isVisible(selected)?'active':''}`} disabled={busy} onClick={()=>void toggleVisible()}><span aria-hidden="true">{isVisible(selected)?'◉':'○'}</span>{isVisible(selected)?'Shown in sidebar':'Show in sidebar'}</button></div></div></div>
          <div class="projects-manager-panel">
            <div class="projects-manager-form">
              <h4 class="projects-manager-section">Identity</h4>
              <label>Name<input value={name} onInput={event=>setName(event.currentTarget.value)}/></label>
              <label>Folder<input class={selected.root_available===false?'missing':''} value={selected.root} readOnly/>{selected.root_available===false&&<span class="project-folder-missing">Folder missing. swe-mux will not recreate it.</span>}</label>
              <label>Group<div><select value={groupId} onChange={event=>setGroupId(event.currentTarget.value)}><option value="">Ungrouped</option>{groups.map(group=><option value={group.id}>{group.name}</option>)}</select><button onClick={onAddGroup}>+</button></div></label>
              <h4 class="projects-manager-section">Defaults</h4>
              <p>Blank inherits the global default. Each value is stored either on this device or in the Project's <code>.swe-mux/config.toml</code>; device wins where both are set.{config?` · .swe-mux/config.toml: ${config.status}${config.error?` · ${config.error}`:''}`:' · reading .swe-mux/config.toml…'}</p>
              <div class="project-setting">
                <label><span class="project-setting-name">Default backend{backendValue&&<em class="project-setting-chip">{backendLayer==='repo'?'repo':'device'}</em>}</span>
                  <select value={backendValue} disabled={busy} onChange={event=>setBackend(event.currentTarget.value,backendLayer)}><option value="">Inherit ({effective?.backend||'shell'})</option>{allBackendNames().map(backend=><option value={backend}>{backend}</option>)}</select>
                </label>
                {layerRow(backendLayer,layer=>setBackend(backendValue,layer),!!backendValue)}
              </div>
              <div class="project-setting">
                <label><span class="project-setting-name">Shell profile{profileValue&&<em class="project-setting-chip">{profileLayer==='repo'?'repo':'device'}</em>}</span>
                  <select value={profileValue} disabled={busy} onChange={event=>setProfile(event.currentTarget.value,profileLayer)}><option value="">Inherit ({effective?.profile_id||'default'})</option>{profiles.filter(profile=>profile.backend==='shell').map(profile=><option value={profile.id}>{profile.label}</option>)}</select>
                </label>
                {layerRow(profileLayer,layer=>setProfile(profileValue,layer),!!profileValue)}
              </div>
              {harnessesWithProfiles.map(([backend,harnessProfiles])=><div class="project-setting" key={backend}>
                <label><span class="project-setting-name">{harnessDisplayName(backend)} launch profile{agentProfileValue(backend)&&<em class="project-setting-chip">{agentProfileLayer(backend)==='repo'?'repo':'device'}</em>}</span>
                  <select value={agentProfileValue(backend)} disabled={busy} onChange={event=>setAgentProfile(backend,event.currentTarget.value,agentProfileLayer(backend))}><option value="">None (plain launch)</option>{harnessProfiles.map(profile=><option value={profile.id}>{profile.label}</option>)}</select>
                </label>
                {layerRow(agentProfileLayer(backend),layer=>setAgentProfile(backend,agentProfileValue(backend),layer),!!agentProfileValue(backend))}
              </div>)}
              <h4 class="projects-manager-section">Repository options</h4>
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
              <AgentAuthority project={selected} busy={busy} onError={setError} />
            </div>
          </div>
          {confirmRemove&&<section class="project-removal-summary" aria-label="Remove Project confirmation">
            <h4>Remove {selected.name} from swe-mux?</h4>
            <dl><div><dt>Folder</dt><dd>{selected.root_available===false?'Missing':'Available'} · {selected.root}</dd></div><div><dt>Live sessions</dt><dd>{liveCount}</dd></div><div><dt>Conversation history</dt><dd>{selected.history_count||0} preserved</dd></div></dl>
            <p>The Project leaves the sidebar and registry. Its folder and <code>.swe-mux</code> contents are not changed. Re-adding this folder restores the same Project identity, history, settings, and layout.</p>
            <div><button onClick={()=>setConfirmRemove(false)}>Cancel</button><button class="danger" disabled={busy} onClick={()=>void remove(liveCount>0)}>{liveCount>0?`Close ${liveCount} session${liveCount===1?'':'s'} and remove`:'Remove from swe-mux'}</button></div>
          </section>}
          {error&&<p class="projects-manager-error" role="alert">{error}</p>}
          <footer><button class="danger" disabled={busy} onClick={()=>setConfirmRemove(true)}>Remove from swe-mux…</button><span>History is preserved. The folder is never deleted.</span><button class="primary" disabled={!dirty||busy||!name.trim()} onClick={()=>void save()}>{busy?'Saving…':'Save changes'}</button></footer>
        </>:<div class="projects-manager-empty"><strong>No Projects configured</strong><p>Add a folder-backed Project to begin.</p><button data-tutorial="add-project" class="primary" onClick={onAdd}>+ Add project</button></div>}</main>
      </div>
    </section>
  </div>
}
