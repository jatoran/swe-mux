import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { compactNumber } from './analyticsPresentation'
import { MetricValue } from './AnalyticsPrimitives'
import { formatDuration } from './automationCost'
import { ModelName } from './ModelName'
import { serverNow } from './serverClock'
import { runDisplayName, sessionDisplayName } from './sessionNames'
import type { Project, Session } from './types'

export type ActivityFilters = {days:number;origin:string;backend:string;project:string;model:string;layer:string;family:string;status:string;evidence:string}
export type ActivityContext = {sessions?:Session[];projects?:Project[];onOpenSession?:(id:string)=>void;onOpenHistory?:(id:string)=>void}
export function telemetryQuery({days,origin,backend='',project='',model='',layer='',family='',status='',evidence=''}:{days:number;origin:string;backend?:string;project?:string;model?:string;layer?:string;family?:string;status?:string;evidence?:string}):string {
  const to=Math.floor(serverNow())
  const query=new URLSearchParams({from:String(days>0?to-days*86400:0),to:String(to),origin})
  for(const [key,value] of Object.entries({backend,project,model,layer,family,status,evidence}))if(value)query.set(key,value)
  return query.toString()
}
export type ActivityRun = {run_id:string;session_id:string;project_id?:string;backend:string;initial_model?:string;final_model?:string;started_at:number;ended_at?:number|null;end_reason?:string;origin:string;name?:string;auto_named?:number;generated_title?:string;history_id?:string;measurement_source?:string;input_tokens:number;output_tokens:number;final_context_pct?:number|null;peak_context_pct?:number|null;started_at_source?:string;parent_run_id?:string;last_observed_at?:number|null;last_event_type?:string|null}
type RunPage={items:ActivityRun[];matching:number;next_cursor?:string|null}
type Turn={turn_id:string;status:string;started_at:number;duration_ms?:number|null}
type Evidence={evidence_id:string;observation?:{observed_at:number;event_type:string;source_kind:string;source_locator?:string}|null}
type RunAudit={run:ActivityRun;turns:Turn[];tool_calls:{total:number;by_status:Record<string,number>;by_layer:Record<string,number>};model_requests:{count:number;failures:number};evidence:Evidence[]}
type TurnAudit={tool_calls:Array<{tool_call_id:string;raw_name:string;status:string;started_at:number;duration_ms?:number|null;invocation_layer:string}>}
const when=(time:number)=>new Date(time*1000).toLocaleString()
const duration=(seconds:number|null|undefined)=>seconds==null?'Not measured':seconds===0?'0s':formatDuration(seconds)
const sameRun=(session:Session,run:ActivityRun)=>(session.agent_run_id||session.id)===run.run_id
const liveState=(session:Session)=>!['exited','crashed'].includes(session.state)

