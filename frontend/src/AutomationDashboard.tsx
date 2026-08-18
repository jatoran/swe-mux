import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { revealSetting } from './settingReveal'
import { api } from './api'
import {
  buildSpendRows, callHealth, exactMoney, formatCount, formatDuration, formatMoney, formatPercent,
  type SpendBreakdown,
} from './automationCost'
import { displayModelName } from './modelDisplay'
import { ModelName } from './ModelName'
import { useModalFocus } from './modalFocus'
import { runDisplayName } from './sessionNames'

type Rule={id:string;name:string;enabled:boolean;shadow:boolean;trigger:string;revision:string;source:string;actions?:Array<{kind:string;model?:string;on_result?:{kind?:string}}>}
type BuiltInRule={id:string;name:string;enabled:boolean;shadow:boolean;trigger:string;source:'builtin';setting_key:string;setting_label:string;input:string;model:string;result:string;description:string}
type Firing={id:string;event_seq:number;event_type:string;rule_id:string;status:string;shadow:number;created_at:number;error?:string;condition_trace:Array<{field:string;matched:boolean;actual:unknown;expected:unknown}>}
type ActionResult={id:string;firing_id:string;action_index:number;kind:string;status:string;detail:unknown;error?:string;created_at:number}
type ObserverCall={id:string;firing_id:string;rule_id:string;status:string;requested_model?:string;resolved_model?:string;input_bytes:number;input_tokens:number;output_tokens:number;cost_usd?:number;latency_ms?:number;provider_name?:string;finish_reason?:string;response_content_type?:string;response_content_length?:number;http_status?:number;retryable?:number;error?:string;created_at:number}
type Annotation={id:string;agent_run_id:string;session_id?:string;tag:string;content:string;provenance:string;requested_model?:string;resolved_model?:string;input_tokens:number;output_tokens:number;cost_usd?:number;confidence?:number;created_at:number}
type InboxItem={id:string;session_id?:string;kind:string;title:string;message:string;severity:string;evidence:Array<{signal:string;value:unknown}>;read_at?:number;created_at:number}
type AutomationData={
  controls:{automation_enabled:boolean;scan_timeline_enabled:boolean}
  engine:{enabled:boolean;diagnostic?:string;rules:Rule[];built_in_rules:BuiltInRule[];queue:{size:number;capacity:number;dropped:number;loop_rejections:number};capabilities:{triggers:string[];observer_schemas:string[]}}
  provider:{secret:{configured:boolean;source:string};models:{models:unknown[];stale:boolean;error?:string};cheap_model:string;standard_model:string}
  spend_today:{tokens:number;cost_usd:number};observer_calls:Record<string,number>;annotations:Record<string,number>;unread_notifications:number
  recent_firings:Firing[];recent_action_results:ActionResult[];recent_observer_calls:ObserverCall[];recent_annotations:Annotation[]
  spend_breakdown?:SpendBreakdown
}
type Telemetry={since:number;dimensions:Array<{backend:string;model:string;runs:number;ended_runs:number;average_duration_s?:number;tokens_in:number;tokens_out:number;average_final_context_pct?:number;average_peak_context_pct?:number;turns_per_run:number;stalls_per_run:number;approvals_per_run:number;completion_evidence_count:number;completion_evidence_runs:number}>;event_counts:Record<string,number>;interpretation:string;observer_spend:{tokens:number;cost_usd:number};provider_cost_dimensions:Array<{backend:string;model:string;tokens:number;cost_usd:number;cost_is_estimate:boolean;attribution:string}>;cost_note:string}
type Experience={id:string;backend:string;error_summary:string;resolution_summary:string;source_run_id:string;confidence?:number;created_at:number}
type InjectionSafety={version:number;research_only:boolean;authorizes_actuation:boolean;shadow_metrics:{evaluations:Record<string,number>;reasons:Record<string,number>;tracked_sessions:number;unknown_duration_s:number;transitions:number};parser_coverage:Array<{session_id:string;backend:string;schema_version?:string;status:string;recognized:number;unknown:number;unknown_rate?:number;unknown_signatures:Record<string,number>;diagnostic?:string}>;sessions:Array<{session_id:string;backend:string;state:string;delivery_state:'safe'|'blocked'|'unknown';reason:string;reasons:string[];candidate_safe:boolean;authorized:boolean;diagnostic:string;checks:Record<string,boolean|null>;evidence:Record<string,unknown>}>}
type EndedRun={id:string;backend:string;name:string;generated_title?:string;auto_named?:number;cwd:string;spawned_at:number;exited_at?:number;transcript_path?:string;project_label?:string}
type ObserverBatch={id:string;kind:string;status:string;run_ids:string[];preview:unknown[];calls:number;tokens:number;cost_usd:number;error?:string;created_at:number;completed_at?:number}
type View='automations'|'attention'|'notes'|'health'|'knowledge'|'cost'|'diagnostics'

