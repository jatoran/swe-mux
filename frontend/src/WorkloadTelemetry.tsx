import { locale as uiLocale } from './localization/index.ts'
import { codeLabel } from './localization/codeLabels.ts'
import { rich, t } from './localization/index.ts'
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
const when=(time:number)=>new Date(time*1000).toLocaleString(uiLocale.value)
const duration=(seconds:number|null|undefined)=>seconds==null?t("WorkloadTelemetry.040f7884d3"):seconds===0?'0s':formatDuration(seconds)
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
    <button onClick={onBack}>{t("WorkloadTelemetry.f6be9f5d54")}</button>
    {error && <div class="usage-error" role="alert">{error}</div>}
    {!audit&&!error ? <p>{t("WorkloadTelemetry.ad8b302769")}</p> : audit&&run && <>
      <h3>{session ? sessionDisplayName(session) : runDisplayName(run) || t("WorkloadTelemetry.dd8a733dab", { backend: run.backend, value2: run.run_id.slice(0,8) })}</h3>
      <p class="analytics-caption">{context.projects?.find(project=>project.id===run.project_id)?.name||run.project_id || t("WorkloadTelemetry.4dffc2c482")} · {run.backend} · <ModelName model={run.final_model||run.initial_model||'unknown'}/></p>
      <div class="analytics-actions">{session&&liveState(session)&&context.onOpenSession && <button onClick={()=>context.onOpenSession!(session.id)}>{t("WorkloadTelemetry.b205bb47f8")}</button>}{run.history_id&&context.onOpenHistory && <button onClick={()=>context.onOpenHistory!(run.history_id!)}>{t("WorkloadTelemetry.002f987da9")}</button>}</div>
      <dl class="analytics-facts">
        <div><dt>{t("WorkloadTelemetry.a3b50c4767")}</dt><dd>{session&&liveState(session) ? session.state : run.ended_at ? t("WorkloadTelemetry.053f885ef4", { value1: run.end_reason?` · ${run.end_reason}`:'' }) : t("WorkloadTelemetry.0fb810bdf0")}</dd></div>
        <div><dt>{t("WorkloadTelemetry.ecbc89cd37")}</dt><dd>{when(run.started_at)}{run.started_at_source==='first_evidence' ? t("WorkloadTelemetry.377a51c96b") : ''}</dd></div>
        <div><dt>{t("WorkloadTelemetry.e7847a402d")}</dt><dd>{duration(run.ended_at?run.ended_at-run.started_at:session&&liveState(session)?serverNow()-run.started_at:null)}</dd></div>
        <div><dt>{t("WorkloadTelemetry.e0cbe2955a")}</dt><dd>{compactNumber(audit.tool_calls.by_layer.model||0)} / {compactNumber(audit.tool_calls.by_layer.runtime||0)}</dd></div>
        <div><dt>{t("WorkloadTelemetry.b014557f5e")}</dt><dd>{compactNumber(audit.tool_calls.by_status.failed||0)} / {compactNumber(audit.tool_calls.by_status.denied||0)}</dd></div>
        <div><dt>{t("WorkloadTelemetry.98619c70dd")}</dt><dd>{audit.model_requests.count ? compactNumber(audit.model_requests.count) : t("WorkloadTelemetry.aab6c5e605")}</dd></div>
        <div><dt>{t("WorkloadTelemetry.44bb8ce667")}</dt><dd>{run.final_context_pct==null ? t("WorkloadTelemetry.040f7884d3") : t("WorkloadTelemetry.6a53a571fa", { value1: Math.round(run.final_context_pct*100) })} / {run.peak_context_pct==null ? t("WorkloadTelemetry.040f7884d3") : t("WorkloadTelemetry.6a53a571fa", { value1: Math.round(run.peak_context_pct*100) })}</dd></div>
      </dl>
      <p class="analytics-caption">{t("WorkloadTelemetry.fd5f0462dc")}</p>
      <details class="analytics-diagnostics"><summary>{rich("WorkloadTelemetry.1249addf20", { value1: checks ? t("WorkloadTelemetry.65de8fe226", { value1: compactNumber(checks.matching) }) : '' })}</summary>{checkError ? <p role="alert">{checkError}</p> : checks ? checks.items.length ? <>{checks.items.map(check=><p key={check.verification_id}>{check.framework} · {check.successful==null ? t("WorkloadTelemetry.164557364f") : check.successful ? t("WorkloadTelemetry.436fe71bb9") : t("WorkloadTelemetry.031a8f0f65")} · {when(check.finished_at)}</p>)}{checks.matching>checks.items.length && <small>{rich("WorkloadTelemetry.8fec4e3c4f", { length: checks.items.length })}</small>}</> : <p>{t("WorkloadTelemetry.a3c8ee6e54")}</p> : <p>{t("WorkloadTelemetry.634e0304fd")}</p>}</details>
      <h3>{rich("WorkloadTelemetry.191045aa66", { value1: compactNumber(audit.turns.length) })}</h3>
      <div class="analytics-run-timeline">{[...audit.turns].reverse().map(item=><div key={item.turn_id}>
        <button class="analytics-event" aria-expanded={turn===item.turn_id} onClick={()=>setTurn(turn===item.turn_id?'':item.turn_id)}><strong>{codeLabel(item.status)}</strong><span>{when(item.started_at)}</span><span>{duration(item.duration_ms==null?null:item.duration_ms/1000)}</span></button>
        {turn===item.turn_id && <div class="analytics-record-body">{turnError ? <p role="alert">{turnError}</p> : calls ? calls.tool_calls.length ? calls.tool_calls.map(call=><div class="analytics-event" key={call.tool_call_id}><strong>{call.raw_name}</strong><span>{call.invocation_layer} · {codeLabel(call.status)}</span><span>{duration(call.duration_ms==null?null:call.duration_ms/1000)}</span></div>) : <p>{t("WorkloadTelemetry.f06f68b846")}</p> : <p>{t("WorkloadTelemetry.e9461c4cee")}</p>}</div>}
      </div>)}</div>
      {!audit.turns.length && <p>{t("WorkloadTelemetry.aae81a1aa9")}</p>}
      <details class="analytics-diagnostics"><summary>{rich("WorkloadTelemetry.04ce66b7cb", { length: audit.evidence.length })}</summary>{audit.evidence.map(item=><p key={item.evidence_id}>{item.observation ? t("WorkloadTelemetry.2731d8fe72", { value1: when(item.observation.observed_at), value2: item.observation.event_type.replace(/_/g,' '), source_kind: item.observation.source_kind }) : t("WorkloadTelemetry.aada78c98c")}</p>)}</details>
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
    <div class="analytics-toolbar analytics-inline-toolbar"><label>{rich("WorkloadTelemetry.c30ae6a164", { value1: <select value={mode} onChange={event=>setMode(event.currentTarget.value as typeof mode)}><option value="recent">{t("WorkloadTelemetry.7fc0ab7470")}</option><option value="live">{t("WorkloadTelemetry.db585beb64")}</option></select> })}</label><span>{mode==='recent' ? page ? t("WorkloadTelemetry.3693369e56", { value1: compactNumber(page.matching) }) : t("WorkloadTelemetry.c582f93f4e") : t("WorkloadTelemetry.4341334a33", { length: live.length })}</span></div>
    {error && <div class="usage-error" role="alert">{error}</div>}
    {mode==='live' ? <><p class="analytics-caption">{t("WorkloadTelemetry.516817bdff")}</p>{live.map(session=><article class="analytics-run-card" key={session.id}><button class="analytics-run-open" onClick={()=>setRunId(session.agent_run_id||session.id)}><strong>{sessionDisplayName(session)}</strong><span>{context.projects?.find(project=>project.id===session.project_id)?.name || t("WorkloadTelemetry.14d33bd014")} · {session.backend} · {codeLabel(session.state)}</span><small>{rich("WorkloadTelemetry.2c73b5c96b", { value1: when(session.last_activity_ts), value2: session.state_detail || t("WorkloadTelemetry.325dd8bad4") })}</small></button>{context.onOpenSession && <button onClick={()=>context.onOpenSession!(session.id)}>{t("WorkloadTelemetry.b205bb47f8")}</button>}</article>)}{!live.length && <p>{t("WorkloadTelemetry.87e72fe983")}</p>}</> : <>
      <p class="analytics-caption">{t("WorkloadTelemetry.ea6030aa52")}</p>
      {page?.items.map(run=>{
        const session=context.sessions?.find(item=>sameRun(item,run))
        return <article class="analytics-run-card" key={run.run_id}><button class="analytics-run-open" onClick={()=>setRunId(run.run_id)}>
          <strong>{session ? sessionDisplayName(session) : runDisplayName(run) || t("WorkloadTelemetry.dd8a733dab", { backend: run.backend, value2: run.run_id.slice(0,8) })}</strong>
          <span>{context.projects?.find(project=>project.id===run.project_id)?.name||run.project_id || t("WorkloadTelemetry.14d33bd014")} · {run.backend} · <ModelName model={run.final_model||run.initial_model||'unknown'}/></span>
          <small>{when(run.started_at)} · {session&&liveState(session) ? session.state : run.ended_at ? t("WorkloadTelemetry.7cdc804e69") : t("WorkloadTelemetry.0fb810bdf0")}{run.ended_at ? t("WorkloadTelemetry.4ba6a66919", { value1: duration(run.ended_at-run.started_at) }) : ''}</small>
          {run.last_observed_at && <small>{rich("WorkloadTelemetry.271decdd95", { value1: when(run.last_observed_at), value2: run.last_event_type?.replace(/_/g,' ') })}</small>}
        </button><div class="analytics-run-meta">{run.measurement_source ? <><span>{t("WorkloadTelemetry.9c94b1dcf2")}</span><MetricValue value={run.input_tokens+run.output_tokens}/></> : <span>{t("WorkloadTelemetry.bede28867e")}</span>}</div></article>
      })}
      {!page&&!error && <p>{t("WorkloadTelemetry.8438ea394d")}</p>}{page&&!page.items.length && <p>{t("WorkloadTelemetry.39bc683490")}</p>}
      {page?.next_cursor && <button disabled={loading} onClick={()=>setCursor(page.next_cursor!)}>{loading ? t("WorkloadTelemetry.ba3bbbe10d") : t("WorkloadTelemetry.627fcc156a")}</button>}
    </>}
  </>
}
