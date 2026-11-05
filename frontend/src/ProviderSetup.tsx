import { locale as uiLocale } from './localization/index.ts'
import { rich, t } from './localization/index.ts'
import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { SetupModelSummary } from './SetupModelSummary'
import { customProviderOverride, type ModelRoutingConfig } from './modelRouting'
import { forgetLlmProvider, LLM_PROVIDER_CHANGED, verifyLlmProvider, type ProviderStatusPayload } from './llmProvider'
import { BudgetControl } from './BudgetControl'
import type { Budget } from './types'
import type { ProviderSetupDraft } from './onboarding'

export type ProviderConfiguration = ModelRoutingConfig & {
  revision:number
  llm_provider:string;custom_llm_base_url:string;custom_llm_model:string;custom_llm_catalog_url:string
  automation_daily_budget:Budget;automation_rule_daily_budget:Budget
}

/** Shared with Settings: credentials have no place in the config draft. */
export async function providerKeyOperation(operation:'test'|'set'|'clear',provider:string,key:string) {
  const result=await api('POST','/api/automation/provider/key',{operation,provider,key:key||undefined,test:true},{timeoutMs:60000})
  forgetLlmProvider()
  return result
}

export function ProviderConnectionFields({draft,onChange,apiKey,onKeyChange,configured}:{draft:ProviderConfiguration;onChange:(key:keyof ProviderConfiguration,value:string)=>void;apiKey:string;onKeyChange:(value:string)=>void;configured:boolean}) {
  return <div class="setup-provider-fields">
    <label data-setting="llm_provider">{rich("ProviderSetup.ae3d807eb2", { value1: <select aria-label={t("ProviderSetup.ea70384c46")} value={draft.llm_provider} onChange={event=>onChange('llm_provider',event.currentTarget.value)}><option value="openrouter">{t("heading.eb70c3bc14")}</option><option value="custom">{t("ProviderSetup.f4d4377c78")}</option></select> })}</label>
    {draft.llm_provider==='custom' && <>
      <label data-setting="custom_llm_base_url">{rich("ProviderSetup.47f5759ea4", { value1: <input type="url" value={draft.custom_llm_base_url} placeholder={t("ProviderSetup.9b514b1a65")} onInput={event=>onChange('custom_llm_base_url',event.currentTarget.value)}/> })}</label>
      <label data-setting="custom_llm_model">{rich("ProviderSetup.c4048704c1", { value1: <input value={draft.custom_llm_model} onInput={event=>onChange('custom_llm_model',event.currentTarget.value)}/> })}</label>
      <label data-setting="custom_llm_catalog_url">{rich("ProviderSetup.678fff6f44", { value1: <input value={draft.custom_llm_catalog_url} placeholder={t("ProviderSetup.15ced3cf6c")} onInput={event=>onChange('custom_llm_catalog_url',event.currentTarget.value)}/> })}</label>
    </>}
    <label>{rich("ProviderSetup.ca6a3cabf1", { value1: (draft.llm_provider==='custom') ? 'yes' : 'no', value2: <input type="password" autoComplete="off" value={apiKey} placeholder={configured ? t("ProviderSetup.333e710475") : t("ProviderSetup.6f97b957fa")} onInput={event=>onKeyChange(event.currentTarget.value)}/> })}</label>
  </div>
}

