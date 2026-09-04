import type { ComponentChildren } from 'preact'
import { useMemo, useState } from 'preact/hooks'
import { api } from './api'
import { AutomationAuthority } from './AutomationAuthority'
import type { AuthorityFieldSpec } from './AutomationAuthority'
import { Dropdown } from './Dropdown'
import { SettingLink } from './SettingLink'
import { automationSetting } from './settingTargets'
import { automationRequested, forgetProjectAutomations, inheritedDefault } from './projectAutomations'
import type { AutomationRegistryEntry } from './projectAutomations'
import { projectDropdownOptions } from './projectOptions'
import { ProjectContextEditor } from './ProjectContextEditor'
import type { StartingSetCatalog } from './projectCreate'

// The policy matrix: every automation is one row, the install-wide answer and
// the selected Project's own answer side by side, with a fleet column saying how
// many Projects run it. This is THE editor for both scopes - the one surface
// that may turn an automation off - so the additive-only grant rule elsewhere
// stays sound.
//
// The Global cell holds *two* controls, exactly as the authority rows below it
// do, because the install has two different things to say about an automation:
//
// - the **default** checkbox, what a Project that never wrote this id down
//   inherits (`automation_project_defaults`). It only ever reaches undecided
//   Projects, so ticking it can never contradict a Project that decided.
// - the **off everywhere** lock, the ceiling (`automation_global_allow`, or a
//   dedicated install switch for three rows). It reaches every Project whatever
//   its file says, cascades over dependents, and only ever subtracts.
//
// They were one checkbox until 2026-08-31, and the missing half is why a new
// Project inherited nothing: the only thing an operator could say install-wide
// was "no", so every "yes" had to be repeated per Project at creation time and
// could never be revised in one place. The `reach` line under the pair renders
// what the default currently decides ("12 inherit · 3 custom"), so a fleet-wide
// change is visible before the click rather than after.
//
// Cascade greying is drawn from the same resolution the daemon enforces:
// `globally_allowed` on each registry entry is the resolved ceiling (closure
// included), and a Project cell under a blocked ceiling is disabled rather
// than rendered as merely off - the Project's own choice is retained on disk.
//
// The Project cell is a three-position dropdown rather than a checkbox for the
// same reason the authority rows' is: "follow global" and "explicitly off" are
// different states, and collapsing them makes the install default unreachable
// once anything has touched the Project. Follow global *removes* the key.

export type MatrixProject={
  project_id:string;project_name:string;status:string;revision:string
  requested:Record<string,boolean>;enabled:string[];blocked:Record<string,string[]>
  unverified?:string[];globally_disabled?:string[]
  llm?:{ready:boolean;reason:string}|null
  /** The effective answer, install default layered under the Project's own. */
  scan_timeline_auto_enable:boolean
  /** What this Project's file says, null where it left the field alone - the
   *  same two readings the authority pair carries, and for the same reason. */
  scan_timeline_auto_enable_own?:boolean|null
  /** The titler's refinement count, the same two readings: effective, and the
   *  Project's own (null where it inherits). */
  title_refinements?:number
  title_refinements_own?:number|null
  // What this Project's own file says (null = unset, which is what lets the
  // Project cell offer "Follow global" as a real third position) and what the
  // daemon resolves after the install default and ceiling are layered on.
  authority:Record<string,string|null>
  authority_effective:Record<string,string>
}
export type MatrixData={
  automations:AutomationRegistryEntry[]
  projects:MatrixProject[]
  global_allow:Record<string,boolean>
  /** The install's inherited default template, as stored. Only the ids the
   *  operator named: the resolved answer per row is the registry entry's
   *  `install_default`, and writing this map back wholesale is what keeps an
   *  untouched row untouched. */
  project_defaults?:Record<string,boolean>
  scan_timeline_auto_enable_default?:boolean
  title_refinements_default?:number
  title_refinements_max?:number
  install_switches:{automation_enabled:boolean;scan_timeline_enabled:boolean;scheduled_runs_enabled:boolean;land_queue_enabled:boolean}
  authority_fields:AuthorityFieldSpec[]
  authority_default:Record<string,string>
  authority_ceiling:Record<string,string|null>
}

const PRESETS_SEEN_KEY='mux.automationPresetsSeen'
const presetsSeen=():boolean=>{try{return !!localStorage.getItem(PRESETS_SEEN_KEY)}catch{return true}}
const markPresetsSeen=():void=>{try{localStorage.setItem(PRESETS_SEEN_KEY,'1')}catch{/* private mode */}}

