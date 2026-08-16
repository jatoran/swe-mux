import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import type { Session } from './types'

type Coverage = {messages_seen:number;facts_seen:number;truncated:boolean;remaining?:number}
type TimelineRecord = {
  id:string;agent_run_id:string;t0:number;t1:number;lifecycle_state:string
  behavior:string[];work_phase:string;target:string[];intent:string;claim:string
  user_ask:string;blocked_on:string;summary:string;approach_status:string
  dead_end:string;novelty:number;confidence:number;trigger:string;observer_model:string
  coverage?:Coverage;repairs?:string[]
}
type Boundary = {id:string;previous_run_id:string;next_run_id:string;reason:string;created_at:number}
type Metrics = {record_reads:number;rehydrations:number;rehydration_rate:number}
type BackfillState = 'idle'|'running'|'completed'|'completed_with_gaps'|'partial'|'failed'
type Backfill = {
  state:BackfillState;processed_chunks:number;total_chunks:number;created_records:number
  failed_chunks?:number;skipped_chunks?:number;reason:string|null;estimated_tokens?:number
}
type Gate = {id:string;label:string;unit:'tokens'|'usd'|'calls';used:number;limit:number}
type TimelineState = {
  session_id:string;project_id:string|null;agent_run_id:string|null;global_enabled:boolean;project_enabled:boolean
  run_enabled:boolean;auto_enable:boolean;run_decided:boolean;model:string;daily_budget_usd:number
  spend_today:{tokens:number;cost_usd:number};run_token_budget:number
  run_spend:{tokens:number;cost_usd:number};metrics:Metrics;gates:Gate[]
  skip_reason:string|null;last_scan_at:number|null;scanning:boolean
  records:TimelineRecord[];boundaries:Boundary[];backfill:Backfill
}

const clock = (value:number) => new Date(value*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})
const percent = (value:number) => `${Math.round(value*100)}%`
const amount = (gate:Gate) => gate.unit==='usd'
  ? `$${gate.used.toFixed(4)} / $${gate.limit.toFixed(2)}`
  : `${Math.round(gate.used).toLocaleString()} / ${Math.round(gate.limit).toLocaleString()} ${gate.unit}`
// Whichever cap is closest to stopping the timeline. Showing the run budget and
// the project dollar figure while an invisible per-rule token cap did the
// stopping is what made a dead timeline look like it had headroom to spare.
const headroom = (gate:Gate) => gate.limit>0 ? gate.used/gate.limit : 0
const bindingGate = (gates:Gate[]) => gates.length
  ? gates.reduce((worst,gate)=>headroom(gate)>headroom(worst)?gate:worst)
  : null
const backfillLabel:Record<BackfillState,string> = {
  idle:'idle', running:'running', completed:'complete',
  completed_with_gaps:'complete with gaps', partial:'stopped early', failed:'failed',
}

