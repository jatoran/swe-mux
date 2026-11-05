import { rich, t } from './localization/index.ts'
import { useEffect } from 'preact/hooks'
import { Dropdown } from './Dropdown'
import { SetupAccounts } from './SetupAccounts'
import type { HarnessRegistryPayload } from './harnessRegistry'
import type { SetupDraft } from './onboarding'

export type ExperienceTier = 'terminal'|'deterministic'|'automations'
export const EXPERIENCE_TIERS = [
  {id:'terminal',get title() { return t("HarnessSetup.4220e2080e") },get blurb() { return t("HarnessSetup.0acc2aca5f") }},
  {id:'deterministic',get title() { return t("HarnessSetup.9f6f919dc1") },get blurb() { return t("HarnessSetup.1584ca994a") }},
  {id:'automations',get title() { return t("HarnessSetup.78a79112df") },get blurb() { return t("HarnessSetup.df6006ca4b") }},
] as const
export type AutonomyLevel = 'supervised'|'assisted'|'autonomous'
export const AUTONOMY_LEVELS = [
  {id:'supervised',get title() { return t("HarnessSetup.a6f703fd0e") },get blurb() { return t("HarnessSetup.479ce31729") }},
  {id:'assisted',get title() { return t("HarnessSetup.3aba4e923d") },get blurb() { return t("HarnessSetup.89c6c35705") }},
  {id:'autonomous',get title() { return t("HarnessSetup.08a3229fa3") },get blurb() { return t("HarnessSetup.982e6a6351") }},
] as const
export type FleetAccessChoice = 'default'|'mcp'|'cli'|'none'
export const FLEET_ACCESS_CHOICES = [
  {id:'default',get label() { return t("HarnessSetup.c35b64c5b7") },get note() { return t("HarnessSetup.d70604e843") }},
  {id:'mcp',get label() { return t("HarnessSetup.cf796b5a0e") },note:''},
  {id:'cli',get label() { return t("HarnessSetup.f971e6a3c6") },note:''},
  {id:'none',get label() { return t("HarnessSetup.7880eef881") },note:''},
] as const
/** Absolute selections: changing back to the default also restores its surfaces. */
export function fleetAccessChanges(choice:FleetAccessChoice,harnesses:readonly string[]):Record<string,Record<string,boolean>> {
  const all=(value:boolean)=>Object.fromEntries(harnesses.map(name=>[name,value]))
  return {
    harness_mcp_enabled:all(choice==='default'||choice==='mcp'),
    harness_cli_enabled:all(choice==='default'||choice==='cli'),
    harness_skill_enabled:all(choice==='cli'),
  }
}
export const DEFAULT_KEYMAP_PRESET='swemux'
export const DEFAULT_THEME='tokyo-night'
export type KeymapPreset={id:string;title:string;description:string;warning:string}
export function keymapNote(presets:KeymapPreset[],selected:string):string {
  const preset=presets.find(item=>item.id===selected)
  return preset?.warning||preset?.description||t("HarnessSetup.3f56db5af2")
}

type Props={
  page:'experience'|'harnesses';draft:SetupDraft;onChange:(patch:Partial<SetupDraft>)=>void
  registry:HarnessRegistryPayload|null;loading:boolean;error:string;onRetry:()=>void
}
export function HarnessSetup({page,draft,onChange,registry,loading,error,onRetry}:Props) {
  const harnesses=registry?.harnesses||[]
  // No selection exists until detection answers. A present empty map is intentional.
  useEffect(()=>{
    if(!registry||'harnesses' in draft)return
    const enabled=Object.fromEntries(registry.harnesses.map(item=>[item.name,!!item.installed]))
    onChange({harnesses:enabled,default_harness:registry.harnesses.find(item=>enabled[item.name])?.name||''})
  },[registry,draft.harnesses])
  if(page==='experience')return <section>
    <h2 data-heading-message="HarnessSetup.99a17fa51b">{t("HarnessSetup.99a17fa51b")}</h2><p class="setup-lede">{t("HarnessSetup.5d79799ad6")}</p>
    <div class="setup-cards">{EXPERIENCE_TIERS.map(tier=><label class={`setup-card ${(draft.tier||'deterministic')===tier.id?'selected':''}`} key={tier.id}>
      <input type="radio" name="experience-tier" checked={(draft.tier||'deterministic')===tier.id} onChange={()=>onChange({tier:tier.id,overrides:{}})}/>
      <span><strong>{tier.title}</strong><small>{tier.blurb}</small>{tier.id==='deterministic' && <small class="setup-recommended">{t("HarnessSetup.d70604e843")}</small>}</span>
    </label>)}</div>
  </section>
  const choices=draft.harnesses
  const selected=harnesses.filter(item=>choices?.[item.name])
  const defaultHarness=selected.some(item=>item.name===draft.default_harness)?draft.default_harness||'':''
  const choose=(name:string,enabled:boolean)=>{
    const next={...choices,[name]:enabled}
    onChange({harnesses:next,default_harness:next[defaultHarness]?defaultHarness:harnesses.find(item=>next[item.name])?.name||''})
  }
  return <section>
    <h2 data-heading-message="HarnessSetup.853acc42ef">{t("HarnessSetup.853acc42ef")}</h2><p class="setup-lede">{t("HarnessSetup.eb1b91436b")}</p>
    <div class="setup-shell-row"><strong>{t("HarnessSetup.a733285486")}</strong><span>{t("HarnessSetup.0fda37724c")}</span><button onClick={()=>onChange({harnesses:{},default_harness:''})}>{t("HarnessSetup.8e11f491ff")}</button></div>
    {loading && <p role="status">{t("HarnessSetup.cacc943a92")}</p>}
    {error && <p role="alert">{error}</p>}
    {registry && <div class="setup-cards">{harnesses.map(item=><label class={`setup-card ${choices?.[item.name]?'selected':''}`} key={item.name}>
      <input type="checkbox" disabled={!item.installed} checked={!!choices?.[item.name]} onChange={event=>choose(item.name,event.currentTarget.checked)}/>
      <span><strong>{item.display_name}</strong><small>{item.resolved_path ? t("HarnessSetup.756a8ba97d") : item.installed ? t("HarnessSetup.f1418eb503") : t("HarnessSetup.a5ee305c97")}</small></span>
    </label>)}</div>}
    <div class="setup-inline-actions"><button disabled={loading} onClick={onRetry}>{error ? t("HarnessSetup.17a0a747db") : t("HarnessSetup.fb7099ad8e")}</button><a href="https://swemux.dev/docs/agent-setup/" target="_blank" rel="noreferrer">{t("HarnessSetup.a941998dbf")}</a></div>
    {selected.length>0 && <label class="setup-select">{rich("HarnessSetup.b31b817625", { value1: <Dropdown ariaLabel={t("HarnessSetup.c039f423b8")} value={defaultHarness} onChange={value=>onChange({default_harness:value})} options={[{value:'',get label() { return t("HarnessSetup.a733285486") }},...selected.map(item=>({value:item.name,label:item.display_name}))]}/> })}</label>}
    {selected.length>0 && <SetupAccounts enabled={selected.map(item=>item.name)}/>}
    <p class="setup-hint">{selected.length ? t("HarnessSetup.bf6ef4d916") : t("HarnessSetup.d3268e49fe")}</p>
  </section>
}
