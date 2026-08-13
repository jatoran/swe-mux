import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import type { Session } from './types'

type TimelineRecord = {
  id:string;agent_run_id:string;t0:number;t1:number;lifecycle_state:string
  behavior:string[];work_phase:string;target:string[];intent:string;claim:string
  user_ask:string;blocked_on:string;summary:string;approach_status:string
  dead_end:string;novelty:number;confidence:number;trigger:string;observer_model:string
}
type Boundary = {id:string;previous_run_id:string;next_run_id:string;reason:string;created_at:number}
type Metrics = {record_reads:number;rehydrations:number;rehydration_rate:number}
type TimelineState = {
  session_id:string;agent_run_id:string|null;global_enabled:boolean;project_enabled:boolean
  run_enabled:boolean;model:string;daily_budget_usd:number
  spend_today:{tokens:number;cost_usd:number};run_token_budget:number
  run_spend:{tokens:number;cost_usd:number};metrics:Metrics
  records:TimelineRecord[];boundaries:Boundary[]
}

const clock = (value:number) => new Date(value*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})
const percent = (value:number) => `${Math.round(value*100)}%`

export function ScanTimelineTab({session}:{session:Session|null}) {
  const [state,setState]=useState<TimelineState|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [expanded,setExpanded]=useState<Record<string,unknown[]|null>>({})
  const sid=session?.id||''
  const run=session?.agent_run_id||''

  const load=async()=>{
    if(!sid){setState(null);return}
    try{setState(await api<TimelineState>('GET',`/api/sessions/${sid}/scan-timeline`));setError('')}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  useEffect(()=>{void load()},[sid,run])
  useEffect(()=>{
    const refresh=()=>void load()
    window.addEventListener('mux:turn-ended',refresh)
    window.addEventListener('mux:transcript-changed',refresh)
    return()=>{window.removeEventListener('mux:turn-ended',refresh);window.removeEventListener('mux:transcript-changed',refresh)}
  },[sid,run])

  const toggle=async(enabled:boolean)=>{
    if(!sid)return
    setBusy(true)
    try{setState(await api<TimelineState>('PUT',`/api/sessions/${sid}/scan-timeline`,{enabled}));setError('')}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const scan=async()=>{
    if(!sid)return
    setBusy(true)
    try{await api('POST',`/api/sessions/${sid}/scan-timeline/scan`,{});await load()}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const source=async(record:TimelineRecord)=>{
    if(expanded[record.id]!==undefined){setExpanded(current=>{const next={...current};delete next[record.id];return next});return}
    setBusy(true)
    try{
      const detail=await api<{source:unknown[];metrics:Metrics}>('GET',`/api/sessions/${sid}/scan-timeline/${record.id}?rehydrate=1`)
      setExpanded(current=>({...current,[record.id]:detail.source||[]}))
      setState(current=>current?{...current,metrics:detail.metrics}:current)
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }

  if(!session)return <p class="drawer-empty">Focus an agent session to view its scan timeline.</p>
  if(!state)return <div class="scan-timeline-panel"><p>{error||'Loading timeline…'}</p></div>
  const allowed=state.global_enabled&&state.project_enabled
  const events=[
    ...state.records.map(record=>({kind:'record' as const,at:record.t1,record})),
    ...state.boundaries.map(boundary=>({kind:'boundary' as const,at:boundary.created_at,boundary})),
  ].sort((left,right)=>left.at-right.at)
  return <section class="scan-timeline-panel">
    <header>
      <div><strong>Behavior timeline</strong><small>{state.model}</small></div>
      <label class="scan-run-toggle"><span>this run</span><input type="checkbox" checked={state.run_enabled} disabled={busy||!allowed||!state.agent_run_id} onChange={event=>void toggle(event.currentTarget.checked)}/></label>
    </header>
    <div class="scan-spend-line" title="Timeline cost and compressed-record source expansion rate">
      <span>${state.spend_today.cost_usd.toFixed(4)} / ${state.daily_budget_usd.toFixed(2)} today</span>
      <span>{state.spend_today.tokens.toLocaleString()} tokens</span>
      <span>run {state.run_spend.tokens.toLocaleString()} / {state.run_token_budget.toLocaleString()} tokens</span>
      <span>rehydration {percent(state.metrics.rehydration_rate)} ({state.metrics.rehydrations}/{state.metrics.record_reads})</span>
    </div>
    {!state.global_enabled&&<p class="scan-gate">The global scan timeline switch is off in Settings.</p>}
    {state.global_enabled&&!state.project_enabled&&<p class="scan-gate">This Project has not permitted Scan timeline and its dependencies.</p>}
    {allowed&&!state.run_enabled&&<p class="scan-gate">Off for this conversation. Enable it here when you want a timeline. It resets on /clear, /new, or session end.</p>}
    {error&&<p class="usage-error">{error}</p>}
    <div class="scan-timeline-list">
      {state.records.length===0&&<p class="drawer-empty">No scan records for this session.</p>}
      {events.map(event=>{
        if(event.kind==='boundary')return <div class="scan-boundary" key={event.boundary.id}><strong>New conversation</strong><span>{clock(event.boundary.created_at)} · {event.boundary.reason}</span></div>
        const record=event.record
        return <div class="scan-record-wrap" key={record.id}>
          <article class="scan-record">
            <header><time>{clock(record.t1)}</time><strong>{record.work_phase}</strong><span>{record.lifecycle_state}</span><em>novelty {percent(record.novelty)}</em></header>
            <p>{record.summary||record.intent||'No semantic change recorded.'}</p>
            {record.user_ask&&<dl><dt>Asked</dt><dd>{record.user_ask}</dd></dl>}
            {record.intent&&<dl><dt>Intent</dt><dd>{record.intent}</dd></dl>}
            {record.claim&&<dl><dt>Claim</dt><dd>{record.claim}</dd></dl>}
            {record.blocked_on!=='none'&&<dl><dt>Blocked</dt><dd>{record.blocked_on}</dd></dl>}
            {record.dead_end&&record.approach_status==='abandoned'&&<dl class="scan-dead-end"><dt>Dead end</dt><dd>{record.dead_end}</dd></dl>}
            {!!record.target.length&&<small>{record.target.join(' · ')}</small>}
            <footer><span>{record.behavior.join(' · ')} · confidence {percent(record.confidence)}</span><button disabled={busy} onClick={()=>void source(record)}>{expanded[record.id]!==undefined?'Hide source':'View source'}</button></footer>
            {expanded[record.id]!==undefined&&<pre class="scan-source">{JSON.stringify(expanded[record.id],null,2)}</pre>}
          </article>
        </div>
      })}
    </div>
    {state.run_enabled&&<footer><button disabled={busy} onClick={()=>void scan()}>Scan now</button><span>Event-triggered · 3 minute heartbeat</span></footer>}
  </section>
}
