import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { BudgetControl } from './BudgetControl'
import type { Budget } from './types'
import { revealSetting } from './settingReveal'
import { SettingLink } from './SettingLink'

// The install-wide limits: budgets, caps, schedule and land-queue bounds, and
// attention policy. One scrolling column rather than the old five-page card
// row, because this now renders inside the Policy tab's "Limits & budgets"
// disclosure and a second navigation layer inside a drawer is a maze.
//
// Deliberately absent, and not to be re-added here:
// - `automation_enabled` and `scan_timeline_enabled`: their controls are the
//   master switch and the scan row's Global cell in `AutomationMatrix`. One
//   owner per switch.
// - Scan-timeline and attention-narration budgets/ceilings: retired. Both
//   features spend under the global automation budget, hourly call cap, and
//   per-call output ceiling below.
type AutomationConfig={
  revision:number
  automation_daily_budget:Budget;automation_rule_daily_budget:Budget
  automation_hourly_call_cap:number;automation_rule_hourly_call_cap:number
  automation_concurrency:number;automation_queue_size:number;automation_max_input_tokens:number
  automation_max_output_tokens:number;automation_retention_days:number
  project_card_daily_budget:Budget;project_card_max_input_tokens:number;project_card_max_output_tokens:number
  project_card_model:string
  scheduled_runs_max_concurrent:number
  scheduled_runs_poll_seconds:number;scheduled_run_retention_days:number
  land_hourly_budget:number;land_hold_timeout_seconds:number
  land_retry_verification:boolean;land_verify_memo_seconds:number
  scan_timeline_model:string
  attention_daily_interrupt_budget:number;attention_hourly_interrupt_cap:number
  attention_incident_window_seconds:number;attention_breakpoint_markers:boolean
  attention_narration_enabled:boolean;attention_narration_model:string
}

const same=(left:unknown,right:unknown)=>JSON.stringify(left)===JSON.stringify(right)

