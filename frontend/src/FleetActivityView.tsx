import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { ModelName } from './ModelName'
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
// from rollups and open days from canonical entities, and no total is computed from a
// displayed page. The controls above the tabs (range, cohort, backend, layer) are the same
// query for every tab, so switching tabs never silently changes the question.

type Domain = 'workloads' | 'tools' | 'context' | 'inefficiencies'

type ToolGroup = {
  backend:string;model:string;project_id:string;origin:string;invocation_layer:string
  family:string;operation:string;transport:string;raw_name:string;calls:number
  statuses:Record<string,number>;duration_count:number;average_duration_ms?:number|null
}
type SkillGroup = {
  backend:string;model:string;project_id:string;skill_name:string;invocation_trigger:string
  skill_source:string;skill_scope:string;invocations:number
}
type Collection = {
  backfilled:number;backfill_completed:boolean;backfill_stream:string;provider_dropped:number
  provider_batches?:number;schema?:{version?:number;drift?:string[]}
}
type CanonicalActivity = {
  from:number;to:number;origin:string;matching_calls:number;groups:ToolGroup[]
  skills:{matching_invocations:number;groups:SkillGroup[]}
  collection?:Collection
}
type QualityCounts = {
  calls:number;with_request:number;with_result:number;with_provider_result:number;with_duration:number
  with_input_hash:number;with_executed_input_hash:number;with_output_hash:number;with_output_size:number
  with_harness_version:number;truncated_outputs:number;runtime_parent_unavailable:number;other_family:number
}
type QualityRow = QualityCounts&{backend:string}
type ParserSignature = {
  backend:string;harness_version:string;parser_version:string;event_name:string
  recognized:number;occurrences:number;first_seen_at:number;last_seen_at:number
}
type CanonicalQuality = {
  totals:QualityCounts;backends:QualityRow[];parsers?:ParserSignature[]
  collection?:Collection
}
type CanonicalToolCall = {
  tool_call_id:string;run_id:string;turn_id?:string;session_id:string;backend:string;model?:string
  invocation_layer:string;raw_name:string;family:string;operation:string;transport:string
  started_at:number;finished_at?:number;status:string;duration_ms?:number|null
  target_preview?:string;output_measurement:string;request_source?:string;result_source?:string
}
type CanonicalToolPage = {matching_calls:number;items:CanonicalToolCall[];next_cursor?:string|null}
type CanonicalAudit = {
  call:CanonicalToolCall
  evidence:Array<{evidence_id:string;contribution:string;precedence_rank:number;conflict:number;observation?:{observed_at:number;event_type:string;source_kind:string;source_version?:string;payload_sha256:string;payload_bytes:number;source_locator?:string;privacy_class:string}|null}>
}
type CanonicalCompactions = {
  total:number
  groups:Array<{backend:string;model:string;project_id:string;trigger:string;count:number;failures:number;duration_count:number;average_duration_ms?:number|null;token_count:number;average_tokens_reclaimed?:number|null}>
  collection?:Collection
}
type InefficiencyResult = {
  interpretation:string
  findings:Array<{kind:string;tool:{backend:string;model:string;project_id:string;invocation_layer:string;raw_name:string;family:string;operation:string;transport:string};evidence:Record<string,number>;coverage:number;confidence:string;suggestion:string}>
  collection:{matching_calls:number;duration:{measured:number;completed:number;average_ms?:number|null}}
  collection_health?:Collection
}

const DOMAINS: Array<[Domain, string]> = [
  ['workloads', 'runs + workload'],
  ['tools', 'tools + skills'],
  ['context', 'context + compaction'],
  ['inefficiencies', 'inefficiencies'],
]
const LAYERS: Array<[string, string]> = [
  ['', 'Every layer'],
  ['model', 'Model-selected'],
  ['runtime', 'Nested runtime'],
]
/** Row kinds the daemon can export; each carries its evidence ids and source locator. */
const EXPORT_KINDS = ['tool_calls', 'runs', 'turns', 'model_requests', 'skills', 'verifications', 'compactions', 'evidence'] as const

function importNote(collection:Collection|undefined):string {
  if(!collection||collection.backfill_completed)return ''
  return `Historical import: ${collection.backfill_stream} · ${collection.backfilled.toLocaleString()} observations preserved.`
}

