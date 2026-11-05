import { rich, t } from './localization/index.ts'
import { LanguagePicker } from './LanguagePicker'
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
const LABELS:Record<SetupStep,string>={get existing() { return t("OnboardingFlow.e5c0e40321") },get experience() { return t("OnboardingFlow.8eab0f09df") },get harnesses() { return t("OnboardingFlow.279b44d2ab") },get projects() { return t("OnboardingFlow.04e2a9728a") },get keymap() { return t("OnboardingFlow.c228df04b8") },get permissions() { return t("OnboardingFlow.5efe62f864") },get provider() { return t("OnboardingFlow.ea70384c46") },get extras() { return t("OnboardingFlow.da4a3b97f1") },get voice() { return t("OnboardingFlow.87bf2bc085") },get phone() { return t("OnboardingFlow.63dceb8800") },get desktop() { return t("OnboardingFlow.9bd88f2485") },get finish() { return t("OnboardingFlow.5fa7aac537") },get complete() { return t("OnboardingFlow.5fa7aac537") }}
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
  const noteRestart=(result:{restart_required?:string[]})=>{if(result.restart_required?.length)setNotice(t("OnboardingFlow.e527ae4d7f"))}
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
    if(!draft.harnesses)throw new Error(t("OnboardingFlow.61bb703040"))
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
  return <div class="harness-setup-backdrop" role="dialog" aria-modal="true" aria-label={t("OnboardingFlow.df1cb173d4")}><section ref={dialog} class="harness-setup onboarding-flow">
    <header><div>{state.step!=='experience'&&state.step!=='existing' && <button class="link" disabled={locked} onClick={()=>void perform(()=>go(back()))}>{t("OnboardingFlow.aceb696a04")}</button>}<strong>{LABELS[state.step]}</strong></div><button disabled={locked} onClick={()=>void perform(defer)}>{t("OnboardingFlow.af0d84c88a")}</button></header>
    <LanguagePicker />
    <div class="setup-progress" aria-label={t("OnboardingFlow.e9b4495ace")}>{coreIndex>=0 ? <><span>{rich("OnboardingFlow.887b038e72", { value1: coreIndex+1 })}</span><span>{CORE.map((step,index)=><i key={step} class={index<=coreIndex?'active':''}/>)}</span></> : <span>{state.step==='existing' ? t("OnboardingFlow.dcec3705ce") : t("OnboardingFlow.0a91367752")}</span>}</div>
    <div class="harness-setup-body"><fieldset class="setup-fields" disabled={busy}>
      {state.step==='existing' && <section><h2 data-heading-message="OnboardingFlow.dc88de2dd6">{t("OnboardingFlow.dc88de2dd6")}</h2><p class="setup-lede">{t("OnboardingFlow.a1e2ece6e0")}</p><div class="setup-inline-actions"><button class="primary" disabled={locked} onClick={()=>void perform(async()=>{await save({action:'reuse'})})}>{t("OnboardingFlow.0e8ac53f76")}</button><button disabled={locked} onClick={()=>void perform(()=>go('experience'))}>{t("OnboardingFlow.5a16e3d43a")}</button><button disabled={locked} onClick={()=>setFreshConfirm(true)}>{t("OnboardingFlow.40d0d7289f")}</button></div>{freshConfirm && <div class="setup-fresh-confirm"><p>{t("OnboardingFlow.4b8af0ca75")}</p><button disabled={locked} onClick={()=>void perform(async()=>{const next=await save({action:'fresh'});noteRestart(next);onDone()})}>{t("OnboardingFlow.39fe0989a1")}</button><button onClick={()=>setFreshConfirm(false)}>{t("OnboardingFlow.19766ed6cc")}</button></div>}</section>}
      {(state.step==='experience'||state.step==='harnesses') && <HarnessSetup page={state.step} draft={draft} onChange={update} registry={detection.registry} loading={detection.loading} error={detection.error} onRetry={detection.retry}/>}
      {state.step==='projects' && <SetupProjects draft={draft} onChange={update} discovery={discovery} onContinue={nextAfterProjects} onBusy={setChildBusy} onProjectsChanged={onDone}/>}
      {state.step==='keymap' && <SetupKeymap draft={draft} onChange={update} onBusy={setChildBusy} onStart={()=>leave({launch:true,core:true})} onContinue={async()=>{update({core_complete:true});await go(draft.tier==='terminal'?'extras':'permissions')}} onCustomize={async()=>{await flush();onCustomizeKeymap()}}/>}
      {state.step==='permissions' && <SetupPermissions draft={draft} onChange={update}/>}
      {state.step==='provider' && <ProviderSetup savedDraft={draft.provider} onDraft={provider=>update({provider})} onBusy={setChildBusy} onReady={async()=>{await flush();await completeSetupModels(currentState.current,save);await go(draft.provider_return==='voice'?'voice':'extras');onDone()}} onLater={async()=>{await go(draft.provider_return==='voice'?'voice':'extras');onDone()}}/>}
      {state.step==='voice' && <VoiceSetup embedded providerDraft={draft.provider} onProviderDraft={provider=>update({provider})} draft={draft.voice} onDraft={voice=>update({voice})} onBusy={setChildBusy} onClose={()=>void perform(()=>go('extras'))} onComplete={async()=>{await flush();await save(current=>({completed:[...new Set([...current.completed,'voice'])],step:'extras'}));onDone()}}/>}
      {state.step==='desktop' && <DesktopSetup onContinue={async(done)=>{await save(current=>({step:'extras',completed:done?[...new Set([...current.completed,'desktop'])]:current.completed}));onDone()}}/>}
      {state.step==='extras' && <section><h2 data-heading-message="OnboardingFlow.e1116677c0">{t("OnboardingFlow.e1116677c0")}</h2><p class="setup-lede">{t("OnboardingFlow.866b157a68")}</p><div class="setup-cards">{([{id:'voice',get title() { return t("OnboardingFlow.87bf2bc085") },get note() { return t("OnboardingFlow.ec714d66cc") }},{id:'desktop',get title() { return t("OnboardingFlow.9bd88f2485") },get note() { return t("OnboardingFlow.7dd49fc165") }},{id:'phone',get title() { return t("OnboardingFlow.63dceb8800") },get note() { return t("OnboardingFlow.963724a5ed") }}] as const).map(item=><button class="setup-card" key={item.id} disabled={locked} onClick={()=>void perform(()=>go(item.id))}><span><strong>{item.title}</strong><small>{item.note}</small><small>{state.completed.includes(item.id) ? t("OnboardingFlow.0c19dea64c") : t("OnboardingFlow.ab61723e9b")}</small></span></button>)}</div>{draft.model_features_pending && <p class="setup-hint">{rich("OnboardingFlow.af2ed2472a", { value1: <button onClick={()=>void perform(async()=>{update({provider_return:'extras'});await go('provider')})}>{t("OnboardingFlow.28bc47218d")}</button> })}</p>}<footer class="setup-step-actions"><button disabled={locked} onClick={()=>void perform(()=>leave({tour:true}))}>{t("OnboardingFlow.e2c028ba36")}</button><button class="primary" disabled={locked} onClick={()=>void perform(()=>leave({launch:true}))}>{t("OnboardingFlow.597bf9ec78")}</button></footer></section>}
      {(state.step==='finish'||state.step==='complete') && <section><h2 data-heading-message="OnboardingFlow.272c31f99e">{t("OnboardingFlow.272c31f99e")}</h2><p class="setup-lede">{t("OnboardingFlow.11cee33310")}</p><div class="setup-inline-actions"><button class="primary" disabled={locked} onClick={()=>void perform(()=>leave({launch:true}))}>{t("OnboardingFlow.597bf9ec78")}</button><button disabled={locked} onClick={()=>void perform(()=>leave({tour:true}))}>{t("OnboardingFlow.e2c028ba36")}</button><button disabled={locked} onClick={()=>void perform(()=>leave())}>{t("OnboardingFlow.f253576d61")}</button></div></section>}
      {state.backup && <p class="setup-hint">{rich("OnboardingFlow.787dbd4132", { value1: <code>{state.backup}</code> })}</p>}
      {notice && <p class="setup-hint" role="status">{notice}</p>}
      {(error||draftError) && <p role="alert">{error || draftError}</p>}
      {(state.step==='experience'||state.step==='harnesses'||state.step==='permissions') && <footer class="setup-step-actions"><button class="primary" disabled={locked||(state.step==='harnesses'&&!draft.harnesses)} onClick={()=>void perform(state.step==='experience'?chooseExperience:state.step==='harnesses'?chooseHarnesses:permissions)}>{t("OnboardingFlow.31fbef1625")}</button></footer>}
    </fieldset></div>
  </section></div>
}