const groups:Array<{id:string;label:string;views:View[]}>=[
  {id:'configure',label:'configure',views:['automations']},
  {id:'attend',label:'attend',views:['attention','health']},
  // Spend is its own destination rather than a section of health: what a thing costs and
  // whether it is behaving are different questions asked at different times.
  {id:'spend',label:'spend',views:['cost']},
  {id:'review',label:'review',views:['notes','knowledge']},
]
const viewLabels:Record<View,string>={automations:'rules & observers',attention:'attention',health:'all-session health',notes:'run notes',knowledge:'learned fixes',cost:'cost breakdown',diagnostics:'diagnostics'}
const healthSignals=[
  ['Needs you','Unattended approvals and requests waiting for user input.'],
  ['Possibly stuck','Stalls, repeated tool failures, and output without meaningful progress.'],
  ['Resource pressure','High context usage, runaway output, and busy descendant processes.'],
  ['Cross-session conflicts','Concurrent work on the same branch, port collisions, and shared dev servers.'],
]
const money=new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',maximumFractionDigits:4})
const integer=new Intl.NumberFormat()

function actionSummary(rule:Rule):string{
  const action=rule.actions?.[0]
  if(!action)return 'Action details unavailable'
  if(action.kind==='llm')return `${action.model?displayModelName(action.model):'configured'} observer → ${action.on_result?.kind||'structured result'}`
  return action.kind
}

