import { useMemo, useState } from 'preact/hooks'
import { api } from './api'
import { Dropdown } from './Dropdown'
import { SettingLink } from './SettingLink'
import { automationSetting } from './settingTargets'
import { forgetProjectAutomations } from './projectAutomations'
import type { AutomationRegistryEntry } from './projectAutomations'
import { projectDropdownOptions } from './projectOptions'
import { ProjectContextEditor } from './ProjectContextEditor'
import type { StartingSetCatalog } from './projectCreate'

// The policy matrix: every automation is one row, its install-wide ceiling and
// the selected Project's opt-in side by side, with a fleet column saying how
// many Projects run it. This is THE editor for both scopes - the one surface
// that may turn an automation off - so the additive-only grant rule elsewhere
// stays sound.
//
// Cascade greying is drawn from the same resolution the daemon enforces:
// `globally_allowed` on each registry entry is the resolved ceiling (closure
// included), and a Project cell under a blocked ceiling is disabled rather
// than rendered as merely off - the Project's own choice is retained on disk.
//
// Three rows' Global cells write dedicated install switches
// (`scan_timeline_enabled`, `scheduled_runs_enabled`, `land_queue_enabled`)
// and every other row's writes the `automation_global_allow` map - one switch,
// one key, decided by the registry's `install_switch` field rather than by
// this file remembering which is which.

export type MatrixProject={
  project_id:string;project_name:string;status:string;revision:string
  requested:Record<string,boolean>;enabled:string[];blocked:Record<string,string[]>
  unverified?:string[];globally_disabled?:string[]
  llm?:{ready:boolean;reason:string}|null
  scan_timeline_auto_enable:boolean
}
export type MatrixData={
  automations:AutomationRegistryEntry[]
  projects:MatrixProject[]
  global_allow:Record<string,boolean>
  install_switches:{automation_enabled:boolean;scan_timeline_enabled:boolean;scheduled_runs_enabled:boolean;land_queue_enabled:boolean}
}

const PRESETS_SEEN_KEY='mux.automationPresetsSeen'
const presetsSeen=():boolean=>{try{return !!localStorage.getItem(PRESETS_SEEN_KEY)}catch{return true}}
const markPresetsSeen=():void=>{try{localStorage.setItem(PRESETS_SEEN_KEY,'1')}catch{/* private mode */}}

