import { useEffect } from 'preact/hooks'
import { Dropdown } from './Dropdown'
import { SetupAccounts } from './SetupAccounts'
import type { HarnessRegistryPayload } from './harnessRegistry'
import type { SetupDraft } from './onboarding'

export type ExperienceTier = 'terminal'|'deterministic'|'automations'
export const EXPERIENCE_TIERS = [
  {id:'terminal',title:'Just terminals',blurb:'Shells and agent CLIs. Minimal extras.'},
  {id:'deterministic',title:'Agent workspace',blurb:'Transcripts, status, notes, and queues. No model API needed.'},
  {id:'automations',title:'Smart workspace',blurb:'Add AI summaries and automations. Connect models later.'},
] as const
export type AutonomyLevel = 'supervised'|'assisted'|'autonomous'
export const AUTONOMY_LEVELS = [
  {id:'supervised',title:'Review first',blurb:'You approve queued messages.'},
  {id:'assisted',title:'Deliver when ready',blurb:'Queued messages send when an agent is ready.'},
  {id:'autonomous',title:'Extended runs',blurb:'Longer unattended sequences, with higher delivery and spawn limits.'},
] as const
export type FleetAccessChoice = 'default'|'mcp'|'cli'|'none'
export const FLEET_ACCESS_CHOICES = [
  {id:'default',label:'MCP tools + agent CLI',note:'Recommended'},
  {id:'mcp',label:'MCP tools only',note:''},
  {id:'cli',label:'Agent CLI + skill file',note:''},
  {id:'none',label:'No fleet access',note:''},
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
  return preset?.warning||preset?.description||'Shortcuts stay editable in Settings.'
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
    <h2>How do you want to start?</h2><p class="setup-lede">Pick a starting point. Change it anytime.</p>
    <div class="setup-cards">{EXPERIENCE_TIERS.map(tier=><label class={`setup-card ${(draft.tier||'deterministic')===tier.id?'selected':''}`} key={tier.id}>
      <input type="radio" name="experience-tier" checked={(draft.tier||'deterministic')===tier.id} onChange={()=>onChange({tier:tier.id,overrides:{}})}/>
      <span><strong>{tier.title}</strong><small>{tier.blurb}</small>{tier.id==='deterministic'&&<small class="setup-recommended">Recommended</small>}</span>
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
    <h2>What do you want to run?</h2><p class="setup-lede">Shell is ready. Choose any agents you also want.</p>
    <div class="setup-shell-row"><strong>Shell</strong><span>Always available</span><button onClick={()=>onChange({harnesses:{},default_harness:''})}>Use Shell only</button></div>
    {loading&&<p role="status">Detecting installed agents…</p>}
    {error&&<p role="alert">{error}</p>}
    {registry&&<div class="setup-cards">{harnesses.map(item=><label class={`setup-card ${choices?.[item.name]?'selected':''}`} key={item.name}>
      <input type="checkbox" disabled={!item.installed} checked={!!choices?.[item.name]} onChange={event=>choose(item.name,event.currentTarget.checked)}/>
      <span><strong>{item.display_name}</strong><small>{item.resolved_path?'Detected':item.installed?'Agent data found - check the CLI is installed':'Install needed'}</small></span>
    </label>)}</div>}
    <div class="setup-inline-actions"><button disabled={loading} onClick={onRetry}>{error?'Retry detection':'Check again'}</button><a href="https://swemux.dev/docs/agent-setup/" target="_blank" rel="noreferrer">Agent installation guide ↗</a></div>
    {selected.length>0&&<label class="setup-select">Start with<Dropdown ariaLabel="Default harness" value={defaultHarness} onChange={value=>onChange({default_harness:value})} options={[{value:'',label:'Shell'},...selected.map(item=>({value:item.name,label:item.display_name}))]}/></label>}
    {selected.length>0&&<SetupAccounts enabled={selected.map(item=>item.name)}/>}
    <p class="setup-hint">{selected.length?'Recent project folders are being found in the background.':'Choose a folder manually on the next page.'}</p>
  </section>
}