export function AutomationDashboard({onClose,onConfigure,onOpenSession,initialSetting,revealToken}:{
  onClose:()=>void;onConfigure:()=>void;onOpenSession:(id:string)=>void
  /** `data-setting` id of a global control to scroll to and flash (`settingTargets.ts`). The
   *  two install-wide automation switches live here rather than in Settings, so a gated
   *  surface deep-links to this dashboard for them. */
  initialSetting?:string
  /** Changes per deep-link request, so the same link twice reveals twice. */
  revealToken?:number
}){
  const [data,setData]=useState<AutomationData|null>(null)
  const [inbox,setInbox]=useState<InboxItem[]>([])
  const [telemetry,setTelemetry]=useState<Telemetry|null>(null)
  const [experiences,setExperiences]=useState<Experience[]>([])
  const [injectionSafety,setInjectionSafety]=useState<InjectionSafety|null>(null)
  const [view,setView]=useState<View>('automations')
  const [showHelp,setShowHelp]=useState(false)
  const [message,setMessage]=useState('Loading automation state…')
  const [error,setError]=useState('')
  const [eventSeq,setEventSeq]=useState('')
  const [dryRun,setDryRun]=useState<unknown>(null)
  const [absenceReport,setAbsenceReport]=useState<unknown>(null)
  const [batchKind,setBatchKind]=useState('experience')
  const [batchRuns,setBatchRuns]=useState<Set<string>>(new Set())
  const [batchCandidates,setBatchCandidates]=useState<EndedRun[]>([])
  const [batches,setBatches]=useState<ObserverBatch[]>([])
  const [batchPreview,setBatchPreview]=useState<Record<string,unknown>|null>(null)
  const panel=useRef<HTMLElement>(null)
  const helpPanel=useRef<HTMLElement>(null)
  useModalFocus(panel,onClose,!showHelp)
  useModalFocus(helpPanel,()=>setShowHelp(false),showHelp)

  const load=async()=>{
    try{
      const [dashboard,notifications,workloads,experience,injection,history,batchHistory]=await Promise.all([
        api<AutomationData>('GET','/api/automation/dashboard'),
        api<{items:InboxItem[]}>('GET','/api/automation/notifications'),
        api<Telemetry>('GET','/api/telemetry/workloads'),
        api<{items:Experience[]}>('GET','/api/experiences'),
        api<InjectionSafety>('GET','/api/automation/injection-safety'),
        api<{items:EndedRun[]}>('GET','/api/history?limit=100'),
        api<{items:ObserverBatch[]}>('GET','/api/automation/batches'),
      ])
      setData(dashboard);setInbox(notifications.items);setTelemetry(workloads);setExperiences(experience.items);setInjectionSafety(injection);setBatchCandidates(history.items.filter(item=>Boolean(item.exited_at&&item.transcript_path)));setBatches(batchHistory.items)
      const enabledBuiltins=(dashboard.engine.built_in_rules||[]).filter(item=>item.enabled).length
      setMessage(`ready · ${enabledBuiltins} system observers on · ${dashboard.engine.rules.length} custom rules · ${dashboard.unread_notifications} unread`);setError('')
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  useEffect(()=>{void load()},[])

  // A deep link to a global control also has to put the view that renders it on screen: the
  // dashboard is tabbed, and every other tab is unmounted. The reveal then waits for the row,
  // which only exists once the dashboard's own fetch lands.
  useEffect(()=>{
    if(!initialSetting)return
    setView('automations')
    const root=panel.current
    if(!root)return
    return revealSetting(root,initialSetting)
  },[initialSetting,revealToken,data!==null])

  const runDry=async()=>{try{setDryRun(null);setMessage('Evaluating selected historical event with side effects disabled…');const result=await api('POST','/api/automation/dry-run',{event_seq:Number(eventSeq)});setDryRun(result);setMessage('Dry-run complete. No actions or model calls were executed.')}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  const markRead=async(item:InboxItem)=>{await api('PATCH',`/api/automation/notifications/${item.id}`,{read:!item.read_at});await load()}
  const updateRule=async(rule:Rule,change:Partial<Pick<Rule,'enabled'|'shadow'>>)=>{try{setMessage(`Updating ${rule.name}…`);await api('PATCH',`/api/automation/rules/${encodeURIComponent(rule.id)}`,change);await load()}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  const updateBuiltin=async(rule:BuiltInRule)=>{try{setMessage(`Updating ${rule.setting_label}…`);await api('PATCH','/api/config',{[rule.setting_key]:!rule.enabled});await load()}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  const updateControl=async(key:'automation_enabled'|'scan_timeline_enabled',enabled:boolean)=>{try{setMessage('Updating global automation controls…');await api('PATCH','/api/config',{[key]:enabled});await load()}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  const previewBatch=async(confirm=false)=>{try{const run_ids=Array.from(batchRuns);const result=await api<Record<string,unknown>>('POST','/api/automation/batches',{kind:batchKind,run_ids,confirm,...(confirm?{preview_token:String(batchPreview?.preview_token||'')}:{})});setBatchPreview(result);setMessage(confirm?'Batch started. Results remain preview/export-only.':'Batch estimate ready; review before starting.');if(confirm)await load()}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  const toggleBatchRun=(id:string)=>{setBatchPreview(null);setBatchRuns(current=>{const next=new Set(current);if(next.has(id))next.delete(id);else if(next.size<25)next.add(id);return next})}
  const enabledBuiltins=(data?.engine.built_in_rules||[]).filter(item=>item.enabled).length
  const attentionObserversEnabled=(data?.engine.built_in_rules||[]).some(item=>item.setting_key==='phase7_observers_enabled'&&item.enabled)
  const activeGroup=groups.find(group=>group.views.includes(view))
  const unread=data?.unread_notifications||0
  const breakdown=data?.spend_breakdown
  const spendRows=useMemo(()=>buildSpendRows(breakdown),[breakdown])
  const spendDays=breakdown?.days||7
  const spendTotals=breakdown?.totals
  // Two different pots of money, never added together: observers bill an OpenRouter key by
  // the call, agents bill a subscription plan and are only ever estimated.
  const agentSpend=(telemetry?.provider_cost_dimensions||[]).reduce((total,row)=>total+(row.cost_usd||0),0)
  const agentTokens=(telemetry?.provider_cost_dimensions||[]).reduce((total,row)=>total+(row.tokens||0),0)
  const calls=callHealth(data?.observer_calls)
  const costShareTotal=spendRows.reduce((total,row)=>total+(row.cost_usd||0),0)

  return <div class="usage-layer automation-layer" role="dialog" aria-modal="true" aria-label="Automation" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="usage-panel automation-panel" ref={panel}>
      <header><div><span>AUTOMATION</span><strong>Observe sessions · surface attention · retain useful findings</strong></div><div class="usage-header-actions"><button class="automation-help-btn" aria-label="How automation works" title="How it works" onClick={()=>setShowHelp(true)}>?</button><button aria-pressed={view==='diagnostics'} title="Developer diagnostics" onClick={()=>setView(view==='diagnostics'?'automations':'diagnostics')}>diagnostics</button><button onClick={onConfigure}>settings</button><button aria-label="Close automation" onClick={onClose}>×</button></div></header>
      <div class="usage-actions automation-tabs" role="tablist">{groups.map(group=><button role="tab" aria-selected={activeGroup?.id===group.id} class={activeGroup?.id===group.id?'active':''} onClick={()=>setView(group.views[0])}>{group.label}{group.id==='attend'&&unread?` [${unread}]`:''}</button>)}</div>
      {activeGroup&&activeGroup.views.length>1&&<div class="usage-actions automation-subtabs" role="tablist">{activeGroup.views.map(sub=><button role="tab" aria-selected={view===sub} class={view===sub?'active':''} onClick={()=>setView(sub)}>{viewLabels[sub]}{sub==='attention'&&unread?` [${unread}]`:''}</button>)}</div>}
      <div class={`usage-progress ${!data&&!error?'running':''}`} role="status" aria-live="polite"><span>{error?'!':data?'·':'◌'}</span><strong>{error||message}</strong></div>
      <main>
        {view==='automations'&&<div class="usage-tables">
          {/* `calls today` used to sum the lifetime status counts, which is neither today's
              figure nor a count of anything the reader asked for. Today's calls come from the
              same ledger as today's cost, so the three spend tiles agree with each other. */}
          <div class="usage-summary"><article><span>automation</span><strong>{data?.engine.enabled?'on':'off'}</strong></article><article><span>system observers</span><strong>{enabledBuiltins}/{data?.engine.built_in_rules?.length||0}</strong></article><article><span>custom rules</span><strong>{data?.engine.rules.length||0}</strong></article><article><span>calls today</span><strong>{formatCount(spendTotals?.today_calls||0)}</strong></article><article><span>tokens today</span><strong title={integer.format(data?.spend_today.tokens||0)}>{formatCount(data?.spend_today.tokens||0)}</strong></article><article><span>cost today</span><strong title={exactMoney(data?.spend_today.cost_usd||0)}>{formatMoney(data?.spend_today.cost_usd||0)}</strong></article></div>
          <section class="usage-table automation-controls"><h3>Global controls</h3><p>Enable and disable automation here. Provider, model, budget, and execution configuration remains in Settings.</p><article class="automation-row automation-rule-row" data-setting="automation_enabled"><span class={`state-dot ${data?.controls.automation_enabled?'idle':'running'}`}/><div><strong>Automation engine</strong><span>Runs enabled system observers and custom rules.</span></div><div class="automation-row-actions"><button disabled={!data} onClick={()=>void updateControl('automation_enabled',!data?.controls.automation_enabled)}>{data?.controls.automation_enabled?'disable':'enable'}</button></div></article><article class="automation-row automation-rule-row" data-setting="scan_timeline_enabled"><span class={`state-dot ${data?.controls.scan_timeline_enabled?'idle':'running'}`}/><div><strong>Scan timeline</strong><span>Global permission for Project and per-run timeline controls.</span></div><div class="automation-row-actions"><button disabled={!data} onClick={()=>void updateControl('scan_timeline_enabled',!data?.controls.scan_timeline_enabled)}>{data?.controls.scan_timeline_enabled?'disable':'enable'}</button></div></article></section>
          <section class="usage-table"><h3>System observers</h3><p>Built-in, read-only rules. The three attention observers share one setting, so enabling or disabling one changes the whole attention group.</p>{data?.engine.built_in_rules?.map(rule=><article class="automation-row automation-rule-row"><span class={`state-dot ${rule.enabled?'idle':'running'}`}/><div><div class="automation-rule-heading"><strong>{rule.name}</strong><span class="automation-pill">system</span></div><span>{rule.description}</span><small>when::{rule.trigger} · reads::{rule.input}</small><em><ModelName model={rule.model}/> → {rule.result} · setting::{rule.setting_label}</em></div><div class="automation-row-actions"><button onClick={()=>void updateBuiltin(rule)}>{rule.enabled?'disable':'enable'}{rule.setting_key==='phase7_observers_enabled'?' group':''}</button></div></article>)}</section>
          <section class="usage-table"><h3>Custom rules</h3><p>Canonical rules saved in the daemon rules file. Configure edits the full TOML definition.</p>{data?.engine.rules.length?data.engine.rules.map(rule=><article class="automation-row automation-rule-row"><span class={`state-dot ${rule.enabled?'idle':'running'}`}/><div><div class="automation-rule-heading"><strong>{rule.name}</strong><span class="automation-pill">custom</span></div><small>{rule.id} · when::{rule.trigger} · {rule.shadow?'shadow only':'live'}</small><em>{actionSummary(rule)} · revision::{rule.revision}</em></div><div class="automation-row-actions"><button onClick={()=>void updateRule(rule,{enabled:!rule.enabled})}>{rule.enabled?'disable':'enable'}</button><button onClick={()=>void updateRule(rule,{shadow:!rule.shadow})}>{rule.shadow?'make live':'shadow'}</button></div></article>):<div class="automation-empty"><strong>No custom rules</strong><span>Only the system observers listed here are currently configured.</span><button onClick={onConfigure}>edit custom rules</button></div>}</section>
        </div>}
        {view==='attention'&&<section class="usage-table"><h3>Attention inbox</h3><p>Alerts that may require you to return to a session. Deterministic health checks and observer triage can both create these items.</p>{inbox.length?inbox.map(item=><article class={`automation-row ${item.read_at?'read':''}`}><span class={`state-dot ${item.severity==='warning'?'awaiting':'idle'}`}/><div><strong>{item.title}</strong><span>{item.message}</span><small>{new Date(item.created_at*1000).toLocaleString()} · {item.kind} · {item.severity}</small>{item.evidence.length>0&&<details><summary>why this was raised</summary><pre>{JSON.stringify(item.evidence,null,2)}</pre></details>}</div>{item.session_id&&<button onClick={()=>onOpenSession(item.session_id!)}>open session</button>}<button onClick={()=>void markRead(item)}>{item.read_at?'mark unread':'mark read'}</button></article>):<div class="automation-empty"><strong>Nothing needs your attention</strong><span>New approval, stall, pressure, and conflict notices will appear here.</span></div>}</section>}
        {view==='notes'&&<section class="usage-table"><h3>Run notes</h3><p>Previously called annotations. These are durable findings attached to a run; they never alter the transcript or send text to the agent.</p>{data?.recent_annotations.length?data.recent_annotations.map(item=><article class="automation-row"><span class="state-dot idle"/><div><strong>{item.tag} · {item.content}</strong><small>{new Date(item.created_at*1000).toLocaleString()} · run::{item.agent_run_id}</small><em>created by::{item.provenance} · model::<ModelName model={item.resolved_model} fallback="deterministic"/> · confidence::{item.confidence??'—'} · cost::{money.format(item.cost_usd||0)}</em></div>{item.session_id&&<button onClick={()=>onOpenSession(item.session_id!)}>open session</button>}</article>):<div class="automation-empty"><strong>No run notes yet</strong><span>Generated titles, summaries, handoff suggestions, and prior-resolution hints appear here.</span></div>}</section>}
        {view==='cost'&&<div class="automation-cost">
          {/* The two pots are never summed. Observers bill a metered OpenRouter key by the
              call; agents bill a subscription and their figures are estimates. Adding them
              would produce a number that is true of nothing. */}
          <div class="usage-summary cost-summary">
            <article><span>observers today</span><strong title={exactMoney(data?.spend_today.cost_usd||0)}>{formatMoney(data?.spend_today.cost_usd||0)}</strong><small>{formatCount(spendTotals?.today_calls||0)} calls · {formatCount(spendTotals?.today_tokens||0)} tokens</small></article>
            <article><span>observers · {spendDays}d</span><strong title={exactMoney(spendTotals?.cost_usd||0)}>{formatMoney(spendTotals?.cost_usd||0)}</strong><small>{formatCount(spendTotals?.calls||0)} calls · {formatCount(spendTotals?.tokens||0)} tokens</small></article>
            <article><span>call outcomes</span><strong>{formatCount(calls.total)}</strong><small class={calls.failed?'warn':''}>{formatCount(calls.failed)} failed or cancelled · {formatPercent(calls.failureRate)}</small></article>
            <article><span>agent models</span><strong title={exactMoney(agentSpend)}>{formatMoney(agentSpend)}</strong><small>estimated · {formatCount(agentTokens)} tokens</small></article>
          </div>
          <section class="usage-table">
            <h3>What automation is costing</h3>
            <p>Every billed observer call of the last {spendDays} days, grouped by what asked for it and ranked by the window rather than by today. Same ledger as the headline, so the rows add up to it exactly.</p>
            {spendRows.length?<div class="usage-table-scroll"><table class="data-table cost-table">
              <thead><tr><th>automation</th><th>today</th><th>{spendDays} days</th><th>calls</th><th>tokens</th><th>model</th></tr></thead>
              <tbody>{spendRows.map(row=><tr class={row.enabled?'':'disabled'} key={row.rule_id}>
                <td data-label="automation">
                  <div class="cost-name"><strong>{row.label}</strong><span class={`automation-pill ${row.kind}`}>{row.kind}</span>{row.enabled?null:<span class="automation-pill off">off</span>}</div>
                  <div class="cost-bar" style={`--share:${Math.max(0.015,costShareTotal>0?row.share:row.callShare)}`}/>
                  <small title={row.rule_id}>{row.detail||row.rule_id}</small>
                </td>
                <td data-label="today" title={exactMoney(row.today_cost_usd)}>{formatMoney(row.today_cost_usd)}</td>
                <td data-label={`${spendDays} days`} title={exactMoney(row.cost_usd)}><strong>{formatMoney(row.cost_usd)}</strong>{costShareTotal>0?<em>{formatPercent(row.share)}</em>:null}</td>
                <td data-label="calls" title={integer.format(row.calls)}>{formatCount(row.calls)}</td>
                <td data-label="tokens" title={integer.format(row.tokens)}>{formatCount(row.tokens)}</td>
                <td data-label="model" class="cost-model">{row.models?.length?row.models.map(model=><ModelName model={model}/>):'—'}</td>
              </tr>)}</tbody>
              <tfoot><tr><td data-label="automation">all automation</td><td data-label="today" title={exactMoney(spendTotals?.today_cost_usd||0)}>{formatMoney(spendTotals?.today_cost_usd||0)}</td><td data-label={`${spendDays} days`} title={exactMoney(spendTotals?.cost_usd||0)}>{formatMoney(spendTotals?.cost_usd||0)}</td><td data-label="calls">{formatCount(spendTotals?.calls||0)}</td><td data-label="tokens">{formatCount(spendTotals?.tokens||0)}</td><td/></tr></tfoot>
            </table></div>:<div class="automation-empty"><strong>No observer spend in the last {spendDays} days</strong><span>Enabled observers that never fired, and deterministic health checks, cost nothing and do not appear here.</span></div>}
          </section>
          <section class="usage-table">
            <h3>Agent model spend</h3>
            <p>Your agent subscription usage, which is a different pot of money from the observer spend above and is never added to it. {telemetry?.cost_note?`${telemetry.cost_note}.`:'Backend/model aggregates from the harness, not attributed to individual runs.'}</p>
            {telemetry?.provider_cost_dimensions.length?<div class="usage-table-scroll"><table class="data-table">
              <thead><tr><th>backend / model</th><th>cost</th><th>tokens</th><th>source</th></tr></thead>
              <tbody>{telemetry.provider_cost_dimensions.map(row=><tr key={`${row.backend}:${row.model}`}>
                <td data-label="backend / model"><strong>{row.backend}</strong> · <ModelName model={row.model}/></td>
                <td data-label="cost" title={exactMoney(row.cost_usd)}>{formatMoney(row.cost_usd)}{row.cost_is_estimate?<em>est</em>:null}</td>
                <td data-label="tokens" title={integer.format(row.tokens)}>{formatCount(row.tokens)}</td>
                <td data-label="source" class="cost-source">{row.attribution}</td>
              </tr>)}</tbody>
              <tfoot><tr><td data-label="backend / model">all backends</td><td data-label="cost" title={exactMoney(agentSpend)}>{formatMoney(agentSpend)}</td><td data-label="tokens">{formatCount(agentTokens)}</td><td/></tr></tfoot>
            </table></div>:<div class="automation-empty"><strong>No agent cost aggregates</strong><span>Harness usage reporting has not produced per-model figures yet.</span></div>}
          </section>
        </div>}
        {view==='health'&&<>
          <section class="usage-table"><h3>What all-session health watches</h3><p>“Fleet” means every live and recent session considered together. These passive checks do not use an LLM and cannot control an agent.</p><div class="automation-health-grid">{healthSignals.map(([title,description])=><article><strong>{title}</strong><span>{description}</span></article>)}<article><strong>Periodic attention digest · {attentionObserversEnabled?'on':'off'}</strong><span>The optional attention-observer setting also summarizes unread attention records every 30 minutes.</span></article></div></section>
          {/* Ten nowrap columns of raw seconds and ten-figure token counts did not fit any
              window, so the one table nobody could read was the one meant to be scanned.
              Rates collapse into one cell, durations and counts are formatted at human scale,
              and cost moved out to the spend tab where it is asked about. */}
          <section class="usage-table"><h3>Observed workload telemetry</h3><p>Descriptive correlation only; it does not rank agents or claim that one model caused an outcome. Costs live under <strong>spend</strong>.</p>{telemetry?.dimensions.length?<div class="usage-table-scroll"><table class="data-table"><thead><tr><th>backend / model</th><th>runs</th><th>avg duration</th><th>context final → peak</th><th>per run</th><th>completion evidence</th><th>tokens</th></tr></thead><tbody>{telemetry.dimensions.map(row=><tr key={`${row.backend}:${row.model}`}><td data-label="backend / model"><strong>{row.backend}</strong> · <ModelName model={row.model}/></td><td data-label="runs">{formatCount(row.ended_runs)}<em>/{formatCount(row.runs)} ended</em></td><td data-label="avg duration">{formatDuration(row.average_duration_s)}</td><td data-label="context final → peak">{formatPercent(row.average_final_context_pct)} → {formatPercent(row.average_peak_context_pct)}</td><td data-label="per run" class="telemetry-rates"><span>{row.turns_per_run.toFixed(1)}<em>turns</em></span><span>{row.stalls_per_run.toFixed(2)}<em>stalls</em></span><span>{row.approvals_per_run.toFixed(2)}<em>approvals</em></span></td><td data-label="completion evidence">{formatCount(row.completion_evidence_runs)}<em>/{formatCount(row.runs)} runs · {formatCount(row.completion_evidence_count)} signals</em></td><td data-label="tokens" title={integer.format((row.tokens_in||0)+(row.tokens_out||0))}>{formatCount((row.tokens_in||0)+(row.tokens_out||0))}</td></tr>)}</tbody></table></div>:<div class="automation-empty"><strong>No workload telemetry yet</strong><span>Figures appear once runs have started and ended under an observed harness.</span></div>}</section>
          <section class="usage-table"><h3>What happened while I was away?</h3><p>Summarizes attention items and run notes since your last terminal attach or input.</p><button onClick={async()=>{const report=await api('GET','/api/attention/absence');setAbsenceReport(report);setMessage('Away report refreshed.')}}>generate away report</button>{absenceReport&&<pre>{JSON.stringify(absenceReport,null,2)}</pre>}</section>
        </>}
        {view==='knowledge'&&<>
          <section class="usage-table"><h3>Learned fixes</h3><p>A learned fix is a reviewed error → demonstrated resolution pair from a completed run. The searchable collection was previously labelled “Experience index.” A matching live failure can only create a run note; nothing is injected into the agent.</p>{experiences.length?experiences.map(item=><article class="automation-row"><span class="state-dot idle"/><div><strong>{item.error_summary}</strong><span>{item.resolution_summary}</span><small>{item.backend} · source run::{item.source_run_id} · confidence::{item.confidence??'—'}</small></div></article>):<div class="automation-empty"><strong>No learned fixes</strong><span>Use the reviewed batch below to extract demonstrated fixes from completed runs.</span></div>}</section>
          <section class="usage-table"><h3>Build knowledge from completed runs</h3><p>Select up to 25 ended runs. Estimate first, then explicitly confirm the bounded observer batch. Results remain preview/export data and never modify a repository.</p><label>finding type<select value={batchKind} onChange={event=>{setBatchKind(event.currentTarget.value);setBatchPreview(null)}}><option value="experience">learned fixes</option><option value="procedure">repeatable procedures</option><option value="doc-drift">documentation drift candidates</option><option value="convention">observed conventions</option><option value="regression">regression candidates</option></select></label><div class="batch-run-picker">{batchCandidates.length?batchCandidates.map(run=><label><input type="checkbox" checked={batchRuns.has(run.id)} disabled={!batchRuns.has(run.id)&&batchRuns.size>=25} onChange={()=>toggleBatchRun(run.id)}/><span><strong>[{run.backend}] {runDisplayName(run)}</strong><small>{run.project_label||'Ungrouped'} · {new Date(run.spawned_at*1000).toLocaleString()}</small></span></label>):<p>No ended agent transcripts are available.</p>}</div><div class="automation-inline"><span>{batchRuns.size}/25 selected</span><button disabled={!batchRuns.size} onClick={()=>void previewBatch(false)}>estimate</button><button class="primary" disabled={!batchPreview||!batchRuns.size} onClick={()=>void previewBatch(true)}>start reviewed batch</button></div>{batchPreview&&<pre>{JSON.stringify(batchPreview,null,2)}</pre>}</section>
          <section class="usage-table"><h3>Recent knowledge batches</h3>{batches.length?batches.map(batch=><details><summary>{batch.kind} · {batch.status} · {batch.run_ids.length} runs</summary><small>{new Date(batch.created_at*1000).toLocaleString()} · {batch.calls} calls · {integer.format(batch.tokens)} tokens · {money.format(batch.cost_usd)}</small>{batch.error&&<p class="usage-error">{batch.error}</p>}<pre>{JSON.stringify(batch.preview,null,2)}</pre></details>):<p>No reviewed batches have run.</p>}</section>
        </>}
        {view==='diagnostics'&&<>
          <section class="usage-table"><h3>Provider and safety boundary</h3><p>OpenRouter key::{data?.provider.secret.source||'none'} · cheap::<ModelName model={data?.provider.cheap_model} fallback="unset"/> · standard::<ModelName model={data?.provider.standard_model} fallback="unset"/></p><p>Observers can create run notes and attention items only. PTY writes, approvals, arbitrary HTTP, workers, repository rules, and model-directed actions are unavailable.</p><p>queue::{data?.engine.queue.size||0}/{data?.engine.queue.capacity||0} · dropped::{data?.engine.queue.dropped||0} · chain loops rejected::{data?.engine.queue.loop_rejections||0}</p>{data?.engine.diagnostic&&<p class="usage-error">{data.engine.diagnostic}</p>}</section>
          <section class="usage-table"><h3>Historical event dry-run</h3><p>Evaluate custom rules against one persisted event without writing a firing, action, checkpoint, model call, or spend record.</p><div class="automation-inline"><input type="number" placeholder="event sequence" value={eventSeq} onInput={event=>setEventSeq(event.currentTarget.value)}/><button disabled={!eventSeq} onClick={()=>void runDry()}>dry-run</button></div>{dryRun&&<pre>{JSON.stringify(dryRun,null,2)}</pre>}</section>
          <section class="usage-table"><h3>Recent rule execution</h3><h4>Firings</h4>{data?.recent_firings.map(firing=><details><summary>{firing.rule_id} · {firing.status} · event::{firing.event_seq}</summary><small>{new Date(firing.created_at*1000).toLocaleString()} · trigger::{firing.event_type}{firing.shadow?' · shadow':''}</small>{firing.error&&<p>{firing.error}</p>}<pre>{JSON.stringify(firing.condition_trace,null,2)}</pre></details>)}<h4>Action results</h4>{data?.recent_action_results.map(item=><details><summary>{item.kind} · {item.status} · action::{item.action_index}</summary><small>firing::{item.firing_id} · {new Date(item.created_at*1000).toLocaleString()}</small>{item.error&&<p>{item.error}</p>}<pre>{JSON.stringify(item.detail,null,2)}</pre></details>)}<h4>Observer calls</h4>{data?.recent_observer_calls.map(item=><details><summary>{item.rule_id} · {item.status} · <ModelName model={item.resolved_model||item.requested_model}/></summary><small>{item.input_tokens+item.output_tokens} tokens · {money.format(item.cost_usd||0)} · {item.latency_ms??'-'}ms · input::{item.input_bytes} bytes</small><small>provider::{item.provider_name||'unknown'} · finish::{item.finish_reason||'unknown'} · response::{item.response_content_type||'unknown'}[{item.response_content_length??0}] · http::{item.http_status??'unknown'} · retryable::{item.retryable===undefined||item.retryable===null?'unknown':item.retryable?'yes':'no'}</small>{item.error&&<p>{item.error}</p>}</details>)}</section>
          <details class="usage-table automation-advanced"><summary>Research-only delivery-readiness diagnostics</summary><p>Provider-neutral replay and live evidence classify each root session as safe, blocked, or unknown. Actuation remains unauthorized in every case.</p><p>contract::v{injectionSafety?.version||'—'} · authorized::{injectionSafety?.authorizes_actuation?'yes':'no'} · transitions::{injectionSafety?.shadow_metrics.transitions||0} · tracked::{injectionSafety?.shadow_metrics.tracked_sessions||0}</p>{injectionSafety?.sessions.map(item=><article class="automation-row"><span class={`state-dot ${item.delivery_state==='safe'?'idle':item.delivery_state==='blocked'?'awaiting':'running'}`}/><div><strong>{item.backend} · {item.session_id}</strong><small>agent::{item.state} · delivery::{item.delivery_state} · candidate::{item.candidate_safe?'yes':'no'} · authorized::no</small><em>{item.diagnostic}</em><details><summary>bounded evidence</summary><pre>{JSON.stringify({checks:item.checks,evidence:item.evidence},null,2)}</pre></details></div></article>)}<h4>Parser coverage</h4>{injectionSafety?.parser_coverage.map(item=><article class="automation-row"><span class={`state-dot ${item.status==='ready'?'idle':item.status==='degraded'?'awaiting':'running'}`}/><div><strong>{item.backend} · {item.session_id}</strong><small>schema::{item.schema_version||'—'} · {item.status} · recognized::{item.recognized} · unknown::{item.unknown} ({item.unknown_rate===undefined||item.unknown_rate===null?'—':`${Math.round(item.unknown_rate*100)}%`})</small><em>{item.diagnostic||'No transcript diagnostics yet.'}</em>{Object.keys(item.unknown_signatures).length>0&&<pre>{JSON.stringify(item.unknown_signatures,null,2)}</pre>}</div></article>)}</details>
        </>}
      </main>
      <footer><span>Out of band · bounded · budgeted · never controls an agent</span><button onClick={()=>void load()}>refresh</button></footer>
    </section>
    {showHelp&&<div class="usage-layer automation-help-layer" role="dialog" aria-modal="true" aria-label="How automation works" onMouseDown={event=>event.target===event.currentTarget&&setShowHelp(false)}>
      <section class="usage-panel automation-panel" ref={helpPanel}>
        <header><div><span>AUTOMATION</span><strong>How it works</strong></div><div class="usage-header-actions"><button aria-label="Close help" onClick={()=>setShowHelp(false)}>×</button></div></header>
        <main>
          <section class="usage-table automation-explainer"><h3>The pipeline</h3><div class="automation-flow"><article><strong>1 · Agent activity</strong><span>Observed harnesses emit normalized lifecycle, turn, tool, approval, and context events.</span></article><article><strong>2 · Matching automation</strong><span>A system or custom rule checks the event. Most health checks are deterministic.</span></article><article><strong>3 · Optional observer</strong><span>A bounded transcript slice may be sent to the configured OpenRouter model.</span></article><article><strong>4 · Visible result</strong><span>The result becomes a run note or an attention item. It never types into the terminal.</span></article></div></section>
          <section class="usage-table"><h3>Terms used here</h3><div class="automation-term-grid"><article><strong>Observer</strong><span>A read-only LLM call over a bounded transcript slice—not another interactive agent.</span></article><article><strong>Run note</strong><span>Durable metadata attached to one agent run, such as a title, summary, or suggestion.</span></article><article><strong>Attention</strong><span>An inbox item intended to bring you back to a session that may need you.</span></article><article><strong>All-session health</strong><span>Evidence calculated across the complete set of live and recent sessions—the “fleet.”</span></article><article><strong>Learned fix</strong><span>A reviewed error → resolution finding extracted from completed session history.</span></article></div></section>
        </main>
      </section>
    </div>}
  </div>
}