const PRESET_COPY:Record<string,{name:string;description:string;cost:string}>={
  recommended:{name:'Free basics',description:'Model-free health checks over what agents actually did: stuck loops, unverified claims, change provenance, doc debt, code structure.',cost:'free - never calls a model'},
  llm:{name:'AI timeline',description:'A model keeps a readable play-by-play of every session: the scan timeline armed per run, adaptive titles, attention narration.',cost:'costs money - bounded by the global budget'},
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
    project?(project.requested[item.id]??(item.default_on===true)):false

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
  const toggleGlobal=(item:AutomationRegistryEntry)=>{
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
  const writeProject=async(automations:Record<string,boolean>)=>{
    if(!project)return
    setSaving(true)
    try{
      await api('PUT',`/api/projects/${project.project_id}/automations`,{
        automations,
        scan_timeline_auto_enable:project.scan_timeline_auto_enable,
        revision:project.revision,
      })
      forgetProjectAutomations(project.project_id)
      await onChanged()
    }catch(cause){onError(cause instanceof Error?cause.message:String(cause))}
    finally{setSaving(false)}
  }
  const toggleProject=(item:AutomationRegistryEntry)=>{
    if(!project)return
    const next={...project.requested}
    const switchOff=(id:string)=>{
      // Off is an explicit `false` for a default-on automation - absent means
      // on there - and absence for everything else, where absent already means
      // off and a false entry is noise the daemon strips anyway.
      if(byId.get(id)?.default_on)next[id]=false
      else delete next[id]
    }
    if(requestedOn(item)){
      switchOff(item.id)
      // Turning substrate off turns off everything that reads from it, rather
      // than leaving dependents enabled-but-inert.
      for(const other of data.automations)if(closure(other.id,new Set()).has(item.id)&&other.id!==item.id)switchOff(other.id)
    }else{
      // Enabling a consumer enables its whole transitive closure: the
      // alternative is a toggle that appears on and silently does nothing.
      for(const id of closure(item.id))next[id]=true
    }
    void writeProject(next)
  }
  const toggleAutoArm=(value:boolean)=>{
    if(!project)return
    setSaving(true)
    void (async()=>{
      try{
        await api('PUT',`/api/projects/${project.project_id}/automations`,{
          automations:project.requested,
          scan_timeline_auto_enable:value,
          revision:project.revision,
        })
        forgetProjectAutomations(project.project_id)
        await onChanged()
      }catch(cause){onError(cause instanceof Error?cause.message:String(cause))}
      finally{setSaving(false)}
    })()
  }

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
  const readsTimeline=(item:AutomationRegistryEntry)=>closure(item.id,new Set()).has('scan_timeline')
  const groups:[string,string,AutomationRegistryEntry[]][]=useMemo(()=>{
    const substrate=data.automations.filter(item=>item.kind==='substrate')
    const consumers=data.automations.filter(item=>item.kind==='consumer')
    return [
      ['Foundations','record facts, never act',substrate],
      ['Deterministic checks','model-free reads over the recorded facts',consumers.filter(item=>item.requires.length>0&&!readsTimeline(item))],
      ['Capabilities','what agents and schedules may do',consumers.filter(item=>!item.requires.length)],
      ['Reads the timeline','distillations of the scan timeline',consumers.filter(item=>readsTimeline(item))],
    ]
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
        const next={...project.requested}
        for(const id of set.automations){
          if(byId.get(id)?.default_on)next[id]=false
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

  const row=(item:AutomationRegistryEntry)=>{
    const ceilingOff=!globallyAllowed(item)
    const busy=saving||!item.implemented
    const projectOn=requestedOn(item)
    const globalOn=item.install_switch
      ?data.install_switches[item.install_switch as keyof MatrixData['install_switches']]
      :data.global_allow[item.id]!==false
    return <div class={`automation-matrix-row${ceilingOff?' globally-off':''}`} key={item.id} role="row">
      <div class="automation-matrix-name" style={`--depth:${depth(item)}`}>
        <span class="project-setting-name"><b>{item.label}</b>
          {item.spends&&<em class="project-setting-chip spends">spends</em>}
          {item.default_on&&<em class="project-setting-chip">on by default</em>}
          {!item.implemented&&<em class="project-setting-chip">not built yet</em>}
        </span>
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
      <label class="check automation-matrix-cell" data-setting={item.install_switch||undefined} title="Allowed anywhere at all - the install-wide ceiling">
        <input type="checkbox" disabled={busy||upstreamBlocked(item)} checked={globalOn}
          onChange={()=>toggleGlobal(item)}/>
      </label>
      <label class="check automation-matrix-cell" data-setting={automationSetting(item.id)} title={ceilingOff?'Disabled install-wide; the Project choice is kept':'This Project opted in'}>
        <input type="checkbox" disabled={busy||ceilingOff||!project||project.status==='read-only'} checked={projectOn}
          onChange={()=>toggleProject(item)}/>
      </label>
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
      <b>Global</b> = allowed anywhere at all (the ceiling) · <b>{project?.project_name||'Project'}</b> = this Project opted in ·
      a greyed Project switch is blocked by the ceiling and keeps the Project's own choice ·
      <em class="project-setting-chip spends">spends</em> = uses a paid model
    </p>
    <div class="automation-matrix-grid" role="table" aria-label="Automation policy matrix" data-setting="automation_global_allow">
      <div class="automation-matrix-head" role="row" data-setting="automations"><span>automation</span><span>global</span><span>{project?.project_name||'project'}</span><span>projects on</span></div>
      {groups.map(([title,hint,items])=>items.length?<div key={title}>
        <h5 class="project-automation-group">{title}<span>{hint}</span></h5>
        {items.map(row)}
        {title==='Foundations'&&project&&<div class={`automation-matrix-row${requestedOn(byId.get('scan_timeline')||({} as never))?'':' globally-off'}`}>
          <div class="automation-matrix-name" style="--depth:3">
            <span class="project-setting-name" data-setting="scan_timeline_auto_enable">Arm every new conversation</span>
            <p class="project-automation-deps">Off, each conversation starts unscanned and is armed in the Timeline tab. On, a new conversation arms itself on its first turn.</p>
          </div>
          <span class="automation-matrix-cell"/>
          <label class="check automation-matrix-cell">
            <input type="checkbox" disabled={saving||!requestedOn(byId.get('scan_timeline')||({} as never))||globallyDisabled.has('scan_timeline')}
              checked={!!project.scan_timeline_auto_enable&&requestedOn(byId.get('scan_timeline')||({} as never))}
              onChange={event=>toggleAutoArm(event.currentTarget.checked)}/>
          </label>
          <span class="automation-matrix-fleet">{data.projects.filter(row=>row.scan_timeline_auto_enable).length}/{data.projects.length}</span>
        </div>}
      </div>:null)}
    </div>
    {/* Project-wide, scan-scoped, and meaningless without the permission above
        it - so it renders here beside the switch rather than in the
        session-scoped Timeline tab or the general Projects registry. */}
    {project&&requestedOn(byId.get('scan_timeline')||({} as never))&&!globallyDisabled.has('scan_timeline')&&
      <ProjectContextEditor projectId={project.project_id} busy={saving} onError={onError}/>}
  </div>
}
