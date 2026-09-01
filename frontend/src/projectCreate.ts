// Pure helpers for the Add-project dialog. Registering an existing folder and
// creating a new one are the same form in two modes: the existing-folder mode names
// a folder that is already there, the new-folder mode names a parent plus the folder
// to make inside it. Keeping the path arithmetic here keeps it testable, since the
// dialog itself cannot ask the daemon what a valid path looks like.

export type InitScript = { id:string; label:string; command:string; default_enabled?:boolean }

export type ProjectCreateDraft = {
  mode:'existing'|'new'
  name:string
  root:string
  parent:string
  folder:string
  folderTouched:boolean
  group_id:string
  scripts:string[]
  /** Per-automation deviations from what this install's defaults say, and nothing
   *  else. An id absent here is an id the new Project's `.swe-mux/config.toml`
   *  will not mention at all, so it keeps following the install default for as
   *  long as nobody changes its mind - which is the whole point of the change
   *  made 2026-08-31.
   *
   *  It replaced three booleans that each wrote a fixed set of ids into the file.
   *  Two things were wrong with that and only the second is obvious. The obvious
   *  one: a new Project inherited nothing, so an operator who wanted the same
   *  posture everywhere had to say so once per Project. The other: because the
   *  sets were written *down*, changing the install's mind later reached none of
   *  the Projects that had ever been created, and there was no way to tell a
   *  Project that had chosen from one that had merely been created. */
  automationOverrides:Record<string,boolean>
  /** Whether to also apply the model-backed starting set (scan timeline armed per
   *  run, re-titling on scope change, model narration, plus their dependency
   *  closure) to this Project explicitly. Never defaulted on: it can bill. */
  llm:boolean
  /** Whether to grant agents in this Project acting authority (spawn and land
   *  without per-request approval, with spawn-request review on for what still
   *  drafts). Never defaulted on: it hands agents real authority. */
  autonomy:boolean
}

export const emptyProjectCreateDraft = ():ProjectCreateDraft => ({
  mode:'existing', name:'', root:'', parent:'', folder:'', folderTouched:false,
  group_id:'', scripts:[],
  // Empty: a new Project inherits this install's defaults and says nothing of its
  // own until somebody expands the panel and disagrees with one.
  automationOverrides:{},
  // The two optional sets still start off and are still written down when ticked:
  // one spends money and the other grants authority, so each is a deliberate act
  // about *this repository* rather than something to inherit quietly.
  llm:false,
  autonomy:false,
})

/** What an automation will do in the new Project before anything is ticked. */
export type InheritedAutomation = {
  id:string; label:string; kind:string; requires:string[]
  implemented:boolean; spends:boolean
  /** The daemon's resolved answer for an id no Project file mentions. */
  install_default?:boolean
  default_on?:boolean
  globally_allowed?:boolean
}

export const inheritedOn = (item:InheritedAutomation):boolean =>
  item.install_default ?? item.default_on === true

/** Which automations a Project created from this draft would actually run.
 *
 *  The inherited answer with the draft's own deviations over it, filtered to what
 *  the install-wide ceiling permits - the same three layers the daemon resolves,
 *  in the same order, because a summary line that disagreed with the daemon would
 *  be worse than no summary line. */
export function projectCreateEffective(
  draft:ProjectCreateDraft, automations:InheritedAutomation[],
):InheritedAutomation[] {
  return automations.filter(item=>
    item.implemented && item.globally_allowed!==false
    && (draft.automationOverrides[item.id] ?? inheritedOn(item)))
}

/** The deviations to write, dropping any that agree with what is inherited.
 *
 *  Ticking a box back to the value it already had must leave the file untouched:
 *  writing "true" over an inherited true would pin the Project to today's answer,
 *  which is the failure this whole form was rebuilt to remove. */
export function projectCreateOverrides(
  draft:ProjectCreateDraft, automations:InheritedAutomation[],
):Record<string,boolean> {
  const byId=new Map(automations.map(item=>[item.id,item]))
  const written:Record<string,boolean>={}
  for(const [id,value] of Object.entries(draft.automationOverrides)){
    const item=byId.get(id)
    if(!item||!item.implemented)continue
    if(value===inheritedOn(item))continue
    written[id]=value
  }
  return written
}

