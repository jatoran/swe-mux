import { codeLabel } from './localization/codeLabels.ts'
import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useEffect, useMemo, useState } from 'preact/hooks'
import { api } from './api'
import { AnalyticsNav, MetricValue } from './AnalyticsPrimitives'
import { compactNumber } from './analyticsPresentation'
import { formatDuration } from './automationCost'
import { LegacyToolTelemetry } from './LegacyToolTelemetry'
import { ModelName } from './ModelName'
import { type Coverage, captionText } from './telemetryCaptionText'
import { telemetryQuery, WorkloadTelemetry, type ActivityContext, type ActivityFilters } from './WorkloadTelemetry'

type Domain = 'workloads' | 'tools' | 'skills' | 'context' | 'inefficiencies'

type ToolGroup = {
  backend:string;model:string;project_id:string;origin:string;invocation_layer:string
  family:string;operation:string;transport:string;raw_name:string;calls:number
  statuses:Record<string,number>;qualities:Record<string,number>;duration_count:number
  average_duration_ms?:number|null;approval_wait_count:number;average_approval_wait_ms?:number|null
}
type SkillGroup = {
  backend:string;model:string;project_id:string;skill_name:string;invocation_trigger:string
  skill_source:string;skill_scope:string;invocations:number
}
type Collection = {
  backfilled:number;backfill_completed:boolean;backfill_stream:string;provider_dropped:number
  provider_batches?:number;schema?:{version?:number;drift?:string[]}
  reconciliation?:{scanned?:number;skipped?:number;errors?:number;inserted?:number;at?:number}
}
type CanonicalActivity = {
  from:number;to:number;origin:string;matching_calls:number;groups:ToolGroup[]
  coverage:Coverage;qualities:Record<string,number>;filters:Record<string,string>
  skills:{matching_invocations:number;groups:SkillGroup[];coverage:Coverage}
  approval_wait:{measured:number;average_ms?:number|null}
  collection?:Collection
}
type QualityCounts = {
  calls:number;with_request:number;with_result:number;with_provider_result:number;with_duration:number
  with_input_hash:number;with_executed_input_hash:number;with_output_hash:number;with_output_size:number
  with_harness_version:number;with_approval_wait:number;truncated_outputs:number
  runtime_parent_unavailable:number;other_family:number
}
type QualityRow = QualityCounts&{backend:string}
type QualityVersionRow = QualityCounts&{backend:string;harness_version:string}
type ParserSignature = {
  backend:string;harness_version:string;parser_version:string;event_name:string
  recognized:number;occurrences:number;first_seen_at:number;last_seen_at:number
}
type CanonicalQuality = {
  totals:QualityCounts;backends:QualityRow[];versions:QualityVersionRow[];parsers?:ParserSignature[]
  capabilities:Record<string,Record<string,string>>
  runs:{runs:number;declared_start:number;first_evidence_start:number;ended:number}
  reconciliation?:{runs:number;by_backend:Array<{backend:string;parser_version:string;status:string;runs:number;tool_events:number}>}
  collection?:Collection
}
type CanonicalToolCall = {
  tool_call_id:string;run_id:string;turn_id?:string;session_id:string;backend:string;model?:string
  invocation_layer:string;raw_name:string;family:string;operation:string;transport:string
  started_at:number;finished_at?:number;status:string;duration_ms?:number|null;approval_wait_ms?:number|null
  target_preview?:string;output_measurement:string;request_source?:string;result_source?:string;evidence_quality:string
}
type CanonicalToolPage = {matching:number;matching_calls:number;items:CanonicalToolCall[];next_cursor?:string|null}
type Observation = {observed_at:number;event_type:string;source_kind:string;source_version?:string;payload_sha256:string;payload_bytes:number;source_locator?:string;privacy_class:string}
type EvidenceLink = {evidence_id:string;contribution:string;precedence_rank:number;conflict:number;observation?:Observation|null}
type CanonicalAudit = {call:CanonicalToolCall;evidence:EvidenceLink[]}
type CanonicalCompactions = {
  total:number;coverage:Coverage
  groups:Array<{backend:string;model:string;project_id:string;trigger:string;count:number;failures:number;duration_count:number;average_duration_ms?:number|null;token_count:number;average_tokens_reclaimed?:number|null}>
  collection?:Collection
}
type Finding = {kind:string;finding_key:string;tool:{backend:string;model:string;project_id?:string;invocation_layer?:string;raw_name:string;family?:string;operation?:string;transport?:string};evidence:Record<string,number>;coverage:number;confidence:string;suggestion:string;review?:{verdict:string;note?:string|null;reviewed_at:number}|null}
type InefficiencyResult = {
  interpretation:string;findings:Finding[];reviewed:number;coverage:Coverage
  adaptive_changes:{offered:number;policy:string}
  collection:{matching_calls:number;duration:{measured:number;completed:number;average_ms?:number|null};approval_wait:{measured:number;average_ms?:number|null}}
  collection_health?:Collection
}
type Comparison = {
  split:string;comparable:boolean;why_not_comparable?:string|null;interpretation:string
  cohorts:Array<{cohort:string;runs:number;completed_turns:number;tool_calls:number;completed_tool_calls:number;failed_tool_calls:number;verifications:number;successful_verifications:number;skill_activations:number;tool_failure_rate?:number|null;verification_success_rate?:number|null;skill_activations_per_run?:number|null;other_dimensions:Record<string,string[]>}>
}
type MetricSummary = {
  metrics:Array<{backend:string;harness_version:string;metric:string;kind:string;points:number;count:number;total:number;min?:number|null;max?:number|null}>
  tool_call_agreement:{runs:number;agree:number;ledger_more:number;provider_more:number;examples:Array<{run_id:string;provider_reported:number;ledger:number;verdict:string}>}
}
type ShadowFlag = {legacy_dashboard_enabled:boolean}


