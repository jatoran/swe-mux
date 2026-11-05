import { locale as uiLocale } from './localization/index.ts'
import { rich, t } from './localization/index.ts'
import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { GrantButton, GrantGate } from './GrantGate'
import type { GrantId } from './grants'
import type { Budget, Session } from './types'

type Coverage = {messages_seen:number;facts_seen:number;truncated:boolean;remaining?:number}
type TimelineRecord = {
  id:string;agent_run_id:string;t0:number;t1:number;lifecycle_state:string
  behavior:string[];work_phase:string;target:string[];intent:string;claim:string
  // `approach_status` and `dead_end` are judgments about the whole run rather than
  // this window, so a record may withhold them. Absent is not `unknown`.
  user_ask:string;blocked_on:string;summary:string;approach_status?:string
  dead_end?:string;novelty:number;confidence:number;trigger:string;observer_model:string
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
  run_enabled:boolean;auto_enable:boolean;run_decided:boolean;model:string;daily_budget:Budget
  spend_today:{tokens:number;cost_usd:number;unpriced_calls?:number};run_budget:Budget
  run_spend:{tokens:number;cost_usd:number};metrics:Metrics;gates:Gate[]
  skip_reason:string|null;last_scan_at:number|null;scanning:boolean
  records:TimelineRecord[];boundaries:Boundary[];backfill:Backfill
}

const clock = (value:number) => new Date(value*1000).toLocaleTimeString(uiLocale.value, {hour:'2-digit',minute:'2-digit',second:'2-digit'})
const percent = (value:number) => `${Math.round(value*100)}%`
const amount = (gate:Gate) => gate.unit==='usd'
  ? `$${gate.used.toFixed(4)} / $${gate.limit.toFixed(2)}`
  : `${Math.round(gate.used).toLocaleString(uiLocale.value)} / ${Math.round(gate.limit).toLocaleString(uiLocale.value)} ${gate.unit}`
// Whichever cap is closest to stopping the timeline. Showing the run budget and
// the project dollar figure while an invisible per-rule token cap did the
// stopping is what made a dead timeline look like it had headroom to spare.
const headroom = (gate:Gate) => gate.limit>0 ? gate.used/gate.limit : 0
const bindingGate = (gates:Gate[]) => gates.length
  ? gates.reduce((worst,gate)=>headroom(gate)>headroom(worst)?gate:worst)
  : null
const backfillLabel:Record<BackfillState,string> = {
  idle:'idle', running:'running', completed:'complete',
  get completed_with_gaps() { return t("ScanTimelineTab.cd426ac56c") }, get partial() { return t("ScanTimelineTab.4c789bcaff") }, failed:'failed',
}
// The one line a collapsed row stands on. A record whose model produced no summary
// still has to be identifiable, or collapsing it would hide that it exists at all.
const gistOf = (record:TimelineRecord) =>
  record.summary||record.intent||record.claim||t("ScanTimelineTab.c5a127f40a")
// Flags a collapsed row cannot afford to drop: the two semantic ones say this window
// is where the run stalled, and the two caveat ones say the record itself is partial.
// Everything else is detail, which is what the expansion is for.
const flagsOf = (record:TimelineRecord) => [
  ...(record.blocked_on&&record.blocked_on!=='none' ? [{key:'blocked',get label() { return t("ScanTimelineTab.6973dddd3e") },tone:'warn'}] : []),
  ...(record.dead_end&&record.approach_status==='abandoned' ? [{key:'dead',get label() { return t("ScanTimelineTab.83e3113c82") },tone:'warn'}] : []),
  ...(record.coverage?.remaining ? [{key:'behind',get label() { return t("ScanTimelineTab.b1b886ce5f") },tone:'note'}] : []),
  ...(record.repairs?.length ? [{key:'repaired',get label() { return t("ScanTimelineTab.3baf75ce88") },tone:'note'}] : []),
]

