import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { BudgetControl } from './BudgetControl'
import type { Budget } from './types'
import { revealSetting } from './settingReveal'
import { SettingLink } from './SettingLink'

type PolicyPage='engine'|'budgets'|'schedules'|'timeline'|'attention'

type AutomationConfig={
  revision:number
  automation_enabled:boolean;automation_daily_budget:Budget;automation_rule_daily_budget:Budget
  automation_hourly_call_cap:number;automation_rule_hourly_call_cap:number
  automation_concurrency:number;automation_queue_size:number;automation_max_input_tokens:number
  automation_max_output_tokens:number;automation_retention_days:number
  project_card_daily_budget:Budget;project_card_max_input_tokens:number;project_card_max_output_tokens:number
  project_card_model:string
  scheduled_runs_enabled:boolean;scheduled_runs_max_concurrent:number
  scheduled_runs_poll_seconds:number;scheduled_run_retention_days:number
  land_queue_enabled:boolean;land_hourly_budget:number;land_hold_timeout_seconds:number
  land_retry_verification:boolean;land_verify_memo_seconds:number
  scan_timeline_enabled:boolean;scan_timeline_daily_budget:Budget;scan_timeline_run_budget:Budget
  scan_timeline_model:string
  scan_timeline_hourly_call_cap:number;scan_timeline_max_output_tokens:number
  attention_daily_interrupt_budget:number;attention_hourly_interrupt_cap:number
  attention_incident_window_seconds:number;attention_breakpoint_markers:boolean
  attention_narration_enabled:boolean;attention_narration_daily_budget:Budget
  attention_narration_max_output_tokens:number;attention_narration_model:string
}

const same=(left:unknown,right:unknown)=>JSON.stringify(left)===JSON.stringify(right)

const pageForSetting=(setting?:string):PolicyPage=>{
  if(!setting)return'engine'
  if(setting.includes('budget')||setting.includes('hourly_call')||setting.startsWith('project_card'))return'budgets'
  if(setting.startsWith('scheduled_')||setting.startsWith('land_'))return'schedules'
  if(setting.startsWith('scan_timeline'))return'timeline'
  if(setting.startsWith('attention_'))return'attention'
  return'engine'
}