export function ScanTimelineTab({session,onOpenProjectSettings}:{
  session:Session|null
  onOpenProjectSettings:(projectId:string)=>void
}) {
  const [state,setState]=useState<TimelineState|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [expanded,setExpanded]=useState<Record<string,unknown[]|null>>({})
  const listRef=useRef<HTMLDivElement|null>(null)
  const pinned=useRef(true)
  const sid=session?.id||''
  const run=session?.agent_run_id||''
  const projectId=session?.project_id||''

  const load=async()=>{
    if(!sid){setState(null);return}
    try{
      setState(await api<TimelineState>('GET',`/api/sessions/${sid}/scan-timeline`))
      setError('')
    }
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }
  useEffect(()=>{void load()},[sid,run])
  // Polled only while there is something to watch: a running full-session scan,
  // or an armed run whose next scan the footer indicator has to catch starting.
  // A timeline that is off for this conversation polls nothing.
  useEffect(()=>{
    if(!sid)return
    const live=state?.backfill.state==='running'
    if(!live&&!state?.run_enabled)return
    const timer=window.setInterval(()=>void load(),live?1200:2000)
    return()=>window.clearInterval(timer)
  },[sid,run,state?.backfill.state,state?.run_enabled])
  useEffect(()=>{
    const refresh=()=>void load()
    window.addEventListener('mux:turn-ended',refresh)
    window.addEventListener('mux:transcript-changed',refresh)
    return()=>{window.removeEventListener('mux:turn-ended',refresh);window.removeEventListener('mux:transcript-changed',refresh)}
  },[sid,run])
  // The newest record is the one worth seeing, and it is at the bottom because
  // the list is chronological — so opening the tab used to land on the oldest.
  // Stays pinned as records arrive, and lets go the moment the reader scrolls up.
  useEffect(()=>{pinned.current=true},[sid,run])
  useEffect(()=>{
    const list=listRef.current
    if(!list||!pinned.current)return
    list.scrollTop=list.scrollHeight
  },[sid,run,state?.records.length,state?.boundaries.length])
  const onListScroll=(event:Event)=>{
    const list=event.currentTarget as HTMLDivElement
    pinned.current=list.scrollHeight-list.scrollTop-list.clientHeight<=48
  }

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
  const backfill=async()=>{
    if(!sid)return
    setBusy(true)
    try{await api('POST',`/api/sessions/${sid}/scan-timeline/backfill`,{});await load()}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const stopBackfill=async()=>{
    if(!sid)return
    setBusy(true)
    try{await api('DELETE',`/api/sessions/${sid}/scan-timeline/backfill`);await load()}
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
  const binding=bindingGate(state.gates||[])
  const running=state.backfill.state==='running'
  const projectButton=<button class="scan-project-link" disabled={!projectId}
    onClick={()=>projectId&&onOpenProjectSettings(projectId)}>Project settings</button>
  if(!allowed)return <section class="scan-timeline-panel">
    <header>
      <div><strong>Behavior timeline</strong><small>{state.model}</small></div>
      {projectButton}
    </header>
    <div class="scan-timeline-off">
      <p>{state.global_enabled
        ? 'This Project has not permitted Scan timeline.'
        : 'The global Scan timeline switch is off in Settings → Automation.'}</p>
      <p class="scan-timeline-off-hint">{state.global_enabled
        ? 'Permission, the Project context sent with each scan, and the option to arm every new conversation automatically all live in the Project’s settings.'
        : 'Turn the master switch on first, then permit this Project in its settings.'}</p>
      {projectButton}
    </div>
  </section>
  const events=[
    ...state.records.map(record=>({kind:'record' as const,at:record.t1,record})),
    ...state.boundaries.map(boundary=>({kind:'boundary' as const,at:boundary.created_at,boundary})),
  ].sort((left,right)=>left.at-right.at)
  return <section class="scan-timeline-panel">
    <header>
      <div><strong>Behavior timeline</strong><small>{state.model}</small></div>
      <label class="scan-run-toggle"><span>this run</span><input type="checkbox" checked={state.run_enabled} disabled={busy||!state.agent_run_id} onChange={event=>void toggle(event.currentTarget.checked)}/></label>
      {projectButton}
    </header>
    {/* One row by default. Six budget lines is the honest set, and it was also
        most of the panel on a narrow drawer; the one closest to binding is the
        only one worth standing costs. */}
    <details class="scan-budget">
      <summary>
        <span>Budget</span>
        {binding
          ? <em class={headroom(binding)>=0.9?'scan-gate-binding':undefined}>{binding.label} {amount(binding)} · {percent(headroom(binding))}</em>
          : <em>no limits reported</em>}
      </summary>
      <div class="scan-spend-line">
        {state.gates.map(gate=><span key={gate.id} class={gate===binding&&headroom(gate)>=0.9?'scan-gate-binding':undefined}>{gate.label} {amount(gate)}</span>)}
      </div>
    </details>
    {!state.run_enabled&&<p class="scan-gate">{state.auto_enable&&!state.run_decided
      ? 'Not armed yet. This Project arms new conversations automatically, so it starts on the next turn.'
      : state.auto_enable
        ? 'Switched off for this conversation. This Project arms new ones automatically, but a conversation you turned off stays off.'
        : 'Off for this conversation. It resets on /clear, /new, or session end — turn on “arm every new conversation” in Project settings to stop repeating this.'}</p>}
    {state.run_enabled&&state.skip_reason&&<p class="scan-gate scan-stopped">Not scanning: {state.skip_reason}.</p>}
    {error&&<p class="usage-error">{error}</p>}
    {state.backfill.state!=='idle'&&<p class={`scan-backfill-status ${state.backfill.state}`}>
      {state.backfill.state==='running'
        ? `Scanning full session · ${state.backfill.processed_chunks}/${state.backfill.total_chunks||'…'} chunks · ${state.backfill.created_records} records`
        : `Full-session scan ${backfillLabel[state.backfill.state]} · ${state.backfill.processed_chunks}/${state.backfill.total_chunks} chunks · ${state.backfill.created_records} records${state.backfill.failed_chunks?` · ${state.backfill.failed_chunks} chunks failed`:''}${state.backfill.skipped_chunks?` · ${state.backfill.skipped_chunks} already covered`:''}${state.backfill.reason?` · ${state.backfill.reason}`:''}`}
    </p>}
    <div class="scan-timeline-list" ref={listRef} onScroll={onListScroll}>
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
            {!!record.target.length&&<details class="scan-record-targets">
              <summary>Evidence targets <span>{record.target.length}</span></summary>
              <ul>{record.target.map(target=><li key={target}><code>{target}</code></li>)}</ul>
            </details>}
            {!!record.coverage?.remaining&&<p class="scan-record-lag">{record.coverage.remaining} later messages were still unscanned when this record was written; a catch-up scan follows.</p>}
            {!!record.repairs?.length&&<details class="scan-record-targets scan-record-repairs">
              <summary>Model output repaired <span>{record.repairs.length}</span></summary>
              <ul>{record.repairs.map(repair=><li key={repair}>{repair}</li>)}</ul>
            </details>}
            <footer><span>{record.behavior.join(' · ')} · confidence {percent(record.confidence)}</span><button disabled={busy} onClick={()=>void source(record)}>{expanded[record.id]!==undefined?'Hide source':'View source'}</button></footer>
            {expanded[record.id]!==undefined&&<pre class="scan-source">{JSON.stringify(expanded[record.id],null,2)}</pre>}
          </article>
        </div>
      })}
    </div>
    <footer>
      <div>
        <button disabled={busy||!state.run_enabled||running} onClick={()=>void scan()}>Scan now</button>
        {running
          ? <button disabled={busy} onClick={()=>void stopBackfill()}>Stop full scan</button>
          : <button disabled={busy||!state.run_enabled} onClick={()=>void backfill()}>Scan full session</button>}
      </div>
      <span class={`scan-activity ${state.scanning||running?'busy':''}`}
        title={state.scanning
          ? 'A scan request is out to the model and its record has not landed yet.'
          : `Event-triggered · 3 minute heartbeat · source expansions ${state.metrics.rehydrations}/${state.metrics.record_reads}`}>
        <b aria-hidden="true"/>
        {state.scanning?'Scanning…':running?'Full-session scan…':'Idle · 3 min heartbeat'}
      </span>
    </footer>
  </section>
}
