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


const DOMAINS:Array<{id:Domain;label:string}>=[{id:'workloads',label:'Runs'},{id:'tools',label:'Tools'},{id:'skills',label:'Checks'},{id:'context',label:'Context'},{id:'inefficiencies',label:'Patterns'}]
const EXPORT_KINDS=['runs','turns','tool_calls','model_requests','skills','verifications','compactions','provider_metrics','evidence']
const when=(time:number)=>new Date(time*1000).toLocaleString()
const duration=(milliseconds:number|null|undefined)=>milliseconds==null?'Not measured':milliseconds===0?'0ms':milliseconds<1000?`${Math.round(milliseconds)}ms`:formatDuration(milliseconds/1000)
const projectName=(id:string|undefined,context:ActivityContext)=>context.projects?.find(project=>project.id===id)?.name||id||'Unassigned'
const toolKey=(row:ToolGroup)=>JSON.stringify([row.backend,row.model,row.project_id,row.origin,row.invocation_layer,row.family,row.operation,row.transport,row.raw_name])

function ToolCalls({query,onRun}:{query:string;onRun:(id:string)=>void}) {
  const [page,setPage]=useState<CanonicalToolPage|null>(null),[cursor,setCursor]=useState(''),[error,setError]=useState(''),[loading,setLoading]=useState(false)
  const [evidence,setEvidence]=useState<CanonicalAudit|null>(null),[selected,setSelected]=useState(''),[auditError,setAuditError]=useState('')
  useEffect(()=>{let cancelled=false;setLoading(true);setError('');api<CanonicalToolPage>('GET',`/api/telemetry/v2/tools?${query}&limit=30${cursor?`&cursor=${encodeURIComponent(cursor)}`:''}`).then(value=>{if(!cancelled)setPage(previous=>cursor&&previous?{...value,items:[...previous.items,...value.items]}:value)}).catch(cause=>{if(!cancelled)setError(String(cause))}).finally(()=>{if(!cancelled)setLoading(false)});return()=>{cancelled=true}},[query,cursor])
  useEffect(()=>{let cancelled=false;setEvidence(null);setAuditError('');if(selected)api<CanonicalAudit>('GET',`/api/telemetry/v2/tools/${encodeURIComponent(selected)}`).then(value=>{if(!cancelled)setEvidence(value)}).catch(cause=>{if(!cancelled)setAuditError(String(cause))});return()=>{cancelled=true}},[selected])
  return <section>{error&&<p role="alert">{error}</p>}<p class="analytics-caption">{page?`${compactNumber(page.matching_calls??page.matching)} matching calls`:'Loading calls…'}</p>
    {page?.items.map(call=><div class="analytics-event" key={call.tool_call_id}><strong>{call.status}</strong><span>{when(call.started_at)} · {duration(call.duration_ms)}</span><div class="analytics-actions"><button onClick={()=>onRun(call.run_id)}>Run</button><button onClick={()=>setSelected(selected===call.tool_call_id?'':call.tool_call_id)}>Evidence</button></div>{selected===call.tool_call_id&&<div class="analytics-evidence">{auditError?<p role="alert">{auditError}</p>:evidence?evidence.evidence.map(item=><p key={item.evidence_id}>{item.observation?`${item.observation.source_kind} · ${item.observation.event_type} · ${when(item.observation.observed_at)}`:'Source unavailable'}{item.conflict?' · conflicting evidence':''}</p>):<p>Loading evidence…</p>}</div>}</div>)}
    {page?.next_cursor&&<button disabled={loading} onClick={()=>setCursor(page.next_cursor!)}>{loading?'Loading…':'Load more calls'}</button>}
  </section>
}