const DOMAINS:Array<{id:Domain;label:string}>=[{id:'workloads',get label() { return t("FleetActivityView.848f54e896") }},{id:'tools',get label() { return t("FleetActivityView.ea93d6a262") }},{id:'skills',get label() { return t("FleetActivityView.de07d07209") }},{id:'context',get label() { return t("FleetActivityView.a6e600a10f") }},{id:'inefficiencies',get label() { return t("FleetActivityView.160a13d94f") }}]
const EXPORT_KINDS=['runs','turns','tool_calls','model_requests','skills','verifications','compactions','provider_metrics','evidence']
const when=(time:number)=>new Date(time*1000).toLocaleString(uiLocale.value)
const duration=(milliseconds:number|null|undefined)=>milliseconds==null?t("FleetActivityView.040f7884d3"):milliseconds===0?'0ms':milliseconds<1000?`${Math.round(milliseconds)}ms`:formatDuration(milliseconds/1000)
const projectName=(id:string|undefined,context:ActivityContext)=>context.projects?.find(project=>project.id===id)?.name||id||'Unassigned'
const toolKey=(row:ToolGroup)=>JSON.stringify([row.backend,row.model,row.project_id,row.origin,row.invocation_layer,row.family,row.operation,row.transport,row.raw_name])

function ToolCalls({query,onRun}:{query:string;onRun:(id:string)=>void}) {
  const [page,setPage]=useState<CanonicalToolPage|null>(null),[cursor,setCursor]=useState(''),[error,setError]=useState(''),[loading,setLoading]=useState(false)
  const [evidence,setEvidence]=useState<CanonicalAudit|null>(null),[selected,setSelected]=useState(''),[auditError,setAuditError]=useState('')
  useEffect(()=>{let cancelled=false;setLoading(true);setError('');api<CanonicalToolPage>('GET',`/api/telemetry/v2/tools?${query}&limit=30${cursor?`&cursor=${encodeURIComponent(cursor)}`:''}`).then(value=>{if(!cancelled)setPage(previous=>cursor&&previous?{...value,items:[...previous.items,...value.items]}:value)}).catch(cause=>{if(!cancelled)setError(String(cause))}).finally(()=>{if(!cancelled)setLoading(false)});return()=>{cancelled=true}},[query,cursor])
  useEffect(()=>{let cancelled=false;setEvidence(null);setAuditError('');if(selected)api<CanonicalAudit>('GET',`/api/telemetry/v2/tools/${encodeURIComponent(selected)}`).then(value=>{if(!cancelled)setEvidence(value)}).catch(cause=>{if(!cancelled)setAuditError(String(cause))});return()=>{cancelled=true}},[selected])
  return <section>{error && <p role="alert">{error}</p>}<p class="analytics-caption">{page ? t("FleetActivityView.fe1ba8332b", { value1: compactNumber(page.matching_calls??page.matching) }) : t("FleetActivityView.e9461c4cee")}</p>
    {page?.items.map(call=><div class="analytics-event" key={call.tool_call_id}><strong>{codeLabel(call.status)}</strong><span>{when(call.started_at)} · {duration(call.duration_ms)}</span><div class="analytics-actions"><button onClick={()=>onRun(call.run_id)}>{t("FleetActivityView.00d60e31a4")}</button><button onClick={()=>setSelected(selected===call.tool_call_id?'':call.tool_call_id)}>{t("FleetActivityView.03867aea70")}</button></div>{selected===call.tool_call_id && <div class="analytics-evidence">{auditError ? <p role="alert">{auditError}</p> : evidence ? evidence.evidence.map(item=><p key={item.evidence_id}>{item.observation ? t("FleetActivityView.49e797696b", { source_kind: item.observation.source_kind, event_type: item.observation.event_type, value3: when(item.observation.observed_at) }) : t("FleetActivityView.a3feb81f9a")}{item.conflict ? t("FleetActivityView.c6e80cfe1a") : ''}</p>) : <p>{t("FleetActivityView.cad42e396d")}</p>}</div>}</div>)}
    {page?.next_cursor && <button disabled={loading} onClick={()=>setCursor(page.next_cursor!)}>{loading ? t("FleetActivityView.ba3bbbe10d") : t("FleetActivityView.d3060c46e5")}</button>}
  </section>
}

