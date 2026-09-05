import { useModalFocus } from './modalFocus'
import { useRef, useState } from 'preact/hooks'
import { api } from './api'
import { HarnessSetup, fleetAccessChanges } from './HarnessSetup'
import { ProviderSetup } from './ProviderSetup'
import { SetupProjects } from './SetupProjects'
import { DesktopSetup } from './DesktopSetup'
import { allHarnessesIncludingDisabled } from './harnessRegistry'
import { hostQuery } from './hostProfile'
import type { OnboardingState, SaveOnboarding, SetupDraft } from './onboarding'

export function OnboardingFlow({state,save,onTour,onLaunch,onDone}:{state:OnboardingState;save:SaveOnboarding;onBrowse?:()=>void;onTour:()=>void;onLaunch:()=>void;onDone:()=>void}) {
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [freshConfirm,setFreshConfirm]=useState(false)
  const perform=async(action:()=>Promise<unknown>)=>{setBusy(true);setError('');try{await action()}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}}
  const defer=async(draft=state.draft)=>{await save({status:'deferred',draft});onDone()}
  const apply=async(draft:SetupDraft,tier=draft.tier||'deterministic')=>{
    const result=await api<{restart_required:string[]}>('POST','/api/experience-tier',{tier,autonomy:tier==='terminal'?undefined:draft.autonomy,overrides:tier===draft.tier?draft.overrides:undefined})
    const patch:Record<string,unknown>={}
    if(draft.theme)patch.theme=draft.theme
    if(draft.rail_desktop!==undefined)patch.rail_enabled_desktop=draft.rail_desktop
    if(draft.rail_mobile!==undefined)patch.rail_enabled_mobile=draft.rail_mobile
    if(Object.keys(patch).length)await api('PATCH','/api/config',patch)
    if(draft.keymap)await api('POST',`/api/keymap-preset?${hostQuery()}`,{preset:draft.keymap})
    if(result.restart_required.length)setError('Some choices take full effect after the next daemon reload. Your sessions remain available.')
  }
  const tierChosen=async(draft:SetupDraft)=>{
    // A tier selection is intent. Model-backed masters are applied only after
    // the provider page has verified the endpoint and every required role.
    if(draft.tier==='automations'||draft.overrides?.automation_enabled||draft.overrides?.scan_timeline_enabled){await save({draft,step:'provider'});return}
    await apply(draft);await save({draft,step:'harnesses'})
  }
  const harnessesChosen=async(draft:SetupDraft)=>{
    const registry=allHarnessesIncludingDisabled()
    const enabled=draft.harnesses||{}
    const patch:Record<string,unknown>={harness_enabled:Object.fromEntries(registry.filter(harness=>enabled[harness.name]!==!!harness.installed).map(harness=>[harness.name,!!enabled[harness.name]]))}
    if(draft.default_harness)patch.default_harness=draft.default_harness
    patch.default_backend=draft.default_harness||'shell'
    if(draft.tier!=='terminal')Object.assign(patch,fleetAccessChanges((draft.fleet_access||'default') as Parameters<typeof fleetAccessChanges>[0],registry.map(harness=>harness.name)))
    await api('PATCH','/api/config',patch)
    if(draft.scan_history)await api('POST','/api/history/scan')
    await save({draft,step:'projects'})
  }
  const finish=async(tour:boolean)=>{
    await save({status:'complete',step:'complete',tour_status:tour?'active':'deferred'})
    onDone();if(tour)onTour()
  }
  const dialog=useRef<HTMLElement>(null)
  useModalFocus(dialog,()=>void perform(()=>defer()),!busy&&state.step!=='experience'&&state.step!=='harnesses','onboarding')
  if(state.step==='experience'||state.step==='harnesses')return <HarnessSetup key={state.step} page={state.step} workflow={{draft:state.draft,onDraft:async(draft)=>{await save({draft})},onTier:tierChosen,onHarnesses:harnessesChosen,onDefer:defer}}/>
  return <div class="harness-setup-backdrop" role="dialog" aria-modal="true" aria-label="Set up swe-mux"><section ref={dialog} class="harness-setup onboarding-flow">
    <header><strong>SET UP::{state.step.toUpperCase()}</strong><button disabled={busy} onClick={()=>void perform(()=>defer())}>Continue later</button></header>
    <div class="harness-setup-body">
      {state.step==='existing'&&<><h2>Use your existing settings?</h2><p>swe-mux found saved preferences. Continue with them, review your experience tier, or start fresh.</p><button class="primary" disabled={busy} onClick={()=>void perform(()=>save({action:'reuse'}))}>Use existing settings</button><button disabled={busy} onClick={()=>void perform(()=>save({step:'experience',status:'active'}))}>Review setup</button><button disabled={busy} onClick={()=>setFreshConfirm(true)}>Start fresh…</button>{freshConfirm&&<div class="setup-fresh-confirm"><p>This resets global preferences and learning progress. A backup is saved first. Projects, project files, history, accounts, credentials, and connection addresses are retained. Some preferences apply after the next daemon reload.</p><button disabled={busy} onClick={()=>void perform(()=>save({action:'fresh'}))}>Back up preferences and start fresh</button><button onClick={()=>setFreshConfirm(false)}>Cancel</button></div>}</>}
      {state.step==='provider'&&<ProviderSetup onBusy={setBusy} onReady={async()=>{await apply(state.draft);await save({step:'harnesses',completed:[...new Set([...state.completed,'provider'])]})}} onLater={async()=>{await apply({...state.draft,tier:'deterministic',overrides:{}},'deterministic');await save({step:'harnesses'});onDone()}}/>}
      {state.step==='projects'&&<SetupProjects autoDiscover={state.draft.scan_history!==false} harnesses={Object.entries(state.draft.harnesses||{}).filter(([,enabled])=>enabled).map(([name])=>name)} onContinue={async()=>{await save({step:'desktop'});onDone()}}/>}
      {state.step==='desktop'&&<DesktopSetup onContinue={async(done)=>{await save({step:'finish',completed:done?[...new Set([...state.completed,'desktop'])]:state.completed})}}/>}
      {(state.step==='finish'||state.step==='complete')&&<><h2>Your workspace is ready</h2><p>Take a short tour of Projects, Run, tabs, splits, resources, Settings, and Help. Phone, voice, and the other first steps stay in Getting started above Usage in the sidebar.</p><button class="primary" disabled={busy} onClick={()=>void perform(()=>finish(true))}>Start the UI tour</button><button disabled={busy} onClick={()=>void perform(async()=>{await finish(false);onLaunch()})}>Launch my first session</button><button disabled={busy} onClick={()=>void perform(()=>finish(false))}>Explore at my own pace</button></>}
      {state.backup&&<p>Preferences backup: <code>{state.backup}</code></p>}
      {error&&<p role="alert">{error}</p>}
    </div>
  </section></div>
}