export function AutomationPolicyView({initialSetting,revealToken=0}:{initialSetting?:string;revealToken?:number}={}){
  const [config,setConfig]=useState<AutomationConfig|null>(null)
  const [draft,setDraft]=useState<AutomationConfig|null>(null)
  const [status,setStatus]=useState(t("AutomationPolicyView.5f26faedca"))
  const root=useRef<HTMLDivElement>(null)

  const load=()=>api<AutomationConfig>('GET','/api/config').then(next=>{setConfig(next);setDraft(next);setStatus(t("AutomationPolicyView.b24d6d3373"))}).catch(cause=>setStatus(cause instanceof Error?cause.message:String(cause)))
  useEffect(()=>{void load()},[])
  useEffect(()=>{
    if(!draft||!initialSetting||!root.current)return
    return revealSetting(root.current,initialSetting)
  },[draft,initialSetting,revealToken])
  const dirty=useMemo(()=>!!config&&!!draft&&!same(config,draft), [config, draft, uiLocale.value, catalogRevision.value])
  const change=<K extends keyof AutomationConfig>(key:K,value:AutomationConfig[K])=>setDraft(current=>current?{...current,[key]:value}:current)
  const save=async()=>{
    if(!config||!draft||!dirty)return
    const changes:Record<string,unknown>={_revision:config.revision}
    for(const key of Object.keys(draft) as (keyof AutomationConfig)[])if(key!=='revision'&&!same(config[key],draft[key]))changes[key]=draft[key]
    setStatus(t("AutomationPolicyView.945d2c0350"))
    try{const next=await api<AutomationConfig>('PATCH','/api/config',changes);setConfig(next);setDraft(next);setStatus(t("AutomationPolicyView.d81c55f49c"))}
    catch(cause){setStatus(cause instanceof Error?cause.message:String(cause))}
  }
  if(!draft)return <p class="automation-empty">{status}</p>
  return <div class="automation-policy-view" ref={root}>
    <section class="usage-table"><h3 data-heading-message="AutomationPolicyView.66ab0223a6">{t("AutomationPolicyView.66ab0223a6")}</h3>
      <p class="settings-warning">{t("AutomationPolicyView.23b2385f0f")}</p>
      <BudgetControl name="automation_daily_budget" label={t("AutomationPolicyView.8c90ad15bb")} value={draft.automation_daily_budget} onChange={value=>change('automation_daily_budget',value)} maxTokens={100000000} maxUsd={10000}/>
      <BudgetControl name="automation_rule_daily_budget" label={t("AutomationPolicyView.e830b05bbb")} value={draft.automation_rule_daily_budget} onChange={value=>change('automation_rule_daily_budget',value)} maxTokens={100000000} maxUsd={10000}/>
      <label>{rich("AutomationPolicyView.99c2e85429", { value1: <input type="number" value={draft.automation_hourly_call_cap} onInput={event=>change('automation_hourly_call_cap',Number(event.currentTarget.value))}/> })}</label>
      <label>{rich("AutomationPolicyView.29f9560cf9", { value1: <input type="number" value={draft.automation_rule_hourly_call_cap} onInput={event=>change('automation_rule_hourly_call_cap',Number(event.currentTarget.value))}/> })}</label>
      <label>{rich("AutomationPolicyView.6699aa2a7b", { value1: <input type="number" value={draft.automation_max_input_tokens} onInput={event=>change('automation_max_input_tokens',Number(event.currentTarget.value))}/> })}</label>
      <label data-setting="automation_max_output_tokens">{rich("AutomationPolicyView.9d4d9148d3", { value1: <input type="number" value={draft.automation_max_output_tokens} onInput={event=>change('automation_max_output_tokens',Number(event.currentTarget.value))}/>, value2: <small>{t("AutomationPolicyView.29fdcf8112")}</small> })}</label>
    </section>
    <section class="usage-table"><h3 data-heading-message="AutomationPolicyView.e30a1e04b0">{t("AutomationPolicyView.e30a1e04b0")}</h3>
      <label>{rich("AutomationPolicyView.84cc2aab6e", { value1: <input type="number" min="1" max="16" value={draft.automation_concurrency} onInput={event=>change('automation_concurrency',Number(event.currentTarget.value))}/> })}</label>
      <label>{rich("AutomationPolicyView.9c5d671396", { value1: <input type="number" min="16" max="4096" value={draft.automation_queue_size} onInput={event=>change('automation_queue_size',Number(event.currentTarget.value))}/>, value2: <small>{t("AutomationPolicyView.bf2a4100b1")}</small> })}</label>
      <label>{rich("AutomationPolicyView.f19e95794d", { value1: <input type="number" value={draft.automation_retention_days} onInput={event=>change('automation_retention_days',Number(event.currentTarget.value))}/> })}</label>
      <div class="model-routing-elsewhere" data-setting="scan_timeline_model"><span>{t("AutomationPolicyView.4de13678da")}</span><code>{draft.scan_timeline_model}</code><SettingLink target="automation.scanTimelineModel">{t("AutomationPolicyView.4f0c966dec")}</SettingLink></div>
    </section>
    <section class="usage-table"><h3 data-heading-message="AutomationPolicyView.4dac3151fb">{t("AutomationPolicyView.4dac3151fb")}</h3>
      <div class="model-routing-elsewhere" data-setting="project_card_model"><span>{t("AutomationPolicyView.98efa71f4f")}</span><code>{draft.project_card_model || t("AutomationPolicyView.97c4a790b8")}</code><SettingLink target="automation.projectCardModel">{t("AutomationPolicyView.4f0c966dec")}</SettingLink></div>
      <BudgetControl name="project_card_daily_budget" label={t("AutomationPolicyView.48b1ca2d78")} value={draft.project_card_daily_budget} onChange={value=>change('project_card_daily_budget',value)} maxTokens={100000000} maxUsd={100}/>
      <label data-setting="project_card_max_input_tokens">{rich("AutomationPolicyView.f548ed0f90", { value1: <input type="number" min="512" max="128000" value={draft.project_card_max_input_tokens} onInput={event=>change('project_card_max_input_tokens',Number(event.currentTarget.value))}/> })}</label>
      <label data-setting="project_card_max_output_tokens">{rich("AutomationPolicyView.1e7c0836b6", { value1: <input type="number" min="128" max="4096" value={draft.project_card_max_output_tokens} onInput={event=>change('project_card_max_output_tokens',Number(event.currentTarget.value))}/> })}</label>
    </section>
    <section class="usage-table"><h3 data-heading-message="AutomationPolicyView.19110a1000">{t("AutomationPolicyView.19110a1000")}</h3>
      <p>{t("AutomationPolicyView.28955da325")}</p>
      <label>{rich("AutomationPolicyView.e8082d5c72", { value1: <input type="number" min="0" max="50" value={draft.scheduled_runs_max_concurrent} onInput={event=>change('scheduled_runs_max_concurrent',Number(event.currentTarget.value))}/> })}</label>
      <label>{rich("AutomationPolicyView.745cdaea3f", { value1: <input type="number" min="1" max="300" value={draft.scheduled_runs_poll_seconds} onInput={event=>change('scheduled_runs_poll_seconds',Number(event.currentTarget.value))}/> })}</label>
      <label>{rich("AutomationPolicyView.6418cabca1", { value1: <input type="number" min="1" max="3650" value={draft.scheduled_run_retention_days} onInput={event=>change('scheduled_run_retention_days',Number(event.currentTarget.value))}/> })}</label>
    </section>
    <section class="usage-table"><h3 data-heading-message="AutomationPolicyView.788d20dbca">{t("AutomationPolicyView.788d20dbca")}</h3>
      <p>{t("AutomationPolicyView.debaa70e80")}</p>
      <label>{rich("AutomationPolicyView.c31adfeef7", { value1: <input type="number" value={draft.land_hourly_budget} onInput={event=>change('land_hourly_budget',Number(event.currentTarget.value))}/> })}</label>
      <label>{rich("AutomationPolicyView.c249679a70", { value1: <input type="number" value={draft.land_hold_timeout_seconds} onInput={event=>change('land_hold_timeout_seconds',Number(event.currentTarget.value))}/> })}</label>
      <label class="check"><span>{t("AutomationPolicyView.c892421cd5")}</span><input type="checkbox" checked={draft.land_retry_verification} onChange={event=>change('land_retry_verification',event.currentTarget.checked)}/></label>
      <label>{rich("AutomationPolicyView.f777f28c98", { value1: <input type="number" value={draft.land_verify_memo_seconds} onInput={event=>change('land_verify_memo_seconds',Number(event.currentTarget.value))}/> })}</label>
    </section>
    <section class="usage-table"><h3 data-heading-message="AutomationPolicyView.c2eb8cd9d9">{t("AutomationPolicyView.c2eb8cd9d9")}</h3>
      <label>{rich("AutomationPolicyView.ad5027c172", { value1: <input type="number" value={draft.attention_daily_interrupt_budget} onInput={event=>change('attention_daily_interrupt_budget',Number(event.currentTarget.value))}/> })}</label>
      <label>{rich("AutomationPolicyView.eaa581eb45", { value1: <input type="number" value={draft.attention_hourly_interrupt_cap} onInput={event=>change('attention_hourly_interrupt_cap',Number(event.currentTarget.value))}/> })}</label>
      <label>{rich("AutomationPolicyView.35613d2d59", { value1: <input type="number" value={draft.attention_incident_window_seconds} onInput={event=>change('attention_incident_window_seconds',Number(event.currentTarget.value))}/> })}</label>
      <label class="check"><span>{t("AutomationPolicyView.5f3691ec85")}</span><input type="checkbox" checked={draft.attention_breakpoint_markers} onChange={event=>change('attention_breakpoint_markers',event.currentTarget.checked)}/></label>
      <label class="check" data-setting="attention_narration_enabled"><span>{t("AutomationPolicyView.c954f05858")}</span><input type="checkbox" checked={draft.attention_narration_enabled} onChange={event=>change('attention_narration_enabled',event.currentTarget.checked)}/></label>
      <div class="model-routing-elsewhere" data-setting="attention_narration_model"><span>{t("AutomationPolicyView.8abd5fe186")}</span><code>{draft.attention_narration_model || t("AutomationPolicyView.97c4a790b8")}</code><SettingLink target="automation.attentionNarrationModel">{t("AutomationPolicyView.4f0c966dec")}</SettingLink></div>
      <p>{t("AutomationPolicyView.15602248d1")}</p>
    </section>
    <footer class="automation-policy-footer"><span aria-live="polite">{status}</span><button disabled={!dirty} onClick={()=>{setDraft(config);setStatus(t("AutomationPolicyView.f3adb99882"))}}>{t("AutomationPolicyView.eb1a70e392")}</button><button class="primary" disabled={!dirty} onClick={()=>void save()}>{t("AutomationPolicyView.237e964be7")}</button></footer>
  </div>
}