function ToolsView({ activity, quality, calls, query }: { activity:CanonicalActivity|null;quality:CanonicalQuality|null;calls:CanonicalToolPage|null;query:string }) {
  const [audit,setAudit]=useState<CanonicalAudit|null>(null)
  const [auditError,setAuditError]=useState('')
  const inspect=(id:string)=>{
    setAuditError('')
    api<CanonicalAudit>('GET',`/api/telemetry/v2/tools/${id}`)
      .then(setAudit)
      .catch(cause=>setAuditError(cause instanceof Error?cause.message:String(cause)))
  }
  const unrecognised=(quality?.parsers||[]).filter(item=>!item.recognized)
  const drift=quality?.collection?.schema?.drift||[]
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">Model-selected calls and nested runtime executions are counted separately. Only explicit provider records qualify as skill activation. {importNote(activity?.collection)}</p>
    <section class="usage-table"><h3>Cross-project tool metrics</h3>{activity?.groups.length?<div class="usage-table-scroll"><table>
      <thead><tr><th>backend/model</th><th>project/layer</th><th>raw tool</th><th>dimensions</th><th>calls</th><th>outcomes</th><th>avg duration</th></tr></thead>
      <tbody>{activity.groups.map(item=><tr key={`${item.backend}-${item.project_id}-${item.invocation_layer}-${item.raw_name}`}>
        <td>{item.backend} · <ModelName model={item.model}/></td><td>{item.project_id?item.project_id.slice(0,8):'unassigned'} · {item.invocation_layer}</td>
        <td>{item.raw_name}</td><td>{item.family} · {item.operation} · {item.transport}</td><td>{item.calls}</td><td>{item.statuses.succeeded||0} ok · {item.statuses.failed||0} failed · {item.statuses.denied||0} denied</td>
        <td>{item.average_duration_ms==null?`unavailable (0/${item.calls})`:`${Math.round(item.average_duration_ms)}ms (${item.duration_count}/${item.calls})`}</td>
      </tr>)}</tbody>
    </table></div>:<p>No explicit tool records yet.</p>}<small>{activity?`${activity.matching_calls.toLocaleString()} calls in range`:''}</small></section>
    <section class="attribution-log"><h3>Explicit skill invocations</h3>{activity?.skills.groups.length?activity.skills.groups.map(item=><article key={`${item.backend}-${item.project_id}-${item.skill_name}-${item.invocation_trigger}`}>
      <strong>{item.skill_name}</strong><span>{item.backend} · {item.invocations} activation(s) · {item.invocation_trigger} · {item.skill_source} / {item.skill_scope}</span>
    </article>):<p>No provider-native skill invocation evidence. Skills are never inferred from Markdown or generic file reads.</p>}</section>
    <section class="usage-table"><h3>Recent canonical calls</h3>{calls?.items.length?<div class="usage-table-scroll"><table><thead><tr><th>time</th><th>session/run</th><th>layer/tool</th><th>status</th><th>duration</th><th>output</th><th>provenance</th></tr></thead><tbody>{calls.items.map(item=><tr key={item.tool_call_id}><td>{new Date(item.started_at*1000).toLocaleString()}</td><td>{item.session_id.slice(0,8)} / {item.run_id.slice(0,8)}</td><td>{item.invocation_layer} · {item.raw_name}</td><td>{item.status}</td><td>{item.duration_ms==null?'unavailable':`${Math.round(item.duration_ms)}ms`}</td><td>{item.output_measurement}</td><td><button onClick={()=>inspect(item.tool_call_id)}>Evidence</button></td></tr>)}</tbody></table></div>:<p>No canonical call details in this range.</p>}<small>{calls?`${calls.items.length} shown of ${calls.matching_calls.toLocaleString()} exact matches`:''}</small></section>
    {auditError&&<div class="usage-error" role="alert">{auditError}</div>}
    {audit&&<section class="attribution-log"><h3>Call evidence</h3><p><strong>{audit.call.raw_name}</strong> · {audit.call.status} · {audit.call.tool_call_id}</p>{audit.evidence.map(item=><article key={`${item.evidence_id}-${item.contribution}`}><strong>{item.observation?.source_kind||'unavailable source'} · {item.contribution}{item.conflict?' · conflict':''}</strong><span>{item.observation?new Date(item.observation.observed_at*1000).toLocaleString():'observation unavailable'} · precedence {item.precedence_rank}</span><small>{item.observation?`${item.observation.event_type} · ${item.observation.payload_bytes} metadata bytes · SHA-256 ${item.observation.payload_sha256}`:'The source observation is no longer available.'}</small></article>)}</section>}
    <section class="attribution-log telemetry-exports"><h3>Export this window</h3><p>Every row in the selected range and cohort, with its evidence identifiers and source locator. Provider output is not in the ledger and so is not in an export.</p>
      <p>{EXPORT_KINDS.map(kind=><span key={kind} class="telemetry-export-kind">{kind.replaceAll('_',' ')} <a href={`/api/telemetry/v2/export/${kind}?${query}&format=jsonl`} download>jsonl</a> <a href={`/api/telemetry/v2/export/${kind}?${query}&format=csv`} download>csv</a></span>)}</p>
    </section>
    {/* Collapsed, and deliberately not a peer of the two tables. Parser coverage does not
        measure the fleet; it says whether the figures above were collectable at all, which
        is the question asked only once the figures already look wrong. Drawn open it read
        as a third metric and pushed the two real ones off the first screen. */}
    <details class="telemetry-collection-health">
      <summary>Collection health · {quality?.totals.with_result||0}/{quality?.totals.calls||0} calls have results · {quality?.totals.with_duration||0} have duration{unrecognised.length?` · ${unrecognised.length} unrecognised provider event name(s)`:''}{drift.length?` · schema drift in ${drift.join(', ')}`:''}</summary>
      <div>
        <p>Missing fields stay missing. Parent unavailable means the provider did not expose a model-to-runtime relationship; it is never inferred from timing. Native means the provider's own telemetry supplied the result; executed input is the argument set the provider reports it ran, hashed, beside the requested target.</p>
        {quality?.backends.length?<div class="usage-table-scroll"><table><thead><tr><th>backend</th><th>calls</th><th>request</th><th>result</th><th>native</th><th>duration</th><th>input hash</th><th>executed input</th><th>output hash</th><th>truncated</th><th>version</th><th>parent unavailable</th><th>other family</th></tr></thead>
          <tbody>{quality.backends.map(item=><tr key={item.backend}><td>{item.backend}</td><td>{item.calls}</td><td>{item.with_request}</td><td>{item.with_result}</td><td>{item.with_provider_result}</td><td>{item.with_duration}</td><td>{item.with_input_hash}</td><td>{item.with_executed_input_hash}</td><td>{item.with_output_hash}</td><td>{item.truncated_outputs}</td><td>{item.with_harness_version}</td><td>{item.runtime_parent_unavailable}</td><td>{item.other_family}</td></tr>)}</tbody></table></div>:<p>No canonical collection-quality rows yet.</p>}
        {quality?.parsers?.length?<div class="usage-table-scroll"><table><thead><tr><th>backend</th><th>harness</th><th>provider event</th><th>understood</th><th>seen</th><th>last</th></tr></thead>
          <tbody>{quality.parsers.map(item=><tr key={`${item.backend}-${item.harness_version}-${item.parser_version}-${item.event_name}`}><td>{item.backend}</td><td>{item.harness_version}</td><td>{item.event_name}</td><td>{item.recognized?'yes':'no'}</td><td>{item.occurrences.toLocaleString()}</td><td>{new Date(item.last_seen_at*1000).toLocaleString()}</td></tr>)}</tbody></table></div>:<p>No native provider telemetry has arrived yet. Enable it in Settings → Usage for new sessions.</p>}
      </div>
    </details>
  </div>
}

