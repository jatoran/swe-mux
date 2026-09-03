import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { LegacyToolTelemetry } from './LegacyToolTelemetry'
import { ModelName } from './ModelName'
import { type Coverage, TelemetryCaption } from './telemetryCaption'
import { telemetryQuery, WorkloadTelemetry } from './WorkloadTelemetry'

// What the fleet has been doing: runs and workload, explicit tool and skill activity, and
// context compaction evidence.
//
// All three were domains of Resources -> Tokens, where none of them measured a token or a
// dollar. Runs, tool calls, and compactions are descriptive behavior; the segment they sat
// in was named for money and carried the source picker, the refresh, and the cache controls
// of a completely different subject, which had to go inert on every one of these three tabs
// and say so in the status line.
//
// They belong beside Processes rather than beside spend. Processes answers what the fleet is
// running right now; this answers what it has been doing. Both are fleet-scoped, both are
// read when something looks wrong rather than when a bill is due, and neither is a currency.
//
// Money is deliberately absent. The Usage dialog is the whole cost picture, and a second
// table of one number under a second name is exactly the drift this split exists to stop.
//
// Every figure here is drawn over the whole selected window: the daemon sums closed days
// and hours from rollups and the rest from canonical entities, and no total is computed
// from a displayed page. The controls above the tabs are one query for every tab, so
// switching tabs never silently changes the question, and every total carries a caption
// naming its range, cohort, denominator, and coverage.

type Domain = 'workloads' | 'tools' | 'skills' | 'context' | 'inefficiencies' | 'legacy'

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
type RunAudit = {
  run:{run_id:string;session_id:string;backend:string;final_model?:string;started_at:number;ended_at?:number|null;started_at_source?:string;origin:string;source_locator?:string;harness_version?:string}
  turns:Array<{turn_id:string;status:string;started_at:number;duration_ms?:number|null}>
  tool_calls:{total:number;by_status:Record<string,number>;by_layer:Record<string,number>;by_quality:Record<string,number>}
  model_requests:{count:number;failures:number;input_tokens:number;output_tokens:number}
  provider_metrics:Array<{metric_name:string;points:number;count:number;total:number}>
  reconciliation?:{status:string;parser_version:string;tool_events:number;reconciled_at:number}|null
  evidence:EvidenceLink[]
}
type TurnAudit = {turn:{turn_id:string;run_id:string;status:string;started_at:number;duration_ms?:number|null};tool_calls:CanonicalToolCall[];model_requests:Array<{model_request_id:string;model?:string;duration_ms?:number|null;input_tokens?:number|null;output_tokens?:number|null}>;evidence:EvidenceLink[]}
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
type VerificationSummary = {
  totals:{verifications:number;successful:number;passed:number;failed:number;errors:number;skipped:number};coverage:Coverage
  groups:Array<{backend:string;model:string;project_id:string;framework:string;verifications:number;successful:number;passed:number;failed:number;errors:number;skipped:number;success_rate?:number|null}>
}
type MetricSummary = {
  metrics:Array<{backend:string;harness_version:string;metric:string;kind:string;points:number;count:number;total:number;min?:number|null;max?:number|null}>
  tool_call_agreement:{runs:number;agree:number;ledger_more:number;provider_more:number;examples:Array<{run_id:string;provider_reported:number;ledger:number;verdict:string}>}
}
type ShadowFlag = {legacy_dashboard_enabled:boolean}

const DOMAINS: Array<[Domain, string]> = [
  ['workloads', 'runs + workload'],
  ['tools', 'tools'],
  ['skills', 'skills + verification'],
  ['context', 'context + compaction'],
  ['inefficiencies', 'inefficiencies'],
]
const LAYERS: Array<[string, string]> = [['', 'Every layer'], ['model', 'Model-selected'], ['runtime', 'Nested runtime']]
const STATUSES = ['succeeded', 'failed', 'denied', 'interrupted', 'abandoned', 'running', 'unknown']
const QUALITIES = ['native', 'transcript', 'hook', 'reconciled', 'legacy', 'none']
const FAMILIES = ['read', 'file', 'search', 'agent', 'skill', 'shell', 'planning', 'web', 'integration', 'other']
/** Row kinds the daemon can export; each carries its evidence ids and source locator. */
const EXPORT_KINDS = ['tool_calls', 'runs', 'turns', 'model_requests', 'skills', 'verifications', 'compactions', 'provider_metrics', 'evidence'] as const
const VERDICTS: Array<[string, string]> = [['useful', 'useful'], ['noise', 'noise'], ['already_known', 'already known']]

type Filters = {days:number;origin:string;backend:string;project:string;model:string;layer:string;family:string;status:string;evidence:string}

