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
  const [status,setStatus]=useState('Loading global limits…')
  const root=useRef<HTMLDivElement>(null)

  const load=()=>api<AutomationConfig>('GET','/api/config').then(next=>{setConfig(next);setDraft(next);setStatus('ready')}).catch(cause=>setStatus(cause instanceof Error?cause.message:String(cause)))
  useEffect(()=>{void load()},[])
  useEffect(()=>{
    if(!draft||!initialSetting||!root.current)return
    return revealSetting(root.current,initialSetting)
  },[draft,initialSetting,revealToken])
  const dirty=useMemo(()=>!!config&&!!draft&&!same(config,draft),[config,draft])
  const change=<K extends keyof AutomationConfig>(key:K,value:AutomationConfig[K])=>setDraft(current=>current?{...current,[key]:value}:current)
  const save=async()=>{
    if(!config||!draft||!dirty)return
    const changes:Record<string,unknown>={_revision:config.revision}
    for(const key of Object.keys(draft) as (keyof AutomationConfig)[])if(key!=='revision'&&!same(config[key],draft[key]))changes[key]=draft[key]
    setStatus('saving…')
    try{const next=await api<AutomationConfig>('PATCH','/api/config',changes);setConfig(next);setDraft(next);setStatus('saved')}
    catch(cause){setStatus(cause instanceof Error?cause.message:String(cause))}
  }
  if(!draft)return <p class="automation-empty">{status}</p>
  return <div class="automation-policy-view" ref={root}>
    <section class="usage-table"><h3>Budgets &amp; ceilings</h3>
      <p class="settings-warning">One set of global bounds covers every automation - the scan timeline and attention narration spend here too. Model observers receive only their bounded transcript slice; they do not crawl Project files.</p>
      <BudgetControl name="automation_daily_budget" label="All automation, daily" value={draft.automation_daily_budget} onChange={value=>change('automation_daily_budget',value)} maxTokens={100000000} maxUsd={10000}/>
      <BudgetControl name="automation_rule_daily_budget" label="Per rule, daily" value={draft.automation_rule_daily_budget} onChange={value=>change('automation_rule_daily_budget',value)} maxTokens={100000000} maxUsd={10000}/>
      <label>Hourly call cap<input type="number" value={draft.automation_hourly_call_cap} onInput={event=>change('automation_hourly_call_cap',Number(event.currentTarget.value))}/></label>
      <label>Per-rule hourly calls<input type="number" value={draft.automation_rule_hourly_call_cap} onInput={event=>change('automation_rule_hourly_call_cap',Number(event.currentTarget.value))}/></label>
      <label>Maximum input tokens<input type="number" value={draft.automation_max_input_tokens} onInput={event=>change('automation_max_input_tokens',Number(event.currentTarget.value))}/></label>
      <label data-setting="automation_max_output_tokens">Maximum output tokens<input type="number" value={draft.automation_max_output_tokens} onInput={event=>change('automation_max_output_tokens',Number(event.currentTarget.value))}/><small>Also bounds every scan-timeline and narration call; below ~900 the scan schema cannot fit its reply.</small></label>
    </section>
    <section class="usage-table"><h3>Engine and execution</h3>
      <label>Concurrent observers<input type="number" min="1" max="16" value={draft.automation_concurrency} onInput={event=>change('automation_concurrency',Number(event.currentTarget.value))}/></label>
      <label>Queue capacity<input type="number" min="16" max="4096" value={draft.automation_queue_size} onInput={event=>change('automation_queue_size',Number(event.currentTarget.value))}/><small>Takes effect after daemon restart.</small></label>
      <label>Runtime retention days<input type="number" value={draft.automation_retention_days} onInput={event=>change('automation_retention_days',Number(event.currentTarget.value))}/></label>
      <div class="model-routing-elsewhere" data-setting="scan_timeline_model"><span>Scan timeline model</span><code>{draft.scan_timeline_model}</code><SettingLink target="automation.scanTimelineModel">Edit in Accounts → Models</SettingLink></div>
    </section>
    <section class="usage-table"><h3>Project context cards</h3>
      <div class="model-routing-elsewhere" data-setting="project_card_model"><span>Project card model</span><code>{draft.project_card_model||'Follows cheap model'}</code><SettingLink target="automation.projectCardModel">Edit in Accounts → Models</SettingLink></div>
      <BudgetControl name="project_card_daily_budget" label="Context cards, daily" value={draft.project_card_daily_budget} onChange={value=>change('project_card_daily_budget',value)} maxTokens={100000000} maxUsd={100}/>
      <label data-setting="project_card_max_input_tokens">Maximum input tokens per card<input type="number" min="512" max="128000" value={draft.project_card_max_input_tokens} onInput={event=>change('project_card_max_input_tokens',Number(event.currentTarget.value))}/></label>
      <label data-setting="project_card_max_output_tokens">Maximum output tokens per card<input type="number" min="128" max="4096" value={draft.project_card_max_output_tokens} onInput={event=>change('project_card_max_output_tokens',Number(event.currentTarget.value))}/></label>
    </section>
    <section class="usage-table"><h3>Scheduled runs</h3>
      <p>Whether schedules may start sessions at all is the Scheduled sessions row's Global switch in the matrix.</p>
      <label>Concurrent scheduled sessions<input type="number" min="0" max="50" value={draft.scheduled_runs_max_concurrent} onInput={event=>change('scheduled_runs_max_concurrent',Number(event.currentTarget.value))}/></label>
      <label>Sweep seconds<input type="number" min="1" max="300" value={draft.scheduled_runs_poll_seconds} onInput={event=>change('scheduled_runs_poll_seconds',Number(event.currentTarget.value))}/></label>
      <label>Run history days<input type="number" min="1" max="3650" value={draft.scheduled_run_retention_days} onInput={event=>change('scheduled_run_retention_days',Number(event.currentTarget.value))}/></label>
    </section>
    <section class="usage-table"><h3>Land queue</h3>
      <p>Whether the queue may move trunks at all is the Land queue row's Global switch in the matrix.</p>
      <label>Agent requests per hour<input type="number" value={draft.land_hourly_budget} onInput={event=>change('land_hourly_budget',Number(event.currentTarget.value))}/></label>
      <label>Busy-worktree hold seconds<input type="number" value={draft.land_hold_timeout_seconds} onInput={event=>change('land_hold_timeout_seconds',Number(event.currentTarget.value))}/></label>
      <label class="check"><span>Retry a failed verification once</span><input type="checkbox" checked={draft.land_retry_verification} onChange={event=>change('land_retry_verification',event.currentTarget.checked)}/></label>
      <label>Green result lifetime seconds<input type="number" value={draft.land_verify_memo_seconds} onInput={event=>change('land_verify_memo_seconds',Number(event.currentTarget.value))}/></label>
    </section>
    <section class="usage-table"><h3>Attention</h3>
      <label>Daily interrupts<input type="number" value={draft.attention_daily_interrupt_budget} onInput={event=>change('attention_daily_interrupt_budget',Number(event.currentTarget.value))}/></label>
      <label>Hourly burst cap<input type="number" value={draft.attention_hourly_interrupt_cap} onInput={event=>change('attention_hourly_interrupt_cap',Number(event.currentTarget.value))}/></label>
      <label>Incident window seconds<input type="number" value={draft.attention_incident_window_seconds} onInput={event=>change('attention_incident_window_seconds',Number(event.currentTarget.value))}/></label>
      <label class="check"><span>Report shell pause markers</span><input type="checkbox" checked={draft.attention_breakpoint_markers} onChange={event=>change('attention_breakpoint_markers',event.currentTarget.checked)}/></label>
      <label class="check" data-setting="attention_narration_enabled"><span>Model narration on ranked items</span><input type="checkbox" checked={draft.attention_narration_enabled} onChange={event=>change('attention_narration_enabled',event.currentTarget.checked)}/></label>
      <div class="model-routing-elsewhere" data-setting="attention_narration_model"><span>Narration model</span><code>{draft.attention_narration_model||'Follows cheap model'}</code><SettingLink target="automation.attentionNarrationModel">Edit in Accounts → Models</SettingLink></div>
      <p>Narration spends under the global automation budget; it has no budget of its own.</p>
    </section>
    <footer class="automation-policy-footer"><span aria-live="polite">{status}</span><button disabled={!dirty} onClick={()=>{setDraft(config);setStatus('changes discarded')}}>Discard</button><button class="primary" disabled={!dirty} onClick={()=>void save()}>Save limits</button></footer>
  </div>
}
