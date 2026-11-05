import { rich, t } from './localization/index.ts'
import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { AUTONOMY_LEVELS, FLEET_ACCESS_CHOICES } from './HarnessSetup'
import { ExperiencePreview } from './ExperiencePreview'
import type { SetupDraft } from './onboarding'

export type TierAssignments={tiers:Record<string,Record<string,unknown>>;autonomy:Record<string,Record<string,unknown>>;overridable:string[]}
const LABELS:Record<string,string>={get agent_shims_on_shell_path() { return t("SetupPermissions.b54e0ee985") },get agent_messaging_enabled() { return t("SetupPermissions.a24d155edc") },get agent_interject_enabled() { return t("SetupPermissions.9c6efd0b14") },get session_control_enabled() { return t("SetupPermissions.009e0ae5e8") },get request_spawn_enabled() { return t("SetupPermissions.980b5b70ec") },get session_watch_enabled() { return t("SetupPermissions.4e74c5baaf") },get scheduled_runs_enabled() { return t("SetupPermissions.19110a1000") },get land_queue_enabled() { return t("SetupPermissions.788d20dbca") },get automation_enabled() { return t("SetupPermissions.eda2fcc24f") },get scan_timeline_enabled() { return t("SetupPermissions.6f260614f6") }}
export function SetupPermissions({draft,onChange}:{draft:SetupDraft;onChange:(patch:Partial<SetupDraft>)=>void}) {
  const [assignments,setAssignments]=useState<TierAssignments|null>(null)
  const [error,setError]=useState('')
  const reload=()=>void api<TierAssignments>('GET','/api/experience-tiers',undefined,{timeoutMs:10000}).then(value=>{setAssignments(value);setError('')}).catch(cause=>setError(cause.message))
  useEffect(reload,[])
  const tier=draft.tier||'deterministic'
  return <section><h2 data-heading-message="SetupPermissions.8d738b6e94">{t("SetupPermissions.8d738b6e94")}</h2><p class="setup-lede">{t("SetupPermissions.cb4909a299")}</p>
    <div class="setup-cards">{AUTONOMY_LEVELS.map(level=><label class={`setup-card ${(draft.autonomy||'supervised')===level.id?'selected':''}`} key={level.id}><input type="radio" name="autonomy" checked={(draft.autonomy||'supervised')===level.id} onChange={()=>onChange({autonomy:level.id,autonomy_overrides:{}})}/><span><strong>{level.title}</strong><small>{level.blurb}</small>{level.id==='supervised' && <small class="setup-recommended">{t("SetupPermissions.d70604e843")}</small>}</span></label>)}</div>
    <p class="setup-hint">{t("SetupPermissions.db4ea5275c")}</p>
    <details><summary>{t("SetupPermissions.7cb42a711c")}</summary>
      {assignments ? <>
        <dl class="setup-limits">{Object.entries(assignments.autonomy[draft.autonomy||'supervised']||{}).map(([key,value])=>{const label=({get auto_delivery_enabled() { return t("SetupPermissions.9ecadfc122") },get auto_delivery_max_consecutive() { return t("SetupPermissions.7292173ab6") },get auto_delivery_session_ttl_minutes() { return t("SetupPermissions.e3f716aa00") },get auto_delivery_reply_window_minutes() { return t("SetupPermissions.b3e6874716") },get agent_spawn_hourly_budget() { return t("SetupPermissions.8d42f5e465") }} as Record<string,string>)[key]||key;return <div key={key}><dt>{label}</dt><dd>{typeof value==='boolean' ? (value ? t("SetupPermissions.1300117561") : t("SetupPermissions.ca7981b46e")) : <input type="number" min="1" step="1" aria-label={label} value={draft.autonomy_overrides?.[key]??Number(value)} onChange={event=>{const next=event.currentTarget.valueAsNumber;if(Number.isInteger(next)&&next>0)onChange({autonomy_overrides:{...draft.autonomy_overrides,[key]:next}})}}/>}</dd></div>})}</dl>
        <div class="setup-feature-list">{assignments.overridable.map(key=><label class="check" key={key}><input type="checkbox" checked={draft.overrides?.[key]??assignments.tiers[tier]?.[key]===true} onChange={event=>onChange({overrides:{...draft.overrides,[key]:event.currentTarget.checked}})}/>{LABELS[key] || key}</label>)}</div>
      </> : <p>{error || t("SetupPermissions.e449143a01")} <button onClick={reload}>{t("SetupPermissions.942087cc2d")}</button></p>}
      {tier!=='terminal' && <label class="setup-select">{rich("SetupPermissions.5e5a91ec34", { value1: <select value={draft.fleet_access||'default'} onChange={event=>onChange({fleet_access:event.currentTarget.value})}>{FLEET_ACCESS_CHOICES.map(choice=><option value={choice.id} key={choice.id}>{choice.label}</option>)}</select> })}</label>}
      <ExperiencePreview tier={tier}/>
    </details>
  </section>
}
