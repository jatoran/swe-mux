import { Dropdown } from './Dropdown'

// The agent authority rows on the Policy tab: one row per field, the install
// default and the selected Project's override side by side, and a fleet count
// saying how far the default actually reaches.
//
// Its own section below the dependency layers rather than more rows in the
// matrix, because these hold a *level* rather than an on/off and the "needs X"
// grouping above says nothing about them. It is the same two-scope shape
// though, and for the same reason: this is THE editor for both scopes, which is
// what keeps the additive-only grant path elsewhere sound.
//
// Three positions on the Project cell, not two. "Follow global" writes null and
// the daemon *removes* the key; picking a level writes it. Collapsing those
// into two positions is what made the install default look broken during
// design review of this feature: the old Projects-registry dropdown always
// wrote an explicit value, so every Project anyone had ever opened was pinned
// and inherited nothing.

export type AuthorityFieldSpec={
  field:string;label:string;levels:string[];builtin:string;gated_by:string|null
}
export type AuthorityRowProject={
  project_id:string;project_name:string;status:string
  authority:Record<string,string|null>
  authority_effective:Record<string,string>
  enabled:string[]
}

// What each level means where a person reads it, and the dependency note under
// the row. Prose about a control rather than a fact about the field, so it lives
// here rather than on the daemon's registry entry - the daemon has no business
// owning a dropdown's wording.
//
// The rows themselves come from the daemon's `authority_fields` payload, so this
// table cannot decide which fields exist; it only supplies copy for the ones
// that do. `settingTargets.test.ts` reads the `setting:` literals here to prove
// every addressable field has a control, which is why they are written out
// rather than derived.
const AUTHORITY_COPY:{setting:string;levels:Record<string,string>;note:string}[]=[
  {
    setting: 'session_control_grant',
    levels:{draft:'A human approves each one',granted:'Acts directly'},
    note:'Needs the Agent session control opt-in above.',
  },
  {
    setting: 'spawn_grant',
    levels:{draft:'A human approves each one',granted:'Creates them directly'},
    note:'Also gated by Agent session control. Still bounded by a per-origin budget.',
  },
  {
    setting: 'land_grant',
    levels:{draft:'A human approves each one',granted:'Starts the pipeline directly'},
    note:'Needs the Land queue opt-in above. The pipeline is fast-forward-only either way.',
  },
  {
    setting: 'land_verify_grant',
    levels:{draft:'You approve the bytes each time',granted:'Edits made here just run'},
    note:'Needs the Land queue opt-in above. Granted covers only bytes this machine'
      +' authored - an uncommitted edit, or a branch commit by your git identity. A gate'
      +' anyone else put on the branch presents for approval whatever this says, which is'
      +' what keeps landing a contributor’s branch from running their script.',
  },
  {
    setting: 'interject_grant',
    levels:{off:'Never (waits for the queue)',granted:'May interject'},
    note:'Granted still requires the receiving session to be interruptible.',
  },
  {
    setting: 'message_envelope',
    levels:{
      full:'Full trust statement',
      compact:'Sender, authority, reply route',
      bare:'Nothing (looks like you typed it)',
    },
    note:'What a delivered agent message says about its sender, on top of the text.'
      +' A sender may ask for more than this and never less. Attribution is kept in the'
      +' queue and the audit trail at every level.',
  },
]

const copyFor=(field:string)=>AUTHORITY_COPY.find(entry=>entry.setting===field)
const levelLabel=(field:string,level:string):string=>copyFor(field)?.levels[level]||level