function ToolsView({data,query,context,onRun}:{data:CanonicalActivity;query:string;context:ActivityContext;onRun:(id:string)=>void}) {
  const [selected,setSelected]=useState('')
  return <>
    <p class="analytics-caption">{compactNumber(data.matching_calls)} calls during this period. Model-selected calls and nested runtime executions are separate layers.</p>
    <div class="analytics-tool-list">{[...data.groups].sort((a,b)=>b.calls-a.calls).map(row=>{
      const id=toolKey(row),params=new URLSearchParams(query)
      for(const [key,value] of Object.entries({backend:row.backend,model:row.model,project:row.project_id,layer:row.invocation_layer,tool:row.raw_name,family:row.family,operation:row.operation,transport:row.transport}))if(value)params.set(key,value)
      return <details class="analytics-diagnostics analytics-tool-record" key={id}>
        <summary><strong>{row.raw_name}</strong><span>{compactNumber(row.calls)} calls</span><small>{projectName(row.project_id,context)} · {row.backend} · <ModelName model={row.model}/> · {row.invocation_layer}</small></summary>
        <dl class="analytics-facts"><div><dt>Calls</dt><dd><MetricValue value={row.calls}/></dd></div><div><dt>Outcomes</dt><dd>{Object.entries(row.statuses).map(([status,count])=>`${compactNumber(count)} ${status}`).join(' · ')}</dd></div><div><dt>Mean duration</dt><dd>{duration(row.average_duration_ms)} · {compactNumber(row.duration_count)}/{compactNumber(row.calls)} measured</dd></div><div><dt>Mean approval wait</dt><dd>{duration(row.average_approval_wait_ms)} · {compactNumber(row.approval_wait_count)} measured</dd></div><div><dt>Operation</dt><dd>{row.family} · {row.operation} · {row.transport}</dd></div><div><dt>Evidence</dt><dd>{Object.entries(row.qualities).map(([quality,count])=>`${compactNumber(count)} ${quality}`).join(' · ')}</dd></div></dl>
        <button onClick={()=>setSelected(selected===id?'':id)}>{selected===id?'Hide calls':'Inspect calls'}</button>{selected===id&&<ToolCalls key={params.toString()} query={params.toString()} onRun={onRun}/>}
      </details>
    })}</div>
    {!data.groups.length&&<p>No tool records match these filters.</p>}
    <details class="analytics-diagnostics"><summary>Explicit skill activations ({compactNumber(data.skills.matching_invocations)})</summary>{data.skills.groups.map((row,index)=><div class="analytics-event" key={`${row.skill_name}-${index}`}><strong>{row.skill_name}</strong><MetricValue value={row.invocations}/><span>{projectName(row.project_id,context)} · {row.backend} · {row.invocation_trigger}</span></div>)}{!data.skills.groups.length&&<p>No explicit skill activations were recorded. Tool use alone does not establish skill activation.</p>}</details>
  </>
}

type Check={verification_id:string;run_id:string;project_id?:string;backend:string;model?:string;framework?:string;finished_at:number;successful?:number|null;passed?:number;failed?:number;errors?:number;skipped?:number}
function ChecksView({query,context,onRun}:{query:string;context:ActivityContext;onRun:(id:string)=>void}) {
  const [page,setPage]=useState<{items:Check[];matching:number;next_cursor?:string|null}|null>(null),[cursor,setCursor]=useState(''),[error,setError]=useState(''),[loading,setLoading]=useState(false)
  useEffect(()=>{let cancelled=false;setLoading(true);setError('');api<NonNullable<typeof page>>('GET',`/api/telemetry/v2/verifications?${query}&limit=50${cursor?`&cursor=${encodeURIComponent(cursor)}`:''}`).then(value=>{if(!cancelled)setPage(previous=>cursor&&previous?{...value,items:[...previous.items,...value.items]}:value)}).catch(cause=>{if(!cancelled)setError(String(cause))}).finally(()=>{if(!cancelled)setLoading(false)});return()=>{cancelled=true}},[query,cursor])
  return <>{error&&<p role="alert">{error}</p>}<p class="analytics-caption">{page?`${compactNumber(page.matching)} recorded checks in this period`:'Loading checks…'}</p>{page?.items.map((check,index)=><details class="analytics-diagnostics" key={check.verification_id||index}><summary>{check.framework||'Verification'} · {check.successful==null?'Outcome unknown':check.successful?'Passed':'Failed'}<small>{projectName(check.project_id,context)} · {when(check.finished_at)}</small></summary><dl class="analytics-facts"><div><dt>Harness / model</dt><dd>{check.backend} · <ModelName model={check.model||'unknown'}/></dd></div>{(['passed','failed','errors','skipped'] as const).map(key=><div key={key}><dt>{key}</dt><dd><MetricValue value={check[key]}/></dd></div>)}</dl><button onClick={()=>onRun(check.run_id)}>Open run</button></details>)}{page&&!page.items.length&&<p>No verification results were recorded for this period. This does not establish whether the work was tested.</p>}{page?.next_cursor&&<button disabled={loading} onClick={()=>setCursor(page.next_cursor!)}>{loading?'Loading…':'Load more checks'}</button>}</>
}

