import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { ModelRoutingSummary } from './ModelRoutingSummary'
import { customProviderOverride, type ModelRoutingConfig } from './modelRouting'
import { forgetLlmProvider, LLM_PROVIDER_CHANGED, verifyLlmProvider, type ProviderStatusPayload } from './llmProvider'
import { BudgetControl } from './BudgetControl'
import type { Budget } from './types'

export type ProviderConfiguration = ModelRoutingConfig & {
  llm_provider:string;custom_llm_base_url:string;custom_llm_model:string;custom_llm_catalog_url:string
  automation_daily_budget:Budget;automation_rule_daily_budget:Budget
}

/** Shared with Settings: credentials have no place in the config draft. */
export async function providerKeyOperation(operation:'test'|'set'|'clear',provider:string,key:string) {
  const result=await api('POST','/api/automation/provider/key',{operation,provider,key:key||undefined,test:true})
  forgetLlmProvider()
  return result
}

export function ProviderConnectionFields({draft,onChange,apiKey,onKeyChange,configured}:{draft:ProviderConfiguration;onChange:(key:keyof ProviderConfiguration,value:string)=>void;apiKey:string;onKeyChange:(value:string)=>void;configured:boolean}) {
  return <div class="setup-provider-fields">
    <label data-setting="llm_provider">Model provider<select aria-label="Model provider" value={draft.llm_provider} onChange={event=>onChange('llm_provider',event.currentTarget.value)}><option value="openrouter">OpenRouter</option><option value="custom">Compatible local or hosted endpoint</option></select></label>
    {draft.llm_provider==='custom'&&<>
      <label data-setting="custom_llm_base_url">Base URL<input type="url" value={draft.custom_llm_base_url} placeholder="http://127.0.0.1:11434/v1" onInput={event=>onChange('custom_llm_base_url',event.currentTarget.value)}/></label>
      <label data-setting="custom_llm_model">Single model (when there is no catalog)<input value={draft.custom_llm_model} onInput={event=>onChange('custom_llm_model',event.currentTarget.value)}/></label>
      <label data-setting="custom_llm_catalog_url">Model catalog URL (optional)<input value={draft.custom_llm_catalog_url} placeholder="Base URL + /models" onInput={event=>onChange('custom_llm_catalog_url',event.currentTarget.value)}/></label>
    </>}
    <label>API key{draft.llm_provider==='custom'?' (optional for local servers)':''}<input type="password" autoComplete="off" value={apiKey} placeholder={configured?'A key is already stored. Enter only to replace it.':'Stored in the platform credential store'} onInput={event=>onKeyChange(event.currentTarget.value)}/></label>
  </div>
}