/** Set one automation for the new Project, dependency closure included.
 *
 *  The same two rules the policy matrix follows, because they are properties of
 *  the DAG rather than of a surface: turning a consumer on turns on everything it
 *  reads from, and turning substrate off turns off everything that reads from it.
 *  A form that let you tick a consumer and leave its substrate alone would offer a
 *  switch the daemon resolves to `blocked`. */
export function setCreateAutomation(
  draft:ProjectCreateDraft, automations:InheritedAutomation[], id:string, on:boolean,
):Record<string,boolean> {
  const byId=new Map(automations.map(item=>[item.id,item]))
  const closure=(name:string, seen=new Set<string>()):Set<string>=>{
    if(seen.has(name))return seen
    seen.add(name)
    for(const dependency of byId.get(name)?.requires||[])closure(dependency,seen)
    return seen
  }
  const next={...draft.automationOverrides}
  if(on)for(const item of closure(id))next[item]=true
  else{
    next[id]=false
    for(const item of automations)
      if(item.id!==id&&closure(item.id,new Set()).has(id))next[item.id]=false
  }
  return next
}

/** One named starting set as `GET /api/grants` serves it: the automations to opt in
 *  and the typed Project fields to set. The ids live daemon-side so the form cannot
 *  offer a set the daemon refuses. */
export type StartingSet = { automations:string[]; values:Record<string,unknown> }
export type StartingSetCatalog = { recommended:StartingSet; llm:StartingSet; autonomy:StartingSet }

/** The union of the sets this draft ticked, as one grant request. One request rather
 *  than one per checkbox: the daemon computes the dependency closure and writes the
 *  Project file once, so there is one revision, one audit record, and no half-applied
 *  state a second call could leave behind.
 *
 *  The free set is no longer one of them. It is a deviation now (see
 *  `seedRecommendedOverrides`), because it is the set an install ought to be able to
 *  inherit; the two here stay explicit writes about *this repository*, since one can
 *  bill and the other hands agents authority. */
export function selectedStartingSets(draft:ProjectCreateDraft, catalog:StartingSetCatalog):StartingSet {
  const picked:StartingSet[]=[]
  if(draft.llm)picked.push(catalog.llm)
  if(draft.autonomy)picked.push(catalog.autonomy)
  const automations:string[]=[]
  const values:Record<string,unknown>={}
  for(const set of picked){
    for(const id of set.automations)if(!automations.includes(id))automations.push(id)
    Object.assign(values,set.values)
  }
  return {automations,values}
}

/** The free analysis set, pre-ticked only where this install has no opinion.
 *
 *  It exists to keep the out-of-box behaviour it used to have without keeping the
 *  cost. Before 2026-08-31 the free set was a ticked checkbox that wrote five ids
 *  into every new Project, so a first Project was never empty - and so no Project
 *  ever followed the install afterwards. Three cases, and the middle one is why
 *  this reads the *stored* defaults map rather than the resolved answer:
 *
 *  - the install says nothing about an id: tick it, so a fresh install's first
 *    Project has working analysis panes on day one, exactly as before.
 *  - the install says `false`: leave it alone. An operator who turned a default off
 *    install-wide is not asking to be overruled by a creation form.
 *  - the install says `true`: leave it alone. It is already inherited, and writing
 *    it down would pin the Project to today's answer.
 *
 *  So the form goes quiet as soon as the operator has expressed a policy, which is
 *  the whole point: you decide once, not once per Project.
 */
