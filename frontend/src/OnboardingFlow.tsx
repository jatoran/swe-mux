import { useEffect, useRef, useState } from 'preact/hooks'
import { useModalFocus } from './modalFocus'
import { api } from './api'
import { HarnessSetup, fleetAccessChanges } from './HarnessSetup'
import { allHarnessesIncludingDisabled } from './harnessRegistry'
import { ProviderSetup } from './ProviderSetup'
import { SetupProjects } from './SetupProjects'
import { SetupKeymap } from './SetupKeymap'
import { SetupPermissions, type TierAssignments } from './SetupPermissions'
import { DesktopSetup } from './DesktopSetup'
import { VoiceSetup } from './VoiceSetup'
import { ConnectPhone } from './ConnectPhone'
import { useSetupHarnesses } from './setupHarnesses'
import { useProjectDiscovery } from './setupDiscovery'
import { completeSetupModels, wantsSetupModels } from './setupActivation'
import { useSetupDraft, type OnboardingState, type SaveOnboarding, type SetupStep } from './onboarding'

type Props={state:OnboardingState;save:SaveOnboarding;onTour:()=>void;onLaunch:(project?:string,backend?:string)=>Promise<void>;onDone:()=>void;onCustomizeKeymap:()=>void}
const CORE=['experience','harnesses','projects','keymap']
const LABELS:Record<SetupStep,string>={existing:'Existing settings',experience:'Experience',harnesses:'Agents',projects:'Projects',keymap:'Keymap',permissions:'Automation permissions',provider:'Model provider',extras:'Optional extras',voice:'Voice',phone:'Phone',desktop:'Desktop',finish:'Ready',complete:'Ready'}
export function OnboardingFlow({state,save,onTour,onLaunch,onDone,onCustomizeKeymap}:Props) {
  const {draft,update,flush,error:draftError}=useSetupDraft(state,save)
  const detection=useSetupHarnesses()
  const enabled=Object.entries(draft.harnesses||{}).filter(([,value])=>value).map(([name])=>name)
  const discovery=useProjectDiscovery(state.step==='experience'||state.step==='existing'?[]:enabled)
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const [busy,setBusy]=useState(false)
  const [childBusy,setChildBusy]=useState(false)
  const [freshConfirm,setFreshConfirm]=useState(false)
  const locked=busy||childBusy
  const dialog=useRef<HTMLElement>(null)
  const currentState=useRef(state);currentState.current=state
  const perform=async(action:()=>Promise<void>)=>{setBusy(true);setError('');try{await action()}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}}
  const go=async(step:SetupStep)=>{await flush();await save({step,status:'active'});setError('')}
  const defer=async()=>{await flush();await save({status:'deferred'});onDone()}
  useModalFocus(dialog,()=>{if(!locked)void perform(defer)},state.step!=='phone','onboarding')
  useEffect(()=>{dialog.current?.querySelector<HTMLElement>('h2')?.focus()},[state.step])
  const noteRestart=(result:{restart_required?:string[]})=>{if(result.restart_required?.length)setNotice('Some instrumentation choices take effect after the next daemon reload. Existing sessions stay running.')}
  const chooseExperience=async()=>{
    await flush()
    const tier=draft.tier||'deterministic'
    // Apply the starting non-model layer once, before agent-specific choices exist.
    // Later provider setup grants model features without reapplying this preset.
    if(draft.applied_experience!==tier){
      const result=await api<{restart_required:string[]}>('POST','/api/experience-tier',{tier:tier==='automations'?'deterministic':tier},{timeoutMs:15000})
      noteRestart(result)
      update({tier,applied_experience:tier,model_features_pending:wantsSetupModels({...draft,tier})})
    }
    await go('harnesses');onDone()
  }
  const chooseHarnesses=async()=>{
    if(!draft.harnesses)throw new Error('Wait for detection or explicitly choose Shell only.')
    const known=detection.registry?.harnesses||allHarnessesIncludingDisabled()
    const chosen=known.filter(item=>draft.harnesses?.[item.name])
    const backend=chosen.some(item=>item.name===draft.default_harness)?draft.default_harness||'':''
    // Explicit all-off entries are necessary even if Shell was chosen before detection.
    // The static registry provides every name while detection is still unresolved.
    const harnessEnabled=Object.fromEntries(known.map(item=>[item.name,!!draft.harnesses?.[item.name]]))
    const patch={harness_enabled:harnessEnabled,default_harness:backend,default_backend:backend||'shell'}
    noteRestart(await api('PATCH','/api/config',patch,{timeoutMs:15000}))
    update({default_harness:backend})
    await go('projects');onDone()
  }
  const permissions=async()=>{
    const assignments=await api<TierAssignments>('GET','/api/experience-tiers',undefined,{timeoutMs:10000})
    const overrides=Object.fromEntries(Object.entries(draft.overrides||{}).filter(([key,value])=>!(['automation_enabled','scan_timeline_enabled'].includes(key)&&value)))
    const changes={...assignments.autonomy[draft.autonomy||'supervised'],...draft.autonomy_overrides,...overrides,...(draft.fleet_access&&draft.tier!=='terminal'?fleetAccessChanges(draft.fleet_access as Parameters<typeof fleetAccessChanges>[0],detection.registry?.harnesses.map(item=>item.name)||[]):{})}
    noteRestart(await api('PATCH','/api/config',changes,{timeoutMs:15000}))
    update({model_features_pending:wantsSetupModels(draft),provider_return:'permissions'})
    await flush()
    await save(current=>({completed:[...new Set([...current.completed,'permissions'])],step:wantsSetupModels(draft)?'provider':'extras'}))
    onDone()
  }
  const leave=async({launch=false,tour=false,core=false}={})=>{
    update({core_complete:true});await flush()
    // Use the canonical draft after pending field saves, including the last added Project.
    const current=await save(s=>({draft:{...s.draft,core_complete:true}}))
    if(launch&&!current.completed.includes('session'))await onLaunch(current.draft.project_id,current.draft.default_harness||'shell')
    await save({status:core?'deferred':'complete',step:core?(draft.tier==='terminal'?'extras':'permissions'):'complete',tour_status:tour?'active':'deferred'})
    onDone();if(tour)onTour()
  }
  const nextAfterProjects=async()=>{await go('keymap');onDone()}
  const back=():SetupStep=>{
    const index=CORE.indexOf(state.step)
    if(index>0)return CORE[index-1] as SetupStep
    if(state.step==='provider')return draft.provider_return||'permissions'
    if(state.step==='permissions')return 'keymap'
    if(state.step==='extras')return draft.tier==='terminal'?'keymap':'permissions'
    if(['desktop','voice','phone'].includes(state.step))return 'extras'
    return 'experience'
  }
  if(state.step==='phone')return <ConnectPhone onClose={()=>void perform(()=>go('extras'))} onComplete={()=>void perform(async()=>{await save(current=>({completed:[...new Set([...current.completed,'phone'])]}));await go('extras')})}/>
  const coreIndex=CORE.indexOf(state.step)
  return <div class="harness-setup-backdrop" role="dialog" aria-modal="true" aria-label="Set up swe-mux"><section ref={dialog} class="harness-setup onboarding-flow">
    <header><div>{state.step!=='experience'&&state.step!=='existing'&&<button class="link" disabled={locked} onClick={()=>void perform(()=>go(back()))}>← Back</button>}<strong>{LABELS[state.step]}</strong></div><button disabled={locked} onClick={()=>void perform(defer)}>Continue later</button></header>
    <div class="setup-progress" aria-label="Setup progress">{coreIndex>=0?<><span>{coreIndex+1} of 4</span><span>{CORE.map((step,index)=><i key={step} class={index<=coreIndex?'active':''}/>)}</span></>:<span>{state.step==='existing'?'Your saved preferences':'Optional setup'}</span>}</div>
    <div class="harness-setup-body"><fieldset class="setup-fields" disabled={busy}>
      {state.step==='existing'&&<section><h2>Use your existing settings?</h2><p class="setup-lede">Keep your preferences or choose a new starting point.</p><div class="setup-inline-actions"><button class="primary" disabled={locked} onClick={()=>void perform(async()=>{await save({action:'reuse'})})}>Keep settings</button><button disabled={locked} onClick={()=>void perform(()=>go('experience'))}>Review setup</button><button disabled={locked} onClick={()=>setFreshConfirm(true)}>Start fresh…</button></div>{freshConfirm&&<div class="setup-fresh-confirm"><p>Back up and reset global preferences. Keep Projects, files, history, accounts, and connection settings.</p><button disabled={locked} onClick={()=>void perform(async()=>{const next=await save({action:'fresh'});noteRestart(next);onDone()})}>Back up and start fresh</button><button onClick={()=>setFreshConfirm(false)}>Cancel</button></div>}</section>}
      {(state.step==='experience'||state.step==='harnesses')&&<HarnessSetup page={state.step} draft={draft} onChange={update} registry={detection.registry} loading={detection.loading} error={detection.error} onRetry={detection.retry}/>}
      {state.step==='projects'&&<SetupProjects draft={draft} onChange={update} discovery={discovery} onContinue={nextAfterProjects} onBusy={setChildBusy} onProjectsChanged={onDone}/>}
      {state.step==='keymap'&&<SetupKeymap draft={draft} onChange={update} onBusy={setChildBusy} onStart={()=>leave({launch:true,core:true})} onContinue={async()=>{update({core_complete:true});await go(draft.tier==='terminal'?'extras':'permissions')}} onCustomize={async()=>{await flush();onCustomizeKeymap()}}/>}
      {state.step==='permissions'&&<SetupPermissions draft={draft} onChange={update}/>}
      {state.step==='provider'&&<ProviderSetup savedDraft={draft.provider} onDraft={provider=>update({provider})} onBusy={setChildBusy} onReady={async()=>{await flush();await completeSetupModels(currentState.current,save);await go(draft.provider_return==='voice'?'voice':'extras');onDone()}} onLater={async()=>{await go(draft.provider_return==='voice'?'voice':'extras');onDone()}}/>}
      {state.step==='voice'&&<VoiceSetup embedded providerDraft={draft.provider} onProviderDraft={provider=>update({provider})} draft={draft.voice} onDraft={voice=>update({voice})} onBusy={setChildBusy} onClose={()=>void perform(()=>go('extras'))} onComplete={async()=>{await flush();await save(current=>({completed:[...new Set([...current.completed,'voice'])],step:'extras'}));onDone()}}/>}
      {state.step==='desktop'&&<DesktopSetup onContinue={async(done)=>{await save(current=>({step:'extras',completed:done?[...new Set([...current.completed,'desktop'])]:current.completed}));onDone()}}/>}
      {state.step==='extras'&&<section><h2>Anything else before you start?</h2><p class="setup-lede">Optional guides. Everything stays available in Getting started.</p><div class="setup-cards">{([{id:'voice',title:'Voice',note:'Read replies, dictate, or add AI assistance.'},{id:'desktop',title:'Desktop',note:'Shortcuts and optional start at sign-in.'},{id:'phone',title:'Phone',note:'Connect privately through Tailscale.'}] as const).map(item=><button class="setup-card" key={item.id} disabled={locked} onClick={()=>void perform(()=>go(item.id))}><span><strong>{item.title}</strong><small>{item.note}</small><small>{state.completed.includes(item.id)?'Completed · Open':'Open guide →'}</small></span></button>)}</div>{draft.model_features_pending&&<p class="setup-hint">Your smart features are waiting for a model provider. <button onClick={()=>void perform(async()=>{update({provider_return:'extras'});await go('provider')})}>Set up models</button></p>}<footer class="setup-step-actions"><button disabled={locked} onClick={()=>void perform(()=>leave({tour:true}))}>Take a short tour</button><button class="primary" disabled={locked} onClick={()=>void perform(()=>leave({launch:true}))}>Start working</button></footer></section>}
      {(state.step==='finish'||state.step==='complete')&&<section><h2>Your workspace is ready.</h2><p class="setup-lede">Start a session, or take the optional tour.</p><div class="setup-inline-actions"><button class="primary" disabled={locked} onClick={()=>void perform(()=>leave({launch:true}))}>Start working</button><button disabled={locked} onClick={()=>void perform(()=>leave({tour:true}))}>Take a short tour</button><button disabled={locked} onClick={()=>void perform(()=>leave())}>Explore the workspace</button></div></section>}
      {state.backup&&<p class="setup-hint">Backup: <code>{state.backup}</code></p>}
      {notice&&<p class="setup-hint" role="status">{notice}</p>}
      {(error||draftError)&&<p role="alert">{error||draftError}</p>}
      {(state.step==='experience'||state.step==='harnesses'||state.step==='permissions')&&<footer class="setup-step-actions"><button class="primary" disabled={locked||(state.step==='harnesses'&&!draft.harnesses)} onClick={()=>void perform(state.step==='experience'?chooseExperience:state.step==='harnesses'?chooseHarnesses:permissions)}>Continue</button></footer>}
    </fieldset></div>
  </section></div>
}