export function ProviderSetup({onReady,onLater,onBusy}:{onReady:()=>Promise<void>;onLater:()=>Promise<void>;onBusy?:(busy:boolean)=>void}) {
  const [draft,setDraft]=useState<ProviderConfiguration|null>(null)
  const [status,setStatus]=useState<ProviderStatusPayload|null>(null)
  const [key,setKey]=useState('')
  const [busy,setBusy]=useState(false)
  const [connected,setConnected]=useState(false)
  const [error,setError]=useState('')
  const [message,setMessage]=useState('')
  const alive=useRef(true)
  useEffect(()=>{onBusy?.(busy)},[busy])
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;onBusy?.(false)}},[])
  const reload=()=>api<ProviderStatusPayload>('GET','/api/automation/provider').then(value=>{setStatus(value);return value})
  useEffect(()=>{
    let live=true
    void Promise.all([api<ProviderConfiguration>('GET','/api/config'),reload()]).then(([config])=>{
      if(live)setDraft(config)
    }).catch(cause=>{if(live)setError(cause.message)})
    return()=>{live=false}
  },[])
  const change=(field:keyof ProviderConfiguration,value:string|Budget)=>{setDraft(current=>current?{...current,[field]:value}:current);setError('')}
  const entry=status?.providers.find(item=>item.id===draft?.llm_provider)
  const hasCatalog=!!entry&&entry.verification.capabilities.catalog!=='none'
  const override=draft?customProviderOverride(draft,hasCatalog):null
  const connection=async()=>{
    if(!draft)return
    setBusy(true);setError('');setMessage('Saving and testing the endpoint…')
    try{
      await api('PATCH','/api/config',{llm_provider:draft.llm_provider,custom_llm_base_url:draft.custom_llm_base_url,custom_llm_model:draft.custom_llm_model,custom_llm_catalog_url:draft.custom_llm_catalog_url})
      if(key){await providerKeyOperation('set',draft.llm_provider,key);setKey('')}
      const verified=await verifyLlmProvider(draft.llm_provider)
      if(!verified.ok)throw new Error(verified.error||'The endpoint check failed.')
      const next=await reload()
      const catalog=next.models.models
      const present=(id:string)=>catalog.some(model=>model.id===id)
      setDraft(current=>current?{...current,
        openrouter_cheap_model:current.openrouter_cheap_model||(present(current.scan_timeline_model)?current.scan_timeline_model:''),
        openrouter_standard_model:current.openrouter_standard_model||(present(current.assistant_model)?current.assistant_model:''),
      }:current)
      setConnected(true);setMessage('Endpoint verified. Review the models and limits, then approve and test their required capabilities.')
    }catch(cause){setError((cause as Error).message);setMessage('')}finally{setBusy(false)}
  }
  const approve=async()=>{
    if(!draft)return
    setBusy(true);setError('');setMessage('Testing structured output and assistant tool calling…')
    try{
      if(!override&&(!draft.openrouter_cheap_model||!draft.openrouter_standard_model))throw new Error('Choose both the cheap and standard models.')
      const {openrouter_cheap_model,openrouter_standard_model,scan_timeline_model,assistant_model,attention_narration_model,tts_summary_model,project_card_model,automation_daily_budget,automation_rule_daily_budget}=draft
      await api('PATCH','/api/config',{openrouter_cheap_model,openrouter_standard_model,scan_timeline_model,assistant_model,attention_narration_model,tts_summary_model,project_card_model,automation_daily_budget,automation_rule_daily_budget})
      await api('POST','/api/onboarding/models/verify',{})
      forgetLlmProvider();window.dispatchEvent(new Event(LLM_PROVIDER_CHANGED))
      if(alive.current)await onReady()
    }catch(cause){setError((cause as Error).message);setMessage('')}finally{setBusy(false)}
  }
  return <section class="setup-provider"><h2>Set up models for Automations</h2><p>Harness logins and model API access are separate. Configure OpenRouter or a compatible endpoint here. Local servers may not need a key. Testing the endpoint sends one small model call, which may be billed.</p>
    {draft&&<>
      {!connected&&<ProviderConnectionFields draft={draft} onChange={(field,value)=>change(field,value)} apiKey={key} onKeyChange={setKey} configured={!!entry?.secret.configured}/>}
      {!connected?<button class="primary" disabled={busy} onClick={()=>void connection()}>Save and test endpoint</button>:<>
        <button class="link" disabled={busy} onClick={()=>setConnected(false)}>Change endpoint</button>
        {override&&<p>This endpoint serves one model. All roles use <strong>{override.model}</strong>.</p>}
        <ModelRoutingSummary draft={draft} catalog={status?.models.models||[]} override={override} catalogKnown={hasCatalog} onChange={change} roles="primary"/>
        {!override&&<details><summary>Other feature overrides</summary><ModelRoutingSummary draft={draft} catalog={status?.models.models||[]} catalogKnown={hasCatalog} onChange={change} roles="overrides"/></details>}
        <BudgetControl name="automation_daily_budget" label="Daily automation limit" value={draft.automation_daily_budget} onChange={value=>change('automation_daily_budget',value)} reportsCost={entry?.readiness.reports_cost}/>
        <BudgetControl name="automation_rule_daily_budget" label="Daily limit per rule" value={draft.automation_rule_daily_budget} onChange={value=>change('automation_rule_daily_budget',value)} reportsCost={entry?.readiness.reports_cost}/>
        <p>Approval sends up to seven small model test calls, which may be billed. No test tool is executed. Automations are enabled only after these checks succeed.</p>
        <button class="primary" disabled={busy} onClick={()=>void approve()}>Approve models and enable Automations</button>
      </>}
    </>}
    {message&&<p role="status">{message}</p>}{error&&<p role="alert">{error}</p>}
    <button disabled={busy} onClick={()=>void onLater().catch(cause=>setError(cause.message))}>Continue with Deterministic</button>
    <p><small>Model-backed features stay off when you defer this step. You can finish it from Getting started.</small></p>
  </section>
}
