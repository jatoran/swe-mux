import { numberFormatter, locale as uiLocale } from './localization/index.ts'
import { codeLabel } from './localization/codeLabels.ts'
import { rich, t } from './localization/index.ts'
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
import type { StartingSetCatalog } from './projectAutomations'
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
  {id:'policy',get label() { return t("AutomationDashboard.c611981fab") }},
  {id:'usage',get label() { return t("AutomationDashboard.8d59829c1e") }},
  {id:'activity',get label() { return t("AutomationDashboard.38da1505ca") }},
]
const healthSignals=() => ([
  [t("AutomationDashboard.74b6abdf59"),t("AutomationDashboard.29c65eedd9")],
  [t("AutomationDashboard.eee698fbd0"),t("AutomationDashboard.7129de83ae")],
  [t("AutomationDashboard.3f765c6c48"),t("AutomationDashboard.71063f8602")],
  [t("AutomationDashboard.1516b60278"),t("AutomationDashboard.3fd8cdcbb9")],
])
const money=numberFormatter({style:'currency',currency:'USD',maximumFractionDigits:4})
const integer=numberFormatter()

function actionSummary(rule:Rule):string{
  const action=rule.actions?.[0]
  if(!action)return t("AutomationDashboard.11c981fcd4")
  if(action.kind==='llm')return t("AutomationDashboard.95d3eca6f5", { value1: action.model?displayModelName(action.model):'configured', value2: action.on_result?.kind||t("AutomationDashboard.6946c4d2b0") })
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
  const [message,setMessage]=useState(t("AutomationDashboard.2af82e98a2"))
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
      setMessage(t("AutomationDashboard.27635176ea", { enabledBuiltins: enabledBuiltins, length: dashboard.engine.rules.length, unread_notifications: dashboard.unread_notifications }));setError('')
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  useEffect(()=>{void load()},[])

  const runDry=async()=>{try{setDryRun(null);setMessage(t("AutomationDashboard.cd3fec3001"));const result=await api('POST','/api/automation/dry-run',{event_seq:Number(eventSeq)});setDryRun(result);setMessage(t("AutomationDashboard.7c5c440a9c"))}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  const updateRule=async(rule:Rule,change:Partial<Pick<Rule,'enabled'|'shadow'>>)=>{try{setMessage(t("AutomationDashboard.c97a0a747f", { name: rule.name }));await api('PATCH',`/api/automation/rules/${encodeURIComponent(rule.id)}`,change);await load()}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  const saveRules=async()=>{
    try{
      setRulesError('');setMessage(t("AutomationDashboard.1170508f8a"))
      await api('PUT','/api/automation/rules?validate=1',{text:rulesText})
      await api('PUT','/api/automation/rules',{text:rulesText})
      setSavedRulesText(rulesText);setMessage(t("AutomationDashboard.6bbfa038b5"))
      await load()
    }catch(cause){setRulesError(cause instanceof Error?cause.message:String(cause))}
  }
  const previewBatch=async(confirm=false)=>{try{const run_ids=Array.from(batchRuns);const result=await api<Record<string,unknown>>('POST','/api/automation/batches',{kind:batchKind,run_ids,confirm,...(confirm?{preview_token:String(batchPreview?.preview_token||'')}:{})});setBatchPreview(result);setMessage(confirm?t("AutomationDashboard.8616f059e4"):t("AutomationDashboard.7df05f58bb"));if(confirm)await load()}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
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

  return <div data-dialog-id="automation" class="usage-layer automation-layer" role="dialog" aria-modal="true" aria-label={t("AutomationDashboard.d909750b1b")} onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="usage-panel automation-panel" ref={panel}>
      <header><div><span>{t("AutomationDashboard.9273c57846")}</span><strong>{t("AutomationDashboard.01638fabbd")}</strong></div><div class="usage-header-actions"><button class="automation-help-btn" aria-label={t("AutomationDashboard.8955a0cfed")} title={t("AutomationDashboard.9c870aa6e5")} onClick={()=>setShowHelp(true)}>?</button><button aria-label={t("AutomationDashboard.98055375da")} onClick={onClose}>×</button></div></header>
      <div data-tab-group="AutomationDashboard.716a965260" class="segmented-tabs automation-tabs" role="tablist" aria-label={t("AutomationDashboard.716a965260")}>{VIEWS.map(item=><button data-tab-id={item.id} key={item.id} role="tab" aria-selected={view===item.id} class={view===item.id?'active':''} onClick={()=>setView(item.id)}>{item.label}{item.id==='activity'&&unread ? <span class="automation-tab-badge">{unread}</span> : null}</button>)}</div>
      <div class={`usage-progress ${!data&&!error?'running':''}`} role="status" aria-live="polite"><span>{error ? '!' : data ? '·' : '◌'}</span><strong>{error || message}</strong></div>
      <main>
        {view==='policy' && <div class="usage-tables automation-policy-tab">
          {matrix ? <AutomationPolicyMatrix
            data={matrix}
            projectId={matrixProject?.project_id||''}
            onSelectProject={setGraphProjectId}
            catalog={catalogue?.project_starting_sets}
            llmReady={catalogue?.llm?.ready}
            onError={setError}
            onChanged={load}
          /> : <p>{t("AutomationDashboard.6f792f6311")}</p>}
          {matrix&&!matrix.projects.length && <div class="automation-empty"><strong>{t("AutomationDashboard.6014b6e399")}</strong><span>{t("AutomationDashboard.bc0634c651")}</span></div>}
          <details class="automation-advanced automation-limits-drawer">
            <summary>{rich("AutomationDashboard.72e50001aa", { value1: <em>{t("AutomationDashboard.b35d4ff9ca")}</em> })}</summary>
            <AutomationPolicyView initialSetting={initialSetting} revealToken={revealToken}/>
          </details>
          <section class="usage-table automation-custom-rules">
            <h3>{rich("AutomationDashboard.1d2ec7e490", { value1: <span class="automation-pill scope global">{t("AutomationDashboard.8001c27439")}</span> })}</h3>
            <p>{t("AutomationDashboard.a8b4e935b2")}</p>
            {(data?.engine.queue.title_retries?.pending||0)>0 && <p class="automation-title-refining">{rich("AutomationDashboard.3801c0a6f5", { pending: data?.engine.queue.title_retries?.pending, value2: ((data?.engine.queue.title_retries?.pending||0)===1) ? 'yes' : 'no' })}</p>}
            {data?.engine.rules.length ? data.engine.rules.map(rule=><article class="automation-row automation-rule-row"><span class={`state-dot ${rule.enabled?'idle':'running'}`}/><div><div class="automation-rule-heading"><strong>{rule.name}</strong><span class="automation-pill scope global">{t("AutomationDashboard.8001c27439")}</span><span class="automation-pill">{t("AutomationDashboard.6cdfd271da")}</span></div><small>{rich("AutomationDashboard.9f7f5dfbd9", { id: rule.id, trigger: rule.trigger, value3: (rule.shadow) ? 'yes' : 'no' })}</small><em>{rich("AutomationDashboard.e1451b2a88", { value1: actionSummary(rule), revision: rule.revision })}</em></div><div class="automation-row-actions"><button onClick={()=>void updateRule(rule,{enabled:!rule.enabled})}>{rule.enabled ? t("AutomationDashboard.e9d8992f34") : t("AutomationDashboard.e97166b545")}</button><button onClick={()=>void updateRule(rule,{shadow:!rule.shadow})}>{rule.shadow ? t("AutomationDashboard.01295df876") : t("AutomationDashboard.0bb09d8060")}</button></div></article>) : <div class="automation-empty"><strong>{t("AutomationDashboard.f47574977e")}</strong><span>{t("AutomationDashboard.e19c566e74")}</span><button onClick={()=>setRulesOpen(true)}>{t("AutomationDashboard.f815151a56")}</button></div>}
            <details class="automation-advanced automation-rules-editor" open={rulesOpen} onToggle={event=>setRulesOpen(event.currentTarget.open)}><summary>{t("AutomationDashboard.42739b7d43", { value1: (rulesText!==savedRulesText) ? 'yes' : 'no' })}</summary>
              <p>{t("AutomationDashboard.e7108a0850")}</p>
              <textarea value={rulesText} spellcheck={false} onInput={event=>setRulesText(event.currentTarget.value)}/>
              {rulesError && <p class="usage-error" role="alert">{rulesError}</p>}
              <div class="automation-inline"><button class="primary" disabled={rulesText===savedRulesText} onClick={()=>void saveRules()}>{t("AutomationDashboard.58c0bb07b2")}</button><button disabled={rulesText===savedRulesText} onClick={()=>{setRulesText(savedRulesText);setRulesError('')}}>{t("AutomationDashboard.dc1c335e8c")}</button></div>
            </details>
          </section>
        </div>}
        {view==='usage' && <div class="usage-tables">
          <div class="usage-summary"><article><span>{t("AutomationDashboard.6d65ed5c75")}</span><strong>{data?.engine.enabled ? t("AutomationDashboard.b8d31e8527") : t("AutomationDashboard.b4dc66dde8")}</strong></article><article><span>{t("AutomationDashboard.93446ec00a")}</span><strong>{formatCount(data?.spend_breakdown?.totals?.today_calls||0)}</strong></article><article><span>{t("AutomationDashboard.21d11c335a")}</span><strong title={integer.format(data?.spend_today.tokens||0)}>{formatCount(data?.spend_today.tokens||0)}</strong></article><article><span>{t("AutomationDashboard.83f4374dc2")}</span><strong>{formatMoney(data?.spend_today.cost_usd||0)}</strong></article></div>
          <AutomationSpendView/>
        </div>}
        {view==='activity' && <div class="usage-tables automation-activity-tab">
          <section class="usage-table automation-activity-alerts"><h3 data-heading-message="AutomationDashboard.74b6abdf59">{t("AutomationDashboard.74b6abdf59")}</h3>
            <p>{t("AutomationDashboard.73f38e4c84")}</p>
            <AttentionInbox onOpenSession={onOpenSession} project={policyProject||undefined}/>
          </section>
          <section class="usage-table"><h3 data-heading-message="AutomationDashboard.d400746060">{t("AutomationDashboard.d400746060")}</h3><h4 data-heading-message="AutomationDashboard.0838951ca7">{t("AutomationDashboard.0838951ca7")}</h4>{data?.recent_firings.map(firing=><details><summary>{rich("AutomationDashboard.b39b261f7e", { rule_id: firing.rule_id, status: codeLabel(firing.status), event_seq: firing.event_seq })}</summary><small>{rich("AutomationDashboard.8b2be8fafb", { value1: new Date(firing.created_at*1000).toLocaleString(uiLocale.value), event_type: firing.event_type, value3: (firing.shadow) ? 'yes' : 'no' })}</small>{firing.error && <p>{firing.error}</p>}<pre>{JSON.stringify(firing.condition_trace,null,2)}</pre></details>)}<h4 data-heading-message="AutomationDashboard.cca70c86ab">{t("AutomationDashboard.cca70c86ab")}</h4>{data?.recent_action_results.map(item=><details><summary>{rich("AutomationDashboard.1286e9e149", { kind: item.kind, status: codeLabel(item.status), action_index: item.action_index })}</summary><small>{rich("AutomationDashboard.ad1bf044f1", { firing_id: item.firing_id, value2: new Date(item.created_at*1000).toLocaleString(uiLocale.value) })}</small>{item.error && <p>{item.error}</p>}<pre>{JSON.stringify(item.detail,null,2)}</pre></details>)}<h4 data-heading-message="AutomationDashboard.94591ba976">{t("AutomationDashboard.94591ba976")}</h4>{data?.recent_observer_calls.map(item=><details><summary>{item.rule_id} · {codeLabel(item.status)} · <ModelName model={item.resolved_model||item.requested_model}/></summary><small>{rich("AutomationDashboard.2b655f15e9", { value1: item.input_tokens+item.output_tokens, value2: money.format(item.cost_usd||0), value3: item.latency_ms ?? '-', input_bytes: item.input_bytes })}</small><small>{rich("AutomationDashboard.b40994d55b", { value1: item.provider_name || t("AutomationDashboard.b23a6a8439"), value2: item.finish_reason || t("AutomationDashboard.b23a6a8439"), value3: item.response_content_type || t("AutomationDashboard.b23a6a8439"), value4: item.response_content_length ?? 0, value5: item.http_status ?? t("AutomationDashboard.b23a6a8439"), value6: item.retryable===undefined||item.retryable===null ? t("AutomationDashboard.b23a6a8439") : item.retryable ? t("AutomationDashboard.8a798890fe") : t("AutomationDashboard.9390298f3f") })}</small>{item.error && <p>{item.error}</p>}</details>)}</section>
          <section class="usage-table"><h3 data-heading-message="AutomationDashboard.d3a6fd6f2a">{t("AutomationDashboard.d3a6fd6f2a")}</h3><p>{t("AutomationDashboard.adf06ca351")}</p>
            <input class="automation-knowledge-search" type="search" value={experienceQuery} placeholder={t("AutomationDashboard.d461a2b445")} onInput={event=>setExperienceQuery(event.currentTarget.value)}/>
            {filteredExperiences.length ? <div class="automation-knowledge-browser"><div class="automation-knowledge-list">{filteredExperiences.map(item=><button class={selectedExperience?.id===item.id?'active':''} onClick={()=>setSelectedExperienceId(item.id)}><strong>{item.error_summary}</strong><small>{item.backend} · {Math.round((item.confidence||0)*100)}%</small></button>)}</div>{selectedExperience && <article class="automation-knowledge-detail"><h4>{selectedExperience.error_summary}</h4><span class="automation-pill">{t("AutomationDashboard.e4f934f321")}</span><p><strong>{t("AutomationDashboard.b866f0ab51")}</strong></p><p>{selectedExperience.resolution_summary}</p><small>{rich("AutomationDashboard.d00f1a159c", { backend: selectedExperience.backend, source_run_id: selectedExperience.source_run_id, value3: selectedExperience.confidence ?? '—' })}</small><div class="automation-inline"><button onClick={()=>void navigator.clipboard.writeText(selectedExperience.resolution_summary)}>{t("AutomationDashboard.b773a081a0")}</button></div></article>}</div> : <div class="automation-empty"><strong>{t("AutomationDashboard.9a1a0d5d21")}</strong><span>{t("AutomationDashboard.c3c8703279")}</span></div>}
          </section>
          <details class="automation-advanced"><summary>{t("AutomationDashboard.4896c457ee")}</summary>
            <section class="usage-table"><p>{t("AutomationDashboard.12a7bc9c8d")}</p><label>{rich("AutomationDashboard.f8847b795c", { value1: <Dropdown value={batchKind} onChange={value=>{setBatchKind(value);setBatchPreview(null)}} options={[{value:'experience',get label() { return t("AutomationDashboard.2c47388282") }},{value:'procedure',get label() { return t("AutomationDashboard.3627a08a03") }},{value:'doc-drift',get label() { return t("AutomationDashboard.ba05dc3539") }},{value:'convention',get label() { return t("AutomationDashboard.1ae72faa85") }},{value:'regression',get label() { return t("AutomationDashboard.269903bc75") }}]}/> })}</label><div class="batch-run-picker">{batchCandidates.length ? batchCandidates.map(run=><label><input type="checkbox" checked={batchRuns.has(run.id)} disabled={!batchRuns.has(run.id)&&batchRuns.size>=25} onChange={()=>toggleBatchRun(run.id)}/><span><strong>[{run.backend}] {runDisplayName(run)}</strong><small>{run.project_label || t("AutomationDashboard.674b38cae7")} · {new Date(run.spawned_at*1000).toLocaleString(uiLocale.value)}</small></span></label>) : <p>{t("AutomationDashboard.ca5144f640")}</p>}</div><div class="automation-inline"><span>{rich("AutomationDashboard.6488dedd88", { size: batchRuns.size })}</span><button disabled={!batchRuns.size} onClick={()=>void previewBatch(false)}>{t("AutomationDashboard.79e1d925e7")}</button><button class="primary" disabled={!batchPreview||!batchRuns.size} onClick={()=>void previewBatch(true)}>{t("AutomationDashboard.db4ed206ca")}</button></div>{batchPreview && <pre>{JSON.stringify(batchPreview,null,2)}</pre>}</section>
            <section class="usage-table"><h3 data-heading-message="AutomationDashboard.bf2cfb449c">{t("AutomationDashboard.bf2cfb449c")}</h3>{batches.length ? batches.map(batch=><details><summary>{rich("AutomationDashboard.5ec91f7c7d", { kind: batch.kind, status: codeLabel(batch.status), length: batch.run_ids.length })}</summary><small>{rich("AutomationDashboard.3163e93a7d", { value1: new Date(batch.created_at*1000).toLocaleString(uiLocale.value), calls: batch.calls, value3: integer.format(batch.tokens), value4: money.format(batch.cost_usd) })}</small>{batch.error && <p class="usage-error">{batch.error}</p>}<pre>{JSON.stringify(batch.preview,null,2)}</pre></details>) : <p>{t("AutomationDashboard.b3d3a294a4")}</p>}</section>
          </details>
          <section class="usage-table automation-diagnostics"><h3 data-heading-message="AutomationDashboard.268f14bbfe">{t("AutomationDashboard.268f14bbfe")}</h3>
            <div class="usage-summary automation-diagnostic-summary"><article><span>{t("AutomationDashboard.00b109cf11")}</span><strong>{data?.engine.queue.size || 0}/{data?.engine.queue.capacity || 0}</strong></article><article><span>{t("AutomationDashboard.e7cd9c3ab5")}</span><strong>{data?.engine.queue.dropped || 0}</strong></article><article><span>{t("AutomationDashboard.5c4c196434")}</span><strong>{data?.provider.secret.source || t("AutomationDashboard.140bedbf9c")}</strong></article><article><span>{t("AutomationDashboard.f0c634b3ee")}</span><strong>{failedExecutions}</strong></article></div>
            {failedExecutions>0 && <div class="automation-diagnostic-issues"><h4 data-heading-message="AutomationDashboard.07297fa94a">{t("AutomationDashboard.07297fa94a")}</h4>{data?.recent_observer_calls.filter(item=>item.status==='failed').map(item=><article class="automation-row"><span class="state-dot awaiting"/><div><strong>{rich("AutomationDashboard.03c23b0224", { rule_id: item.rule_id })}</strong><small>{item.error || t("AutomationDashboard.061d98bebb", { value1: item.http_status??'unknown', value2: item.finish_reason||t("AutomationDashboard.f36635a729") })}</small></div></article>)}</div>}
            <p>{rich("AutomationDashboard.5b4eb0c6f3", { value1: data?.provider.secret.source || t("AutomationDashboard.140bedbf9c"), value2: <ModelName model={data?.provider.cheap_model} fallback={t("AutomationDashboard.6cbf83e080")}/>, value3: <ModelName model={data?.provider.standard_model} fallback={t("AutomationDashboard.6cbf83e080")}/> })}</p>
            <p>{t("AutomationDashboard.fda98be687")}</p>
            <p>{rich("AutomationDashboard.0b2e323f20", { value1: data?.engine.queue.size || 0, value2: data?.engine.queue.capacity || 0, value3: data?.engine.queue.dropped || 0, value4: data?.engine.queue.loop_rejections || 0 })}</p>
            {data?.engine.diagnostic && <p class="usage-error">{data.engine.diagnostic}</p>}
            <details class="automation-advanced"><summary>{t("AutomationDashboard.18bc38cd4a")}</summary><p>{t("AutomationDashboard.5d9ddd1aa7")}</p><div class="automation-inline"><input type="number" placeholder={t("AutomationDashboard.3ba932c0ca")} value={eventSeq} onInput={event=>setEventSeq(event.currentTarget.value)}/><button disabled={!eventSeq} onClick={()=>void runDry()}>{t("AutomationDashboard.25d33183d0")}</button></div>{dryRun && <pre>{JSON.stringify(dryRun,null,2)}</pre>}</details>
            <details class="automation-advanced"><summary>{t("AutomationDashboard.a3b15fdfb7")}</summary><p>{t("AutomationDashboard.4b94fb6f9d")}</p><p>{rich("AutomationDashboard.24b15de735", { value1: injectionSafety?.version || '—', value2: (injectionSafety?.authorizes_actuation) ? 'yes' : 'no', value3: injectionSafety?.shadow_metrics.transitions || 0, value4: injectionSafety?.shadow_metrics.tracked_sessions || 0 })}</p>{injectionSafety?.sessions.map(item=><article class="automation-row"><span class={`state-dot ${item.delivery_state==='safe'?'idle':item.delivery_state==='blocked'?'awaiting':'running'}`}/><div><strong>{item.backend} · {item.session_id}</strong><small>{rich("AutomationDashboard.330ff8e710", { state: codeLabel(item.state), delivery_state: item.delivery_state, value3: (item.candidate_safe) ? 'yes' : 'no' })}</small><em>{item.diagnostic}</em><details><summary>{t("AutomationDashboard.30093e91ea")}</summary><pre>{JSON.stringify({checks:item.checks,evidence:item.evidence},null,2)}</pre></details></div></article>)}<h4 data-heading-message="AutomationDashboard.0bc9a7d546">{t("AutomationDashboard.0bc9a7d546")}</h4>{injectionSafety?.parser_coverage.map(item=><article class="automation-row"><span class={`state-dot ${item.status==='ready'?'idle':item.status==='degraded'?'awaiting':'running'}`}/><div><strong>{item.backend} · {item.session_id}</strong><small>{rich("AutomationDashboard.8c1f450b51", { value1: item.schema_version || '—', status: codeLabel(item.status), recognized: item.recognized, unknown: item.unknown, value5: item.unknown_rate===undefined||item.unknown_rate===null ? '—' : t("AutomationDashboard.6a53a571fa", { value1: Math.round(item.unknown_rate*100) }) })}</small><em>{item.diagnostic || t("AutomationDashboard.440b16a729")}</em>{Object.keys(item.unknown_signatures).length>0 && <pre>{JSON.stringify(item.unknown_signatures,null,2)}</pre>}</div></article>)}</details>
          </section>
        </div>}
      </main>
    </section>
    {showHelp && <div class="usage-layer automation-help-layer" role="dialog" aria-modal="true" aria-label={t("AutomationDashboard.8955a0cfed")} onMouseDown={event=>event.target===event.currentTarget&&setShowHelp(false)}>
      <section class="usage-panel automation-panel" ref={helpPanel}>
        <header><div><span>{t("AutomationDashboard.9273c57846")}</span><strong>{t("AutomationDashboard.9c870aa6e5")}</strong></div><div class="usage-header-actions"><button aria-label={t("AutomationDashboard.88f2b69280")} onClick={()=>setShowHelp(false)}>×</button></div></header>
        <main>
          <section class="usage-table automation-explainer"><h3 data-heading-message="AutomationDashboard.940e5ca338">{t("AutomationDashboard.940e5ca338")}</h3><div class="automation-flow"><article><strong>{t("AutomationDashboard.6bc1f41939")}</strong><span>{t("AutomationDashboard.5286d410f6")}</span></article><article><strong>{t("AutomationDashboard.ea8e69edda")}</strong><span>{t("AutomationDashboard.bee2e5ff78")}</span></article><article><strong>{t("AutomationDashboard.dd9757f928")}</strong><span>{t("AutomationDashboard.e615e86ba2")}</span></article><article><strong>{t("AutomationDashboard.10e60b4948")}</strong><span>{t("AutomationDashboard.762c8af867")}</span></article></div></section>
          <section class="usage-table"><h3 data-heading-message="AutomationDashboard.9c6e886e95">{t("AutomationDashboard.9c6e886e95")}</h3><p>{rich("AutomationDashboard.e8dbee4972", { value1: <strong>{t("AutomationDashboard.38da1505ca")}</strong> })}</p><div class="automation-health-grid">{healthSignals().map(([title,description])=><article key={title}><strong>{title}</strong><span>{description}</span></article>)}<article><strong>{t("AutomationDashboard.7983c697a8", { value1: ((data?.engine.built_in_rules||[]).some(item=>item.automation_id==='attention_observers'&&item.enabled)) ? 'yes' : 'no' })}</strong><span>{t("AutomationDashboard.86020cb850")}</span></article></div></section>
          <section class="usage-table"><h3 data-heading-message="AutomationDashboard.04d4f47a3e">{t("AutomationDashboard.04d4f47a3e")}</h3><div class="automation-term-grid"><article><strong>{t("AutomationDashboard.f20b6db009")}</strong><span>{t("AutomationDashboard.a648b45918")}</span></article><article><strong>{t("AutomationDashboard.15031dd8b0")}</strong><span>{t("AutomationDashboard.0eaada6c59")}</span></article><article><strong>{t("AutomationDashboard.c2eb8cd9d9")}</strong><span>{t("AutomationDashboard.cd1197fc86")}</span></article><article><strong>{t("AutomationDashboard.0021f80359")}</strong><span>{t("AutomationDashboard.1d9ac7726c")}</span></article><article><strong>{t("AutomationDashboard.a622389504")}</strong><span>{t("AutomationDashboard.c3785a36a7")}</span></article></div></section>
        </main>
      </section>
    </div>}
  </div>
}
