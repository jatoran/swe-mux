import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { Dropdown } from './Dropdown'
import { formatCount, formatMoney } from './automationCost'
import type { SpendBreakdown } from './automationCost'
import { displayModelName } from './modelDisplay'
import { ModelName } from './ModelName'
import { AutomationSpendView } from './AutomationSpendView'
import { AttentionInbox } from './AttentionInbox'
import { useModalFocus } from './modalFocus'
import { runDisplayName } from './sessionNames'
import { AutomationPolicyView } from './AutomationPolicyView'
import { AutomationPolicyMatrix } from './AutomationMatrix'
import type { MatrixData } from './AutomationMatrix'
import type { StartingSetCatalog } from './projectCreate'
import { revealSetting } from './settingReveal'
import type { Project } from './types'

type Rule={id:string;name:string;enabled:boolean;shadow:boolean;trigger:string;revision:string;source:string;scope?:string;actions?:Array<{kind:string;model?:string;on_result?:{kind?:string}}>}
type BuiltInRule={id:string;name:string;enabled:boolean;shadow:boolean;trigger:string;source:'builtin';automation_id:string;setting_label:string;input:string;model:string;result:string;description:string}
type Firing={id:string;event_seq:number;event_type:string;rule_id:string;status:string;shadow:number;created_at:number;error?:string;condition_trace:Array<{field:string;matched:boolean;actual:unknown;expected:unknown}>}
type ActionResult={id:string;firing_id:string;action_index:number;kind:string;status:string;detail:unknown;error?:string;created_at:number}
type ObserverCall={id:string;firing_id:string;rule_id:string;status:string;requested_model?:string;resolved_model?:string;input_bytes:number;input_tokens:number;output_tokens:number;cost_usd?:number;latency_ms?:number;provider_name?:string;finish_reason?:string;response_content_type?:string;response_content_length?:number;http_status?:number;retryable?:number;error?:string;created_at:number}
type AutomationData={
  controls:{automation_enabled:boolean;scan_timeline_enabled:boolean}
  engine:{enabled:boolean;diagnostic?:string;rules:Rule[];built_in_rules:BuiltInRule[];queue:{size:number;capacity:number;dropped:number;loop_rejections:number;title_retries?:{pending:number;exhausted:number}};capabilities:{triggers:string[];observer_schemas:string[]}}
  provider:{secret:{configured:boolean;source:string};models:{models:unknown[];stale:boolean;error?:string};cheap_model:string;standard_model:string}
  spend_today:{tokens:number;cost_usd:number};observer_calls:Record<string,number>;annotations:Record<string,number>;unread_notifications:number
  recent_firings:Firing[];recent_action_results:ActionResult[];recent_observer_calls:ObserverCall[]
  spend_breakdown?:SpendBreakdown
}
type Experience={id:string;backend:string;error_summary:string;resolution_summary:string;source_run_id:string;confidence?:number;created_at:number}
type InjectionSafety={version:number;research_only:boolean;authorizes_actuation:boolean;shadow_metrics:{evaluations:Record<string,number>;reasons:Record<string,number>;tracked_sessions:number;unknown_duration_s:number;transitions:number};parser_coverage:Array<{session_id:string;backend:string;schema_version?:string;status:string;recognized:number;unknown:number;unknown_rate?:number;unknown_signatures:Record<string,number>;diagnostic?:string}>;sessions:Array<{session_id:string;backend:string;state:string;delivery_state:'safe'|'blocked'|'unknown';reason:string;reasons:string[];candidate_safe:boolean;authorized:boolean;diagnostic:string;checks:Record<string,boolean|null>;evidence:Record<string,unknown>}>}
type EndedRun={id:string;backend:string;name:string;generated_title?:string;auto_named?:number;cwd:string;spawned_at:number;exited_at?:number;transcript_path?:string;project_label?:string}
type ObserverBatch={id:string;kind:string;status:string;run_ids:string[];preview:unknown[];calls:number;tokens:number;cost_usd:number;error?:string;created_at:number;completed_at?:number}
type RulesFile={text:string}
type GrantsCatalogue={project_starting_sets:StartingSetCatalog;llm?:{ready:boolean;reason:string}}
// Three tabs, and the tab IS the question:
//
// - **Policy** answers and edits what may run and where: the master switch,
//   the starting-set presets, the Global × Project matrix (this workspace is
//   the one editor that may turn an automation off), and the install-wide
//   limits and rule corpus behind disclosures.
// - **Usage** answers what it costs, as the same `AutomationSpendView` the
//   Usage dialog's Automation segment draws - one component, two homes, so the
//   two readings can never drift.
// - **Activity** answers what it did: the attention inbox (the same
//   `AttentionInbox` component as the Alerts drawer tab, same endpoints and
//   read state, so the two surfaces cannot disagree), the firing/observer
//   trail, learned fixes, and diagnostics.
export type AutomationView='policy'|'usage'|'activity'