function importNote(collection:Collection|undefined):string {
  if(!collection||collection.backfill_completed)return ''
  return `Historical import: ${collection.backfill_stream} · ${collection.backfilled.toLocaleString()} observations preserved.`
}
function when(ts:number|null|undefined):string { return ts==null?'unavailable':new Date(ts*1000).toLocaleString() }
function ms(value:number|null|undefined):string { return value==null?'unavailable':`${Math.round(value)}ms` }
function percent(value:number|null|undefined):string { return value==null?'n/a':`${Math.round(value*100)}%` }

function EvidenceList({evidence}:{evidence:EvidenceLink[]}) {
  return <>{evidence.map(item=><article key={`${item.evidence_id}-${item.contribution}`}><strong>{item.observation?.source_kind||'unavailable source'} · {item.contribution.replace(/_/g,' ')}{item.conflict?' · conflict':''}</strong><span>{item.observation?when(item.observation.observed_at):'observation unavailable'} · precedence {item.precedence_rank}</span><small>{item.observation?`${item.observation.event_type} · ${item.observation.payload_bytes} metadata bytes · SHA-256 ${item.observation.payload_sha256}${item.observation.source_locator?` · ${item.observation.source_locator}`:''}`:'The source observation is no longer available.'}</small></article>)}</>
}

function DrillDown({audit,run,turn,onRun,onTurn}:{audit:CanonicalAudit|null;run:RunAudit|null;turn:TurnAudit|null;onRun:(id:string)=>void;onTurn:(id:string)=>void}) {
  return <>
    {run&&<section class="attribution-log telemetry-drill"><h3>Run {run.run.run_id.slice(0,8)}</h3>
      <p><strong>{run.run.backend}</strong> · <ModelName model={run.run.final_model||'unknown'}/> · {run.run.origin} · started {when(run.run.started_at)} ({run.run.started_at_source||'unknown'} start){run.run.ended_at?` · ended ${when(run.run.ended_at)}`:' · still open'}{run.run.harness_version?` · ${run.run.harness_version}`:''}</p>
      <p>{run.tool_calls.total} tool calls ({Object.entries(run.tool_calls.by_status).map(([k,v])=>`${v} ${k}`).join(', ')||'none'}; evidence {Object.entries(run.tool_calls.by_quality).map(([k,v])=>`${v} ${k}`).join(', ')||'none'}) · {run.model_requests.count} model requests, {run.model_requests.failures} failed · {run.reconciliation?`native store ${run.reconciliation.status} (${run.reconciliation.tool_events} tool events, ${run.reconciliation.parser_version})`:'native store not reconciled yet'}</p>
      {run.provider_metrics.length?<p>Provider self-report: {run.provider_metrics.map(item=>`${item.metric_name} ${item.total}`).join(' · ')}</p>:null}
      <div class="usage-table-scroll"><table><thead><tr><th>turn</th><th>status</th><th>started</th><th>duration</th><th></th></tr></thead><tbody>{run.turns.map(item=><tr key={item.turn_id}><td>{item.turn_id.slice(0,8)}</td><td>{item.status}</td><td>{when(item.started_at)}</td><td>{ms(item.duration_ms)}</td><td><button onClick={()=>onTurn(item.turn_id)}>Calls</button></td></tr>)}</tbody></table></div>
      <EvidenceList evidence={run.evidence}/>
    </section>}
    {turn&&<section class="attribution-log telemetry-drill"><h3>Turn {turn.turn.turn_id.slice(0,8)} · {turn.turn.status}</h3>
      <p>Run <button onClick={()=>onRun(turn.turn.run_id)}>{turn.turn.run_id.slice(0,8)}</button> · started {when(turn.turn.started_at)} · {ms(turn.turn.duration_ms)} · {turn.model_requests.length} model requests</p>
      <div class="usage-table-scroll"><table><thead><tr><th>time</th><th>layer/tool</th><th>status</th><th>duration</th><th>evidence</th></tr></thead><tbody>{turn.tool_calls.map(item=><tr key={item.tool_call_id}><td>{when(item.started_at)}</td><td>{item.invocation_layer} · {item.raw_name}</td><td>{item.status}</td><td>{ms(item.duration_ms)}</td><td>{item.evidence_quality}</td></tr>)}</tbody></table></div>
      <EvidenceList evidence={turn.evidence}/>
    </section>}
    {audit&&<section class="attribution-log telemetry-drill"><h3>Call evidence</h3><p><strong>{audit.call.raw_name}</strong> · {audit.call.status} · {audit.call.evidence_quality} · run <button onClick={()=>onRun(audit.call.run_id)}>{audit.call.run_id.slice(0,8)}</button>{audit.call.turn_id?<> · turn <button onClick={()=>onTurn(audit.call.turn_id!)}>{audit.call.turn_id.slice(0,8)}</button></>:null}{audit.call.approval_wait_ms!=null?` · waited ${ms(audit.call.approval_wait_ms)} for approval`:''}</p><EvidenceList evidence={audit.evidence}/></section>}
  </>
}