function ToolsView({data,query,context,onRun}:{data:CanonicalActivity;query:string;context:ActivityContext;onRun:(id:string)=>void}) {
  const [selected,setSelected]=useState('')
  return <>
    <p class="analytics-caption">{rich("FleetActivityView.344ff16a66", { value1: compactNumber(data.matching_calls) })}</p>
    <div class="analytics-tool-list">{[...data.groups].sort((a,b)=>b.calls-a.calls).map(row=>{
      const id=toolKey(row),params=new URLSearchParams(query)
      for(const [key,value] of Object.entries({backend:row.backend,model:row.model,project:row.project_id,layer:row.invocation_layer,tool:row.raw_name,family:row.family,operation:row.operation,transport:row.transport}))if(value)params.set(key,value)
      return <details class="analytics-diagnostics analytics-tool-record" key={id}>
        <summary><strong>{row.raw_name}</strong><span>{rich("FleetActivityView.2dc3004861", { value1: compactNumber(row.calls) })}</span><small>{projectName(row.project_id,context)} · {row.backend} · <ModelName model={row.model}/> · {row.invocation_layer}</small></summary>
        <dl class="analytics-facts"><div><dt>{t("FleetActivityView.b73a5e2ca6")}</dt><dd><MetricValue value={row.calls}/></dd></div><div><dt>{t("FleetActivityView.912d8e962a")}</dt><dd>{Object.entries(row.statuses).map(([status,count])=>`${compactNumber(count)} ${status}`).join(' · ')}</dd></div><div><dt>{t("FleetActivityView.0aa3184ff9")}</dt><dd>{rich("FleetActivityView.8bb3dc7376", { value1: duration(row.average_duration_ms), value2: compactNumber(row.duration_count), value3: compactNumber(row.calls) })}</dd></div><div><dt>{t("FleetActivityView.6213a82dd1")}</dt><dd>{rich("FleetActivityView.f2266973a9", { value1: duration(row.average_approval_wait_ms), value2: compactNumber(row.approval_wait_count) })}</dd></div><div><dt>{t("FleetActivityView.0f044feb6c")}</dt><dd>{row.family} · {row.operation} · {row.transport}</dd></div><div><dt>{t("FleetActivityView.03867aea70")}</dt><dd>{Object.entries(row.qualities).map(([quality,count])=>`${compactNumber(count)} ${quality}`).join(' · ')}</dd></div></dl>
        <button onClick={()=>setSelected(selected===id?'':id)}>{selected===id ? t("FleetActivityView.a36a66844f") : t("FleetActivityView.9277bf74f7")}</button>{selected===id && <ToolCalls key={params.toString()} query={params.toString()} onRun={onRun}/>}
      </details>
    })}</div>
    {!data.groups.length && <p>{t("FleetActivityView.6c191c5935")}</p>}
    <details class="analytics-diagnostics"><summary>{rich("FleetActivityView.31e11a3185", { value1: compactNumber(data.skills.matching_invocations) })}</summary>{data.skills.groups.map((row,index)=><div class="analytics-event" key={`${row.skill_name}-${index}`}><strong>{row.skill_name}</strong><MetricValue value={row.invocations}/><span>{projectName(row.project_id,context)} · {row.backend} · {row.invocation_trigger}</span></div>)}{!data.skills.groups.length && <p>{t("FleetActivityView.cda8b76341")}</p>}</details>
  </>
}