export function AutomationAuthority({fields,defaults,ceiling,projects,projectId,busy,onPatchConfig,onWriteProject}:{
  fields:AuthorityFieldSpec[]
  defaults:Record<string,string>
  /** null where the operator has not locked the row. */
  ceiling:Record<string,string|null>
  projects:AuthorityRowProject[]
  projectId:string
  busy:boolean
  onPatchConfig:(changes:Record<string,unknown>)=>void
  onWriteProject:(authority:Record<string,string|null>)=>void
}){
  const project=projects.find(item=>item.project_id===projectId)||projects[0]
  if(!fields.length)return null

  // How many Projects the Global cell actually decides. A locked row reaches
  // every Project; an unlocked one reaches only those that did not write their
  // own value. Rendering this is what stops a global edit being a silent
  // fleet-wide change - the consequence is visible before the click, not after.
  const pinned=(field:string):number=>projects.filter(item=>item.authority[field]!=null).length
  const cleanCeiling=(next:Record<string,string|null>):Record<string,string>=>{
    const cleaned:Record<string,string>={}
    for(const [key,value] of Object.entries(next))if(value!=null)cleaned[key]=value
    return cleaned
  }
  // A locked row's ceiling *is* its default, so changing the level has to move
  // both maps. Moving only the default would leave the lock enforcing the level
  // it was ticked at while the dropdown beside it showed a different one - the
  // control would be lying about what it does, which is the failure the coverage
  // line below exists to prevent in the other direction.
  const setDefault=(field:string,level:string)=>{
    const changes:Record<string,unknown>={agent_authority_default:{...defaults,[field]:level}}
    if(ceiling[field]!=null)changes.agent_authority_ceiling=cleanCeiling({...ceiling,[field]:level})
    onPatchConfig(changes)
  }
  const toggleLock=(field:string)=>{
    const next={...ceiling}
    // The lock enforces whatever the row's default currently says, so ticking
    // it never silently changes the level beside it.
    if(next[field]==null)next[field]=defaults[field]
    else delete next[field]
    onPatchConfig({agent_authority_ceiling:cleanCeiling(next)})
  }

  const row=(spec:AuthorityFieldSpec)=>{
    const locked=ceiling[spec.field]!=null
    // A level on a capability this Project has not opted into is a control that
    // does nothing, so it greys the way a ceiling-blocked automation cell does.
    const inert=!!spec.gated_by&&!!project&&!project.enabled.includes(spec.gated_by)
    const own=project?.authority[spec.field]??null
    const effective=project?.authority_effective[spec.field]||spec.builtin
    const options=spec.levels.map(level=>({value:level,label:levelLabel(spec.field,level)}))
    const reach=locked
      ?`all ${projects.length}`
      :`${projects.length-pinned(spec.field)} of ${projects.length}`
    return <div class={`automation-matrix-row automation-authority-row${inert?' globally-off':''}`} key={spec.field} role="row">
      <div class="automation-matrix-name">
        <span class="project-setting-name"><b>{spec.label}</b>
          {locked&&<em class="project-setting-chip">enforced</em>}
        </span>
        <p class="project-automation-deps">{copyFor(spec.field)?.note||''}</p>
      </div>
      <div class="automation-matrix-cell automation-authority-global">
        <Dropdown ariaLabel={`Install default for ${spec.label}`} value={defaults[spec.field]||spec.builtin}
          disabled={busy} options={options} onChange={level=>setDefault(spec.field,level)}/>
        <label class="check" title="Override Projects that set their own value">
          <input type="checkbox" disabled={busy} checked={locked} onChange={()=>toggleLock(spec.field)}/>
          <span>enforce everywhere</span>
        </label>
        <small>applies to {reach} Projects{locked?'':`, ${pinned(spec.field)} set their own`}</small>
      </div>
      <div class="automation-matrix-cell" data-setting={spec.field}>
        <Dropdown ariaLabel={`${spec.label} in this Project`} value={own??''}
          disabled={busy||!project||project.status==='read-only'}
          options={[{value:'',label:`Follow global (${levelLabel(spec.field,defaults[spec.field]||spec.builtin)})`},...options]}
          onChange={level=>onWriteProject({[spec.field]:level||null})}/>
        {locked&&own!=null&&own!==effective&&
          <small class="automation-authority-overridden">Enforced down to {levelLabel(spec.field,effective)}.</small>}
      </div>
      <span class="automation-matrix-fleet">{projects.filter(item=>
        (item.authority_effective[spec.field]||spec.builtin)===spec.levels[spec.levels.length-1]
      ).length}/{projects.length}</span>
    </div>
  }

  return <div class="automation-matrix-grid automation-authority-grid" role="table" aria-label="Agent authority">
    <div class="automation-matrix-head" role="row" data-setting="agent_authority">
      <span>agent authority</span><span>global</span><span>{project?.project_name||'project'}</span><span>widest</span>
    </div>
    <h5 class="project-automation-group">Agent authority
      <span>whether an agent still needs a human, once the automation above is on</span>
    </h5>
    {fields.map(row)}
  </div>
}