export function RunInspector({id,query,context,onBack}:{id:string;query:string;context:ActivityContext;onBack:()=>void}) {
  const [audit,setAudit]=useState<RunAudit|null>(null)
  const [turn,setTurn]=useState('')
  const [calls,setCalls]=useState<TurnAudit|null>(null)
  const [error,setError]=useState('')
  const [turnError,setTurnError]=useState('')
  const [checks,setChecks]=useState<{matching:number;items:Array<{verification_id:string;framework:string;successful?:number|null;finished_at:number}>}|null>(null)
  const [checkError,setCheckError]=useState('')
  useEffect(()=>{
    let cancelled=false;setAudit(null);setError('');setTurn('');setCalls(null)
    api<RunAudit>('GET',`/api/telemetry/v2/runs/${encodeURIComponent(id)}`).then(value=>{if(!cancelled)setAudit(value)}).catch(cause=>{if(!cancelled)setError(String(cause))})
    return()=>{cancelled=true}
  },[id,query])
  useEffect(()=>{
    let cancelled=false;setCalls(null);setTurnError('')
    if(turn)api<TurnAudit>('GET',`/api/telemetry/v2/turns/${encodeURIComponent(turn)}`).then(value=>{if(!cancelled)setCalls(value)}).catch(cause=>{if(!cancelled)setTurnError(String(cause))})
    return()=>{cancelled=true}
  },[turn])
  useEffect(()=>{let cancelled=false;setChecks(null);setCheckError('');api<NonNullable<typeof checks>>('GET',`/api/telemetry/v2/verifications?from=0&to=${Math.ceil(serverNow())}&origin=all&run_id=${encodeURIComponent(id)}&limit=30`).then(value=>{if(!cancelled)setChecks(value)}).catch(cause=>{if(!cancelled)setCheckError(String(cause))});return()=>{cancelled=true}},[id,query])
  const run=audit?.run,session=run?context.sessions?.find(item=>sameRun(item,run)):undefined
  return <section class="analytics-run-detail">
    <button onClick={onBack}>← Runs</button>
    {error&&<div class="usage-error" role="alert">{error}</div>}
    {!audit&&!error?<p>Loading run…</p>:audit&&run&&<>
      <h3>{session?sessionDisplayName(session):runDisplayName(run)||`${run.backend} run ${run.run_id.slice(0,8)}`}</h3>
      <p class="analytics-caption">{context.projects?.find(project=>project.id===run.project_id)?.name||run.project_id||'Unassigned project'} · {run.backend} · <ModelName model={run.final_model||run.initial_model||'unknown'}/></p>
      <div class="analytics-actions">{session&&liveState(session)&&context.onOpenSession&&<button onClick={()=>context.onOpenSession!(session.id)}>Open session</button>}{run.history_id&&context.onOpenHistory&&<button onClick={()=>context.onOpenHistory!(run.history_id!)}>Open transcript</button>}</div>
      <dl class="analytics-facts">
        <div><dt>State</dt><dd>{session&&liveState(session)?session.state:run.ended_at?`Ended${run.end_reason?` · ${run.end_reason}`:''}`:'No end recorded'}</dd></div>
        <div><dt>Started</dt><dd>{when(run.started_at)}{run.started_at_source==='first_evidence'?' · earliest evidence':''}</dd></div>
        <div><dt>Elapsed wall time</dt><dd>{duration(run.ended_at?run.ended_at-run.started_at:session&&liveState(session)?serverNow()-run.started_at:null)}</dd></div>
        <div><dt>Model / runtime tools</dt><dd>{compactNumber(audit.tool_calls.by_layer.model||0)} / {compactNumber(audit.tool_calls.by_layer.runtime||0)}</dd></div>
        <div><dt>Failed / denied tools</dt><dd>{compactNumber(audit.tool_calls.by_status.failed||0)} / {compactNumber(audit.tool_calls.by_status.denied||0)}</dd></div>
        <div><dt>Model requests recorded</dt><dd>{audit.model_requests.count?compactNumber(audit.model_requests.count):'None recorded'}</dd></div>
        <div><dt>Final / peak context</dt><dd>{run.final_context_pct==null?'Not measured':`${Math.round(run.final_context_pct*100)}%`} / {run.peak_context_pct==null?'Not measured':`${Math.round(run.peak_context_pct*100)}%`}</dd></div>
      </dl>
      <p class="analytics-caption">Wall time includes idle time. Recorded outcomes describe execution, not task completion.</p>
      <details class="analytics-diagnostics"><summary>Recorded checks{checks?` (${compactNumber(checks.matching)})`:''}</summary>{checkError?<p role="alert">{checkError}</p>:checks?checks.items.length?<>{checks.items.map(check=><p key={check.verification_id}>{check.framework} · {check.successful==null?'Outcome unknown':check.successful?'Passed':'Failed'} · {when(check.finished_at)}</p>)}{checks.matching>checks.items.length&&<small>Showing the latest {checks.items.length} checks.</small>}</>:<p>No checks were recorded for this run.</p>:<p>Loading checks…</p>}</details>
      <h3>Turns ({compactNumber(audit.turns.length)})</h3>
      <div class="analytics-run-timeline">{[...audit.turns].reverse().map(item=><div key={item.turn_id}>
        <button class="analytics-event" aria-expanded={turn===item.turn_id} onClick={()=>setTurn(turn===item.turn_id?'':item.turn_id)}><strong>{item.status}</strong><span>{when(item.started_at)}</span><span>{duration(item.duration_ms==null?null:item.duration_ms/1000)}</span></button>
        {turn===item.turn_id&&<div class="analytics-record-body">{turnError?<p role="alert">{turnError}</p>:calls?calls.tool_calls.length?calls.tool_calls.map(call=><div class="analytics-event" key={call.tool_call_id}><strong>{call.raw_name}</strong><span>{call.invocation_layer} · {call.status}</span><span>{duration(call.duration_ms==null?null:call.duration_ms/1000)}</span></div>):<p>No tool calls linked to this turn.</p>:<p>Loading calls…</p>}</div>}
      </div>)}</div>
      {!audit.turns.length&&<p>No turn records were collected for this run.</p>}
      <details class="analytics-diagnostics"><summary>Lifecycle evidence ({audit.evidence.length})</summary>{audit.evidence.map(item=><p key={item.evidence_id}>{item.observation?`${when(item.observation.observed_at)} · ${item.observation.event_type.replace(/_/g,' ')} · ${item.observation.source_kind}`:'Source evidence unavailable'}</p>)}</details>
    </>}
  </section>
}