type Check={verification_id:string;run_id:string;project_id?:string;backend:string;model?:string;framework?:string;finished_at:number;successful?:number|null;passed?:number;failed?:number;errors?:number;skipped?:number}
function ChecksView({query,context,onRun}:{query:string;context:ActivityContext;onRun:(id:string)=>void}) {
  const [page,setPage]=useState<{items:Check[];matching:number;next_cursor?:string|null}|null>(null),[cursor,setCursor]=useState(''),[error,setError]=useState(''),[loading,setLoading]=useState(false)
  useEffect(()=>{let cancelled=false;setLoading(true);setError('');api<NonNullable<typeof page>>('GET',`/api/telemetry/v2/verifications?${query}&limit=50${cursor?`&cursor=${encodeURIComponent(cursor)}`:''}`).then(value=>{if(!cancelled)setPage(previous=>cursor&&previous?{...value,items:[...previous.items,...value.items]}:value)}).catch(cause=>{if(!cancelled)setError(String(cause))}).finally(()=>{if(!cancelled)setLoading(false)});return()=>{cancelled=true}},[query,cursor])
  return <>{error && <p role="alert">{error}</p>}<p class="analytics-caption">{page ? t("FleetActivityView.ec6e794c62", { value1: compactNumber(page.matching) }) : t("FleetActivityView.634e0304fd")}</p>{page?.items.map((check,index)=><details class="analytics-diagnostics" key={check.verification_id||index}><summary>{check.framework || t("FleetActivityView.7140f4f19d")} · {check.successful==null ? t("FleetActivityView.164557364f") : check.successful ? t("FleetActivityView.436fe71bb9") : t("FleetActivityView.031a8f0f65")}<small>{projectName(check.project_id,context)} · {when(check.finished_at)}</small></summary><dl class="analytics-facts"><div><dt>{t("FleetActivityView.e93a2b1f1e")}</dt><dd>{check.backend} · <ModelName model={check.model||'unknown'}/></dd></div>{(['passed','failed','errors','skipped'] as const).map(key=><div key={key}><dt>{key}</dt><dd><MetricValue value={check[key]}/></dd></div>)}</dl><button onClick={()=>onRun(check.run_id)}>{t("FleetActivityView.95fe7393c8")}</button></details>)}{page&&!page.items.length && <p>{t("FleetActivityView.ab9a1b5623")}</p>}{page?.next_cursor && <button disabled={loading} onClick={()=>setCursor(page.next_cursor!)}>{loading ? t("FleetActivityView.ba3bbbe10d") : t("FleetActivityView.ac0d66abe6")}</button>}</>
}

function ContextView({data,context}:{data:CanonicalCompactions;context:ActivityContext}) {
  return <><p class="analytics-caption">{rich("FleetActivityView.2de00002a4", { value1: compactNumber(data.total) })}</p>{data.groups.map((row,index)=><details class="analytics-diagnostics" key={index}><summary>{projectName(row.project_id,context)} · <ModelName model={row.model}/><span>{rich("FleetActivityView.7d8407a678", { value1: compactNumber(row.count) })}</span><small>{row.backend} · {row.trigger}</small></summary><dl class="analytics-facts"><div><dt>{t("FleetActivityView.7bd31a025d")}</dt><dd><MetricValue value={row.failures}/></dd></div><div><dt>{t("FleetActivityView.0aa3184ff9")}</dt><dd>{rich("FleetActivityView.f2266973a9", { value1: duration(row.average_duration_ms), value2: compactNumber(row.duration_count) })}</dd></div><div><dt>{t("FleetActivityView.7069923f90")}</dt><dd>{rich("FleetActivityView.f2266973a9", { value1: <MetricValue value={row.average_tokens_reclaimed}/>, value2: compactNumber(row.token_count) })}</dd></div></dl></details>)}{!data.groups.length && <p>{t("FleetActivityView.1780a09d95")}</p>}</>
}