function ContextView({ data }: { data: CanonicalCompactions | null }) {
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">Compaction counts require explicit provider-native evidence. Token drops alone remain unknown and are never counted. {importNote(data?.collection)}</p>
    <section class="attribution-log"><h3>Compaction history</h3>{data?.groups.length?data.groups.map(item=><article key={`${item.backend}-${item.model}-${item.project_id}-${item.trigger}`}>
      <strong>{item.backend} · <ModelName model={item.model}/> · {item.count} compaction(s)</strong>
      <span>{item.project_id?item.project_id.slice(0,8):'unassigned'} · {item.trigger} · {item.failures} failed</span>
      <small>{item.average_duration_ms==null?'duration unavailable':`${Math.round(item.average_duration_ms)}ms average`} · {item.average_tokens_reclaimed==null?'token change unavailable':`${Math.round(item.average_tokens_reclaimed).toLocaleString()} tokens reclaimed average`}</small>
    </article>):<p>No explicit compaction records are available.</p>}</section>
  </div>
}

function InefficiencyView({data}:{data:InefficiencyResult|null}) {
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">Deterministic review candidates, not causal claims or automatic configuration changes. Each finding retains its measured denominator and coverage.</p>
    <section class="attribution-log"><h3>Inefficiency candidates</h3>{data?.findings.length?data.findings.map((item,index)=><article key={`${item.kind}-${item.tool.backend}-${item.tool.raw_name}-${index}`}><strong>{item.kind.replaceAll('_',' ')} · {item.tool.raw_name}</strong><span>{item.tool.backend} · {item.tool.model} · {item.tool.invocation_layer} · confidence {item.confidence} · coverage {Math.round(item.coverage*100)}%</span><small>{Object.entries(item.evidence).map(([key,value])=>`${key} ${Number(value).toLocaleString()}`).join(' · ')}. {item.suggestion}</small></article>):<p>No deterministic inefficiency candidates in this range.</p>}</section>
    {data&&<p class="telemetry-caveat">Evaluated {data.collection.matching_calls.toLocaleString()} calls. Duration measured for {data.collection.duration.measured.toLocaleString()} of {data.collection.duration.completed.toLocaleString()} completed calls.</p>}
  </div>
}