function ContextView({data,context}:{data:CanonicalCompactions;context:ActivityContext}) {
  return <><p class="analytics-caption">{compactNumber(data.total)} compactions during this period.</p>{data.groups.map((row,index)=><details class="analytics-diagnostics" key={index}><summary>{projectName(row.project_id,context)} · <ModelName model={row.model}/><span>{compactNumber(row.count)} compactions</span><small>{row.backend} · {row.trigger}</small></summary><dl class="analytics-facts"><div><dt>Failures</dt><dd><MetricValue value={row.failures}/></dd></div><div><dt>Mean duration</dt><dd>{duration(row.average_duration_ms)} · {compactNumber(row.duration_count)} measured</dd></div><div><dt>Mean tokens reclaimed</dt><dd><MetricValue value={row.average_tokens_reclaimed}/> · {compactNumber(row.token_count)} measured</dd></div></dl></details>)}{!data.groups.length&&<p>No compaction evidence in this period.</p>}</>
}

function ActivityComparison({query,context}:{query:string;context:ActivityContext}) {
  const [split,setSplit]=useState('model'),[data,setData]=useState<Comparison|null>(null),[error,setError]=useState('')
  useEffect(()=>{let cancelled=false;setData(null);setError('');api<Comparison>('GET',`/api/telemetry/v2/compare?${query}&split=${split}`).then(value=>{if(!cancelled)setData(value)}).catch(cause=>{if(!cancelled)setError(String(cause))});return()=>{cancelled=true}},[query,split])
  return <section><label class="analytics-chart-picker">Compare by<select value={split} onChange={event=>setSplit(event.currentTarget.value)}><option value="model">Model</option><option value="backend">Harness</option><option value="project">Project</option></select></label>{error&&<p role="alert">{error}</p>}{data?<><p>{data.comparable?'These cohorts share the measured comparison dimensions.':`Not comparable: ${data.why_not_comparable||'different workloads'}`}</p>{data.cohorts.map(row=><details class="analytics-diagnostics" key={row.cohort}><summary>{split==='project'?projectName(row.cohort,context):row.cohort}</summary><dl class="analytics-facts"><div><dt>Runs started</dt><dd><MetricValue value={row.runs}/></dd></div><div><dt>Tools recorded</dt><dd><MetricValue value={row.tool_calls}/></dd></div><div><dt>Tool failure rate</dt><dd>{row.tool_failure_rate==null?'Not measured':`${Math.round(row.tool_failure_rate*100)}% of ${compactNumber(row.completed_tool_calls)} completed calls`}</dd></div><div><dt>Successful checks</dt><dd>{compactNumber(row.successful_verifications)}/{compactNumber(row.verifications)}</dd></div></dl><small>{Object.entries(row.other_dimensions).map(([key,values])=>`${key}: ${values.map(value=>key==='project'?projectName(value,context):value).join(', ')}`).join(' · ')}</small></details>)}</>:!error&&<p>Loading comparison…</p>}</section>
}

function ProviderMetrics({query}:{query:string}) {
  const [data,setData]=useState<MetricSummary|null>(null),[error,setError]=useState('')
  useEffect(()=>{let cancelled=false;api<MetricSummary>('GET',`/api/telemetry/v2/metrics/summary?${query}`).then(value=>{if(!cancelled)setData(value)}).catch(cause=>{if(!cancelled)setError(String(cause))});return()=>{cancelled=true}},[query])
  return <>{error&&<p role="alert">{error}</p>}{data?<><p>Provider and ledger tool counts agree for {data.tool_call_agreement.agree}/{data.tool_call_agreement.runs} observed runs.</p>{data.metrics.map((row,index)=><div class="analytics-event" key={index}><strong>{row.backend} · {row.metric}</strong><span>{row.harness_version} · {row.kind}</span><MetricValue value={row.total}/></div>)}</>:!error&&<p>Loading provider metrics…</p>}</>
}