function ActivityComparison({query,context}:{query:string;context:ActivityContext}) {
  const [split,setSplit]=useState('model'),[data,setData]=useState<Comparison|null>(null),[error,setError]=useState('')
  useEffect(()=>{let cancelled=false;setData(null);setError('');api<Comparison>('GET',`/api/telemetry/v2/compare?${query}&split=${split}`).then(value=>{if(!cancelled)setData(value)}).catch(cause=>{if(!cancelled)setError(String(cause))});return()=>{cancelled=true}},[query,split])
  return <section><label class="analytics-chart-picker">{rich("FleetActivityView.758641b352", { value1: <select value={split} onChange={event=>setSplit(event.currentTarget.value)}><option value="model">{t("FleetActivityView.5e2c614c23")}</option><option value="backend">{t("FleetActivityView.e3b5b45953")}</option><option value="project">{t("FleetActivityView.9859597853")}</option></select> })}</label>{error && <p role="alert">{error}</p>}{data ? <><p>{data.comparable ? t("FleetActivityView.c333739066") : t("FleetActivityView.a219289d42", { value1: data.why_not_comparable||t("FleetActivityView.04f4dac8c9") })}</p>{data.cohorts.map(row=><details class="analytics-diagnostics" key={row.cohort}><summary>{split==='project' ? projectName(row.cohort,context) : row.cohort}</summary><dl class="analytics-facts"><div><dt>{t("FleetActivityView.996362bc22")}</dt><dd><MetricValue value={row.runs}/></dd></div><div><dt>{t("FleetActivityView.816a1fbe1f")}</dt><dd><MetricValue value={row.tool_calls}/></dd></div><div><dt>{t("FleetActivityView.c927d856af")}</dt><dd>{row.tool_failure_rate==null ? t("FleetActivityView.040f7884d3") : t("FleetActivityView.d45d6e1cdf", { value1: Math.round(row.tool_failure_rate*100), value2: compactNumber(row.completed_tool_calls) })}</dd></div><div><dt>{t("FleetActivityView.9f3714a97b")}</dt><dd>{compactNumber(row.successful_verifications)}/{compactNumber(row.verifications)}</dd></div></dl><small>{Object.entries(row.other_dimensions).map(([key,values])=>`${key}: ${values.map(value=>key==='project'?projectName(value,context):value).join(', ')}`).join(' · ')}</small></details>)}</> : !error && <p>{t("FleetActivityView.dc01cb009b")}</p>}</section>
}

function ProviderMetrics({query}:{query:string}) {
  const [data,setData]=useState<MetricSummary|null>(null),[error,setError]=useState('')
  useEffect(()=>{let cancelled=false;api<MetricSummary>('GET',`/api/telemetry/v2/metrics/summary?${query}`).then(value=>{if(!cancelled)setData(value)}).catch(cause=>{if(!cancelled)setError(String(cause))});return()=>{cancelled=true}},[query])
  return <>{error && <p role="alert">{error}</p>}{data ? <><p>{rich("FleetActivityView.d9b2a48840", { agree: data.tool_call_agreement.agree, runs: data.tool_call_agreement.runs })}</p>{data.metrics.map((row,index)=><div class="analytics-event" key={index}><strong>{row.backend} · {row.metric}</strong><span>{row.harness_version} · {row.kind}</span><MetricValue value={row.total}/></div>)}</> : !error && <p>{t("FleetActivityView.bac10fd5c8")}</p>}</>
}