export function WorkloadTelemetry({query,filters,initialRunId='',onClearRun, ...context}:ActivityContext&{query:string;filters:ActivityFilters;initialRunId?:string;onClearRun?:()=>void}) {
  const [page,setPage]=useState<RunPage|null>(null)
  const [cursor,setCursor]=useState('')
  const [error,setError]=useState('')
  const [loading,setLoading]=useState(false)
  const [runId,setRunId]=useState(initialRunId)
  const [mode,setMode]=useState<'recent'|'live'>('recent')
  useEffect(()=>{setRunId(initialRunId)},[initialRunId])
  useEffect(()=>{
    let cancelled=false;setError('');setLoading(true)
    api<RunPage>('GET',`/api/telemetry/v2/runs?${query}&limit=50${cursor?`&cursor=${encodeURIComponent(cursor)}`:''}`)
      .then(value=>{if(!cancelled)setPage(previous=>cursor&&previous?{...value,items:[...previous.items,...value.items]}:value)})
      .catch(cause=>{if(!cancelled)setError(String(cause))}).finally(()=>{if(!cancelled)setLoading(false)})
    return()=>{cancelled=true}
  },[query,cursor])
  if(runId)return <RunInspector id={runId} query={query} context={context} onBack={()=>{setRunId('');onClearRun?.()}}/>
  const live=(context.sessions||[]).filter(session=>liveState(session)&&session.backend!=='shell'&&(!filters.backend||session.backend===filters.backend)&&(!filters.project||session.project_id===filters.project)&&(!filters.model||session.model===filters.model)&&filters.origin!=='imported')
  return <>
    <div class="analytics-toolbar analytics-inline-toolbar"><label>Show<select value={mode} onChange={event=>setMode(event.currentTarget.value as typeof mode)}><option value="recent">Runs started in this period</option><option value="live">Live agents now</option></select></label><span>{mode==='recent'?page?`${compactNumber(page.matching)} matching starts`:'Count unavailable':`${live.length} live agents`}</span></div>
    {error&&<div class="usage-error" role="alert">{error}</div>}
    {mode==='live'?<><p class="analytics-caption">Current sessions, including runs started before the selected period.</p>{live.map(session=><article class="analytics-run-card" key={session.id}><button class="analytics-run-open" onClick={()=>setRunId(session.agent_run_id||session.id)}><strong>{sessionDisplayName(session)}</strong><span>{context.projects?.find(project=>project.id===session.project_id)?.name||'Unassigned'} · {session.backend} · {session.state}</span><small>Last activity {when(session.last_activity_ts)} · {session.state_detail||'Open run details'}</small></button>{context.onOpenSession&&<button onClick={()=>context.onOpenSession!(session.id)}>Open session</button>}</article>)}{!live.length&&<p>No live agents match these filters.</p>}</>:<>
      <p class="analytics-caption">Each row is one run started in the selected period. Open a run for its full recorded lifetime.</p>
      {page?.items.map(run=>{
        const session=context.sessions?.find(item=>sameRun(item,run))
        return <article class="analytics-run-card" key={run.run_id}><button class="analytics-run-open" onClick={()=>setRunId(run.run_id)}>
          <strong>{session?sessionDisplayName(session):runDisplayName(run)||`${run.backend} run ${run.run_id.slice(0,8)}`}</strong>
          <span>{context.projects?.find(project=>project.id===run.project_id)?.name||run.project_id||'Unassigned'} · {run.backend} · <ModelName model={run.final_model||run.initial_model||'unknown'}/></span>
          <small>{when(run.started_at)} · {session&&liveState(session)?session.state:run.ended_at?'Ended':'No end recorded'}{run.ended_at?` · ${duration(run.ended_at-run.started_at)} elapsed`:''}</small>
          {run.last_observed_at&&<small>Last evidence {when(run.last_observed_at)} · {run.last_event_type?.replace(/_/g,' ')}</small>}
        </button><div class="analytics-run-meta">{run.measurement_source?<><span>Recorded input + output</span><MetricValue value={run.input_tokens+run.output_tokens}/></>:<span>Tokens not measured</span>}</div></article>
      })}
      {!page&&!error&&<p>Loading runs…</p>}{page&&!page.items.length&&<p>No runs started in this period. Try Live agents now or a wider range.</p>}
      {page?.next_cursor&&<button disabled={loading} onClick={()=>setCursor(page.next_cursor!)}>{loading?'Loading…':'Load more runs'}</button>}
    </>}
  </>
}
