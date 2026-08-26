import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { Dropdown } from './Dropdown'
import {
  buildSpendRows, callHealth, exactMoney, formatCount, formatDuration, formatMoney, formatPercent,
  type SpendBreakdown,
} from './automationCost'
import { displayModelName } from './modelDisplay'
import { ModelName } from './ModelName'
import { AutomationSpendView } from './AutomationSpendView'
import { useModalFocus } from './modalFocus'
import { runDisplayName } from './sessionNames'
import { AutomationPolicyView } from './AutomationPolicyView'
import { AutomationOptIns } from './ProjectsManager'
import { useProjectConfig } from './projectConfigState'
import { revealSetting } from './settingReveal'
import type { Project } from './types'

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
type MatrixAutomation={id:string;kind:'substrate'|'consumer';label:string;requires:string[];implemented:boolean;spends?:boolean;needs_llm?:boolean;default_on?:boolean}
type MatrixProject={project_id:string;project_name:string;status:string;requested:Record<string,boolean>;enabled:string[];blocked:Record<string,string[]>;scan_timeline_auto_enable:boolean}
type ProjectMatrix={automations:MatrixAutomation[];projects:MatrixProject[]}
type RulesFile={text:string}
// Seven views, flat.
//
// The pipeline produces exactly two things — an attention item or a run note — and each
// has exactly one place it is read (Alerts; Activity → Findings). This dashboard links to
// both and draws second copies of neither.
//
// The read-only "control graph" view is gone: it drew the whole dependency picture with
// zero controls while `projects` had the controls with no structure — the same objects
// from the same endpoint split across two tabs. The `projects` view is now both at once:
// the grouped dependency map of one Project's policy, with the toggles on the nodes.
//
// Global policy and per-Project control-plane opt-ins live in this workspace. Settings
// retains a status portal, and the general Projects registry links here instead of
// duplicating the editor.
export type AutomationView='overview'|'rules'|'projects'|'policy'|'cost'|'knowledge'|'diagnostics'