export function ProviderSetup({onReady,onLater,onBusy,savedDraft,onDraft}:{onReady:()=>Promise<void>;onLater:()=>Promise<void>;onBusy?:(busy:boolean)=>void;savedDraft?:ProviderSetupDraft;onDraft?:(draft:ProviderSetupDraft)=>void}) {
  const [draft,setDraft]=useState<ProviderConfiguration|null>(null)
  const [status,setStatus]=useState<ProviderStatusPayload|null>(null)
  const [key,setKey]=useState('')
  const [busy,setBusy]=useState(false)
  const [connected,setConnected]=useState(false)
  const [error,setError]=useState('')
  const [message,setMessage]=useState('')
  const alive=useRef(true)
  const edits=useRef<ProviderSetupDraft>(savedDraft||{})
  const budgetChanged=useRef(savedDraft?.automation_daily_budget!==undefined)
  useEffect(()=>{onBusy?.(busy)},[busy])
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;onBusy?.(false)}},[])
  const load=async()=>{
    const [config,provider]=await Promise.all([
      api<ProviderConfiguration>('GET','/api/config',undefined,{timeoutMs:10000}),
      api<ProviderStatusPayload>('GET','/api/automation/provider',undefined,{timeoutMs:15000}),
    ])
    if(!alive.current)return
    const present=(id:string)=>provider.models.models.some(model=>model.id===id)
    const proposed={...config,openrouter_cheap_model:config.openrouter_cheap_model||(present(config.scan_timeline_model)?config.scan_timeline_model:''),openrouter_standard_model:config.openrouter_standard_model||(present(config.assistant_model)?config.assistant_model:''),...edits.current}
    setDraft(proposed)
    const unchanged=['llm_provider','custom_llm_base_url','custom_llm_model','custom_llm_catalog_url'].every(field=>proposed[field as keyof ProviderConfiguration]===config[field as keyof ProviderConfiguration])
    setStatus(provider);setConnected(unchanged&&!!provider.activation?.ready);setError('')
  }
  useEffect(()=>{void load().catch(cause=>setError(cause.message))},[])
  const change=(field:keyof ProviderSetupDraft,value:string|Budget)=>{edits.current={...edits.current,[field]:value};onDraft?.(edits.current);setDraft(current=>current?{...current,[field]:value}:current);setError('')}
  const entry=status?.providers.find(item=>item.id===draft?.llm_provider)
  const hasCatalog=!!entry&&entry.verification.capabilities.catalog!=='none'
  const override=draft?customProviderOverride(draft,hasCatalog):null
  const connection=async()=>{
    if(!draft)return
    setBusy(true);setError('');setMessage(t("ProviderSetup.33f8dde16b"))
    try{
      await api('PATCH','/api/config',{llm_provider:draft.llm_provider,custom_llm_base_url:draft.custom_llm_base_url,custom_llm_model:draft.custom_llm_model,custom_llm_catalog_url:draft.custom_llm_catalog_url},{timeoutMs:15000})
      if(key){await providerKeyOperation('set',draft.llm_provider,key);setKey('')}
      const verified=await verifyLlmProvider(draft.llm_provider)
      if(!verified.ok)throw new Error(verified.error||t("ProviderSetup.3661445f1b"))
      await load()
      setConnected(true);setMessage(t("ProviderSetup.d098f66842"))
    }catch(cause){setError((cause as Error).message);setMessage('')}finally{setBusy(false)}
  }
  const approve=async()=>{
    if(!draft)return
    setBusy(true);setError('');setMessage(t("ProviderSetup.0e2ebe8bdf"))
    try{
      const current=await api<ProviderConfiguration>('GET','/api/config',undefined,{timeoutMs:10000})
      for(const field of ['llm_provider','custom_llm_base_url','custom_llm_model','custom_llm_catalog_url'] as const){
        if(current[field]!==draft[field])throw new Error(t("ProviderSetup.feedf2d863"))
      }
      if(!override){
        if(!draft.openrouter_cheap_model||!draft.openrouter_standard_model)throw new Error(t("ProviderSetup.0e3a2b162f"))
        await api('POST','/api/onboarding/models/configure',{revision:current.revision,cheap:draft.openrouter_cheap_model,regular:draft.openrouter_standard_model},{timeoutMs:15000})
      }
      if(budgetChanged.current)await api('PATCH','/api/config',{automation_daily_budget:draft.automation_daily_budget},{timeoutMs:15000})
      await api('POST','/api/onboarding/models/verify',{}, {timeoutMs:240000})
      edits.current={};onDraft?.({});budgetChanged.current=false
      forgetLlmProvider();window.dispatchEvent(new Event(LLM_PROVIDER_CHANGED))
      if(alive.current)await onReady()
    }catch(cause){setError((cause as Error).message);setMessage('')}finally{setBusy(false)}
  }
  const budget=draft?.automation_daily_budget
  const limit=budget?[budget.mode!=='tokens'&&budget.usd!==null?`$${budget.usd}`:null,budget.mode!=='usd'&&budget.tokens!==null?`${budget.tokens.toLocaleString(uiLocale.value)} tokens`:null].filter(Boolean).join(' or '):''
  return <section class="setup-provider"><h2 data-heading-message="ProviderSetup.8e85d4c120">{t("ProviderSetup.8e85d4c120")}</h2><p class="setup-lede">{t("ProviderSetup.6370a85bef")}</p><fieldset class="setup-fields" disabled={busy}>
    {draft ? <>
      {!connected ? <><ProviderConnectionFields draft={draft} onChange={(field,value)=>change(field as keyof ProviderSetupDraft,value)} apiKey={key} onKeyChange={setKey} configured={!!entry?.secret.configured}/><p class="setup-hint">{t("ProviderSetup.0786f315c4")}</p><button class="primary" disabled={busy} onClick={()=>void connection()}>{t("ProviderSetup.942efbb5b7")}</button></> : <>
        <div class="setup-inline-actions"><span>{t("ProviderSetup.3ff4601037")}</span><button class="link" disabled={busy} onClick={()=>setConnected(false)}>{t("ProviderSetup.c8d2a94468")}</button></div>
        <SetupModelSummary draft={draft} catalog={status?.models.models||[]} override={override} onChange={change}/>
        {override && <p class="setup-hint">{t("ProviderSetup.c0a2fe436a")}</p>}
        <details><summary>{rich("ProviderSetup.020acfea82", { value1: limit || t("ProviderSetup.b53557b24e") })}</summary><BudgetControl name="automation_daily_budget" label={t("ProviderSetup.1ad57ba524")} value={draft.automation_daily_budget} onChange={value=>{budgetChanged.current=true;change('automation_daily_budget',value)}} reportsCost={entry?.readiness.reports_cost}/></details>
        {entry?.readiness.reports_cost===false && <p class="setup-hint">{t("ProviderSetup.740ca70830")}</p>}
        <p class="setup-hint">{t("ProviderSetup.7040a99afb")}</p>
        <button class="primary" disabled={busy} onClick={()=>void approve()}>{t("ProviderSetup.15f02c914e")}</button>
      </>}
    </> : !error && <p role="status">{t("ProviderSetup.7773992997")}</p>}
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error} <button disabled={busy} onClick={()=>void load().catch(cause=>setError(cause.message))}>{t("ProviderSetup.5037c4f222")}</button></p>}
    <footer class="setup-step-actions"><button disabled={busy} onClick={()=>void onLater().catch(cause=>setError(cause.message))}>{t("ProviderSetup.eb5e104944")}</button></footer>
  </fieldset></section>
}