export function seedRecommendedOverrides(
  recommended:string[], automations:InheritedAutomation[], storedDefaults:Record<string,boolean>,
):Record<string,boolean> {
  const byId=new Map(automations.map(item=>[item.id,item]))
  const closure=(name:string, seen=new Set<string>()):Set<string>=>{
    if(seen.has(name))return seen
    seen.add(name)
    for(const dependency of byId.get(name)?.requires||[])closure(dependency,seen)
    return seen
  }
  const seeded:Record<string,boolean>={}
  for(const id of recommended){
    const item=byId.get(id)
    if(!item||!item.implemented||item.globally_allowed===false)continue
    if(id in storedDefaults||inheritedOn(item))continue
    // The whole closure, or the seeded consumer resolves to `blocked` and the
    // pane it feeds stays as empty as it was before any of this.
    for(const dependency of closure(id)){
      if(dependency in storedDefaults)continue
      seeded[dependency]=true
    }
  }
  return seeded
}

// Windows is the primary platform and its separator is also the one a drive-letter
// path implies, so it is the fallback when the parent carries no separator of its own.
export function pathSeparator(path:string):string {
  if(path.includes('\\'))return '\\'
  if(path.includes('/'))return '/'
  return '\\'
}

export function joinPath(parent:string, name:string):string {
  const base=parent.trim().replace(/[\\/]+$/,'')
  const leaf=name.trim().replace(/^[\\/]+/,'')
  if(!base)return leaf
  if(!leaf)return base
  // A bare drive letter needs its separator back: `D:` and `D:\` mean different things.
  const separator=/^[A-Za-z]:$/.test(base)?'\\':pathSeparator(parent)
  return `${base}${separator}${leaf}`
}

// A Project name is free text; a folder name is not. Characters Windows rejects
// outright become hyphens rather than being dropped, so two distinct names cannot
// silently collapse into the same folder.
export function suggestFolderName(name:string):string {
  return name.trim()
    .replace(/[<>:"/\\|?*\u0000-\u001f\u007f]/g,'-')
    .replace(/\s+/g,'-')
    .replace(/-{2,}/g,'-')
    .replace(/^[-.]+|[-. ]+$/g,'')
}

export function parentPath(path:string):string {
  const trimmed=path.trim().replace(/[\\/]+$/,'')
  const index=Math.max(trimmed.lastIndexOf('\\'),trimmed.lastIndexOf('/'))
  if(index<=0)return ''
  const parent=trimmed.slice(0,index)
  // A drive-letter parent keeps its separator: `D:` alone is a relative path.
  return /^[A-Za-z]:$/.test(parent)?`${parent}\\`:parent
}

/**
 * The parent directory holding the most registered project roots — the Settings
 * placeholder for the assistant's new-project location. Case-insensitive count
 * (Windows is the primary platform); ties keep the first-seen spelling.
 */
export function commonestParent(roots:string[]):string {
  const counts=new Map<string,{count:number;value:string}>()
  for(const root of roots){
    const parent=parentPath(root)
    if(!parent)continue
    const key=parent.toLowerCase()
    const entry=counts.get(key)
    if(entry)entry.count+=1
    else counts.set(key,{count:1,value:parent})
  }
  let best='',bestCount=0
  for(const {count,value} of counts.values()){
    if(count>bestCount){best=value;bestCount=count}
  }
  return best
}

export function projectCreateFolder(draft:ProjectCreateDraft):string {
  return draft.folderTouched?draft.folder.trim():suggestFolderName(draft.name)
}

/** The exact canonical root the daemon will be asked to register. */
export function projectCreateRoot(draft:ProjectCreateDraft):string {
  if(draft.mode==='existing')return draft.root.trim()
  const folder=projectCreateFolder(draft)
  return draft.parent.trim()&&folder?joinPath(draft.parent,folder):''
}

export function projectCreateReady(draft:ProjectCreateDraft):boolean {
  return !!draft.name.trim()&&!!projectCreateRoot(draft)
}

/** Init scripts start unchecked unless their definition opts in. */
export function defaultInitScriptSelection(scripts:InitScript[]):string[] {
  return scripts.filter(script=>script.default_enabled).map(script=>script.id)
}

export function toggleInitScript(selected:string[], id:string, on:boolean):string[] {
  const without=selected.filter(item=>item!==id)
  return on?[...without,id]:without
}