export function AutomationPolicyView({initialSetting,revealToken=0}:{initialSetting?:string;revealToken?:number}={}){
  const [config,setConfig]=useState<AutomationConfig|null>(null)
  const [draft,setDraft]=useState<AutomationConfig|null>(null)
  const [page,setPage]=useState<PolicyPage>(()=>pageForSetting(initialSetting))
  const [status,setStatus]=useState('Loading global policy…')
  const root=useRef<HTMLDivElement>(null)

  const load=()=>api<AutomationConfig>('GET','/api/config').then(next=>{setConfig(next);setDraft(next);setStatus('ready')}).catch(cause=>setStatus(cause instanceof Error?cause.message:String(cause)))
  useEffect(()=>{void load()},[])
  useEffect(()=>setPage(pageForSetting(initialSetting)),[initialSetting,revealToken])
  useEffect(()=>{
    if(!draft||!initialSetting||!root.current)return
    return revealSetting(root.current,initialSetting)
  },[draft,page,initialSetting,revealToken])
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
  const cards:Array<{id:PolicyPage;label:string;summary:string}>=[
    {id:'engine',label:'Engine',summary:draft.automation_enabled?'on':'off'},
    {id:'budgets',label:'Budgets',summary:`${draft.automation_concurrency} concurrent`},
    {id:'schedules',label:'Schedules & landing',summary:`schedules ${draft.scheduled_runs_enabled?'on':'off'} · land ${draft.land_queue_enabled?'on':'off'}`},
    {id:'timeline',label:'Scan timeline',summary:draft.scan_timeline_enabled?'on':'off'},
    {id:'attention',label:'Attention',summary:`${draft.attention_daily_interrupt_budget}/day`},
  ]
  return <div class="automation-policy-view" ref={root}>
    <div class="automation-policy-cards">{cards.map(card=><button class={page===card.id?'active':''} onClick={()=>setPage(card.id)}><strong>{card.label}</strong><span>{card.summary}</span></button>)}</div>
    {page==='engine'&&<section class="usage-table"><h3>Engine and execution</h3>
      <p class="settings-warning">Model observers receive only their bounded transcript slice. They do not crawl Project files.</p>
      <label class="settings-toggle" data-setting="automation_enabled"><input type="checkbox" checked={draft.automation_enabled} onChange={event=>change('automation_enabled',event.currentTarget.checked)}/>Run automation<small>Master switch for system observers and custom rules.</small></label>
      <label>Concurrent observers<input type="number" min="1" max="16" value={draft.automation_concurrency} onInput={event=>change('automation_concurrency',Number(event.currentTarget.value))}/></label>
      <label>Queue capacity<input type="number" min="16" max="4096" value={draft.automation_queue_size} onInput={event=>change('automation_queue_size',Number(event.currentTarget.value))}/><small>Takes effect after daemon restart.</small></label>
      <label>Maximum input tokens<input type="number" value={draft.automation_max_input_tokens} onInput={event=>change('automation_max_input_tokens',Number(event.currentTarget.value))}/></label>
      <label>Maximum output tokens<input type="number" value={draft.automation_max_output_tokens} onInput={event=>change('automation_max_output_tokens',Number(event.currentTarget.value))}/></label>
      <label>Runtime retention days<input type="number" value={draft.automation_retention_days} onInput={event=>change('automation_retention_days',Number(event.currentTarget.value))}/></label>
    </section>}
    {page==='budgets'&&<section class="usage-table"><h3>Budgets</h3>
      <BudgetControl name="automation_daily_budget" label="All automation, daily" value={draft.automation_daily_budget} onChange={value=>change('automation_daily_budget',value)} maxTokens={100000000} maxUsd={10000}/>
      <BudgetControl name="automation_rule_daily_budget" label="Per rule, daily" value={draft.automation_rule_daily_budget} onChange={value=>change('automation_rule_daily_budget',value)} maxTokens={100000000} maxUsd={10000}/>
      <label>Hourly call cap<input type="number" value={draft.automation_hourly_call_cap} onInput={event=>change('automation_hourly_call_cap',Number(event.currentTarget.value))}/></label>
      <label>Per-rule hourly calls<input type="number" value={draft.automation_rule_hourly_call_cap} onInput={event=>change('automation_rule_hourly_call_cap',Number(event.currentTarget.value))}/></label>
      <h4>Project context cards</h4>
      <div class="model-routing-elsewhere" data-setting="project_card_model"><span>Project card model</span><code>{draft.project_card_model||'Follows cheap model'}</code><SettingLink target="automation.projectCardModel">Edit in Accounts → Models</SettingLink></div>
      <BudgetControl name="project_card_daily_budget" label="Context cards, daily" value={draft.project_card_daily_budget} onChange={value=>change('project_card_daily_budget',value)} maxTokens={100000000} maxUsd={100}/>
      <label data-setting="project_card_max_input_tokens">Maximum input tokens per card<input type="number" min="512" max="128000" value={draft.project_card_max_input_tokens} onInput={event=>change('project_card_max_input_tokens',Number(event.currentTarget.value))}/></label>
      <label data-setting="project_card_max_output_tokens">Maximum output tokens per card<input type="number" min="128" max="4096" value={draft.project_card_max_output_tokens} onInput={event=>change('project_card_max_output_tokens',Number(event.currentTarget.value))}/></label>
    </section>}
    {page==='schedules'&&<><section class="usage-table"><h3>Scheduled runs</h3>
      <label class="settings-toggle" data-setting="scheduled_runs_enabled"><input type="checkbox" checked={draft.scheduled_runs_enabled} onChange={event=>change('scheduled_runs_enabled',event.currentTarget.checked)}/>Let schedules start sessions</label>
      <label>Concurrent scheduled sessions<input type="number" min="0" max="50" value={draft.scheduled_runs_max_concurrent} onInput={event=>change('scheduled_runs_max_concurrent',Number(event.currentTarget.value))}/></label>
      <label>Sweep seconds<input type="number" min="1" max="300" value={draft.scheduled_runs_poll_seconds} onInput={event=>change('scheduled_runs_poll_seconds',Number(event.currentTarget.value))}/></label>
      <label>Run history days<input type="number" min="1" max="3650" value={draft.scheduled_run_retention_days} onInput={event=>change('scheduled_run_retention_days',Number(event.currentTarget.value))}/></label>
    </section><section class="usage-table"><h3>Land queue</h3>
      <label class="settings-toggle" data-setting="land_queue_enabled"><input type="checkbox" checked={draft.land_queue_enabled} onChange={event=>change('land_queue_enabled',event.currentTarget.checked)}/>Let the land queue move trunks</label>
      <label>Agent requests per hour<input type="number" value={draft.land_hourly_budget} onInput={event=>change('land_hourly_budget',Number(event.currentTarget.value))}/></label>
      <label>Busy-worktree hold seconds<input type="number" value={draft.land_hold_timeout_seconds} onInput={event=>change('land_hold_timeout_seconds',Number(event.currentTarget.value))}/></label>
      <label class="check"><span>Retry a failed verification once</span><input type="checkbox" checked={draft.land_retry_verification} onChange={event=>change('land_retry_verification',event.currentTarget.checked)}/></label>
      <label>Green result lifetime seconds<input type="number" value={draft.land_verify_memo_seconds} onInput={event=>change('land_verify_memo_seconds',Number(event.currentTarget.value))}/></label>
    </section></>}
    {page==='timeline'&&<section class="usage-table"><h3>Scan timeline</h3>
      <label class="settings-toggle" data-setting="scan_timeline_enabled"><input type="checkbox" checked={draft.scan_timeline_enabled} onChange={event=>change('scan_timeline_enabled',event.currentTarget.checked)}/>Allow scan timeline</label>
      <div class="model-routing-elsewhere" data-setting="scan_timeline_model"><span>Scan timeline model</span><code>{draft.scan_timeline_model}</code><SettingLink target="automation.scanTimelineModel">Edit in Accounts → Models</SettingLink></div>
      <BudgetControl name="scan_timeline_daily_budget" label="Daily across all runs" value={draft.scan_timeline_daily_budget} onChange={value=>change('scan_timeline_daily_budget',value)} maxTokens={100000000} maxUsd={1000}/>
      <BudgetControl name="scan_timeline_run_budget" label="Per conversation" value={draft.scan_timeline_run_budget} onChange={value=>change('scan_timeline_run_budget',value)} maxTokens={20000000} maxUsd={1000}/>
      <label>Hourly scan cap<input type="number" value={draft.scan_timeline_hourly_call_cap} onInput={event=>change('scan_timeline_hourly_call_cap',Number(event.currentTarget.value))}/></label>
      <label>Maximum output tokens<input type="number" value={draft.scan_timeline_max_output_tokens} onInput={event=>change('scan_timeline_max_output_tokens',Number(event.currentTarget.value))}/></label>
    </section>}
    {page==='attention'&&<section class="usage-table"><h3>Attention</h3>
      <label>Daily interrupts<input type="number" value={draft.attention_daily_interrupt_budget} onInput={event=>change('attention_daily_interrupt_budget',Number(event.currentTarget.value))}/></label>
      <label>Hourly burst cap<input type="number" value={draft.attention_hourly_interrupt_cap} onInput={event=>change('attention_hourly_interrupt_cap',Number(event.currentTarget.value))}/></label>
      <label>Incident window seconds<input type="number" value={draft.attention_incident_window_seconds} onInput={event=>change('attention_incident_window_seconds',Number(event.currentTarget.value))}/></label>
      <label class="check"><span>Report shell pause markers</span><input type="checkbox" checked={draft.attention_breakpoint_markers} onChange={event=>change('attention_breakpoint_markers',event.currentTarget.checked)}/></label>
      <label class="check"><span>Model narration on ranked items</span><input type="checkbox" checked={draft.attention_narration_enabled} onChange={event=>change('attention_narration_enabled',event.currentTarget.checked)}/></label>
      <div class="model-routing-elsewhere" data-setting="attention_narration_model"><span>Narration model</span><code>{draft.attention_narration_model||'Follows cheap model'}</code><SettingLink target="automation.attentionNarrationModel">Edit in Accounts → Models</SettingLink></div>
      <BudgetControl name="attention_narration_daily_budget" label="Narration, daily" value={draft.attention_narration_daily_budget} onChange={value=>change('attention_narration_daily_budget',value)} maxTokens={100000000} maxUsd={100}/>
      <label>Maximum narration tokens<input type="number" value={draft.attention_narration_max_output_tokens} onInput={event=>change('attention_narration_max_output_tokens',Number(event.currentTarget.value))}/></label>
    </section>}
    <footer class="automation-policy-footer"><span aria-live="polite">{status}</span><button disabled={!dirty} onClick={()=>{setDraft(config);setStatus('changes discarded')}}>Discard</button><button class="primary" disabled={!dirty} onClick={()=>void save()}>Save policy</button></footer>
  </div>
}