export function FleetActivityView(context:ActivityContext={}) {
  const [domain,setDomain]=useState<Domain>('workloads')
  const [filters,setFilters]=useState<ActivityFilters>({days:7,origin:'mux_owned',backend:'',project:'',model:'',layer:'',family:'',status:'',evidence:''})
  const [revision,setRevision]=useState(0),[selectedRun,setSelectedRun]=useState(''),[legacyOpen,setLegacyOpen]=useState(false),[comparisonOpen,setComparisonOpen]=useState(false),[metricsOpen,setMetricsOpen]=useState(false)
  const [options,setOptions]=useState<{backends:string[];projects:string[];models:string[]}>({backends:[],projects:[],models:[]})
  const [quality,setQuality]=useState<CanonicalQuality|null>(null),[qualityError,setQualityError]=useState('')
  const [activity,setActivity]=useState<CanonicalActivity|null>(null),[compactions,setCompactions]=useState<CanonicalCompactions|null>(null)
  const [patterns,setPatterns]=useState<InefficiencyResult|null>(null),[error,setError]=useState(''),[legacyEnabled,setLegacyEnabled]=useState(false)
  const query=useMemo(()=>telemetryQuery({...filters,layer:'',family:'',status:'',evidence:''}), [filters.days, filters.origin, filters.backend, filters.project, filters.model, revision, uiLocale.value, catalogRevision.value])
  const toolQuery=useMemo(()=>{const params=new URLSearchParams(query);for(const [key,value] of Object.entries({layer:filters.layer,family:filters.family,status:filters.status,evidence:filters.evidence}))if(value)params.set(key,value);return params.toString()}, [query, filters.layer, filters.family, filters.status, filters.evidence, uiLocale.value, catalogRevision.value])
  const set=(patch:Partial<ActivityFilters>)=>{setSelectedRun('');setFilters(current=>({...current,...patch}))}
  const onRun=(id:string)=>{setSelectedRun(id);setDomain('workloads')}
  useEffect(()=>{
    let cancelled=false
    const base=telemetryQuery({days:filters.days,origin:filters.origin})
    api<{dimensions:Array<{backend:string;model:string;project_id:string}>}>('GET',`/api/telemetry/v2/workload?${base}`).then(data=>{if(cancelled)return;const unique=(values:string[])=>[...new Set(values.filter(Boolean))].sort();setOptions({backends:unique(data.dimensions.map(row=>row.backend)),models:unique(data.dimensions.map(row=>row.model)),projects:unique(data.dimensions.map(row=>row.project_id))})}).catch(cause=>{if(!cancelled)setQualityError(String(cause))})
    return()=>{cancelled=true}
  },[filters.days,filters.origin,revision])
  useEffect(()=>{let cancelled=false;setQuality(null);setQualityError('');api<CanonicalQuality>('GET',`/api/telemetry/v2/quality?${query}`).then(value=>{if(!cancelled)setQuality(value)}).catch(cause=>{if(!cancelled)setQualityError(String(cause))});return()=>{cancelled=true}},[query])
  useEffect(()=>{let cancelled=false;api<ShadowFlag>('GET',`/api/telemetry/v2/shadow?${query}`).then(value=>{if(!cancelled)setLegacyEnabled(value.legacy_dashboard_enabled)}).catch(()=>{});return()=>{cancelled=true}},[query])
  useEffect(()=>{
    let cancelled=false;setError('');setActivity(null);setCompactions(null);setPatterns(null)
    const fail=(cause:unknown)=>{if(!cancelled)setError(String(cause))}
    if(domain==='tools')api<CanonicalActivity>('GET',`/api/telemetry/v2/tools/summary?${toolQuery}`).then(value=>{if(!cancelled)setActivity(value)}).catch(fail)
    if(domain==='context')api<CanonicalCompactions>('GET',`/api/telemetry/v2/compactions?${query}`).then(value=>{if(!cancelled)setCompactions(value)}).catch(fail)
    if(domain==='inefficiencies')api<InefficiencyResult>('GET',`/api/telemetry/v2/inefficiencies?${toolQuery}`).then(value=>{if(!cancelled)setPatterns(value)}).catch(fail)
    return()=>{cancelled=true}
  },[domain,query,toolQuery])
  const review=async(finding:Finding,verdict:string)=>{try{await api('POST','/api/telemetry/v2/inefficiencies/review',{finding_key:finding.finding_key,kind:finding.kind,verdict});setPatterns(current=>current?{...current,findings:current.findings.map(item=>item.finding_key===finding.finding_key?{...item,review:{verdict,reviewed_at:Date.now()/1000}}:item)}:current)}catch(cause){setError(String(cause))}}
  const toolTab=domain==='tools'||domain==='inefficiencies'
  const filterCount=[filters.origin!=='mux_owned',!!filters.backend,!!filters.project,!!filters.model,...(toolTab?[!!filters.layer,!!filters.family,!!filters.status,!!filters.evidence]:[])].filter(Boolean).length
  const fields:Array<[keyof ActivityFilters,string,string[]]>=[['backend','Harness',options.backends],['project','Project',options.projects],['model','Model',options.models],...(toolTab?[['layer','Layer',['model','runtime']],['family',t("FleetActivityView.8f8f5e2e35"),['read','file','search','agent','skill','shell','planning','web','integration','other']],['status','Outcome',['succeeded','failed','denied','interrupted','abandoned','running','unknown']],['evidence','Evidence',['native','transcript','hook','reconciled','legacy','none']]] as Array<[keyof ActivityFilters,string,string[]]>:[])]
  return <>
    <AnalyticsNav label={t("FleetActivityView.859891986c")} value={domain} onChange={value=>{setDomain(value);setSelectedRun('')}} items={DOMAINS}/>
    <div class="analytics-toolbar"><label>{rich("FleetActivityView.6287ca43ed", { value1: <select value={filters.days} onChange={event=>set({days:Number(event.currentTarget.value)})}><option value="1">{t("FleetActivityView.f0514e8df8")}</option><option value="7">{t("FleetActivityView.7f920bb639")}</option><option value="30">{t("FleetActivityView.ffd7280513")}</option><option value="0">{t("FleetActivityView.d81ae686f7")}</option></select> })}</label>
      <details class="analytics-filter-menu"><summary>{rich("FleetActivityView.bf5cc1c15c", { value1: filterCount ? t("FleetActivityView.bd95960874", { filterCount: filterCount }) : '' })}</summary><div><label>{rich("FleetActivityView.41ea3a77ff", { value1: <select value={filters.origin} onChange={event=>set({origin:event.currentTarget.value})}><option value="mux_owned">{t("FleetActivityView.db6eef2a6d")}</option><option value="all">{t("FleetActivityView.a076c2ac64")}</option><option value="imported">{t("FleetActivityView.8460dae16a")}</option></select> })}</label>
        {fields.map(([key,label,values])=><label key={key}>{label}<select value={filters[key]} onChange={event=>set({[key]:event.currentTarget.value})}><option value="">{t("FleetActivityView.a52ace420f")}</option>{[...new Set([...values,...(filters[key]?[String(filters[key])]:[])])].map(value=><option key={value} value={value}>{key==='project' ? projectName(value,context) : value}</option>)}</select></label>)}
      </div></details><button class="analytics-refresh" onClick={()=>setRevision(value=>value+1)}>{t("FleetActivityView.10d39b0ca3")}</button>
    </div>
    <main class="analytics-content">
      {/* One caption for the section, because every view below it is windowed by the same
          controls. The words come from `telemetryCaptionText` so no view spells a range or
          a cohort its own way, and each view then states its own denominator. */}
      <p class="analytics-caption">{captionText({days:filters.days,origin:filters.origin,filters:[filters.project?projectName(filters.project,context):'',filters.backend,filters.model,...(toolTab?[filters.layer,filters.family,filters.status,filters.evidence]:[])]})}</p>
      {error && <div class="usage-error" role="alert">{error}</div>}
      {domain==='workloads' && <WorkloadTelemetry key={query} {...context} query={query} filters={filters} initialRunId={selectedRun} onClearRun={()=>setSelectedRun('')}/>}
      {domain==='tools' && (activity ? <ToolsView key={toolQuery} data={activity} query={toolQuery} context={context} onRun={onRun}/> : !error && <p>{t("FleetActivityView.f49a50f912")}</p>)}
      {domain==='skills' && <ChecksView key={query} query={query} context={context} onRun={onRun}/>}
      {domain==='context' && (compactions ? <ContextView data={compactions} context={context}/> : !error && <p>{t("FleetActivityView.8cf9312a50")}</p>)}
      {domain==='inefficiencies' && (patterns ? <><p class="analytics-caption">{t("FleetActivityView.f013248530")}</p>{patterns.findings.map(finding=><details class="analytics-diagnostics" key={finding.finding_key}><summary>{finding.kind.replace(/_/g,' ')} · {finding.tool.raw_name || finding.tool.backend}<small>{rich("FleetActivityView.d22bf75d29", { value1: projectName(finding.tool.project_id,context), confidence: finding.confidence })}</small></summary><p>{finding.suggestion}</p><dl class="analytics-facts">{Object.entries(finding.evidence).map(([key,value])=><div key={key}><dt>{key.replace(/_/g,' ')}</dt><dd><MetricValue value={value}/></dd></div>)}</dl><div class="analytics-actions"><button onClick={()=>{set({backend:finding.tool.backend,model:finding.tool.model,project:finding.tool.project_id||'',layer:finding.tool.invocation_layer||''});setDomain('tools')}}>{t("FleetActivityView.19fd70b691")}</button>{['useful','noise','already_known'].map(verdict=><button class={finding.review?.verdict===verdict?'active':''} onClick={()=>void review(finding,verdict)}>{verdict.replace(/_/g,' ')}</button>)}</div></details>)}{!patterns.findings.length && <p>{t("FleetActivityView.6c3777e7ba")}</p>}</> : !error && <p>{t("FleetActivityView.a92e1e5363")}</p>)}
      {domain==='inefficiencies' && <details class="analytics-diagnostics" onToggle={event=>setComparisonOpen(event.currentTarget.open)}><summary>{t("FleetActivityView.50feb5fdb2")}</summary>{comparisonOpen && <ActivityComparison key={query} query={query} context={context}/>}</details>}
      <details class="analytics-diagnostics analytics-collection"><summary>{rich("FleetActivityView.2cc6ac6bb8", { value1: qualityError ? t("FleetActivityView.ca18449697") : quality ? t("FleetActivityView.98e7db5aa4", { value1: compactNumber(quality.totals.with_duration), value2: compactNumber(quality.totals.calls) }) : t("FleetActivityView.dc380888c4") })}</summary>
        {qualityError && <p role="alert">{qualityError}</p>}{quality && <><p>{t("FleetActivityView.75900f7e48")}</p>{quality.backends.map(row=><div class="analytics-event" key={row.backend}><strong>{row.backend}</strong><span>{rich("FleetActivityView.1512cb94a5", { value1: compactNumber(row.calls), value2: compactNumber(row.with_result), value3: compactNumber(row.with_duration) })}</span></div>)}<p>{rich("FleetActivityView.7a915a07a5", { value1: quality.collection?.provider_dropped || 0, value2: quality.collection?.reconciliation?.errors || 0 })}</p>
          <details><summary>{t("FleetActivityView.b81cd33b76")}</summary>{quality.reconciliation?.by_backend.map((row,index)=><p key={index}>{rich("FleetActivityView.3182bac27c", { backend: row.backend, status: codeLabel(row.status), value3: compactNumber(row.runs), parser_version: row.parser_version })}</p>)}{quality.parsers?.filter(row=>!row.recognized).map((row,index)=><p key={index}>{rich("FleetActivityView.c89fce56ec", { backend: row.backend, harness_version: row.harness_version, event_name: row.event_name, value4: compactNumber(row.occurrences) })}</p>)}{quality.collection?.schema?.drift?.map(item=><p>{item}</p>)}</details>
        </>}
        {quality && <details><summary>{t("FleetActivityView.23567ab1d9")}</summary>{Object.entries(quality.capabilities).map(([backend,fields])=><p key={backend}><strong>{backend}</strong> · {Object.entries(fields).map(([key,value])=>`${key.replace(/_/g,' ')}: ${value.replace(/_/g,' ')}`).join(' · ')}</p>)}{quality.versions.map((row,index)=><p key={index}>{rich("FleetActivityView.a42a173b0f", { backend: row.backend, harness_version: row.harness_version, value3: compactNumber(row.with_result), value4: compactNumber(row.calls), value5: compactNumber(row.with_duration), value6: compactNumber(row.runtime_parent_unavailable) })}</p>)}</details>}
        <details onToggle={event=>setMetricsOpen(event.currentTarget.open)}><summary>{t("FleetActivityView.bd2da8bfaf")}</summary>{metricsOpen && <ProviderMetrics key={query} query={query}/>}</details>
        <details><summary>{t("FleetActivityView.f8c91b067a")}</summary><div class="analytics-export-list">{EXPORT_KINDS.map(kind=><span key={kind}>{kind.replace(/_/g,' ')} <a href={`/api/telemetry/v2/export/${kind}?${query}&format=csv`} download>{t("FleetActivityView.eb6b42f54c")}</a> <a href={`/api/telemetry/v2/export/${kind}?${query}&format=jsonl`} download>{t("FleetActivityView.125a78400b")}</a></span>)}</div></details>
        {legacyEnabled && <details onToggle={event=>setLegacyOpen(event.currentTarget.open)}><summary>{t("FleetActivityView.1b6531d1ad")}</summary>{legacyOpen && <LegacyToolTelemetry from={Number(new URLSearchParams(query).get('from'))}/>}</details>}
      </details>
    </main>
  </>
}