const PRESET_COPY:Record<string,{name:string;description:string;cost:string}>={
  recommended:{name:'Free basics',description:'Model-free health checks over what agents actually did: stuck loops, unverified claims, change provenance, doc debt, code structure.',cost:'free - never calls a model'},
  // "re-titles", not "titles": naming a pane is the built-in Session titler, an
  // install-wide switch under Automation that runs whatever a Project opted into.
  // Two features whose descriptions both said "titles" made this preset read as
  // the one that turns titling on, so declining it looked like declining titles.
  llm:{name:'AI timeline',description:'A model keeps a readable play-by-play of every session: the scan timeline armed per run, sessions re-titled when their scope changes, attention narration.',cost:'costs money - bounded by the global budget'},
  autonomy:{name:'Agent autonomy',description:'Agents may interrupt or end sessions and land finished branches through the queue; whatever still drafts gets its review surface.',cost:'free - each act is bounded and logged'},
}

export function AutomationPolicyMatrix({data,projectId,onSelectProject,catalog,llmReady,onError,onChanged}:{
  data:MatrixData
  /** The selected Project follows the dashboard's own selection. */
  projectId:string
  onSelectProject:(id:string)=>void
  /** `project_starting_sets` from GET /api/grants, so the preset cards offer
   *  exactly what the daemon will accept. Absent while that read is in flight. */
  catalog?:StartingSetCatalog|null
  llmReady?:boolean
  onError:(message:string)=>void
  /** Re-fetch everything this matrix draws. Called after every write, because
   *  a toggle's consequences (the cascade, the fleet counts) are the daemon's
   *  resolution to compute, not this component's to predict. */
  onChanged:()=>Promise<void>|void
}){
  const [saving,setSaving]=useState(false)
  // Which rows have their description open. Collapsed by default and held here
  // rather than persisted: the matrix is a grid of ~30 switches, and it is only
  // readable as a grid while every row is one line. Expanding is a question about
  // one row ("what does this actually do"), not a mode to come back to.
  const [openRows,setOpenRows]=useState<Set<string>>(new Set())
  const toggleRow=(id:string)=>setOpenRows(current=>{
    const next=new Set(current)
    if(next.has(id))next.delete(id)
    else next.add(id)
    return next
  })
  /** A row's name cell: the label and its chips, as the button that expands the
   *  description under it. The name is the click target rather than the whole
   *  row, because the row's other three cells are the controls themselves. */
  const nameCell=(id:string,label:ComponentChildren,description:string)=>{
    const open=openRows.has(id)
    return <>
      <button type="button" class="project-setting-name"
        aria-expanded={open} aria-controls={`automation-about-${id}`}
        onClick={()=>toggleRow(id)}>
        <span class="project-setting-mark" aria-hidden="true">{open?'▾':'▸'}</span>
        {label}
      </button>
      {open&&<p class="project-automation-deps" id={`automation-about-${id}`}>{description}</p>}
    </>
  }
  const [presetsOpen,setPresetsOpen]=useState(()=>!presetsSeen())
  const byId=useMemo(()=>new Map(data.automations.map(item=>[item.id,item])),[data.automations])
  const project=data.projects.find(item=>item.project_id===projectId)||data.projects[0]

  // The transitive dependency closure, walked client-side for the write path
  // only; every *reading* (greying, fleet counts) comes from the daemon's own
  // resolution rather than being recomputed here.
  const closure=(id:string,seen=new Set<string>()):Set<string>=>{
    if(seen.has(id))return seen
    seen.add(id)
    for(const dependency of byId.get(id)?.requires||[])closure(dependency,seen)
    return seen
  }
  const requestedOn=(item:AutomationRegistryEntry):boolean=>
    project?automationRequested(project.requested,item):false
  /** What this Project's own file says about a row: on, off, or nothing at all. */
  const ownState=(item:AutomationRegistryEntry):'inherit'|'on'|'off'=>{
    const own=project?.requested[item.id]
    return own===undefined?'inherit':own?'on':'off'
  }
  const defaults=data.project_defaults||{}
  const defaultOn=(item:AutomationRegistryEntry):boolean=>inheritedDefault(item)

  const patchConfig=async(changes:Record<string,unknown>)=>{
    setSaving(true)
    try{
      // Fresh revision per flip: the matrix has no Save transaction, and a
      // stale pinned revision would refuse the second toggle of a pair.
      const current=await api<{revision:number}>('GET','/api/config')
      await api('PATCH','/api/config',{_revision:current.revision,...changes})
      await onChanged()
    }catch(cause){onError(cause instanceof Error?cause.message:String(cause))}
    finally{setSaving(false)}
  }
  // The ceiling half of the Global cell: "not anywhere". Three rows write their
  // dedicated install switch and every other row an `automation_global_allow`
  // entry - one switch, one key, decided by the registry payload rather than by
  // this file remembering which is which.
  const toggleCeiling=(item:AutomationRegistryEntry)=>{
    if(item.install_switch){
      const key=item.install_switch as keyof MatrixData['install_switches']
      void patchConfig({[item.install_switch]:!data.install_switches[key]})
      return
    }
    const next={...data.global_allow}
    if(next[item.id]===false)delete next[item.id] // absent means allowed
    else next[item.id]=false
    void patchConfig({automation_global_allow:next})
  }
  // The default half: what an undecided Project inherits. Cascades exactly like
  // the Project cell does, and for the same reason - a default naming a consumer
  // without its substrate would resolve to blocked, and a default left on a
  // substrate whose readers were just withdrawn is a switch that appears to do
  // nothing. The daemon completes the closure too; doing it here as well is what
  // makes the stored map say what it means.
  const toggleDefault=(item:AutomationRegistryEntry)=>{
    const next={...defaults}
    const on=defaultOn(item)
    const clear=(id:string)=>{
      // An id the registry itself defaults on needs an explicit false to be
      // withdrawn; anything else is withdrawn by having no entry at all.
      if(byId.get(id)?.default_on)next[id]=false
      else delete next[id]
    }
    if(on){
      clear(item.id)
      for(const other of data.automations)
        if(other.id!==item.id&&closure(other.id,new Set()).has(item.id)&&defaultOn(other))clear(other.id)
    }else{
      for(const id of closure(item.id))next[id]=true
    }
    void patchConfig({automation_project_defaults:next})
  }
  const writeProject=async(automations:Record<string,boolean>)=>{
    if(!project)return
    setSaving(true)
    try{
      await api('PUT',`/api/projects/${project.project_id}/automations`,{
        automations,
        // The Project's *own* answer, null included: echoing the effective one
        // would pin the inherited value into the file on any unrelated edit.
        scan_timeline_auto_enable:project.scan_timeline_auto_enable_own??null,
        title_refinements:project.title_refinements_own??null,
        revision:project.revision,
      })
      forgetProjectAutomations(project.project_id)
      await onChanged()
    }catch(cause){onError(cause instanceof Error?cause.message:String(cause))}
    finally{setSaving(false)}
  }
  // Authority rides the same per-Project write as the opt-ins, because the
  // Project's file is one revision and a second endpoint would race this one.
  // The opt-in table goes along unchanged: the daemon replaces it wholesale,
  // so sending the current one is how "change only the authority" is spelled.
  const writeAuthority=async(authority:Record<string,string|null>)=>{
    if(!project)return
    setSaving(true)
    try{
      await api('PUT',`/api/projects/${project.project_id}/automations`,{
        automations:project.requested,
        scan_timeline_auto_enable:project.scan_timeline_auto_enable_own??null,
        title_refinements:project.title_refinements_own??null,
        authority,
        revision:project.revision,
      })
      forgetProjectAutomations(project.project_id)
      await onChanged()
    }catch(cause){onError(cause instanceof Error?cause.message:String(cause))}
    finally{setSaving(false)}
  }
  /** Move one row to one of its three positions in this Project.
   *
   *  `inherit` deletes the key, which is the only spelling of "follow global"
   *  the file has; `off` writes an explicit false, which the daemon now persists
   *  for every id rather than only for the default-on ones, because absence has
   *  stopped meaning off and started meaning inherit. */
  const setProjectState=(item:AutomationRegistryEntry,state:'inherit'|'on'|'off')=>{
    if(!project)return
    const next={...project.requested}
    if(state==='inherit')delete next[item.id]
    else if(state==='on'){
      // Enabling a consumer enables its whole transitive closure: the
      // alternative is a toggle that appears on and silently does nothing.
      for(const id of closure(item.id))next[id]=true
    }else{
      next[item.id]=false
      // Turning substrate off turns off everything that reads from it, rather
      // than leaving dependents enabled-but-inert. A dependent that was only
      // inheriting is left inheriting: it is already off through this one, and
      // pinning it would outlive the choice that caused it.
      for(const other of data.automations)
        if(other.id!==item.id&&closure(other.id,new Set()).has(item.id)&&project.requested[other.id])
          next[other.id]=false
    }
    void writeProject(next)
  }
  /** Write one of the two Project fields that qualify an opt-in rather than
   *  being one, carrying the other one's *own* answer so a write to either
   *  never pins the inherited value of the other. */
  const writeQualifier=(fields:{scan_timeline_auto_enable?:boolean|null;title_refinements?:number|null})=>{
    if(!project)return
    setSaving(true)
    void (async()=>{
      try{
        await api('PUT',`/api/projects/${project.project_id}/automations`,{
          automations:project.requested,
          scan_timeline_auto_enable:project.scan_timeline_auto_enable_own??null,
          title_refinements:project.title_refinements_own??null,
          ...fields,
          revision:project.revision,
        })
        forgetProjectAutomations(project.project_id)
        await onChanged()
      }catch(cause){onError(cause instanceof Error?cause.message:String(cause))}
      finally{setSaving(false)}
    })()
  }
  const setAutoArm=(value:boolean|null)=>writeQualifier({scan_timeline_auto_enable:value})
  const setTitleRefinements=(value:number|null)=>writeQualifier({title_refinements:value})
  const refinementsMax=data.title_refinements_max??5
  const refinementsDefault=data.title_refinements_default??2
  const refinementOptions=Array.from({length:refinementsMax+1},(_,count)=>({value:String(count),label:count===0?'0 · name once':String(count)}))

  const fleetCount=(id:string)=>data.projects.filter(row=>row.enabled.includes(id)).length
  const unverified=new Set(project?.unverified||[])
  const globallyDisabled=new Set(project?.globally_disabled||[])
  const globallyAllowed=(item:AutomationRegistryEntry)=>item.globally_allowed!==false
  // The Global cell itself greys only when something *upstream* is blocked -
  // its own off state is what the switch is for.
  const upstreamBlocked=(item:AutomationRegistryEntry)=>
    item.requires.some(id=>byId.get(id)?.globally_allowed===false)

  // Grouped the way the dependencies actually flow, same grouping the old
  // editor drew: the structure IS the "needs X" story, so rows carry no
  // per-row dependency prose.
  // Grouped by what a row is *about* - the registry's `family` - never by what it
  // depends on: dependency is already drawn per row by the depth indentation and
  // the greying cascade, and grouping by it a second time put the session titler
  // two groups away from the re-titler. Rows keep the daemon's order (registry
  // order) inside a family, so placement is the registry's decision and not the
  // id's spelling. The family table itself (`automation_registry.FAMILIES`) is
  // the daemon's too; this list only fixes the drawing order and the hints, and a
  // family the daemon sends that is not named here is drawn last rather than lost.
  const groups:[string,string,AutomationRegistryEntry[]][]=useMemo(()=>{
    const known:[string,string,string][]=[
      ['facts','Foundations','record facts, never act'],
      ['checks','Deterministic checks','model-free reads over the recorded facts'],
      ['titling','Titling','name a pane, then keep the name honest'],
      ['attention','Attention','what needs you, and why'],
      ['timeline','Reads the timeline','distillations of the scan timeline'],
      ['capabilities','Capabilities','what agents and schedules may do'],
    ]
    const named=new Set(known.map(([family])=>family))
    const rows=known.map(([family,title,hint]):[string,string,AutomationRegistryEntry[]]=>
      [title,hint,data.automations.filter(item=>(item.family||'capabilities')===family)])
    const extra=data.automations.filter(item=>item.family&&!named.has(item.family))
    return extra.length?[...rows,['Other','',extra]]:rows
  },[data.automations])

  const depth=(item:AutomationRegistryEntry):number=>{
    let level=0
    for(const id of item.requires){
      const parent=byId.get(id)
      if(parent)level=Math.max(level,depth(parent)+1)
    }
    return level
  }

  const presetState=(setName:keyof StartingSetCatalog):'on'|'partial'|'off'=>{
    const set=catalog?.[setName]
    if(!set||!project)return 'off'
    const on=set.automations.filter(id=>requestedOn(byId.get(id)||({id,requires:[]} as never))).length
    if(on===set.automations.length)return 'on'
    return on>0?'partial':'off'
  }
  const presetBlocked=(setName:keyof StartingSetCatalog):boolean=>
    !!catalog?.[setName]?.automations.some(id=>byId.get(id)?.globally_allowed===false)
  const applyPreset=async(setName:keyof StartingSetCatalog)=>{
    const set=catalog?.[setName]
    if(!set||!project)return
    setSaving(true)
    try{
      if(presetState(setName)==='on'){
        // Withdrawal is this editor's alone, so "turn off" clears exactly the
        // ids the preset named and leaves the substrate under them - substrate
        // records and never acts, and another consumer may still read it.
        //
        // An id the install defaults on is written explicitly false rather than
        // deleted: deleting it would hand it straight back through inheritance,
        // which is a "turn off" button that does not.
        const next={...project.requested}
        for(const id of set.automations){
          const entry=byId.get(id)
          if(entry&&inheritedDefault(entry))next[id]=false
          else delete next[id]
        }
        await writeProject(next)
      }else{
        // On goes through the ordinary grant: the daemon computes the closure,
        // writes the file once, and leaves one audit record.
        await api('POST','/api/grants',{project_id:project.project_id,automations:set.automations,values:set.values})
        forgetProjectAutomations(project.project_id)
        await onChanged()
      }
      markPresetsSeen()
    }catch(cause){onError(cause instanceof Error?cause.message:String(cause))}
    finally{setSaving(false)}
  }

  // How many Projects the Default checkbox actually decides, rendered before the
  // click rather than discovered after it. A locked row reaches every Project;
  // an unlocked one reaches only those that never wrote the id down.
  const pinned=(id:string):number=>data.projects.filter(row=>row.requested[id]!==undefined).length

  const row=(item:AutomationRegistryEntry)=>{
    const ceilingOff=!globallyAllowed(item)
    const busy=saving||!item.implemented
    const own=ownState(item)
    const inherited=defaultOn(item)
    const ceilingLocked=item.install_switch
      ?!data.install_switches[item.install_switch as keyof MatrixData['install_switches']]
      :data.global_allow[item.id]===false
    const custom=pinned(item.id)
    const reach=ceilingLocked
      ?`${data.projects.length} off`
      :`${data.projects.length-custom} inherit${custom?` · ${custom} custom`:''}`
    return <div class={`automation-matrix-row${ceilingOff?' globally-off':''}`} key={item.id} role="row">
      <div class="automation-matrix-name" style={`--depth:${depth(item)}`}>
        {nameCell(item.id,<>
          <b>{item.label}</b>
          {item.spends&&<em class="project-setting-chip spends">spends</em>}
          {item.default_on&&<em class="project-setting-chip">on by default</em>}
          {!item.implemented&&<em class="project-setting-chip">not built yet</em>}
        </>,item.description||'')}
        {/* The permission is real and the thing it permits has no model to call;
            a row that showed only a tick would be a silent downstream failure.
            The daemon's sentence is rendered verbatim - it distinguishes
            never-verified from edited-since-verified, and a paraphrase would
            collapse the two. */}
        {unverified.has(item.id)&&<p class="project-automation-deps">
          <strong>On, and waiting on a model provider.</strong>{' '}
          {project?.llm?.reason}{' '}
          <SettingLink target="accounts.llmProvider" variant="link">Choose or verify one</SettingLink>
        </p>}
      </div>
      <div class="automation-matrix-cell automation-authority-global">
        <label class="check" title="What a Project that has not decided inherits">
          <input type="checkbox" disabled={busy||ceilingOff||upstreamBlocked(item)} checked={inherited&&!ceilingOff}
            onChange={()=>toggleDefault(item)}/>
          <span>default</span>
        </label>
        <div class="automation-authority-meta">
          <label class="check" data-setting={item.install_switch||undefined} title="Off in every Project, whatever its file says">
            <input type="checkbox" disabled={busy||upstreamBlocked(item)} checked={ceilingLocked}
              onChange={()=>toggleCeiling(item)}/>
            <span>off everywhere</span>
          </label>
          <small>{reach}</small>
        </div>
      </div>
      <div class="automation-matrix-cell" data-setting={automationSetting(item.id)}>
        <Dropdown ariaLabel={`${item.label} in this Project`} value={own==='inherit'?'':own}
          disabled={busy||ceilingOff||!project||project.status==='read-only'}
          options={[
            {value:'',label:`Follow global (${inherited?'on':'off'})`},
            {value:'on',label:'On'},
            {value:'off',label:'Off'},
          ]}
          onChange={value=>setProjectState(item,(value||'inherit') as 'inherit'|'on'|'off')}/>
      </div>
      <span class="automation-matrix-fleet"><b>{fleetCount(item.id)}</b>/{data.projects.length}</span>
    </div>
  }

  return <div class="automation-matrix-editor">
    <div class="automation-masterbar">
      <label class="check automation-master-switch" data-setting="automation_enabled">
        <input type="checkbox" disabled={saving} checked={data.install_switches.automation_enabled}
          onChange={()=>void patchConfig({automation_enabled:!data.install_switches.automation_enabled})}/>
        <strong>Global Automation</strong>
      </label>
      <button class="automation-preset-toggle" onClick={()=>setPresetsOpen(open=>!open)}>{presetsOpen?'Choose preset ▴':'Choose preset ▾'}</button>
      <div class="automation-project-toolbar">
        <label>Project<Dropdown value={project?.project_id||''} onChange={onSelectProject} filter filterPlaceholder="Filter Projects…" options={projectDropdownOptions(data.projects,item=>({value:item.project_id,label:item.project_name}))}/></label>
      </div>
    </div>
    {presetsOpen&&<section class="usage-table automation-presets">
      <h3>{presetsSeen()?`Presets for ${project?.project_name||'this Project'}`:`Welcome - pick a starting point for ${project?.project_name||'this Project'}`}</h3>
      <p>Each bundle is one decision, dependencies included; any single switch below can be changed afterwards.</p>
      <div class="automation-preset-cards">
        {(['recommended','llm','autonomy'] as const).map(setName=>{
          const copy=PRESET_COPY[setName]
          const state=presetState(setName)
          const blocked=presetBlocked(setName)
          return <article class={`automation-preset ${state}`} key={setName}>
            <header><h4>{copy.name}</h4><span class="automation-pill">{state==='on'?'on':state==='partial'?'partial':'off'}</span></header>
            <p>{copy.description}</p>
            <small>{copy.cost}{setName==='llm'&&llmReady===false?' · no verified model provider yet':''}</small>
            {blocked&&<small class="automation-preset-blocked">Blocked: part of this set is disabled install-wide.</small>}
            <button disabled={saving||!catalog||!project||blocked} onClick={()=>void applyPreset(setName)}>{state==='on'?'Turn off':'Turn on'}</button>
          </article>
        })}
      </div>
      <button class="automation-preset-dismiss" onClick={()=>{markPresetsSeen();setPresetsOpen(false)}}>{presetsSeen()?'close':"skip, I'll use the switches below"}</button>
    </section>}
    <p class="automation-matrix-legend">
      <b>default</b> = what a Project that has not decided inherits · <b>off everywhere</b> = the ceiling, whatever a Project's file says ·
      <b>{project?.project_name||'Project'}</b> = this Project's own answer, or <i>Follow global</i> to keep inheriting ·
      a greyed Project control is blocked by the ceiling and keeps the Project's own choice ·
      <em class="project-setting-chip spends">spends</em> = uses a paid model
    </p>
    <div class="automation-matrix-grid" role="table" aria-label="Automation policy matrix" data-setting="automation_global_allow">
      {/* The Default column has no single control to mark - it is one checkbox per
          row - so the column header is its anchor, which is also the right place
          for a deep link to land: the whole column is the answer to "where do new
          Projects get this from". */}
      <div class="automation-matrix-head" role="row" data-setting="automations"><span>automation</span><span data-setting="automation_project_defaults">global</span><span>{project?.project_name||'project'}</span><span>projects on</span></div>
      {groups.map(([title,hint,items])=>items.length?<div key={title}>
        <h5 class="project-automation-group">{title}<span>{hint}</span></h5>
        {items.map(item=>[
          row(item),
          // The titler's refinement count, drawn directly under the titler it
          // qualifies - the same shape as the arming rule under the timeline:
          // an install default and a Project answer that may follow it.
          item.id==='session_titler'&&project?<div class={`automation-matrix-row${requestedOn(item)?'':' globally-off'}`} key="title_refinements">
            {/* The deep-link mark sits on the name cell rather than on the label
                itself, so a reveal scrolls to and flashes the whole row name. */}
            <div class="automation-matrix-name" style="--depth:1" data-setting="title_refinements">
              {nameCell('title_refinements','Title refinements','How many times a provisional title may be revised after the first, while the work is still taking shape. 0 names a pane once and never revises it.')}
            </div>
            <div class="automation-matrix-cell automation-authority-global">
              <label class="check" data-setting="title_refinements_default" title="What a Project that has not decided inherits">
                <Dropdown ariaLabel="Default title refinements" value={String(refinementsDefault)} disabled={saving}
                  options={refinementOptions}
                  onChange={value=>void patchConfig({title_refinements_default:Number(value)})}/>
                <span>default</span>
              </label>
              <div class="automation-authority-meta">
                <small>{data.projects.filter(row=>row.title_refinements_own==null).length} inherit</small>
              </div>
            </div>
            <div class="automation-matrix-cell">
              <Dropdown ariaLabel="Title refinements in this Project"
                value={project.title_refinements_own==null?'':String(project.title_refinements_own)}
                disabled={saving||!requestedOn(item)||globallyDisabled.has('session_titler')}
                options={[{value:'',label:`Follow global (${refinementsDefault})`},...refinementOptions]}
                onChange={value=>setTitleRefinements(value===''?null:Number(value))}/>
            </div>
            <span class="automation-matrix-fleet">{data.projects.filter(row=>row.title_refinements_own!=null).length} custom</span>
          </div>:null,
        ])}
        {title==='Foundations'&&project&&<div class={`automation-matrix-row${requestedOn(byId.get('scan_timeline')||({} as never))?'':' globally-off'}`}>
          <div class="automation-matrix-name" style="--depth:3" data-setting="scan_timeline_auto_enable">
            {nameCell('scan_timeline_auto_enable','Arm every new conversation','Off, each conversation starts unscanned and is armed from its Timeline tab. On, a new conversation arms itself on its first turn.')}
          </div>
          {/* The one Project field that qualifies an opt-in rather than being
              one, so it gets the same two scopes as the rows above: an install
              default and a three-position Project answer. It used to be written
              into every Project the creation form armed, which is exactly why
              an operator could not change their mind about it in one place. */}
          <div class="automation-matrix-cell automation-authority-global">
            <label class="check" data-setting="scan_timeline_auto_enable_default" title="What a Project that has not decided inherits">
              <input type="checkbox" disabled={saving} checked={!!data.scan_timeline_auto_enable_default}
                onChange={event=>void patchConfig({scan_timeline_auto_enable_default:event.currentTarget.checked})}/>
              <span>default</span>
            </label>
            <div class="automation-authority-meta">
              <small>{data.projects.filter(row=>row.scan_timeline_auto_enable_own==null).length} inherit</small>
            </div>
          </div>
          <div class="automation-matrix-cell">
            <Dropdown ariaLabel="Arm every new conversation in this Project"
              value={project.scan_timeline_auto_enable_own==null?'':project.scan_timeline_auto_enable_own?'on':'off'}
              disabled={saving||!requestedOn(byId.get('scan_timeline')||({} as never))||globallyDisabled.has('scan_timeline')}
              options={[
                {value:'',label:`Follow global (${data.scan_timeline_auto_enable_default?'on':'off'})`},
                {value:'on',label:'On'},
                {value:'off',label:'Off'},
              ]}
              onChange={value=>setAutoArm(value===''?null:value==='on')}/>
          </div>
          <span class="automation-matrix-fleet">{data.projects.filter(row=>row.scan_timeline_auto_enable).length}/{data.projects.length}</span>
        </div>}
      </div>:null)}
    </div>
    <AutomationAuthority fields={data.authority_fields||[]} defaults={data.authority_default||{}}
      ceiling={data.authority_ceiling||{}} projects={data.projects} projectId={project?.project_id||''}
      busy={saving} onPatchConfig={changes=>void patchConfig(changes)}
      onWriteProject={authority=>void writeAuthority(authority)}/>
    {/* Project-wide, scan-scoped, and meaningless without the permission above
        it - so it renders here beside the switch rather than in the
        session-scoped Timeline tab or the general Projects registry. */}
    {project&&requestedOn(byId.get('scan_timeline')||({} as never))&&!globallyDisabled.has('scan_timeline')&&
      <ProjectContextEditor projectId={project.project_id} busy={saving} onError={onError}/>}
  </div>
}