function ToolsView({activity,quality,calls,filters,query,onDrill}:{activity:CanonicalActivity|null;quality:CanonicalQuality|null;calls:CanonicalToolPage|null;filters:Filters;query:string;onDrill:(group:ToolGroup)=>void}) {
  const [audit,setAudit]=useState<CanonicalAudit|null>(null)
  const [run,setRun]=useState<RunAudit|null>(null)
  const [turn,setTurn]=useState<TurnAudit|null>(null)
  const [drillError,setDrillError]=useState('')
  const fail=(cause:unknown)=>setDrillError(cause instanceof Error?cause.message:String(cause))
  const inspect=(id:string)=>{setDrillError('');api<CanonicalAudit>('GET',`/api/telemetry/v2/tools/${id}`).then(setAudit).catch(fail)}
  const openRun=(id:string)=>{setDrillError('');api<RunAudit>('GET',`/api/telemetry/v2/runs/${id}`).then(setRun).catch(fail)}
  const openTurn=(id:string)=>{setDrillError('');api<TurnAudit>('GET',`/api/telemetry/v2/turns/${id}`).then(setTurn).catch(fail)}
  const unrecognised=(quality?.parsers||[]).filter(item=>!item.recognized)
  const drift=quality?.collection?.schema?.drift||[]
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">Model-selected calls and nested runtime executions are counted separately. Only explicit provider records qualify as skill activation. {importNote(activity?.collection)}</p>
    <section class="usage-table"><h3>Cross-project tool metrics</h3>{activity?.groups.length?<div class="usage-table-scroll"><table>
      <thead><tr><th>backend/model</th><th>project/layer</th><th>raw tool</th><th>dimensions</th><th>calls</th><th>outcomes</th><th>evidence</th><th>avg duration</th><th>approval wait</th><th></th></tr></thead>
      <tbody>{activity.groups.map(item=><tr key={`${item.backend}-${item.model}-${item.project_id}-${item.invocation_layer}-${item.raw_name}`}>
        <td>{item.backend} · <ModelName model={item.model}/></td><td>{item.project_id?item.project_id.slice(0,8):'unassigned'} · {item.invocation_layer}</td>
        <td>{item.raw_name}</td><td>{item.family} · {item.operation} · {item.transport}</td><td>{item.calls}</td><td>{item.statuses.succeeded||0} ok · {item.statuses.failed||0} failed · {item.statuses.denied||0} denied</td>
        <td>{Object.entries(item.qualities).map(([k,v])=>`${v} ${k}`).join(' · ')}</td>
        <td>{item.average_duration_ms==null?`unavailable (0/${item.calls})`:`${Math.round(item.average_duration_ms)}ms (${item.duration_count}/${item.calls})`}</td>
        <td>{item.average_approval_wait_ms==null?'none measured':`${Math.round(item.average_approval_wait_ms/1000)}s (${item.approval_wait_count})`}</td>
        <td><button onClick={()=>onDrill(item)}>Calls</button></td>
      </tr>)}</tbody>
    </table></div>:<p>No explicit tool records yet.</p>}
    <TelemetryCaption days={filters.days} origin={filters.origin} denominator={`${(activity?.matching_calls||0).toLocaleString()} calls, ${Object.entries(activity?.qualities||{}).map(([k,v])=>`${v} ${k}`).join(', ')||'no evidence'}`} coverage={activity?.coverage} filters={activity?.filters}/></section>
    <section class="usage-table"><h3>Canonical calls</h3>{calls?.items.length?<div class="usage-table-scroll"><table><thead><tr><th>time</th><th>session/run</th><th>layer/tool</th><th>status</th><th>evidence</th><th>duration</th><th>output</th><th></th></tr></thead><tbody>{calls.items.map(item=><tr key={item.tool_call_id}><td>{when(item.started_at)}</td><td>{item.session_id.slice(0,8)} / <button onClick={()=>openRun(item.run_id)}>{item.run_id.slice(0,8)}</button>{item.turn_id?<> / <button onClick={()=>openTurn(item.turn_id!)}>turn</button></>:null}</td><td>{item.invocation_layer} · {item.raw_name}</td><td>{item.status}</td><td>{item.evidence_quality}</td><td>{ms(item.duration_ms)}</td><td>{item.output_measurement}</td><td><button onClick={()=>inspect(item.tool_call_id)}>Evidence</button></td></tr>)}</tbody></table></div>:<p>No canonical call details in this range.</p>}
    <TelemetryCaption days={filters.days} origin={filters.origin} denominator={calls?`${calls.items.length} shown of ${(calls.matching_calls??calls.matching).toLocaleString()} exact matches`:'no page'} filters={{backend:filters.backend,project:filters.project,model:filters.model,layer:filters.layer,family:filters.family,status:filters.status,evidence:filters.evidence}}/></section>
    {drillError&&<div class="usage-error" role="alert">{drillError}</div>}
    <DrillDown audit={audit} run={run} turn={turn} onRun={openRun} onTurn={openTurn}/>
    <section class="attribution-log telemetry-exports"><h3>Export this window</h3><p>Every row in the selected range, cohort, and filters, with its evidence identifiers and source locator. Provider output is not in the ledger and so is not in an export.</p>
      <p>{EXPORT_KINDS.map(kind=><span key={kind} class="telemetry-export-kind">{kind.replace(/_/g,' ')} <a href={`/api/telemetry/v2/export/${kind}?${query}&format=jsonl`} download>jsonl</a> <a href={`/api/telemetry/v2/export/${kind}?${query}&format=csv`} download>csv</a></span>)}</p>
    </section>
    {/* Collapsed, and deliberately not a peer of the two tables. Parser coverage does not
        measure the fleet; it says whether the figures above were collectable at all, which
        is the question asked only once the figures already look wrong. Drawn open it read
        as a third metric and pushed the two real ones off the first screen. */}
    <details class="telemetry-collection-health">
      <summary>Collection health · {quality?.totals.with_result||0}/{quality?.totals.calls||0} calls have results · {quality?.totals.with_duration||0} have duration{unrecognised.length?` · ${unrecognised.length} unrecognised provider event name(s)`:''}{drift.length?` · schema drift in ${drift.join(', ')}`:''}</summary>
      <div>
        <p>Missing fields stay missing. Parent unavailable means the provider did not expose a model-to-runtime relationship; it is never inferred from timing. Native means the provider's own telemetry supplied the result; executed input is the argument set the provider reports it ran, hashed, beside the requested target. Runs: {quality?.runs.declared_start||0} with a declared start, {quality?.runs.first_evidence_start||0} dated from their first evidence, of {quality?.runs.runs||0}.</p>
        {quality?.backends.length?<div class="usage-table-scroll"><table><thead><tr><th>backend</th><th>calls</th><th>request</th><th>result</th><th>native</th><th>duration</th><th>input hash</th><th>executed input</th><th>output hash</th><th>approval wait</th><th>truncated</th><th>version</th><th>parent unavailable</th><th>other family</th></tr></thead>
          <tbody>{quality.backends.map(item=><tr key={item.backend}><td>{item.backend}</td><td>{item.calls}</td><td>{item.with_request}</td><td>{item.with_result}</td><td>{item.with_provider_result}</td><td>{item.with_duration}</td><td>{item.with_input_hash}</td><td>{item.with_executed_input_hash}</td><td>{item.with_output_hash}</td><td>{item.with_approval_wait}</td><td>{item.truncated_outputs}</td><td>{item.with_harness_version}</td><td>{item.runtime_parent_unavailable}</td><td>{item.other_family}</td></tr>)}</tbody></table></div>:<p>No canonical collection-quality rows yet.</p>}
        {quality?.versions.length?<div class="usage-table-scroll"><table><thead><tr><th>backend</th><th>harness</th><th>calls</th><th>result</th><th>native</th><th>duration</th><th>executed input</th><th>output hash</th></tr></thead>
          <tbody>{quality.versions.map(item=><tr key={`${item.backend}-${item.harness_version}`}><td>{item.backend}</td><td>{item.harness_version}</td><td>{item.calls}</td><td>{item.with_result}</td><td>{item.with_provider_result}</td><td>{item.with_duration}</td><td>{item.with_executed_input_hash}</td><td>{item.with_output_hash}</td></tr>)}</tbody></table></div>:null}
        {quality?.capabilities?<div class="usage-table-scroll"><table><thead><tr><th>capability</th>{Object.keys(quality.capabilities).map(name=><th key={name}>{name}</th>)}</tr></thead>
          <tbody>{Object.keys(Object.values(quality.capabilities)[0]||{}).map(capability=><tr key={capability}><td>{capability.replace(/_/g,' ')}</td>{Object.entries(quality.capabilities).map(([name,record])=><td key={name} data-capability={record[capability]}>{record[capability].replace(/_/g,' ')}</td>)}</tr>)}</tbody></table></div>:null}
        {quality?.parsers?.length?<div class="usage-table-scroll"><table><thead><tr><th>backend</th><th>harness</th><th>provider event</th><th>understood</th><th>seen</th><th>last</th></tr></thead>
          <tbody>{quality.parsers.map(item=><tr key={`${item.backend}-${item.harness_version}-${item.parser_version}-${item.event_name}`}><td>{item.backend}</td><td>{item.harness_version}</td><td>{item.event_name}</td><td>{item.recognized?'yes':'no'}</td><td>{item.occurrences.toLocaleString()}</td><td>{when(item.last_seen_at)}</td></tr>)}</tbody></table></div>:<p>No native provider telemetry has arrived yet. Enable it in Settings → Usage for new sessions.</p>}
        {quality?.reconciliation?.by_backend.length?<p>Native stores reconciled directly: {quality.reconciliation.by_backend.map(item=>`${item.backend} ${item.status} ${item.runs} (${item.tool_events} tool events, ${item.parser_version})`).join(' · ')}.</p>:null}
      </div>
    </details>
  </div>
}