export function FleetActivityView(context:ActivityContext={}) {
  const [domain,setDomain]=useState<Domain>('workloads')
  const [filters,setFilters]=useState<ActivityFilters>({days:7,origin:'mux_owned',backend:'',project:'',model:'',layer:'',family:'',status:'',evidence:''})
  const [revision,setRevision]=useState(0),[selectedRun,setSelectedRun]=useState(''),[legacyOpen,setLegacyOpen]=useState(false),[comparisonOpen,setComparisonOpen]=useState(false),[metricsOpen,setMetricsOpen]=useState(false)
  const [options,setOptions]=useState<{backends:string[];projects:string[];models:string[]}>({backends:[],projects:[],models:[]})
  const [quality,setQuality]=useState<CanonicalQuality|null>(null),[qualityError,setQualityError]=useState('')
  const [activity,setActivity]=useState<CanonicalActivity|null>(null),[compactions,setCompactions]=useState<CanonicalCompactions|null>(null)
  const [patterns,setPatterns]=useState<InefficiencyResult|null>(null),[error,setError]=useState(''),[legacyEnabled,setLegacyEnabled]=useState(false)
  const query=useMemo(()=>telemetryQuery({...filters,layer:'',family:'',status:'',evidence:''}),[filters.days,filters.origin,filters.backend,filters.project,filters.model,revision])
  const toolQuery=useMemo(()=>{const params=new URLSearchParams(query);for(const [key,value] of Object.entries({layer:filters.layer,family:filters.family,status:filters.status,evidence:filters.evidence}))if(value)params.set(key,value);return params.toString()},[query,filters.layer,filters.family,filters.status,filters.evidence])
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
  const fields:Array<[keyof ActivityFilters,string,string[]]>=[['backend','Harness',options.backends],['project','Project',options.projects],['model','Model',options.models],...(toolTab?[['layer','Layer',['model','runtime']],['family','Tool family',['read','file','search','agent','skill','shell','planning','web','integration','other']],['status','Outcome',['succeeded','failed','denied','interrupted','abandoned','running','unknown']],['evidence','Evidence',['native','transcript','hook','reconciled','legacy','none']]] as Array<[keyof ActivityFilters,string,string[]]>:[])]
  return <>
    <AnalyticsNav label="Activity views" value={domain} onChange={value=>{setDomain(value);setSelectedRun('')}} items={DOMAINS}/>
    <div class="analytics-toolbar"><label>Range<select value={filters.days} onChange={event=>set({days:Number(event.currentTarget.value)})}><option value="1">24 hours</option><option value="7">7 days</option><option value="30">30 days</option><option value="0">All retained</option></select></label>
      <details class="analytics-filter-menu"><summary>Filters{filterCount?` (${filterCount})`:''}</summary><div><label>History<select value={filters.origin} onChange={event=>set({origin:event.currentTarget.value})}><option value="mux_owned">Mux-owned</option><option value="all">Include imported</option><option value="imported">Imported only</option></select></label>
        {fields.map(([key,label,values])=><label key={key}>{label}<select value={filters[key]} onChange={event=>set({[key]:event.currentTarget.value})}><option value="">All</option>{[...new Set([...values,...(filters[key]?[String(filters[key])]:[])])].map(value=><option key={value} value={value}>{key==='project'?projectName(value,context):value}</option>)}</select></label>)}
      </div></details><button class="analytics-refresh" onClick={()=>setRevision(value=>value+1)}>Reload activity</button>
    </div>
    <main class="analytics-content">
      {/* One caption for the section, because every view below it is windowed by the same
          controls. The words come from `telemetryCaptionText` so no view spells a range or
          a cohort its own way, and each view then states its own denominator. */}
      <p class="analytics-caption">{captionText({days:filters.days,origin:filters.origin,filters:[filters.project?projectName(filters.project,context):'',filters.backend,filters.model,...(toolTab?[filters.layer,filters.family,filters.status,filters.evidence]:[])]})}</p>
      {error&&<div class="usage-error" role="alert">{error}</div>}
      {domain==='workloads'&&<WorkloadTelemetry key={query} {...context} query={query} filters={filters} initialRunId={selectedRun} onClearRun={()=>setSelectedRun('')}/>}
      {domain==='tools'&&(activity?<ToolsView key={toolQuery} data={activity} query={toolQuery} context={context} onRun={onRun}/>:!error&&<p>Loading tool activity…</p>)}
      {domain==='skills'&&<ChecksView key={query} query={query} context={context} onRun={onRun}/>}
      {domain==='context'&&(compactions?<ContextView data={compactions} context={context}/>:!error&&<p>Loading context evidence…</p>)}
      {domain==='inefficiencies'&&(patterns?<><p class="analytics-caption">Observed patterns to inspect. Repetition or long duration alone does not establish wasted work.</p>{patterns.findings.map(finding=><details class="analytics-diagnostics" key={finding.finding_key}><summary>{finding.kind.replace(/_/g,' ')} · {finding.tool.raw_name||finding.tool.backend}<small>{projectName(finding.tool.project_id,context)} · confidence {finding.confidence}</small></summary><p>{finding.suggestion}</p><dl class="analytics-facts">{Object.entries(finding.evidence).map(([key,value])=><div key={key}><dt>{key.replace(/_/g,' ')}</dt><dd><MetricValue value={value}/></dd></div>)}</dl><div class="analytics-actions"><button onClick={()=>{set({backend:finding.tool.backend,model:finding.tool.model,project:finding.tool.project_id||'',layer:finding.tool.invocation_layer||''});setDomain('tools')}}>Inspect matching tools</button>{['useful','noise','already_known'].map(verdict=><button class={finding.review?.verdict===verdict?'active':''} onClick={()=>void review(finding,verdict)}>{verdict.replace(/_/g,' ')}</button>)}</div></details>)}{!patterns.findings.length&&<p>No patterns flagged for this period.</p>}</>:!error&&<p>Loading patterns…</p>)}
      {domain==='inefficiencies'&&<details class="analytics-diagnostics" onToggle={event=>setComparisonOpen(event.currentTarget.open)}><summary>Compare cohorts</summary>{comparisonOpen&&<ActivityComparison key={query} query={query} context={context}/>}</details>}
      <details class="analytics-diagnostics analytics-collection"><summary>Collection health · {qualityError?'Unavailable':quality?`${compactNumber(quality.totals.with_duration)}/${compactNumber(quality.totals.calls)} calls timed`:'Loading'}</summary>
        {qualityError&&<p role="alert">{qualityError}</p>}{quality&&<><p>Missing measurements are not zero. Historical and provider coverage differ by field.</p>{quality.backends.map(row=><div class="analytics-event" key={row.backend}><strong>{row.backend}</strong><span>{compactNumber(row.calls)} calls · {compactNumber(row.with_result)} with results · {compactNumber(row.with_duration)} timed</span></div>)}<p>{quality.collection?.provider_dropped||0} provider batches dropped · {quality.collection?.reconciliation?.errors||0} errors in the last reconciliation pass</p>
          <details><summary>Parser and reconciliation details</summary>{quality.reconciliation?.by_backend.map((row,index)=><p key={index}>{row.backend} · {row.status} · {compactNumber(row.runs)} runs · {row.parser_version}</p>)}{quality.parsers?.filter(row=>!row.recognized).map((row,index)=><p key={index}>{row.backend} {row.harness_version} · unrecognized {row.event_name}: {compactNumber(row.occurrences)}</p>)}{quality.collection?.schema?.drift?.map(item=><p>{item}</p>)}</details>
        </>}
        {quality&&<details><summary>Measurement capabilities and versions</summary>{Object.entries(quality.capabilities).map(([backend,fields])=><p key={backend}><strong>{backend}</strong> · {Object.entries(fields).map(([key,value])=>`${key.replace(/_/g,' ')}: ${value.replace(/_/g,' ')}`).join(' · ')}</p>)}{quality.versions.map((row,index)=><p key={index}>{row.backend} {row.harness_version} · {compactNumber(row.with_result)}/{compactNumber(row.calls)} results · {compactNumber(row.with_duration)} timed · {compactNumber(row.runtime_parent_unavailable)} runtime parents unavailable</p>)}</details>}
        <details onToggle={event=>setMetricsOpen(event.currentTarget.open)}><summary>Provider metrics and count agreement</summary>{metricsOpen&&<ProviderMetrics key={query} query={query}/>}</details>
        <details><summary>Export this period</summary><div class="analytics-export-list">{EXPORT_KINDS.map(kind=><span key={kind}>{kind.replace(/_/g,' ')} <a href={`/api/telemetry/v2/export/${kind}?${query}&format=csv`} download>CSV</a> <a href={`/api/telemetry/v2/export/${kind}?${query}&format=jsonl`} download>JSONL</a></span>)}</div></details>
        {legacyEnabled&&<details onToggle={event=>setLegacyOpen(event.currentTarget.open)}><summary>Legacy comparison</summary>{legacyOpen&&<LegacyToolTelemetry from={Number(new URLSearchParams(query).get('from'))}/>}</details>}
      </details>
    </main>
  </>
}