const VIEWS:Array<{id:AutomationView;label:string}>=[
  {id:'policy',label:'Policy'},
  {id:'usage',label:'Usage'},
  {id:'activity',label:'Activity'},
]
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

export function AutomationDashboard({onClose,onOpenSession,projects=[],initialView='policy',initialProjectId='',initialSetting,revealToken=0}:{
  onClose:()=>void;onOpenSession:(id:string)=>void;initialView?:AutomationView
  projects?:Project[];initialProjectId?:string;initialSetting?:string;revealToken?:number
}){
  const [data,setData]=useState<AutomationData|null>(null)
  const [experiences,setExperiences]=useState<Experience[]>([])
  const [experienceQuery,setExperienceQuery]=useState('')
  const [selectedExperienceId,setSelectedExperienceId]=useState('')
  const [injectionSafety,setInjectionSafety]=useState<InjectionSafety|null>(null)
  const [matrix,setMatrix]=useState<MatrixData|null>(null)
  const [catalogue,setCatalogue]=useState<GrantsCatalogue|null>(null)
  const [graphProjectId,setGraphProjectId]=useState(initialProjectId)
  const [view,setView]=useState<AutomationView>(initialView)
  const [showHelp,setShowHelp]=useState(false)
  const [message,setMessage]=useState('Loading automation state…')
  const [error,setError]=useState('')
  const [eventSeq,setEventSeq]=useState('')
  const [dryRun,setDryRun]=useState<unknown>(null)
  const [batchKind,setBatchKind]=useState('experience')
  const [batchRuns,setBatchRuns]=useState<Set<string>>(new Set())
  const [batchCandidates,setBatchCandidates]=useState<EndedRun[]>([])
  const [batches,setBatches]=useState<ObserverBatch[]>([])
  const [batchPreview,setBatchPreview]=useState<Record<string,unknown>|null>(null)
  // The rules.toml editor is this dashboard's, not Settings': a rule's definition, its
  // live/shadow state, and the firings it produces are one object, and splitting the text
  // from the rest across two overlays is how a Save in one overwrote an edit in the other.
  const [rulesText,setRulesText]=useState('')
  const [savedRulesText,setSavedRulesText]=useState('')
  const [rulesOpen,setRulesOpen]=useState(false)
  const [rulesError,setRulesError]=useState('')
  const panel=useRef<HTMLElement>(null)
  const helpPanel=useRef<HTMLElement>(null)
  useModalFocus(panel,onClose,!showHelp)
  useModalFocus(helpPanel,()=>setShowHelp(false),showHelp)
  useEffect(()=>{
    setView(initialView)
    if(initialProjectId)setGraphProjectId(initialProjectId)
  },[initialView,initialProjectId,revealToken])

  const load=async()=>{
    try{
      const [dashboard,experience,injection,history,batchHistory,projectMatrix,rulesFile,grants]=await Promise.all([
        api<AutomationData>('GET','/api/automation/dashboard'),
        api<{items:Experience[]}>('GET','/api/experiences'),
        api<InjectionSafety>('GET','/api/automation/injection-safety'),
        api<{items:EndedRun[]}>('GET','/api/history?limit=100'),
        api<{items:ObserverBatch[]}>('GET','/api/automation/batches'),
        api<MatrixData>('GET','/api/automation/projects'),
        api<RulesFile>('GET','/api/automation/rules'),
        api<GrantsCatalogue>('GET','/api/grants'),
      ])
      setData(dashboard);setExperiences(experience.items);setInjectionSafety(injection);setBatchCandidates(history.items.filter(item=>Boolean(item.exited_at&&item.transcript_path)));setBatches(batchHistory.items);setMatrix(projectMatrix);setCatalogue(grants)
      setRulesText(current=>current===savedRulesText?rulesFile.text:current);setSavedRulesText(rulesFile.text)
      const enabledBuiltins=(dashboard.engine.built_in_rules||[]).filter(item=>item.enabled).length
      setMessage(`ready · ${enabledBuiltins} system observers on · ${dashboard.engine.rules.length} custom rules · ${dashboard.unread_notifications} unread`);setError('')
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  useEffect(()=>{void load()},[])

  const runDry=async()=>{try{setDryRun(null);setMessage('Evaluating selected historical event with side effects disabled…');const result=await api('POST','/api/automation/dry-run',{event_seq:Number(eventSeq)});setDryRun(result);setMessage('Dry-run complete. No actions or model calls were executed.')}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  const updateRule=async(rule:Rule,change:Partial<Pick<Rule,'enabled'|'shadow'>>)=>{try{setMessage(`Updating ${rule.name}…`);await api('PATCH',`/api/automation/rules/${encodeURIComponent(rule.id)}`,change);await load()}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  const saveRules=async()=>{
    try{
      setRulesError('');setMessage('Validating rules.toml…')
      await api('PUT','/api/automation/rules?validate=1',{text:rulesText})
      await api('PUT','/api/automation/rules',{text:rulesText})
      setSavedRulesText(rulesText);setMessage('Rules saved and reloaded.')
      await load()
    }catch(cause){setRulesError(cause instanceof Error?cause.message:String(cause))}
  }
  const previewBatch=async(confirm=false)=>{try{const run_ids=Array.from(batchRuns);const result=await api<Record<string,unknown>>('POST','/api/automation/batches',{kind:batchKind,run_ids,confirm,...(confirm?{preview_token:String(batchPreview?.preview_token||'')}:{})});setBatchPreview(result);setMessage(confirm?'Batch started. Results remain preview/export-only.':'Batch estimate ready; review before starting.');if(confirm)await load()}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  const toggleBatchRun=(id:string)=>{setBatchPreview(null);setBatchRuns(current=>{const next=new Set(current);if(next.has(id))next.delete(id);else if(next.size<25)next.add(id);return next})}
  const unread=data?.unread_notifications||0
  const failedExecutions=(data?.recent_firings||[]).filter(item=>item.status==='failed').length+(data?.recent_action_results||[]).filter(item=>item.status==='failed').length+(data?.recent_observer_calls||[]).filter(item=>item.status==='failed').length
  const filteredExperiences=experiences.filter(item=>`${item.error_summary} ${item.resolution_summary} ${item.backend}`.toLowerCase().includes(experienceQuery.trim().toLowerCase()))
  const selectedExperience=filteredExperiences.find(item=>item.id===selectedExperienceId)||filteredExperiences[0]
  const matrixProject=matrix?.projects.find(item=>item.project_id===graphProjectId)||matrix?.projects.find(item=>item.enabled.some(id=>id!=='session_control'))||matrix?.projects[0]
  const policyProject=projects.find(item=>item.id===matrixProject?.project_id)||null
  useEffect(()=>{
    // One reveal over the whole panel: `revealSetting` opens the disclosures
    // above the marked control, so a limits field inside the drawer and a
    // matrix cell arrive the same way.
    if(view!=='policy'||!initialSetting||!panel.current)return
    return revealSetting(panel.current,initialSetting)
  },[view,initialSetting,revealToken,matrix?matrixProject?.project_id:''])

  return <div class="usage-layer automation-layer" role="dialog" aria-modal="true" aria-label="Automation" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="usage-panel automation-panel" ref={panel}>
      <header><div><span>AUTOMATION</span><strong>Policy · usage · activity</strong></div><div class="usage-header-actions"><button class="automation-help-btn" aria-label="How automation works" title="How it works" onClick={()=>setShowHelp(true)}>?</button><button aria-label="Close automation" onClick={onClose}>×</button></div></header>
      <div class="segmented-tabs automation-tabs" role="tablist" aria-label="Automation view">{VIEWS.map(item=><button key={item.id} role="tab" aria-selected={view===item.id} class={view===item.id?'active':''} onClick={()=>setView(item.id)}>{item.label}{item.id==='activity'&&unread?<span class="automation-tab-badge">{unread}</span>:null}</button>)}</div>
      <div class={`usage-progress ${!data&&!error?'running':''}`} role="status" aria-live="polite"><span>{error?'!':data?'·':'◌'}</span><strong>{error||message}</strong></div>
      <main>
        {view==='policy'&&<div class="usage-tables automation-policy-tab">
          {matrix?<AutomationPolicyMatrix
            data={matrix}
            projectId={matrixProject?.project_id||''}
            onSelectProject={setGraphProjectId}
            catalog={catalogue?.project_starting_sets}
            llmReady={catalogue?.llm?.ready}
            onError={setError}
            onChanged={load}
          />:<p>Loading the policy matrix…</p>}
          {matrix&&!matrix.projects.length&&<div class="automation-empty"><strong>No Projects registered</strong><span>Automations run only on registered Projects that opted in.</span></div>}
          <details class="automation-advanced automation-limits-drawer">
            <summary>Limits &amp; budgets<em> · global only</em></summary>
            <AutomationPolicyView initialSetting={initialSetting} revealToken={revealToken}/>
          </details>
          <section class="usage-table automation-custom-rules">
            <h3>Custom rules<span class="automation-pill scope global">global</span></h3>
            <p>User-authored rules in the daemon rules file. They run install-wide - one enable per rule, in every Project - unlike the matrix above, which is also where the built-in observers (the session titler and the attention observers) are switched, globally and per Project like every other automation. The editor below owns the full TOML definition.</p>
            {(data?.engine.queue.title_retries?.pending||0)>0&&<p class="automation-title-refining">{data?.engine.queue.title_retries?.pending} session title{(data?.engine.queue.title_retries?.pending||0)===1?'':'s'} still refining - a provisional title revises until the task settles.</p>}
            {data?.engine.rules.length?data.engine.rules.map(rule=><article class="automation-row automation-rule-row"><span class={`state-dot ${rule.enabled?'idle':'running'}`}/><div><div class="automation-rule-heading"><strong>{rule.name}</strong><span class="automation-pill scope global">global</span><span class="automation-pill">custom</span></div><small>{rule.id} · when::{rule.trigger} · {rule.shadow?'shadow only':'live'}</small><em>{actionSummary(rule)} · revision::{rule.revision}</em></div><div class="automation-row-actions"><button onClick={()=>void updateRule(rule,{enabled:!rule.enabled})}>{rule.enabled?'disable':'enable'}</button><button onClick={()=>void updateRule(rule,{shadow:!rule.shadow})}>{rule.shadow?'make live':'shadow'}</button></div></article>):<div class="automation-empty"><strong>No custom rules</strong><span>The built-in observers are rows in the matrix above; nothing user-authored is configured.</span><button onClick={()=>setRulesOpen(true)}>edit custom rules</button></div>}
            <details class="automation-advanced automation-rules-editor" open={rulesOpen} onToggle={event=>setRulesOpen(event.currentTarget.open)}><summary>rules.toml editor{rulesText!==savedRulesText?' · unsaved':''}</summary>
              <p>Saving validates the complete file first; an invalid file changes nothing. Repository .swe-mux/rules.toml files remain diagnostic and inert.</p>
              <textarea value={rulesText} spellcheck={false} onInput={event=>setRulesText(event.currentTarget.value)}/>
              {rulesError&&<p class="usage-error" role="alert">{rulesError}</p>}
              <div class="automation-inline"><button class="primary" disabled={rulesText===savedRulesText} onClick={()=>void saveRules()}>validate + save</button><button disabled={rulesText===savedRulesText} onClick={()=>{setRulesText(savedRulesText);setRulesError('')}}>discard edits</button></div>
            </details>
          </section>
        </div>}
        {view==='usage'&&<div class="usage-tables">
          <div class="usage-summary"><article><span>automation</span><strong>{data?.engine.enabled?'on':'off'}</strong></article><article><span>calls today</span><strong>{formatCount(data?.spend_breakdown?.totals?.today_calls||0)}</strong></article><article><span>tokens today</span><strong title={integer.format(data?.spend_today.tokens||0)}>{formatCount(data?.spend_today.tokens||0)}</strong></article><article><span>cost today</span><strong>{formatMoney(data?.spend_today.cost_usd||0)}</strong></article></div>
          <AutomationSpendView/>
        </div>}
        {view==='activity'&&<div class="usage-tables automation-activity-tab">
          <section class="usage-table automation-activity-alerts"><h3>Needs you</h3>
            <p>The attention inbox - the same items, read state, and actions as the Alerts drawer tab, so the two surfaces can never disagree.</p>
            <AttentionInbox onOpenSession={onOpenSession} project={policyProject||undefined}/>
          </section>
          <section class="usage-table"><h3>Recent rule execution</h3><h4>Firings</h4>{data?.recent_firings.map(firing=><details><summary>{firing.rule_id} · {firing.status} · event::{firing.event_seq}</summary><small>{new Date(firing.created_at*1000).toLocaleString()} · trigger::{firing.event_type}{firing.shadow?' · shadow':''}</small>{firing.error&&<p>{firing.error}</p>}<pre>{JSON.stringify(firing.condition_trace,null,2)}</pre></details>)}<h4>Action results</h4>{data?.recent_action_results.map(item=><details><summary>{item.kind} · {item.status} · action::{item.action_index}</summary><small>firing::{item.firing_id} · {new Date(item.created_at*1000).toLocaleString()}</small>{item.error&&<p>{item.error}</p>}<pre>{JSON.stringify(item.detail,null,2)}</pre></details>)}<h4>Observer calls</h4>{data?.recent_observer_calls.map(item=><details><summary>{item.rule_id} · {item.status} · <ModelName model={item.resolved_model||item.requested_model}/></summary><small>{item.input_tokens+item.output_tokens} tokens · {money.format(item.cost_usd||0)} · {item.latency_ms??'-'}ms · input::{item.input_bytes} bytes</small><small>provider::{item.provider_name||'unknown'} · finish::{item.finish_reason||'unknown'} · response::{item.response_content_type||'unknown'}[{item.response_content_length??0}] · http::{item.http_status??'unknown'} · retryable::{item.retryable===undefined||item.retryable===null?'unknown':item.retryable?'yes':'no'}</small>{item.error&&<p>{item.error}</p>}</details>)}</section>
          <section class="usage-table"><h3>Learned fixes</h3><p>Reviewed error → resolution pairs extracted from completed runs.</p>
            <input class="automation-knowledge-search" type="search" value={experienceQuery} placeholder="Search errors, tools, or resolutions…" onInput={event=>setExperienceQuery(event.currentTarget.value)}/>
            {filteredExperiences.length?<div class="automation-knowledge-browser"><div class="automation-knowledge-list">{filteredExperiences.map(item=><button class={selectedExperience?.id===item.id?'active':''} onClick={()=>setSelectedExperienceId(item.id)}><strong>{item.error_summary}</strong><small>{item.backend} · {Math.round((item.confidence||0)*100)}%</small></button>)}</div>{selectedExperience&&<article class="automation-knowledge-detail"><h4>{selectedExperience.error_summary}</h4><span class="automation-pill">reviewed</span><p><strong>Demonstrated resolution</strong></p><p>{selectedExperience.resolution_summary}</p><small>{selectedExperience.backend} · source run::{selectedExperience.source_run_id} · confidence::{selectedExperience.confidence??'—'}</small><div class="automation-inline"><button onClick={()=>void navigator.clipboard.writeText(selectedExperience.resolution_summary)}>Copy resolution</button></div></article>}</div>:<div class="automation-empty"><strong>No learned fixes</strong><span>Use the reviewed batch below to extract demonstrated fixes from completed runs.</span></div>}
          </section>
          <details class="automation-advanced"><summary>Build knowledge from completed runs</summary>
            <section class="usage-table"><p>Select up to 25 ended runs; estimate first, then confirm the bounded batch. Results are preview/export only and never modify a repository.</p><label>finding type<Dropdown value={batchKind} onChange={value=>{setBatchKind(value);setBatchPreview(null)}} options={[{value:'experience',label:'learned fixes'},{value:'procedure',label:'repeatable procedures'},{value:'doc-drift',label:'documentation drift candidates'},{value:'convention',label:'observed conventions'},{value:'regression',label:'regression candidates'}]}/></label><div class="batch-run-picker">{batchCandidates.length?batchCandidates.map(run=><label><input type="checkbox" checked={batchRuns.has(run.id)} disabled={!batchRuns.has(run.id)&&batchRuns.size>=25} onChange={()=>toggleBatchRun(run.id)}/><span><strong>[{run.backend}] {runDisplayName(run)}</strong><small>{run.project_label||'Ungrouped'} · {new Date(run.spawned_at*1000).toLocaleString()}</small></span></label>):<p>No ended agent transcripts are available.</p>}</div><div class="automation-inline"><span>{batchRuns.size}/25 selected</span><button disabled={!batchRuns.size} onClick={()=>void previewBatch(false)}>estimate</button><button class="primary" disabled={!batchPreview||!batchRuns.size} onClick={()=>void previewBatch(true)}>start reviewed batch</button></div>{batchPreview&&<pre>{JSON.stringify(batchPreview,null,2)}</pre>}</section>
            <section class="usage-table"><h3>Recent knowledge batches</h3>{batches.length?batches.map(batch=><details><summary>{batch.kind} · {batch.status} · {batch.run_ids.length} runs</summary><small>{new Date(batch.created_at*1000).toLocaleString()} · {batch.calls} calls · {integer.format(batch.tokens)} tokens · {money.format(batch.cost_usd)}</small>{batch.error&&<p class="usage-error">{batch.error}</p>}<pre>{JSON.stringify(batch.preview,null,2)}</pre></details>):<p>No reviewed batches have run.</p>}</section>
          </details>
          <section class="usage-table automation-diagnostics"><h3>Diagnostics</h3>
            <div class="usage-summary automation-diagnostic-summary"><article><span>queue</span><strong>{data?.engine.queue.size||0}/{data?.engine.queue.capacity||0}</strong></article><article><span>dropped</span><strong>{data?.engine.queue.dropped||0}</strong></article><article><span>provider</span><strong>{data?.provider.secret.source||'none'}</strong></article><article><span>recent failures</span><strong>{failedExecutions}</strong></article></div>
            {failedExecutions>0&&<div class="automation-diagnostic-issues"><h4>Needs review</h4>{data?.recent_observer_calls.filter(item=>item.status==='failed').map(item=><article class="automation-row"><span class="state-dot awaiting"/><div><strong>{item.rule_id} · observer failed</strong><small>{item.error||`HTTP ${item.http_status??'unknown'} · ${item.finish_reason||'unknown finish'}`}</small></div></article>)}</div>}
            <p>OpenRouter key::{data?.provider.secret.source||'none'} · cheap::<ModelName model={data?.provider.cheap_model} fallback="unset"/> · standard::<ModelName model={data?.provider.standard_model} fallback="unset"/></p>
            <p>Observers can create run notes and attention items only. PTY writes, approvals, arbitrary HTTP, workers, repository rules, and model-directed actions are unavailable.</p>
            <p>queue::{data?.engine.queue.size||0}/{data?.engine.queue.capacity||0} · dropped::{data?.engine.queue.dropped||0} · chain loops rejected::{data?.engine.queue.loop_rejections||0}</p>
            {data?.engine.diagnostic&&<p class="usage-error">{data.engine.diagnostic}</p>}
            <details class="automation-advanced"><summary>Historical event dry-run</summary><p>Evaluate custom rules against one persisted event with every side effect disabled.</p><div class="automation-inline"><input type="number" placeholder="event sequence" value={eventSeq} onInput={event=>setEventSeq(event.currentTarget.value)}/><button disabled={!eventSeq} onClick={()=>void runDry()}>dry-run</button></div>{dryRun&&<pre>{JSON.stringify(dryRun,null,2)}</pre>}</details>
            <details class="automation-advanced"><summary>Research-only delivery-readiness diagnostics</summary><p>Provider-neutral replay and live evidence classify each root session as safe, blocked, or unknown. Actuation remains unauthorized in every case.</p><p>contract::v{injectionSafety?.version||'—'} · authorized::{injectionSafety?.authorizes_actuation?'yes':'no'} · transitions::{injectionSafety?.shadow_metrics.transitions||0} · tracked::{injectionSafety?.shadow_metrics.tracked_sessions||0}</p>{injectionSafety?.sessions.map(item=><article class="automation-row"><span class={`state-dot ${item.delivery_state==='safe'?'idle':item.delivery_state==='blocked'?'awaiting':'running'}`}/><div><strong>{item.backend} · {item.session_id}</strong><small>agent::{item.state} · delivery::{item.delivery_state} · candidate::{item.candidate_safe?'yes':'no'} · authorized::no</small><em>{item.diagnostic}</em><details><summary>bounded evidence</summary><pre>{JSON.stringify({checks:item.checks,evidence:item.evidence},null,2)}</pre></details></div></article>)}<h4>Parser coverage</h4>{injectionSafety?.parser_coverage.map(item=><article class="automation-row"><span class={`state-dot ${item.status==='ready'?'idle':item.status==='degraded'?'awaiting':'running'}`}/><div><strong>{item.backend} · {item.session_id}</strong><small>schema::{item.schema_version||'—'} · {item.status} · recognized::{item.recognized} · unknown::{item.unknown} ({item.unknown_rate===undefined||item.unknown_rate===null?'—':`${Math.round(item.unknown_rate*100)}%`})</small><em>{item.diagnostic||'No transcript diagnostics yet.'}</em>{Object.keys(item.unknown_signatures).length>0&&<pre>{JSON.stringify(item.unknown_signatures,null,2)}</pre>}</div></article>)}</details>
          </section>
        </div>}
      </main>
    </section>
    {showHelp&&<div class="usage-layer automation-help-layer" role="dialog" aria-modal="true" aria-label="How automation works" onMouseDown={event=>event.target===event.currentTarget&&setShowHelp(false)}>
      <section class="usage-panel automation-panel" ref={helpPanel}>
        <header><div><span>AUTOMATION</span><strong>How it works</strong></div><div class="usage-header-actions"><button aria-label="Close help" onClick={()=>setShowHelp(false)}>×</button></div></header>
        <main>
          <section class="usage-table automation-explainer"><h3>The pipeline</h3><div class="automation-flow"><article><strong>1 · Agent activity</strong><span>Observed harnesses emit normalized lifecycle, turn, tool, approval, and context events.</span></article><article><strong>2 · Matching automation</strong><span>A system or custom rule checks the event. Most health checks are deterministic.</span></article><article><strong>3 · Optional observer</strong><span>A bounded transcript slice may be sent to the configured OpenRouter model.</span></article><article><strong>4 · Visible result</strong><span>The result becomes a run note or an attention item. It never types into the terminal.</span></article></div></section>
          <section class="usage-table"><h3>What all-session health watches</h3><p>“Fleet” means every live and recent session considered together. These passive checks do not use an LLM and cannot control an agent, and what they conclude becomes an attention item you read on the <strong>Activity</strong> tab or the Alerts drawer.</p><div class="automation-health-grid">{healthSignals.map(([title,description])=><article key={title}><strong>{title}</strong><span>{description}</span></article>)}<article><strong>Periodic attention digest · {(data?.engine.built_in_rules||[]).some(item=>item.automation_id==='attention_observers'&&item.enabled)?'on':'off'}</strong><span>The optional attention-observer setting also summarizes unread attention records every 30 minutes.</span></article></div></section>
          <section class="usage-table"><h3>Terms used here</h3><div class="automation-term-grid"><article><strong>Observer</strong><span>A read-only LLM call over a bounded transcript slice—not another interactive agent.</span></article><article><strong>Run note</strong><span>Durable metadata attached to one agent run, such as a title, summary, or suggestion.</span></article><article><strong>Attention</strong><span>An inbox item intended to bring you back to a session that may need you.</span></article><article><strong>Global ceiling</strong><span>The matrix's Global column: an automation switched off there is off in every Project, with everything that depends on it.</span></article><article><strong>Learned fix</strong><span>A reviewed error → resolution finding extracted from completed session history.</span></article></div></section>
        </main>
      </section>
    </div>}
  </div>
}