export function ScanTimelineTab({session,onOpenProjectSettings}:{
  session:Session|null
  onOpenProjectSettings:(projectId:string)=>void
}) {
  const [state,setState]=useState<TimelineState|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [expanded,setExpanded]=useState<Record<string,unknown[]|null>>({})
  // Which records are showing their detail. Deliberately *not* persisted: record ids are
  // per-run and unbounded, so a device store keyed by them would grow forever to remember
  // something that is worth one tap. Compact is the resting state of every row, every time.
  const [openRecords,setOpenRecords]=useState<Record<string,true>>({})
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
  // A new conversation replaces every record, so both per-record maps are keyed by ids
  // that no longer exist. Dropping them is what keeps them bounded.
  useEffect(()=>{pinned.current=true;setOpenRecords({});setExpanded({})},[sid,run])
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
  const toggleRecord=(id:string)=>setOpenRecords(current=>{
    const next={...current}
    if(next[id])delete next[id]
    else next[id]=true
    return next
  })
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

  if(!session)return <p class="drawer-empty">{t("ScanTimelineTab.8fcecc4789")}</p>
  if(!state)return <div class="scan-timeline-panel"><p>{error || t("ScanTimelineTab.3f40157f05")}</p></div>
  const allowed=state.global_enabled&&state.project_enabled
  const binding=bindingGate(state.gates||[])
  const running=state.backfill.state==='running'
  const projectButton=<button class="scan-project-link" disabled={!projectId}
    onClick={()=>projectId&&onOpenProjectSettings(projectId)}>{t("ScanTimelineTab.fccd73a69e")}</button>
  // Two switches gate this, and both are granted here in one act. Naming only the outer
  // one meant turning it on, walking back, and finding a second gate with a second link
  // to a second overlay - two full round trips to see one pane. The gate is drawn only
  // while the timeline is off and vanishes once it is on, so the Project-wide permission
  // never becomes a standing control in a session-scoped tab.
  if(!allowed){
    const missing:GrantId[]=[
      ...(state.global_enabled?[]:['automation.scanTimeline' as const]),
      ...(state.project_enabled?[]:['project.scanTimeline' as const]),
    ]
    return <section class="scan-timeline-panel">
      <header>
        <div><strong>{t("ScanTimelineTab.9ef803e89d")}</strong><small>{state.model}</small></div>
        {projectButton}
      </header>
      <div class="scan-timeline-off">
        <GrantGate
          ids={missing}
          projectId={projectId||undefined}
          heading={state.global_enabled ? t("ScanTimelineTab.55800b8efe") : t("ScanTimelineTab.608890d77c")}
          confirmLabel={t("ScanTimelineTab.fbd6de54ce")}
          onGranted={load}
        >
          <p>{t("ScanTimelineTab.c7b59b34d1")}</p>
        </GrantGate>
      </div>
    </section>
  }
  const events=[
    ...state.records.map(record=>({kind:'record' as const,at:record.t1,record})),
    ...state.boundaries.map(boundary=>({kind:'boundary' as const,at:boundary.created_at,boundary})),
  ].sort((left,right)=>left.at-right.at)
  return <section class="scan-timeline-panel">
    <header>
      <div><strong>{t("ScanTimelineTab.9ef803e89d")}</strong><small>{state.model}</small></div>
      <label class="scan-run-toggle"><span>{t("ScanTimelineTab.c35c81578e")}</span><input type="checkbox" checked={state.run_enabled} disabled={busy||!state.agent_run_id} onChange={event=>void toggle(event.currentTarget.checked)}/></label>
      {projectButton}
    </header>
    {/* One row by default. Six budget lines is the honest set, and it was also
        most of the panel on a narrow drawer; the one closest to binding is the
        only one worth standing costs. */}
    <details class="scan-budget">
      <summary>
        <span>{t("ScanTimelineTab.1c6225ec70")}</span>
        {binding ? <em class={headroom(binding)>=0.9?'scan-gate-binding':undefined}>{binding.label} {amount(binding)} · {percent(headroom(binding))}</em> : <em>{t("ScanTimelineTab.5fcb495929")}</em>}
      </summary>
      <div class="scan-spend-line">
        {state.gates.map(gate=><span key={gate.id} class={gate===binding&&headroom(gate)>=0.9?'scan-gate-binding':undefined}>{gate.label} {amount(gate)}</span>)}
      </div>
    </details>
    {!state.run_enabled && <p class="scan-gate">{state.auto_enable&&!state.run_decided ? t("ScanTimelineTab.52de345951") : state.auto_enable ? t("ScanTimelineTab.fd5f49264c") : <>{rich("ScanTimelineTab.74ce464b90", { value1: <GrantButton id="project.scanTimelineAutoArm" projectId={projectId||undefined}
            onGranted={load}
            title={t("ScanTimelineTab.12e743d51a")}
          >{t("ScanTimelineTab.f49049c042")}</GrantButton> })}</>}</p>}
    {state.run_enabled&&state.skip_reason && <p class="scan-gate scan-stopped">{rich("ScanTimelineTab.73e9c30218", { skip_reason: state.skip_reason })}</p>}
    {error && <p class="usage-error">{error}</p>}
    {state.backfill.state!=='idle' && <p class={`scan-backfill-status ${state.backfill.state}`}>
      {state.backfill.state==='running' ? t("ScanTimelineTab.49a08a8eef", { processed_chunks: state.backfill.processed_chunks, value2: state.backfill.total_chunks||'…', created_records: state.backfill.created_records }) : t("ScanTimelineTab.532b7d4147", { value1: backfillLabel[state.backfill.state], processed_chunks: state.backfill.processed_chunks, total_chunks: state.backfill.total_chunks, created_records: state.backfill.created_records, value5: state.backfill.failed_chunks?t("ScanTimelineTab.f521f03d88", { value1: state.backfill.failed_chunks }):'', value6: state.backfill.skipped_chunks?t("ScanTimelineTab.d9825a6acd", { value1: state.backfill.skipped_chunks }):'', value7: state.backfill.reason?` · ${state.backfill.reason}`:'' })}
    </p>}
    <div class="scan-timeline-list" ref={listRef} onScroll={onListScroll}>
      {state.records.length===0 && <p class="drawer-empty">{t("ScanTimelineTab.83299179e2")}</p>}
      {events.map(event=>{
        if(event.kind==='boundary')return <div class="scan-boundary" key={event.boundary.id}><strong>{t("ScanTimelineTab.396c946f0c")}</strong><span>{clock(event.boundary.created_at)} · {event.boundary.reason}</span></div>
        const record=event.record
        // Compact is the resting state. The row still carries the record's identity:
        // when it happened, what phase it belongs to, one line of what happened, and
        // whichever flags say this window stalled or this record is partial. So the
        // expansion is for detail rather than for working out which record this is.
        const open=!!openRecords[record.id]
        const detailId=`scan-record-detail-${record.id}`
        return <div class="scan-record-wrap" key={record.id}>
          <article class={`scan-record${open?' open':''}`}>
            <button class="scan-record-head" aria-expanded={open} aria-controls={detailId}
              title={open ? t("ScanTimelineTab.639af6857d") : gistOf(record)}
              onClick={()=>toggleRecord(record.id)}>
              <span class="scan-record-line">
                <time>{clock(record.t1)}</time>
                <strong>{record.work_phase}</strong>
                <span class="scan-record-state">{record.lifecycle_state}</span>
                {flagsOf(record).map(flag=><span key={flag.key} class={`scan-record-flag ${flag.tone}`}>{flag.label}</span>)}
              </span>
              <span class="scan-record-gist">{gistOf(record)}</span>
              <b class="scan-record-caret" aria-hidden="true">›</b>
            </button>
            {open && <div class="scan-record-detail" id={detailId}>
              <p class="scan-record-novelty">{rich("ScanTimelineTab.115d42f307", { value1: percent(record.novelty) })}</p>
              {record.user_ask && <dl><dt>{t("ScanTimelineTab.821c038b7a")}</dt><dd>{record.user_ask}</dd></dl>}
              {record.intent && <dl><dt>{t("ScanTimelineTab.29131ad07d")}</dt><dd>{record.intent}</dd></dl>}
              {record.claim && <dl><dt>{t("ScanTimelineTab.4ca41db028")}</dt><dd>{record.claim}</dd></dl>}
              {/* Same guard as the row's `blocked` flag: an empty blocker is not a blocker,
                  and drew a labelled row with nothing in it. */}
              {record.blocked_on&&record.blocked_on!=='none' && <dl><dt>{t("ScanTimelineTab.18f2a0947f")}</dt><dd>{record.blocked_on}</dd></dl>}
              {record.dead_end&&record.approach_status==='abandoned' && <dl class="scan-dead-end"><dt>{t("ScanTimelineTab.f2fd4044ac")}</dt><dd>{record.dead_end}</dd></dl>}
              {!!record.target.length && <details class="scan-record-targets">
                <summary>{rich("ScanTimelineTab.a59148de0f", { value1: <span>{record.target.length}</span> })}</summary>
                <ul>{record.target.map(target=><li key={target}><code>{target}</code></li>)}</ul>
              </details>}
              {!!record.coverage?.remaining && <p class="scan-record-lag">{rich("ScanTimelineTab.4f736984f2", { remaining: record.coverage.remaining })}</p>}
              {!!record.repairs?.length && <details class="scan-record-targets scan-record-repairs">
                <summary>{rich("ScanTimelineTab.22f8c5803d", { value1: <span>{record.repairs.length}</span> })}</summary>
                <ul>{record.repairs.map(repair=><li key={repair}>{repair}</li>)}</ul>
              </details>}
              <footer><span>{rich("ScanTimelineTab.e3070ac4f9", { value1: record.behavior.join(' · '), value2: percent(record.confidence) })}</span><button disabled={busy} onClick={()=>void source(record)}>{expanded[record.id]!==undefined ? t("ScanTimelineTab.a52ddc94bb") : t("ScanTimelineTab.6ee818aa2d")}</button></footer>
              {expanded[record.id]!==undefined && <pre class="scan-source">{JSON.stringify(expanded[record.id],null,2)}</pre>}
            </div>}
          </article>
        </div>
      })}
    </div>
    <footer>
      <div>
        <button disabled={busy||!state.run_enabled||running} onClick={()=>void scan()}>{t("ScanTimelineTab.c01d65df01")}</button>
        {running ? <button disabled={busy} onClick={()=>void stopBackfill()}>{t("ScanTimelineTab.9028ed3f30")}</button> : <button disabled={busy||!state.run_enabled} onClick={()=>void backfill()}>{t("ScanTimelineTab.7041f36728")}</button>}
      </div>
      <span class={`scan-activity ${state.scanning||running?'busy':''}`}
        title={state.scanning ? t("ScanTimelineTab.b9cfcf474b") : t("ScanTimelineTab.01fa11e4c2", { rehydrations: state.metrics.rehydrations, record_reads: state.metrics.record_reads })}>
        <b aria-hidden="true"/>
        {state.scanning ? t("ScanTimelineTab.38d96da6e8") : running ? t("ScanTimelineTab.96992b9dec") : t("ScanTimelineTab.987fe3220a")}
      </span>
    </footer>
  </section>
}
