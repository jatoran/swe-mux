import { useEffect, useRef, useState } from 'preact/hooks'
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
type Backfill = {state:'idle'|'running'|'completed'|'partial'|'failed';processed_chunks:number;total_chunks:number;created_records:number;reason:string|null}
type ProjectContext = {project_id:string;path:string;exists:boolean;revision:string;markdown:string;max_bytes:number;generation_prompt:string}
type TimelineState = {
  session_id:string;project_id:string|null;agent_run_id:string|null;global_enabled:boolean;project_enabled:boolean
  run_enabled:boolean;model:string;daily_budget_usd:number
  spend_today:{tokens:number;cost_usd:number};run_token_budget:number
  run_spend:{tokens:number;cost_usd:number};metrics:Metrics
  records:TimelineRecord[];boundaries:Boundary[];backfill:Backfill
}

const clock = (value:number) => new Date(value*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})
const percent = (value:number) => `${Math.round(value*100)}%`

export function ScanTimelineTab({session}:{session:Session|null}) {
  const [state,setState]=useState<TimelineState|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [expanded,setExpanded]=useState<Record<string,unknown[]|null>>({})
  const [context,setContext]=useState<ProjectContext|null>(null)
  const [contextDraft,setContextDraft]=useState('')
  const [contextOpen,setContextOpen]=useState(false)
  const [contextMessage,setContextMessage]=useState('')
  const contextProject=useRef('')
  const savedContext=useRef('')
  const draftContext=useRef('')
  const contextRevision=useRef('missing')
  const sid=session?.id||''
  const run=session?.agent_run_id||''

  const load=async()=>{
    if(!sid){setState(null);return}
    try{
      const timeline=await api<TimelineState>('GET',`/api/sessions/${sid}/scan-timeline`)
      setState(timeline)
      if(timeline.project_id){
        const value=await api<ProjectContext>('GET',`/api/projects/${timeline.project_id}/project-context`)
        const sameProject=contextProject.current===value.project_id
        const dirty=sameProject&&draftContext.current!==savedContext.current
        if(!dirty){
          setContext(value);setContextDraft(value.markdown)
          contextProject.current=value.project_id;savedContext.current=value.markdown
          draftContext.current=value.markdown;contextRevision.current=value.revision
        }else if(value.revision!==contextRevision.current){
          setContextMessage('File changed externally. Copy your draft, then switch sessions or reopen Timeline to reload it.')
        }
      }else{setContext(null);setContextDraft('');contextProject.current='';savedContext.current='';draftContext.current='';contextRevision.current='missing'}
      setError('')
    }
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  useEffect(()=>{void load()},[sid,run])
  useEffect(()=>{
    if(state?.backfill.state!=='running')return
    const timer=window.setInterval(()=>void load(),1200)
    return()=>window.clearInterval(timer)
  },[sid,run,state?.backfill.state])
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
  const toggleProject=async(enabled:boolean)=>{
    if(!sid)return
    setBusy(true)
    try{setState(await api<TimelineState>('PUT',`/api/sessions/${sid}/scan-timeline/project`,{enabled}));await load()}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const backfill=async()=>{
    if(!sid)return
    setBusy(true)
    try{await api('POST',`/api/sessions/${sid}/scan-timeline/backfill`,{});await load()}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const saveContext=async()=>{
    if(!state?.project_id||!context)return
    setBusy(true);setContextMessage('')
    try{
      const value=await api<ProjectContext>('PUT',`/api/projects/${state.project_id}/project-context`,{markdown:contextDraft,revision:context.revision})
      setContext(value);setContextDraft(value.markdown);savedContext.current=value.markdown;draftContext.current=value.markdown;contextRevision.current=value.revision;setContextMessage('Saved')
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const copySetupPrompt=async()=>{
    if(!context)return
    try{await navigator.clipboard.writeText(context.generation_prompt);setContextMessage('Setup prompt copied')}
    catch{setContextMessage('Clipboard access failed')}
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
    <div class="scan-project-section">
      <header><div><strong>Project timeline</strong><small>Applies to every session in this Project</small></div><label class="scan-run-toggle"><span>enabled</span><input type="checkbox" checked={state.project_enabled} disabled={busy||!state.project_id} onChange={event=>void toggleProject(event.currentTarget.checked)}/></label></header>
      <button class="scan-context-toggle" aria-expanded={contextOpen} onClick={()=>setContextOpen(value=>!value)}><span>Project context</span><small>{context?.markdown.trim()?'configured':'empty'} · {context?.path||'.swe-mux/project-context.md'}</small><b>{contextOpen?'−':'+'}</b></button>
      {contextOpen&&context&&<div class="scan-context-editor">
        <p>User-authored Markdown sent with timeline scans. swe-mux does not derive it from repository docs.</p>
        <textarea value={contextDraft} placeholder="Describe this Project for timeline scans…" onInput={event=>{draftContext.current=event.currentTarget.value;setContextDraft(event.currentTarget.value);setContextMessage('')}}/>
        <div><span>{new TextEncoder().encode(contextDraft).length.toLocaleString()} / {context.max_bytes.toLocaleString()} bytes</span><button disabled={busy||contextDraft===context.markdown||new TextEncoder().encode(contextDraft).length>context.max_bytes} onClick={()=>void saveContext()}>Save</button><button disabled={busy} onClick={()=>void copySetupPrompt()}>Copy setup prompt</button></div>
        {contextMessage&&<small role="status">{contextMessage}</small>}
      </div>}
    </div>
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
    {!state.global_enabled&&<p class="scan-gate">The global Scan timeline switch is off in Automation.</p>}
    {state.global_enabled&&!state.project_enabled&&<p class="scan-gate">This Project has not permitted Scan timeline and its dependencies.</p>}
    {allowed&&!state.run_enabled&&<p class="scan-gate">Off for this conversation. Enable it here when you want a timeline. It resets on /clear, /new, or session end.</p>}
    {error&&<p class="usage-error">{error}</p>}
    {state.backfill.state!=='idle'&&<p class={`scan-backfill-status ${state.backfill.state}`}>{state.backfill.state==='running'?`Scanning full session · ${state.backfill.processed_chunks}/${state.backfill.total_chunks||'…'} chunks`:`Full-session scan ${state.backfill.state} · ${state.backfill.created_records} records${state.backfill.reason?` · ${state.backfill.reason}`:''}`}</p>}
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
    <footer><div><button disabled={busy||!state.run_enabled||state.backfill.state==='running'} onClick={()=>void scan()}>Scan now</button><button disabled={busy||!state.run_enabled||state.backfill.state==='running'} onClick={()=>void backfill()}>Scan full session</button></div><span>Event-triggered · 3 minute heartbeat</span></footer>
  </section>
}
