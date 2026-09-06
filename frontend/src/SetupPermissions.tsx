import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { AUTONOMY_LEVELS, FLEET_ACCESS_CHOICES } from './HarnessSetup'
import { ExperiencePreview } from './ExperiencePreview'
import type { SetupDraft } from './onboarding'

export type TierAssignments={tiers:Record<string,Record<string,unknown>>;autonomy:Record<string,Record<string,unknown>>;overridable:string[]}
const LABELS:Record<string,string>={agent_shims_on_shell_path:'Track agents launched from Shell',agent_messaging_enabled:'Agent messages',agent_interject_enabled:'Mid-turn messages',session_control_enabled:'Agent session control',request_spawn_enabled:'Agent spawn requests',session_watch_enabled:'Session watches',scheduled_runs_enabled:'Scheduled runs',land_queue_enabled:'Auto-merge',automation_enabled:'AI automations',scan_timeline_enabled:'AI activity timeline'}
export function SetupPermissions({draft,onChange}:{draft:SetupDraft;onChange:(patch:Partial<SetupDraft>)=>void}) {
  const [assignments,setAssignments]=useState<TierAssignments|null>(null)
  const [error,setError]=useState('')
  const reload=()=>void api<TierAssignments>('GET','/api/experience-tiers',undefined,{timeoutMs:10000}).then(value=>{setAssignments(value);setError('')}).catch(cause=>setError(cause.message))
  useEffect(reload,[])
  const tier=draft.tier||'deterministic'
  return <section><h2>How much should happen automatically?</h2><p class="setup-lede">Choose how queued messages reach your agents.</p>
    <div class="setup-cards">{AUTONOMY_LEVELS.map(level=><label class={`setup-card ${(draft.autonomy||'supervised')===level.id?'selected':''}`} key={level.id}><input type="radio" name="autonomy" checked={(draft.autonomy||'supervised')===level.id} onChange={()=>onChange({autonomy:level.id,autonomy_overrides:{}})}/><span><strong>{level.title}</strong><small>{level.blurb}</small>{level.id==='supervised'&&<small class="setup-recommended">Recommended</small>}</span></label>)}</div>
    <p class="setup-hint">Model access and Project permissions are separate choices.</p>
    <details><summary>Limits and individual features</summary>
      {assignments?<>
        <dl class="setup-limits">{Object.entries(assignments.autonomy[draft.autonomy||'supervised']||{}).map(([key,value])=>{const label=({auto_delivery_enabled:'Automatic delivery',auto_delivery_max_consecutive:'Consecutive sends',auto_delivery_session_ttl_minutes:'Session allowance (minutes)',auto_delivery_reply_window_minutes:'Reply window (minutes)',agent_spawn_hourly_budget:'Agent starts per hour'} as Record<string,string>)[key]||key;return <div key={key}><dt>{label}</dt><dd>{typeof value==='boolean'?(value?'On':'Off'):<input type="number" min="1" step="1" aria-label={label} value={draft.autonomy_overrides?.[key]??Number(value)} onChange={event=>{const next=event.currentTarget.valueAsNumber;if(Number.isInteger(next)&&next>0)onChange({autonomy_overrides:{...draft.autonomy_overrides,[key]:next}})}}/>}</dd></div>})}</dl>
        <div class="setup-feature-list">{assignments.overridable.map(key=><label class="check" key={key}><input type="checkbox" checked={draft.overrides?.[key]??assignments.tiers[tier]?.[key]===true} onChange={event=>onChange({overrides:{...draft.overrides,[key]:event.currentTarget.checked}})}/>{LABELS[key]||key}</label>)}</div>
      </>:<p>{error||'Loading feature choices…'} <button onClick={reload}>Retry</button></p>}
      {tier!=='terminal'&&<label class="setup-select">Agent fleet access<select value={draft.fleet_access||'default'} onChange={event=>onChange({fleet_access:event.currentTarget.value})}>{FLEET_ACCESS_CHOICES.map(choice=><option value={choice.id} key={choice.id}>{choice.label}</option>)}</select></label>}
      <ExperiencePreview tier={tier}/>
    </details>
  </section>
}