function SkillsView({activity,verifications,metrics,filters}:{activity:CanonicalActivity|null;verifications:VerificationSummary|null;metrics:MetricSummary|null;filters:Filters}) {
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">Only explicit provider records qualify as skill activation; Skills are never inferred from Markdown or generic file reads. A verification is a parsed test outcome, never a tool that merely contains the word "test". {importNote(activity?.collection)}</p>
    <section class="attribution-log"><h3>Explicit skill invocations</h3>{activity?.skills.groups.length?activity.skills.groups.map(item=><article key={`${item.backend}-${item.project_id}-${item.skill_name}-${item.invocation_trigger}`}>
      <strong>{item.skill_name}</strong><span>{item.backend} · <ModelName model={item.model}/> · {item.invocations} activation(s) · {item.invocation_trigger} · {item.skill_source} / {item.skill_scope}</span>
    </article>):<p>No provider-native skill invocation evidence.</p>}
    <TelemetryCaption days={filters.days} origin={filters.origin} denominator={`${(activity?.skills.matching_invocations||0).toLocaleString()} activations`} coverage={activity?.skills.coverage}/></section>
    <section class="usage-table"><h3>Verification outcomes</h3>{verifications?.groups.length?<div class="usage-table-scroll"><table><thead><tr><th>backend/model</th><th>project</th><th>framework</th><th>runs of the suite</th><th>successful</th><th>passed</th><th>failed</th><th>errors</th><th>skipped</th></tr></thead>
      <tbody>{verifications.groups.map(item=><tr key={`${item.backend}-${item.model}-${item.project_id}-${item.framework}`}><td>{item.backend} · <ModelName model={item.model}/></td><td>{item.project_id?item.project_id.slice(0,8):'unassigned'}</td><td>{item.framework}</td><td>{item.verifications}</td><td>{item.successful} ({percent(item.success_rate)})</td><td>{item.passed}</td><td>{item.failed}</td><td>{item.errors}</td><td>{item.skipped}</td></tr>)}</tbody></table></div>:<p>No parsed verification outcomes in this range.</p>}
    <TelemetryCaption days={filters.days} origin={filters.origin} denominator={`${(verifications?.totals.verifications||0).toLocaleString()} verifications, ${(verifications?.totals.successful||0).toLocaleString()} successful`} coverage={verifications?.coverage}/></section>
    <section class="usage-table"><h3>Provider self-reported counters</h3><p class="telemetry-caveat">Aggregated metrics the CLI exported about itself, kept as aggregates beside the ledger's entities. Where the provider counts its own tool calls per run, the ledger's count for that run is placed beside it: agreement is evidence the ledger is complete, disagreement names the run to look at.</p>
      {metrics?.tool_call_agreement.runs?<p>Tool-call agreement: {metrics.tool_call_agreement.agree}/{metrics.tool_call_agreement.runs} runs agree · {metrics.tool_call_agreement.ledger_more} ledger holds more · {metrics.tool_call_agreement.provider_more} provider reported more.</p>:<p>No provider-reported tool-call counters in this range.</p>}
      {metrics?.tool_call_agreement.examples.length?<div class="usage-table-scroll"><table><thead><tr><th>run</th><th>provider reported</th><th>ledger</th><th>verdict</th></tr></thead><tbody>{metrics.tool_call_agreement.examples.map(item=><tr key={item.run_id}><td>{item.run_id.slice(0,8)}</td><td>{item.provider_reported}</td><td>{item.ledger}</td><td>{item.verdict.replace(/_/g,' ')}</td></tr>)}</tbody></table></div>:null}
      {metrics?.metrics.length?<div class="usage-table-scroll"><table><thead><tr><th>backend</th><th>harness</th><th>metric</th><th>kind</th><th>points</th><th>count</th><th>total</th><th>min</th><th>max</th></tr></thead><tbody>{metrics.metrics.map(item=><tr key={`${item.backend}-${item.harness_version}-${item.metric}`}><td>{item.backend}</td><td>{item.harness_version}</td><td>{item.metric}</td><td>{item.kind}</td><td>{item.points}</td><td>{item.count}</td><td>{Math.round(item.total).toLocaleString()}</td><td>{item.min==null?'·':Math.round(item.min)}</td><td>{item.max==null?'·':Math.round(item.max)}</td></tr>)}</tbody></table></div>:null}
    </section>
  </div>
}

