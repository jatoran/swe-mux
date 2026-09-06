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
    <label data-setting="llm_provider">Model provider<select aria-label="Model provider" value={draft.llm_provider} onChange={event=>onChange('llm_provider',event.currentTarget.value)}><option value="openrouter">OpenRouter</option><option value="custom">Compatible local or hosted endpoint</option></select></label>
    {draft.llm_provider==='custom'&&<>
      <label data-setting="custom_llm_base_url">Base URL<input type="url" value={draft.custom_llm_base_url} placeholder="http://127.0.0.1:11434/v1" onInput={event=>onChange('custom_llm_base_url',event.currentTarget.value)}/></label>
      <label data-setting="custom_llm_model">Single model (when there is no catalog)<input value={draft.custom_llm_model} onInput={event=>onChange('custom_llm_model',event.currentTarget.value)}/></label>
      <label data-setting="custom_llm_catalog_url">Model catalog URL (optional)<input value={draft.custom_llm_catalog_url} placeholder="Base URL + /models" onInput={event=>onChange('custom_llm_catalog_url',event.currentTarget.value)}/></label>
    </>}
    <label>API key{draft.llm_provider==='custom'?' (optional for local servers)':''}<input type="password" autoComplete="off" value={apiKey} placeholder={configured?'A key is already stored. Enter only to replace it.':'Stored in the platform credential store'} onInput={event=>onKeyChange(event.currentTarget.value)}/></label>
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
    setBusy(true);setError('');setMessage('Checking the connection…')
    try{
      await api('PATCH','/api/config',{llm_provider:draft.llm_provider,custom_llm_base_url:draft.custom_llm_base_url,custom_llm_model:draft.custom_llm_model,custom_llm_catalog_url:draft.custom_llm_catalog_url},{timeoutMs:15000})
      if(key){await providerKeyOperation('set',draft.llm_provider,key);setKey('')}
      const verified=await verifyLlmProvider(draft.llm_provider)
      if(!verified.ok)throw new Error(verified.error||'The endpoint check failed.')
      await load()
      setConnected(true);setMessage('Connected. These are your starting models.')
    }catch(cause){setError((cause as Error).message);setMessage('')}finally{setBusy(false)}
  }
  const approve=async()=>{
    if(!draft)return
    setBusy(true);setError('');setMessage('Checking model capabilities…')
    try{
      const current=await api<ProviderConfiguration>('GET','/api/config',undefined,{timeoutMs:10000})
      for(const field of ['llm_provider','custom_llm_base_url','custom_llm_model','custom_llm_catalog_url'] as const){
        if(current[field]!==draft[field])throw new Error('The provider changed on another device. Reload and check it again.')
      }
      if(!override){
        if(!draft.openrouter_cheap_model||!draft.openrouter_standard_model)throw new Error('Choose a cheap and a regular model.')
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
  const limit=budget?[budget.mode!=='tokens'&&budget.usd!==null?`$${budget.usd}`:null,budget.mode!=='usd'&&budget.tokens!==null?`${budget.tokens.toLocaleString()} tokens`:null].filter(Boolean).join(' or '):''
  return <section class="setup-provider"><h2>Connect models for smart features.</h2><p class="setup-lede">Agent subscriptions and model API access are separate.</p><fieldset class="setup-fields" disabled={busy}>
    {draft?<>
      {!connected?<><ProviderConnectionFields draft={draft} onChange={(field,value)=>change(field as keyof ProviderSetupDraft,value)} apiKey={key} onKeyChange={setKey} configured={!!entry?.secret.configured}/><p class="setup-hint">Connection testing sends a small model request, which may be billed.</p><button class="primary" disabled={busy} onClick={()=>void connection()}>Save and test connection</button></>:<>
        <div class="setup-inline-actions"><span>Provider connected</span><button class="link" disabled={busy} onClick={()=>setConnected(false)}>Change provider</button></div>
        <SetupModelSummary draft={draft} catalog={status?.models.models||[]} override={override} onChange={change}/>
        {override&&<p class="setup-hint">This endpoint serves one model for both roles.</p>}
        <details><summary>Daily automation limit: {limit||'no limit'} · Adjust</summary><BudgetControl name="automation_daily_budget" label="Daily automation limit" value={draft.automation_daily_budget} onChange={value=>{budgetChanged.current=true;change('automation_daily_budget',value)}} reportsCost={entry?.readiness.reports_cost}/></details>
        {entry?.readiness.reports_cost===false&&<p class="setup-hint">This endpoint does not report costs. Use a token limit to bound usage.</p>}
        <p class="setup-hint">Verification makes up to seven small model calls that may be billed. No test tool executes.</p>
        <button class="primary" disabled={busy} onClick={()=>void approve()}>Verify models and continue</button>
      </>}
    </>:!error&&<p role="status">Loading provider settings…</p>}
    {message&&<p role="status">{message}</p>}{error&&<p role="alert">{error} <button disabled={busy} onClick={()=>void load().catch(cause=>setError(cause.message))}>Reload choices</button></p>}
    <footer class="setup-step-actions"><button disabled={busy} onClick={()=>void onLater().catch(cause=>setError(cause.message))}>Set up later</button></footer>
  </fieldset></section>
}