export function FleetActivityView() {
  const [domain,setDomain]=useState<Domain>('workloads')
  const [days,setDays]=useState(7)
  const [origin,setOrigin]=useState('mux_owned')
  const [backend,setBackend]=useState('')
  const [layer,setLayer]=useState('')
  const [backends,setBackends]=useState<string[]>([])
  const [activity,setActivity]=useState<CanonicalActivity|null>(null)
  const [quality,setQuality]=useState<CanonicalQuality|null>(null)
  const [compactions,setCompactions]=useState<CanonicalCompactions|null>(null)
  const [calls,setCalls]=useState<CanonicalToolPage|null>(null)
  const [inefficiencies,setInefficiencies]=useState<InefficiencyResult|null>(null)
  const [error,setError]=useState('')
  const query=telemetryQuery({days,origin,backend,layer})

  // The backend picker offers what the ledger has actually seen in this window and
  // cohort, read once per window rather than from the harness registry: a harness that
  // never ran here is not a filter anyone can use, and an imported one that mux never
  // launched still is.
  useEffect(()=>{
    let stale=false
    api<CanonicalQuality>('GET',`/api/telemetry/v2/quality?${telemetryQuery({days,origin})}`)
      .then(next=>{if(!stale)setBackends(next.backends.map(item=>item.backend))})
      .catch(()=>{})
    return()=>{stale=true}
  },[days,origin])
  useEffect(()=>{
    if(domain!=='tools')return
    let stale=false
    Promise.all([
      api<CanonicalActivity>('GET',`/api/telemetry/v2/tools/summary?${query}`),
      api<CanonicalQuality>('GET',`/api/telemetry/v2/quality?${query}`),
      api<CanonicalToolPage>('GET',`/api/telemetry/v2/tools?${query}&limit=100`),
    ])
      .then(([nextActivity,nextQuality,nextCalls])=>{if(!stale){setActivity(nextActivity);setQuality(nextQuality);setCalls(nextCalls)}})
      .catch(cause=>{if(!stale)setError(cause instanceof Error?cause.message:String(cause))})
    return()=>{stale=true}
  },[domain,query])
  useEffect(()=>{
    if(domain!=='inefficiencies')return
    let stale=false
    api<InefficiencyResult>('GET',`/api/telemetry/v2/inefficiencies?${query}`)
      .then(next=>{if(!stale)setInefficiencies(next)})
      .catch(cause=>{if(!stale)setError(cause instanceof Error?cause.message:String(cause))})
    return()=>{stale=true}
  },[domain,query])
  useEffect(()=>{
    if(domain!=='context')return
    let stale=false
    api<CanonicalCompactions>('GET',`/api/telemetry/v2/compactions?${query}`)
      .then(next=>{if(!stale)setCompactions(next)})
      .catch(cause=>{if(!stale)setError(cause instanceof Error?cause.message:String(cause))})
    return()=>{stale=true}
  },[domain,query])

  const backendOptions=backend&&!backends.includes(backend)?[...backends,backend]:backends
  return <>
    <div class="usage-domain-tabs" role="tablist" aria-label="Fleet activity">
      {DOMAINS.map(([id,label])=><button role="tab" key={id} aria-selected={domain===id} class={domain===id?'active':''} onClick={()=>setDomain(id)}>{label}</button>)}
    </div>
    <div class="usage-controls">
      <label>Range<select value={days} onChange={event=>setDays(Number(event.currentTarget.value))}><option value={1}>24 hours</option><option value={7}>7 days</option><option value={30}>30 days</option><option value={0}>All retained</option></select></label>
      <label>Cohort<select value={origin} onChange={event=>setOrigin(event.currentTarget.value)}><option value="mux_owned">Mux-owned</option><option value="all">Include imported</option></select></label>
      <label>Backend<select value={backend} onChange={event=>setBackend(event.currentTarget.value)}><option value="">Every backend</option>{backendOptions.map(name=><option key={name} value={name}>{name}</option>)}</select></label>
      {(domain==='tools'||domain==='inefficiencies')&&<label>Layer<select value={layer} onChange={event=>setLayer(event.currentTarget.value)}>{LAYERS.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>}
    </div>
    <main>
      {error&&<div class="usage-error" role="alert">{error}</div>}
      {domain==='workloads'?<WorkloadTelemetry days={days} origin={origin} backend={backend}/>
        :domain==='tools'?<ToolsView activity={activity} quality={quality} calls={calls} query={query}/>
        :domain==='context'?<ContextView data={compactions}/>
        :<InefficiencyView data={inefficiencies}/>}
    </main>
    <footer><span>Durable local evidence · retained, never deleted · descriptive only, never a ranking</span></footer>
  </>
}