function ContextView({data,filters}:{data:CanonicalCompactions|null;filters:Filters}) {
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">Compaction counts require explicit provider-native evidence. Token drops alone remain unknown and are never counted. {importNote(data?.collection)}</p>
    <section class="attribution-log"><h3>Compaction history</h3>{data?.groups.length?data.groups.map(item=><article key={`${item.backend}-${item.model}-${item.project_id}-${item.trigger}`}>
      <strong>{item.backend} · <ModelName model={item.model}/> · {item.count} compaction(s)</strong>
      <span>{item.project_id?item.project_id.slice(0,8):'unassigned'} · {item.trigger} · {item.failures} failed</span>
      <small>{item.average_duration_ms==null?'duration unavailable':`${Math.round(item.average_duration_ms)}ms average`} · {item.average_tokens_reclaimed==null?'token change unavailable':`${Math.round(item.average_tokens_reclaimed).toLocaleString()} tokens reclaimed average`}</small>
    </article>):<p>No explicit compaction records are available.</p>}
    <TelemetryCaption days={filters.days} origin={filters.origin} denominator={`${(data?.total||0).toLocaleString()} compactions`} coverage={data?.coverage}/></section>
  </div>
}

function InefficiencyView({data,comparison,split,filters,onSplit,onReview}:{data:InefficiencyResult|null;comparison:Comparison|null;split:string;filters:Filters;onSplit:(value:string)=>void;onReview:(finding:Finding,verdict:string)=>void}) {
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">Deterministic review candidates, not causal claims or automatic configuration changes. Each finding retains its measured denominator and coverage. Your verdict on a finding is recorded and is the only feedback the daemon acts on; {data?.adaptive_changes.policy||'no configuration change is generated automatically.'}</p>
    <section class="attribution-log"><h3>Inefficiency candidates</h3>{data?.findings.length?data.findings.map(item=><article key={item.finding_key} data-reviewed={item.review?item.review.verdict:''}><strong>{item.kind.replace(/_/g,' ')} · {item.tool.raw_name}</strong><span>{item.tool.backend} · {item.tool.model}{item.tool.invocation_layer?` · ${item.tool.invocation_layer}`:''} · confidence {item.confidence} · coverage {Math.round(item.coverage*100)}%{item.review?` · reviewed: ${item.review.verdict.replace(/_/g,' ')}`:''}</span><small>{Object.entries(item.evidence).map(([key,value])=>`${key.replace(/_/g,' ')} ${Number(value).toLocaleString()}`).join(' · ')}. {item.suggestion}</small><span class="telemetry-review">{VERDICTS.map(([value,label])=><button key={value} onClick={()=>onReview(item,value)} disabled={item.review?.verdict===value}>{label}</button>)}</span></article>):<p>No deterministic inefficiency candidates in this range.</p>}
    {data&&<TelemetryCaption days={filters.days} origin={filters.origin} denominator={`${data.collection.matching_calls.toLocaleString()} calls evaluated · duration measured for ${data.collection.duration.measured.toLocaleString()} of ${data.collection.duration.completed.toLocaleString()} completed · ${data.collection.approval_wait.measured.toLocaleString()} approval waits measured · ${data.reviewed} reviewed`} coverage={data.coverage}/>}</section>
    <section class="usage-table"><h3>Cohort comparison</h3>
      <p class="telemetry-caveat">Skill activation and verification outcomes across cohorts split on one dimension. Cohorts are comparable only when every other dimension is fixed by a filter; otherwise the table says so and shows what differs. Not a ranking.</p>
      <label>Split by<select value={split} onChange={event=>onSplit(event.currentTarget.value)}><option value="model">model</option><option value="backend">backend</option><option value="project_id">project</option></select></label>
      {comparison?<div>
        <p>{comparison.comparable?'Comparable: every other dimension is shared.':`Not comparable: ${comparison.why_not_comparable||'the cohorts differ on another dimension'}.`}</p>
        <div class="usage-table-scroll"><table><thead><tr><th>cohort</th><th>runs</th><th>completed turns</th><th>tool calls</th><th>tool failure rate</th><th>verifications</th><th>verification success</th><th>skill activations / run</th><th>other dimensions</th></tr></thead>
          <tbody>{comparison.cohorts.map(item=><tr key={item.cohort}><td>{item.cohort}</td><td>{item.runs}</td><td>{item.completed_turns}</td><td>{item.tool_calls}</td><td>{item.tool_failure_rate==null?'n/a':`${percent(item.tool_failure_rate)} of ${item.completed_tool_calls}`}</td><td>{item.verifications}</td><td>{item.verification_success_rate==null?'n/a (0 verifications)':`${percent(item.verification_success_rate)} of ${item.verifications}`}</td><td>{item.skill_activations_per_run==null?'n/a':item.skill_activations_per_run.toFixed(2)}</td><td>{Object.entries(item.other_dimensions).map(([key,values])=>`${key}: ${values.join(', ')||'·'}`).join(' · ')}</td></tr>)}</tbody></table></div>
        <small>{comparison.interpretation.replace(/_/g,' ')}</small>
      </div>:<p>Comparing…</p>}
    </section>
  </div>
}