const VIEWS:Array<{id:AutomationView;label:string}>=[
  {id:'overview',label:'overview'},
  {id:'rules',label:'rules'},
  // Per-Project participation is its own view: the global state beside the rules says
  // whether the pipeline may run, and this answers — and edits — where it actually does.
  {id:'projects',label:'projects'},
  {id:'policy',label:'global policy'},
  // Spend is its own destination rather than a section of anything: what a thing costs and
  // whether it is behaving are different questions asked at different times.
  {id:'cost',label:'cost breakdown'},
  {id:'knowledge',label:'learned fixes'},
  {id:'diagnostics',label:'diagnostics'},
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

export function AutomationDashboard({onClose,onOpenSession,onOpenAlerts,onOpenFindings,onOpenUsage,projects=[],initialView='overview',initialProjectId='',initialSetting,revealToken=0}:{
  onClose:()=>void;onOpenSession:(id:string)=>void;initialView?:AutomationView
  projects?:Project[];initialProjectId?:string;initialSetting?:string;revealToken?:number
  /** The single home for attention items. This dashboard used to draw a second copy. */
  onOpenAlerts:()=>void
  /** The single home for run notes. Same story. */
  onOpenFindings:()=>void
  /** The other reading of the same spend table. Not a duplicate: this dashboard answers
   *  which rule burned it, and Usage answers what that is a share of. */
  onOpenUsage:()=>void
}){
  const [data,setData]=useState<AutomationData|null>(null)
  const [experiences,setExperiences]=useState<Experience[]>([])
  const [experienceQuery,setExperienceQuery]=useState('')
  const [selectedExperienceId,setSelectedExperienceId]=useState('')
  const [injectionSafety,setInjectionSafety]=useState<InjectionSafety|null>(null)
  const [matrix,setMatrix]=useState<ProjectMatrix|null>(null)
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
      const [dashboard,experience,injection,history,batchHistory,projectMatrix,rulesFile]=await Promise.all([
        api<AutomationData>('GET','/api/automation/dashboard'),
        api<{items:Experience[]}>('GET','/api/experiences'),
        api<InjectionSafety>('GET','/api/automation/injection-safety'),
        api<{items:EndedRun[]}>('GET','/api/history?limit=100'),
        api<{items:ObserverBatch[]}>('GET','/api/automation/batches'),
        api<ProjectMatrix>('GET','/api/automation/projects'),
        api<RulesFile>('GET','/api/automation/rules'),
      ])
      setData(dashboard);setExperiences(experience.items);setInjectionSafety(injection);setBatchCandidates(history.items.filter(item=>Boolean(item.exited_at&&item.transcript_path)));setBatches(batchHistory.items);setMatrix(projectMatrix)
      setRulesText(current=>current===savedRulesText?rulesFile.text:current);setSavedRulesText(rulesFile.text)
      const enabledBuiltins=(dashboard.engine.built_in_rules||[]).filter(item=>item.enabled).length
      setMessage(`ready · ${enabledBuiltins} system observers on · ${dashboard.engine.rules.length} custom rules · ${dashboard.unread_notifications} unread`);setError('')
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  useEffect(()=>{void load()},[])

  const runDry=async()=>{try{setDryRun(null);setMessage('Evaluating selected historical event with side effects disabled…');const result=await api('POST','/api/automation/dry-run',{event_seq:Number(eventSeq)});setDryRun(result);setMessage('Dry-run complete. No actions or model calls were executed.')}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  const updateRule=async(rule:Rule,change:Partial<Pick<Rule,'enabled'|'shadow'>>)=>{try{setMessage(`Updating ${rule.name}…`);await api('PATCH',`/api/automation/rules/${encodeURIComponent(rule.id)}`,change);await load()}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  const updateBuiltin=async(rule:BuiltInRule)=>{try{setMessage(`Updating ${rule.setting_label}…`);await api('PATCH','/api/config',{[rule.setting_key]:!rule.enabled});await load()}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
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
  const enabledBuiltins=(data?.engine.built_in_rules||[]).filter(item=>item.enabled).length
  const attentionObserversEnabled=(data?.engine.built_in_rules||[]).some(item=>item.setting_key==='attention_observers_enabled'&&item.enabled)
  const unread=data?.unread_notifications||0
  const failedExecutions=(data?.recent_firings||[]).filter(item=>item.status==='failed').length+(data?.recent_action_results||[]).filter(item=>item.status==='failed').length+(data?.recent_observer_calls||[]).filter(item=>item.status==='failed').length
  const filteredExperiences=experiences.filter(item=>`${item.error_summary} ${item.resolution_summary} ${item.backend}`.toLowerCase().includes(experienceQuery.trim().toLowerCase()))
  const selectedExperience=filteredExperiences.find(item=>item.id===selectedExperienceId)||filteredExperiences[0]
  const graphProject=matrix?.projects.find(project=>project.project_id===graphProjectId)||matrix?.projects.find(project=>project.enabled.length)||matrix?.projects[0]
  const policyProject=projects.find(project=>project.id===graphProject?.project_id)||null
  const projectPolicyStore=useProjectConfig(policyProject)
  useEffect(()=>{
    if(view!=='projects'||!initialSetting||!panel.current)return
    return revealSetting(panel.current,initialSetting)
  },[view,initialSetting,revealToken,policyProject?.id])
  const graphEntries=(matrix?.automations||[]).filter(entry=>entry.implemented)
  const blockedCount=Object.keys(graphProject?.blocked||{}).length

  return <div class="usage-layer automation-layer" role="dialog" aria-modal="true" aria-label="Automation" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="usage-panel automation-panel" ref={panel}>
      <header><div><span>AUTOMATION</span><strong>Control plane · policy · runtime · knowledge</strong></div><div class="usage-header-actions"><button class="automation-help-btn" aria-label="How automation works" title="How it works" onClick={()=>setShowHelp(true)}>?</button><button aria-label="Close automation" onClick={onClose}>×</button></div></header>
      <div class="segmented-tabs automation-tabs" role="tablist" aria-label="Automation view">{VIEWS.map(item=><button key={item.id} role="tab" aria-selected={view===item.id} class={view===item.id?'active':''} onClick={()=>setView(item.id)}>{item.label}</button>)}</div>
      {/* The way out to the two surfaces this dashboard used to duplicate. Kept as a
          permanent row rather than an empty-state hint, because "where did the attention
          inbox go" is asked by someone who is looking at a full one somewhere else. */}
      <div class="usage-actions automation-elsewhere">
        <span>Read elsewhere</span>
        <div>
          <button onClick={onOpenAlerts}>Attention inbox{unread?` [${unread}]`:''}</button>
          <button onClick={onOpenFindings}>Run notes</button>
          {/* Not a surface this dashboard duplicates - it is the other half of the same
              question. The spend table below is one of three pots, and only Usage draws
              the other two, so "is this a lot" is answerable only over there. */}
          <button onClick={onOpenUsage}>Usage &amp; spend</button>
        </div>
      </div>
      <div class={`usage-progress ${!data&&!error?'running':''}`} role="status" aria-live="polite"><span>{error?'!':data?'·':'◌'}</span><strong>{error||message}</strong></div>
      <main>
        {view==='overview'&&<div class="usage-tables automation-overview">
          <div class="usage-summary"><article><span>engine</span><strong>{data?.engine.enabled?'on':'off'}</strong></article><article><span>{graphProject?`on in ${graphProject.project_name}`:'on here'}</span><strong>{graphProject?.enabled.length||0}/{graphEntries.length||0}</strong></article><article><span>system rules</span><strong>{enabledBuiltins}/{data?.engine.built_in_rules?.length||0}</strong></article><article><span>Projects</span><strong>{matrix?.projects.filter(project=>project.enabled.length).length||0}/{matrix?.projects.length||0}</strong></article><article><span>cost today</span><strong>{formatMoney(data?.spend_today.cost_usd||0)}</strong></article><article><span>runtime issues</span><strong>{data?.engine.diagnostic?'1':'0'}</strong></article></div>
          <section class="usage-table"><h3>Automation workspace</h3><p>What runs, where it runs, what it may spend, and what it produced.</p><div class="automation-overview-links"><button onClick={()=>setView('projects')}>Projects &amp; control map</button><button onClick={()=>setView('rules')}>Rules</button><button onClick={()=>setView('policy')}>Global policy</button><button onClick={()=>setView('cost')}>Cost breakdown</button><button onClick={()=>setView('knowledge')}>Learned fixes</button><button onClick={()=>setView('diagnostics')}>Diagnostics</button></div></section>
          <section class="usage-table"><h3>Needs review</h3>
            {data&&!data.engine.enabled&&<article class="automation-row automation-rule-row"><span class="state-dot running"/><div><strong>The automation engine is off</strong><span>Nothing runs anywhere until it is enabled.</span></div><div class="automation-row-actions"><button onClick={()=>setView('policy')}>Open global policy</button></div></article>}
            {data?.engine.diagnostic?<p class="usage-error">{data.engine.diagnostic}</p>:data?.engine.enabled?<p>Runtime queue healthy. No engine diagnostic is active.</p>:null}
            {matrix&&matrix.projects.filter(project=>Object.keys(project.blocked).length).length>0&&<p>{matrix.projects.filter(project=>Object.keys(project.blocked).length).length} Project(s) have requested automations blocked by missing dependencies.</p>}
          </section>
        </div>}
        {view==='rules'&&<div class="usage-tables">
          {/* `calls today` used to sum the lifetime status counts, which is neither today's
              figure nor a count of anything the reader asked for. Today's calls come from the
              same ledger as today's cost, so the three spend tiles agree with each other. */}
          <div class="usage-summary"><article><span>automation</span><strong>{data?.engine.enabled?'on':'off'}</strong></article><article><span>system observers</span><strong>{enabledBuiltins}/{data?.engine.built_in_rules?.length||0}</strong></article><article><span>custom rules</span><strong>{data?.engine.rules.length||0}</strong></article><article><span>calls today</span><strong>{formatCount(data?.spend_breakdown?.totals?.today_calls||0)}</strong></article><article><span>tokens today</span><strong title={integer.format(data?.spend_today.tokens||0)}>{formatCount(data?.spend_today.tokens||0)}</strong></article><article><span>cost today</span><strong title={exactMoney(data?.spend_today.cost_usd||0)}>{formatMoney(data?.spend_today.cost_usd||0)}</strong></article></div>
          <section class="usage-table automation-controls"><h3>Global switches</h3><p>These states are edited with their limits in Global policy.</p><article class="automation-row automation-rule-row"><span class={`state-dot ${data?.controls.automation_enabled?'idle':'running'}`}/><div><strong>Automation engine · {data?.controls.automation_enabled?'on':'off'}</strong><span>Runs enabled system observers and custom rules.</span></div><div class="automation-row-actions"><button onClick={()=>setView('policy')}>Open global policy</button></div></article><article class="automation-row automation-rule-row"><span class={`state-dot ${data?.controls.scan_timeline_enabled?'idle':'running'}`}/><div><strong>Scan timeline · {data?.controls.scan_timeline_enabled?'on':'off'}</strong><span>Global permission for Project and per-run timeline controls.</span></div><div class="automation-row-actions"><button onClick={()=>setView('policy')}>Open global policy</button></div></article></section>
          <section class="usage-table"><h3>System observers</h3><p>Built-in, read-only rules; the three attention observers share one setting.</p>{data?.engine.built_in_rules?.map(rule=><article class="automation-row automation-rule-row"><span class={`state-dot ${rule.enabled?'idle':'running'}`}/><div><div class="automation-rule-heading"><strong>{rule.name}</strong><span class="automation-pill">system</span></div><span>{rule.description}</span><small>when::{rule.trigger} · reads::{rule.input}</small><em><ModelName model={rule.model}/> → {rule.result} · setting::{rule.setting_label}</em></div><div class="automation-row-actions"><button onClick={()=>void updateBuiltin(rule)}>{rule.enabled?'disable':'enable'}{rule.setting_key==='attention_observers_enabled'?' group':''}</button></div></article>)}</section>
          <section class="usage-table"><h3>Custom rules</h3><p>Saved in the daemon rules file; the editor below owns the full TOML definition.</p>{data?.engine.rules.length?data.engine.rules.map(rule=><article class="automation-row automation-rule-row"><span class={`state-dot ${rule.enabled?'idle':'running'}`}/><div><div class="automation-rule-heading"><strong>{rule.name}</strong><span class="automation-pill">custom</span></div><small>{rule.id} · when::{rule.trigger} · {rule.shadow?'shadow only':'live'}</small><em>{actionSummary(rule)} · revision::{rule.revision}</em></div><div class="automation-row-actions"><button onClick={()=>void updateRule(rule,{enabled:!rule.enabled})}>{rule.enabled?'disable':'enable'}</button><button onClick={()=>void updateRule(rule,{shadow:!rule.shadow})}>{rule.shadow?'make live':'shadow'}</button></div></article>):<div class="automation-empty"><strong>No custom rules</strong><span>Only the system observers listed here are currently configured.</span><button onClick={()=>setRulesOpen(true)}>edit custom rules</button></div>}
          {/* The rules corpus is content this dashboard owns end to end. It was a Settings
              textarea saved inside the Settings transaction, which meant a stale copy held
              open there could overwrite a rule toggled or edited here. One owner now. */}
          <details class="automation-advanced automation-rules-editor" open={rulesOpen} onToggle={event=>setRulesOpen(event.currentTarget.open)}><summary>rules.toml editor{rulesText!==savedRulesText?' · unsaved':''}</summary>
            <p>Saving validates the complete file first; an invalid file changes nothing. Repository .swe-mux/rules.toml files remain diagnostic and inert.</p>
            <textarea value={rulesText} spellcheck={false} onInput={event=>setRulesText(event.currentTarget.value)}/>
            {rulesError&&<p class="usage-error" role="alert">{rulesError}</p>}
            <div class="automation-inline"><button class="primary" disabled={rulesText===savedRulesText} onClick={()=>void saveRules()}>validate + save</button><button disabled={rulesText===savedRulesText} onClick={()=>{setRulesText(savedRulesText);setRulesError('')}}>discard edits</button></div>
          </details></section>
        </div>}
        {view==='projects'&&<div class="usage-tables">
          <section class="usage-table automation-project-picker"><h3>Where automations run</h3>
            <div class="automation-project-toolbar">
              <label>Project<Dropdown value={graphProject?.project_id||''} onChange={setGraphProjectId} options={(matrix?.projects||[]).map(project=>({value:project.project_id,label:project.project_name}))}/></label>
              <span>{graphProject?`${graphProject.enabled.length} of ${graphEntries.length} on${blockedCount?` · ${blockedCount} blocked`:''}`:'Loading…'}</span>
            </div>
            <p>Select a Project to inspect and edit its policy — every switch below turns something on or off for that Project alone, and nothing runs on a Project that did not opt in.</p>
            {matrix&&!matrix.projects.length&&<div class="automation-empty"><strong>No Projects registered</strong><span>Automations run only on registered Projects that opted in.</span></div>}
          </section>
          {policyProject&&<section class="usage-table automation-project-policy"><h3>{policyProject.name} policy</h3>
            <AutomationOptIns project={policyProject} busy={false} onError={setError} store={projectPolicyStore}/>
          </section>}
          <section class="usage-table"><h3>All Projects</h3>
            {matrix?<div class="usage-table-scroll"><table class="data-table automation-matrix">
              <thead><tr><th>project</th><th>enabled</th><th>what is on</th><th/></tr></thead>
              <tbody>{matrix.projects.map(project=>{
                const implemented=matrix.automations.filter(item=>item.implemented)
                const labels=project.enabled.map(id=>matrix.automations.find(item=>item.id===id)?.label||id)
                const projectBlocked=Object.keys(project.blocked).length
                return <tr key={project.project_id}>
                  <td data-label="project"><strong>{project.project_name}</strong>{project.status!=='ready'&&project.status!=='read-only'?<em> · config {project.status}</em>:null}</td>
                  <td data-label="enabled">{project.enabled.length}/{implemented.length}{projectBlocked?<em> · {projectBlocked} blocked</em>:null}</td>
                  <td data-label="what is on" class="automation-matrix-list">{labels.length?labels.join(' · '):'nothing'}</td>
                  <td data-label="open"><button class={graphProject?.project_id===project.project_id?'active':''} onClick={()=>{setGraphProjectId(project.project_id);panel.current?.querySelector('.automation-project-picker')?.scrollIntoView({block:'start',behavior:'smooth'})}}>Edit policy</button></td>
                </tr>})}</tbody>
            </table></div>:<p>Loading Projects…</p>}
          </section>
        </div>}
        {view==='policy'&&<AutomationPolicyView initialSetting={initialSetting} revealToken={revealToken}/>}
        {view==='cost'&&<AutomationSpendView/>}
        {view==='knowledge'&&<>
          <section class="usage-table"><h3>Learned fixes</h3><p>Reviewed error → resolution pairs extracted from completed runs.</p>
            <input class="automation-knowledge-search" type="search" value={experienceQuery} placeholder="Search errors, tools, or resolutions…" onInput={event=>setExperienceQuery(event.currentTarget.value)}/>
            {filteredExperiences.length?<div class="automation-knowledge-browser"><div class="automation-knowledge-list">{filteredExperiences.map(item=><button class={selectedExperience?.id===item.id?'active':''} onClick={()=>setSelectedExperienceId(item.id)}><strong>{item.error_summary}</strong><small>{item.backend} · {Math.round((item.confidence||0)*100)}%</small></button>)}</div>{selectedExperience&&<article class="automation-knowledge-detail"><h4>{selectedExperience.error_summary}</h4><span class="automation-pill">reviewed</span><p><strong>Demonstrated resolution</strong></p><p>{selectedExperience.resolution_summary}</p><small>{selectedExperience.backend} · source run::{selectedExperience.source_run_id} · confidence::{selectedExperience.confidence??'—'}</small><div class="automation-inline"><button onClick={()=>void navigator.clipboard.writeText(selectedExperience.resolution_summary)}>Copy resolution</button></div></article>}</div>:<div class="automation-empty"><strong>No learned fixes</strong><span>Use the reviewed batch below to extract demonstrated fixes from completed runs.</span></div>}
          </section>
          <section class="usage-table"><h3>Build knowledge from completed runs</h3><p>Select up to 25 ended runs; estimate first, then confirm the bounded batch. Results are preview/export only and never modify a repository.</p><label>finding type<Dropdown value={batchKind} onChange={value=>{setBatchKind(value);setBatchPreview(null)}} options={[{value:'experience',label:'learned fixes'},{value:'procedure',label:'repeatable procedures'},{value:'doc-drift',label:'documentation drift candidates'},{value:'convention',label:'observed conventions'},{value:'regression',label:'regression candidates'}]}/></label><div class="batch-run-picker">{batchCandidates.length?batchCandidates.map(run=><label><input type="checkbox" checked={batchRuns.has(run.id)} disabled={!batchRuns.has(run.id)&&batchRuns.size>=25} onChange={()=>toggleBatchRun(run.id)}/><span><strong>[{run.backend}] {runDisplayName(run)}</strong><small>{run.project_label||'Ungrouped'} · {new Date(run.spawned_at*1000).toLocaleString()}</small></span></label>):<p>No ended agent transcripts are available.</p>}</div><div class="automation-inline"><span>{batchRuns.size}/25 selected</span><button disabled={!batchRuns.size} onClick={()=>void previewBatch(false)}>estimate</button><button class="primary" disabled={!batchPreview||!batchRuns.size} onClick={()=>void previewBatch(true)}>start reviewed batch</button></div>{batchPreview&&<pre>{JSON.stringify(batchPreview,null,2)}</pre>}</section>
          <section class="usage-table"><h3>Recent knowledge batches</h3>{batches.length?batches.map(batch=><details><summary>{batch.kind} · {batch.status} · {batch.run_ids.length} runs</summary><small>{new Date(batch.created_at*1000).toLocaleString()} · {batch.calls} calls · {integer.format(batch.tokens)} tokens · {money.format(batch.cost_usd)}</small>{batch.error&&<p class="usage-error">{batch.error}</p>}<pre>{JSON.stringify(batch.preview,null,2)}</pre></details>):<p>No reviewed batches have run.</p>}</section>
        </>}
        {view==='diagnostics'&&<>
          <div class="usage-summary automation-diagnostic-summary"><article><span>queue</span><strong>{data?.engine.queue.size||0}/{data?.engine.queue.capacity||0}</strong></article><article><span>dropped</span><strong>{data?.engine.queue.dropped||0}</strong></article><article><span>provider</span><strong>{data?.provider.secret.source||'none'}</strong></article><article><span>recent failures</span><strong>{failedExecutions}</strong></article></div>
          {failedExecutions>0&&<section class="usage-table automation-diagnostic-issues"><h3>Needs review</h3>{data?.recent_observer_calls.filter(item=>item.status==='failed').map(item=><article class="automation-row"><span class="state-dot awaiting"/><div><strong>{item.rule_id} · observer failed</strong><small>{item.error||`HTTP ${item.http_status??'unknown'} · ${item.finish_reason||'unknown finish'}`}</small></div></article>)}</section>}
          <section class="usage-table"><h3>Provider and safety boundary</h3><p>OpenRouter key::{data?.provider.secret.source||'none'} · cheap::<ModelName model={data?.provider.cheap_model} fallback="unset"/> · standard::<ModelName model={data?.provider.standard_model} fallback="unset"/></p><p>Observers can create run notes and attention items only. PTY writes, approvals, arbitrary HTTP, workers, repository rules, and model-directed actions are unavailable.</p><p>queue::{data?.engine.queue.size||0}/{data?.engine.queue.capacity||0} · dropped::{data?.engine.queue.dropped||0} · chain loops rejected::{data?.engine.queue.loop_rejections||0}</p>{data?.engine.diagnostic&&<p class="usage-error">{data.engine.diagnostic}</p>}</section>
          <section class="usage-table"><h3>Historical event dry-run</h3><p>Evaluate custom rules against one persisted event with every side effect disabled.</p><div class="automation-inline"><input type="number" placeholder="event sequence" value={eventSeq} onInput={event=>setEventSeq(event.currentTarget.value)}/><button disabled={!eventSeq} onClick={()=>void runDry()}>dry-run</button></div>{dryRun&&<pre>{JSON.stringify(dryRun,null,2)}</pre>}</section>
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
          {/* The deterministic checks, explained where every other explanation of this
              pipeline already lived. This was a whole dashboard view ("all-session
              health") whose other two sections went to Resources and to Alerts; what was
              left was documentation, and documentation belongs in the help panel. */}
          <section class="usage-table"><h3>What all-session health watches</h3><p>“Fleet” means every live and recent session considered together. These passive checks do not use an LLM and cannot control an agent, and what they conclude becomes an attention item you read in <strong>Alerts</strong>.</p><div class="automation-health-grid">{healthSignals.map(([title,description])=><article key={title}><strong>{title}</strong><span>{description}</span></article>)}<article><strong>Periodic attention digest · {attentionObserversEnabled?'on':'off'}</strong><span>The optional attention-observer setting also summarizes unread attention records every 30 minutes.</span></article></div></section>
          <section class="usage-table"><h3>Terms used here</h3><div class="automation-term-grid"><article><strong>Observer</strong><span>A read-only LLM call over a bounded transcript slice—not another interactive agent.</span></article><article><strong>Run note</strong><span>Durable metadata attached to one agent run, such as a title, summary, or suggestion.</span></article><article><strong>Attention</strong><span>An inbox item intended to bring you back to a session that may need you.</span></article><article><strong>All-session health</strong><span>Evidence calculated across the complete set of live and recent sessions—the “fleet.”</span></article><article><strong>Learned fix</strong><span>A reviewed error → resolution finding extracted from completed session history.</span></article></div></section>
        </main>
      </section>
    </div>}
  </div>
}