export function FleetActivityView() {
  const [domain,setDomain]=useState<Domain>('workloads')
  const [filters,setFilters]=useState<Filters>({days:7,origin:'mux_owned',backend:'',project:'',model:'',layer:'',family:'',status:'',evidence:''})
  const [options,setOptions]=useState<{backends:string[];projects:string[];models:string[]}>({backends:[],projects:[],models:[]})
  const [legacyEnabled,setLegacyEnabled]=useState(false)
  const [activity,setActivity]=useState<CanonicalActivity|null>(null)
  const [quality,setQuality]=useState<CanonicalQuality|null>(null)
  const [compactions,setCompactions]=useState<CanonicalCompactions|null>(null)
  const [calls,setCalls]=useState<CanonicalToolPage|null>(null)
  const [drill,setDrill]=useState<ToolGroup|null>(null)
  const [verifications,setVerifications]=useState<VerificationSummary|null>(null)
  const [metrics,setMetrics]=useState<MetricSummary|null>(null)
  const [inefficiencies,setInefficiencies]=useState<InefficiencyResult|null>(null)
  const [comparison,setComparison]=useState<Comparison|null>(null)
  const [split,setSplit]=useState('model')
  const [error,setError]=useState('')
  const set=(patch:Partial<Filters>)=>{setDrill(null);setFilters(current=>({...current,...patch}))}
  const query=telemetryQuery(filters)
  const toolQuery=drill?`${telemetryQuery({...filters,backend:drill.backend,project:drill.project_id,model:drill.model,layer:drill.invocation_layer})}&tool=${encodeURIComponent(drill.raw_name)}`:query
  const fail=(cause:unknown)=>setError(cause instanceof Error?cause.message:String(cause))

  // The pickers offer what the ledger has actually seen in this window and cohort,
  // read once per window rather than from the harness registry: a harness that never
  // ran here is not a filter anyone can use, and an imported one that mux never
  // launched still is.
  useEffect(()=>{
    let stale=false
    const base=telemetryQuery({days:filters.days,origin:filters.origin})
    Promise.all([
      api<CanonicalActivity>('GET',`/api/telemetry/v2/tools/summary?${base}`),
      api<ShadowFlag>('GET',`/api/telemetry/v2/shadow?${base}`).catch(()=>({legacy_dashboard_enabled:false})),
    ]).then(([summary,shadow])=>{
      if(stale)return
      const unique=(values:string[])=>Array.from(new Set(values.filter(Boolean))).sort()
      setOptions({backends:unique(summary.groups.map(g=>g.backend)),projects:unique(summary.groups.map(g=>g.project_id)),models:unique(summary.groups.map(g=>g.model))})
      setLegacyEnabled(Boolean(shadow.legacy_dashboard_enabled))
    }).catch(()=>{})
    return()=>{stale=true}
  },[filters.days,filters.origin])
  useEffect(()=>{
    if(domain!=='tools'&&domain!=='skills')return
    let stale=false
    const reads:Promise<unknown>[]=[api<CanonicalActivity>('GET',`/api/telemetry/v2/tools/summary?${query}`)]
    if(domain==='tools'){
      reads.push(api<CanonicalQuality>('GET',`/api/telemetry/v2/quality?${query}`),api<CanonicalToolPage>('GET',`/api/telemetry/v2/tools?${toolQuery}&limit=100`))
    }else{
      reads.push(api<VerificationSummary>('GET',`/api/telemetry/v2/verifications/summary?${query}`),api<MetricSummary>('GET',`/api/telemetry/v2/metrics/summary?${query}`))
    }
    Promise.all(reads).then(results=>{
      if(stale)return
      setActivity(results[0] as CanonicalActivity)
      if(domain==='tools'){setQuality(results[1] as CanonicalQuality);setCalls(results[2] as CanonicalToolPage)}
      else{setVerifications(results[1] as VerificationSummary);setMetrics(results[2] as MetricSummary)}
    }).catch(cause=>{if(!stale)fail(cause)})
    return()=>{stale=true}
  },[domain,query,toolQuery])
  useEffect(()=>{
    if(domain!=='inefficiencies')return
    let stale=false
    Promise.all([
      api<InefficiencyResult>('GET',`/api/telemetry/v2/inefficiencies?${query}`),
      api<Comparison>('GET',`/api/telemetry/v2/compare?${query}&split=${split}`),
    ]).then(([next,compared])=>{if(!stale){setInefficiencies(next);setComparison(compared)}}).catch(cause=>{if(!stale)fail(cause)})
    return()=>{stale=true}
  },[domain,query,split])
  useEffect(()=>{
    if(domain!=='context')return
    let stale=false
    api<CanonicalCompactions>('GET',`/api/telemetry/v2/compactions?${query}`)
      .then(next=>{if(!stale)setCompactions(next)})
      .catch(cause=>{if(!stale)fail(cause)})
    return()=>{stale=true}
  },[domain,query])

  const review=(finding:Finding,verdict:string)=>{
    api<{verdict:string;reviewed_at:number;note?:string|null}>('POST','/api/telemetry/v2/inefficiencies/review',{finding_key:finding.finding_key,kind:finding.kind,verdict})
      .then(saved=>setInefficiencies(current=>current?{...current,reviewed:current.findings.filter(f=>f.review||f.finding_key===finding.finding_key).length,findings:current.findings.map(f=>f.finding_key===finding.finding_key?{...f,review:{verdict:saved.verdict,note:saved.note,reviewed_at:saved.reviewed_at}}:f)}:current))
      .catch(fail)
  }
  const withOption=(values:string[],current:string)=>current&&!values.includes(current)?[...values,current]:values
  const toolTab=domain==='tools'||domain==='inefficiencies'
  const domains:Array<[Domain,string]>=legacyEnabled?[...DOMAINS,['legacy','legacy + shadow']]:DOMAINS
  return <>
    <div class="usage-domain-tabs" role="tablist" aria-label="Fleet activity">
      {domains.map(([id,label])=><button role="tab" key={id} aria-selected={domain===id} class={domain===id?'active':''} onClick={()=>setDomain(id)}>{label}</button>)}
    </div>
    <div class="usage-controls">
      <label>Range<select value={filters.days} onChange={event=>set({days:Number(event.currentTarget.value)})}><option value={1}>24 hours</option><option value={7}>7 days</option><option value={30}>30 days</option><option value={0}>All retained</option></select></label>
      <label>Cohort<select value={filters.origin} onChange={event=>set({origin:event.currentTarget.value})}><option value="mux_owned">Mux-owned</option><option value="all">Include imported</option><option value="imported">Imported only</option></select></label>
      <label>Backend<select value={filters.backend} onChange={event=>set({backend:event.currentTarget.value})}><option value="">Every backend</option>{withOption(options.backends,filters.backend).map(name=><option key={name} value={name}>{name}</option>)}</select></label>
      <label>Project<select value={filters.project} onChange={event=>set({project:event.currentTarget.value})}><option value="">Every project</option>{withOption(options.projects,filters.project).map(name=><option key={name} value={name}>{name.slice(0,8)}</option>)}</select></label>
      <label>Model<select value={filters.model} onChange={event=>set({model:event.currentTarget.value})}><option value="">Every model</option>{withOption(options.models,filters.model).map(name=><option key={name} value={name}>{name}</option>)}</select></label>
      {toolTab&&<label>Layer<select value={filters.layer} onChange={event=>set({layer:event.currentTarget.value})}>{LAYERS.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>}
      {toolTab&&<label>Family<select value={filters.family} onChange={event=>set({family:event.currentTarget.value})}><option value="">Every family</option>{FAMILIES.map(name=><option key={name} value={name}>{name}</option>)}</select></label>}
      {toolTab&&<label>Outcome<select value={filters.status} onChange={event=>set({status:event.currentTarget.value})}><option value="">Every outcome</option>{STATUSES.map(name=><option key={name} value={name}>{name}</option>)}</select></label>}
      {toolTab&&<label>Evidence<select value={filters.evidence} onChange={event=>set({evidence:event.currentTarget.value})}><option value="">Any evidence</option>{QUALITIES.map(name=><option key={name} value={name}>{name}</option>)}</select></label>}
      {drill&&<button class="telemetry-drill-clear" onClick={()=>setDrill(null)}>Calls narrowed to {drill.raw_name} · clear</button>}
    </div>
    <main>
      {error&&<div class="usage-error" role="alert">{error}</div>}
      {domain==='workloads'?<WorkloadTelemetry days={filters.days} origin={filters.origin} backend={filters.backend} project={filters.project} model={filters.model}/>
        :domain==='tools'?<ToolsView activity={activity} quality={quality} calls={calls} filters={filters} query={query} onDrill={setDrill}/>
        :domain==='skills'?<SkillsView activity={activity} verifications={verifications} metrics={metrics} filters={filters}/>
        :domain==='context'?<ContextView data={compactions} filters={filters}/>
        :domain==='legacy'?<LegacyToolTelemetry from={filters.days>0?Math.floor(Date.now()/1000)-filters.days*86400:0}/>
        :<InefficiencyView data={inefficiencies} comparison={comparison} split={split} filters={filters} onSplit={setSplit} onReview={review}/>}
    </main>
    <footer><span>Durable local evidence · retained, never deleted · descriptive only, never a ranking</span></footer>
  </>
}
