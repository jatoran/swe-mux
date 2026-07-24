import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { useModalFocus } from './modalFocus'
import type { Project } from './types'

export type HistoryMatch={ordinal:number;role:'user'|'assistant';ts?:string;excerpt:string}
export type HistoryEntry = {
  id:string;native_id:string;backend:string;name:string;cwd:string
  spawned_at:number;exited_at?:number;exit_reason?:string;transcript_path?:string
  project_id?:string;project_label?:string;final_state?:string;external?:number
  project_scope_id?:string;repo_group_id?:string;project_root?:string
  context_window?:number;final_context_pct?:number;peak_context_pct?:number
  tokens_in?:number;tokens_out?:number;model?:string;measurement_source?:string
  compaction_count?:number;last_compaction_at?:number;compaction_capability?:string;compaction_confidence?:string
  native_started_at?:number|null;last_message_at?:number|null;last_message_role?:'user'|'assistant'|null
  transcript_size?:number|null;time_summary_size?:number|null
  note_id?:string
  auto_named?:number;generated_title?:string;matches?:HistoryMatch[];match_count?:number
}
type DerivedAnnotation={id:string;tag:string;content:string;provenance:string;resolved_model?:string;confidence?:number;cost_usd?:number;created_at:number}
type TranscriptMessage={role:string;ts?:string;content:Array<{type:string;text?:string;name?:string;input?:unknown}>}
type Transcript={entry:HistoryEntry;messages:TranscriptMessage[];annotations:DerivedAnnotation[];matches:HistoryMatch[]}
type LineageEdge={id:string;parent_run_id:string;child_run_id:string;relation:string;created_at:number}
type HistoryPage={items:HistoryEntry[];next_cursor:string|null}
type HistoryProject={project_id:string|null;label:string;root?:string;sessions:number;last_activity:number}
type BackfillJob={
  id:string;project_id:string;project_name:string;status:string;phase:string;scanned:number;total:number;processed:number
  discovered:number;indexed:number;indexed_messages:number;unchanged:number;ambiguous:number;unreadable:number
  error?:string;cancel_requested:boolean
}

type Props={
  projects:Project[]
  initialProjectId:string
  onClose:()=>void
  onResume:(entry:HistoryEntry)=>void|Promise<void>
  onSessionNote:(entry:HistoryEntry)=>void
  onSecondOpinion:(entry:HistoryEntry)=>void|Promise<void>
  onHandoff:(entry:HistoryEntry)=>void|Promise<void>
}

const money=new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',maximumFractionDigits:4})
const historyName=(entry:HistoryEntry)=>entry.auto_named!==0&&entry.generated_title?entry.generated_title:entry.name
const timestampDate=(value?:string|number|null)=>{
  if(value===undefined||value===null||value==='')return null
  const numeric=typeof value==='number'?value:/^\d+(?:\.\d+)?$/.test(value)?Number(value):null
  const date=new Date(numeric===null?value:numeric>10_000_000_000?numeric:numeric*1000)
  return Number.isNaN(date.getTime())?null:date
}
const timestampLabel=(value?:string|number|null)=>timestampDate(value)?.toLocaleString()||'timestamp unavailable'
const timestampIso=(value?:string|number|null)=>timestampDate(value)?.toISOString()
const historyStart=(entry:HistoryEntry)=>entry.native_started_at??(entry.external?null:entry.spawned_at)
const transcriptBytes=(entry:HistoryEntry)=>entry.transcript_size??entry.time_summary_size??null
const formatBytes=(bytes?:number|null)=>{
  if(bytes===undefined||bytes===null||bytes<=0)return null
  const units=['B','KB','MB','GB'];let value=bytes,unit=0
  while(value>=1024&&unit<units.length-1){value/=1024;unit++}
  return `${value>=100||unit===0?Math.round(value):value.toFixed(1)} ${units[unit]}`
}

export function HistoryBrowser({projects,initialProjectId,onClose,onResume,onSessionNote,onSecondOpinion,onHandoff}:Props){
  const [items,setItems]=useState<HistoryEntry[]>([])
  const [historyProjects,setHistoryProjects]=useState<HistoryProject[]>([])
  const [nextCursor,setNextCursor]=useState<string|null>(null)
  const [loading,setLoading]=useState(false)
  const [error,setError]=useState('')
  const [query,setQuery]=useState('')
  const [scope,setScope]=useState<'all'|'user'|'assistant'|'metadata'>('all')
  const [backend,setBackend]=useState('')
  const [project,setProject]=useState(initialProjectId)
  const [state,setState]=useState('')
  const [external,setExternal]=useState('')
  const [dateFrom,setDateFrom]=useState('')
  const [dateTo,setDateTo]=useState('')
  const [timeBasis,setTimeBasis]=useState<'started'|'last_message'>('started')
  const [transcript,setTranscript]=useState<Transcript|null>(null)
  const [confirmDelete,setConfirmDelete]=useState<string|null>(null)
  const [activeMatch,setActiveMatch]=useState(0)
  const [lineage,setLineage]=useState<LineageEdge[]>([])
  const [job,setJob]=useState<BackfillJob|null>(null)
  const requestSequence=useRef(0)
  const transcriptBody=useRef<HTMLDivElement>(null)
  const panel=useRef<HTMLElement>(null)
  useModalFocus(panel,onClose)

  const parameters=(cursor?:string)=>{
    const value=new URLSearchParams({limit:'50',scope})
    if(query)value.set('q',query)
    if(backend)value.set('backend',backend)
    if(project)value.set('project',project)
    if(state)value.set('state',state)
    if(external)value.set('external',external)
    if(dateFrom)value.set('date_from',String(new Date(dateFrom).getTime()/1000))
    if(dateTo)value.set('date_to',String(new Date(dateTo).getTime()/1000))
    value.set('time_basis',timeBasis)
    if(cursor)value.set('cursor',cursor)
    return value
  }

  const load=async(append=false)=>{
    const sequence=++requestSequence.current
    setLoading(true);setError('')
    try{
      const page=await api<HistoryPage>('GET',`/api/history?${parameters(append?nextCursor||undefined:undefined)}`)
      if(sequence!==requestSequence.current)return
      setItems(current=>append?[...current,...page.items]:page.items)
      setNextCursor(page.next_cursor)
    }catch(cause){if(sequence===requestSequence.current)setError(cause instanceof Error?cause.message:String(cause))}
    finally{if(sequence===requestSequence.current)setLoading(false)}
  }

  const refreshProjects=()=>api<{items:HistoryProject[]}>('GET','/api/history/projects').then(result=>setHistoryProjects(result.items)).catch(()=>undefined)

  useEffect(()=>{void refreshProjects()},[])
  useEffect(()=>{
    if(!project||project==='__ungrouped__'){setJob(null);return}
    void api<{items:BackfillJob[]}>('GET',`/api/history/backfills?project_id=${encodeURIComponent(project)}`).then(result=>setJob(result.items[0]||null)).catch(()=>undefined)
  },[project])
  useEffect(()=>{
    const timer=window.setTimeout(()=>void load(false),query?220:0)
    return()=>window.clearTimeout(timer)
  },[query,scope,backend,project,state,external,dateFrom,dateTo,timeBasis])

  useEffect(()=>{
    if(!job||!['queued','running'].includes(job.status))return
    const timer=window.setInterval(()=>{
      void api<{job:BackfillJob}>('GET',`/api/history/backfills/${job.id}`).then(result=>{
        setJob(result.job)
        if(!['queued','running'].includes(result.job.status)){void load(false);void refreshProjects()}
      }).catch(cause=>setError(cause instanceof Error?cause.message:String(cause)))
    },700)
    return()=>window.clearInterval(timer)
  },[job?.id,job?.status])

  useEffect(()=>{
    const match=transcript?.matches[activeMatch]
    if(!match)return
    transcriptBody.current?.querySelector<HTMLElement>(`[data-message-ordinal="${match.ordinal}"]`)?.scrollIntoView({block:'center',behavior:'smooth'})
  },[transcript?.entry.id,activeMatch])

  const grouped=useMemo(()=>{
    const groups=new Map<string|null,{label:string;entries:HistoryEntry[]}>()
    for(const entry of items){
      const key=entry.project_id||null
      const known=historyProjects.find(item=>item.project_id===key)
      const configured=projects.find(item=>item.id===key)
      const group=groups.get(key)||{label:known?.label||configured?.name||entry.project_label||'Unassigned',entries:[]}
      group.entries.push(entry);groups.set(key,group)
    }
    return [...groups.entries()]
  },[items,historyProjects,projects])

  const view=async(entry:HistoryEntry)=>{
    setError('');setActiveMatch(0);setLineage([])
    try{
      const search=new URLSearchParams({scope});if(query)search.set('q',query)
      setTranscript(await api<Transcript>('GET',`/api/history/${entry.id}/transcript?${search}`))
      void api<{items:LineageEdge[]}>('GET',`/api/lineage?run_id=${encodeURIComponent(entry.id)}`).then(result=>setLineage(result.items)).catch(()=>setLineage([]))
    }catch(cause){setTranscript(null);setError(cause instanceof Error?cause.message:String(cause))}
  }

  const remove=async(entry:HistoryEntry)=>{
    if(confirmDelete!==entry.id){setConfirmDelete(entry.id);return}
    await api('DELETE',`/api/history/${entry.id}`)
    setItems(current=>current.filter(item=>item.id!==entry.id));setConfirmDelete(null)
    if(transcript?.entry.id===entry.id)setTranscript(null)
  }

  const startBackfill=async()=>{
    if(!project||project==='__ungrouped__')return
    setError('')
    try{setJob((await api<{job:BackfillJob}>('POST','/api/history/backfills',{project_id:project})).job)}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }

  const cancelBackfill=()=>job&&api<{job:BackfillJob}>('DELETE',`/api/history/backfills/${job.id}`).then(result=>setJob(result.job)).catch(cause=>setError(cause instanceof Error?cause.message:String(cause)))
  const moveMatch=(offset:number)=>{if(!transcript?.matches.length)return;setActiveMatch(current=>(current+offset+transcript.matches.length)%transcript.matches.length)}
  const activeOrdinal=transcript?.matches[activeMatch]?.ordinal

  const scopeProject=projects.find(item=>item.id===project)
  return <div class="modal-layer history-layer" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section ref={panel} class="modal history-modal" role="dialog" aria-modal="true" aria-label="Agent session history">
    <div class="modal-heading"><div><span>SESSION HISTORY</span><h2>{scopeProject?scopeProject.name:'All projects'}</h2></div><button type="button" aria-label="Close session history" onClick={onClose}>×</button></div>
    <section class="history-workspace" aria-label="Agent session history">
    <div class="history-body">
      <aside>
        <div class="history-search">
          <div class="history-query-row"><input aria-label="Search session history" placeholder="Search prompts, replies, titles, and paths…" value={query} onInput={event=>setQuery(event.currentTarget.value)}/><select aria-label="Search within" value={scope} onChange={event=>setScope(event.currentTarget.value as typeof scope)}><option value="all">All content</option><option value="user">User prompts</option><option value="assistant">Agent replies</option><option value="metadata">Titles + paths</option></select></div>
          <select aria-label="Filter history project" value={project} onChange={event=>{setProject(event.currentTarget.value);setTranscript(null)}}><option value="">All projects</option>{projects.map(item=><option value={item.id}>{item.name}</option>)}<option value="__ungrouped__">Unassigned</option></select>
          <select aria-label="Filter history provider" value={backend} onChange={event=>setBackend(event.currentTarget.value)}><option value="">Claude + Codex</option><option value="claude">Claude</option><option value="codex">Codex</option></select>
          <select aria-label="Filter history state" value={state} onChange={event=>setState(event.currentTarget.value)}><option value="">All states</option><option value="idle">Completed</option><option value="exited">Exited</option><option value="crashed">Crashed</option></select>
          <select aria-label="Filter history origin" value={external} onChange={event=>setExternal(event.currentTarget.value)}><option value="">Mux + external</option><option value="false">Mux sessions</option><option value="true">External sessions</option></select>
          <select aria-label="Filter history time field" value={timeBasis} onChange={event=>setTimeBasis(event.currentTarget.value as typeof timeBasis)}><option value="started">Time: session started</option><option value="last_message">Time: last message</option></select>
          <label>from<input type="datetime-local" value={dateFrom} onChange={event=>setDateFrom(event.currentTarget.value)}/></label><label>to<input type="datetime-local" value={dateTo} onChange={event=>setDateTo(event.currentTarget.value)}/></label>
          <div class="history-backfill-control"><button disabled={!project||project==='__ungrouped__'||!!job&&['queued','running'].includes(job.status)} onClick={()=>void startBackfill()}>Scan historical sessions</button><small>{project&&project!=='__ungrouped__'?'Discovers and indexes all native history for this Project.':'Select one Project to scan its complete native history.'}</small></div>
          {job&&<div class={`history-backfill-status ${job.status}`}><div><strong>{job.phase}</strong><span>{job.total?`${job.processed}/${job.total}`:`${job.scanned} scanned`}</span></div>{['queued','running'].includes(job.status)&&<progress max={Math.max(1,job.total)} value={job.processed}/>}<small>{job.discovered} discovered · {job.indexed} indexed ({job.indexed_messages} messages) · {job.unchanged} unchanged · {job.unreadable} unreadable{job.ambiguous?` · ${job.ambiguous} ambiguous`:''}</small>{job.error&&<small class="error">{job.error}</small>}{['queued','running'].includes(job.status)&&<button onClick={()=>void cancelBackfill()}>Cancel scan</button>}</div>}
        </div>
        {error&&<div class="history-inline-state error" role="alert">{error}</div>}
        {!loading&&!error&&!items.length&&<div class="history-inline-state">No Claude or Codex history matches these filters.</div>}
        {grouped.map(([id,group])=><section class="history-project" key={id||'ungrouped'}><div class="history-project-heading"><strong>▾ {group.label}</strong><span>{group.entries.length}</span></div>{group.entries.map(entry=><article class={`history-row ${transcript?.entry.id===entry.id?'active':''}`}><button onClick={()=>void view(entry)}><strong>[{entry.backend}] {historyName(entry)}</strong><span class="history-times"><time dateTime={timestampIso(historyStart(entry))}>Started {timestampLabel(historyStart(entry))}</time><time dateTime={timestampIso(entry.last_message_at)}>Last {entry.last_message_role==='assistant'?'agent':entry.last_message_role==='user'?'you':'message'} · {timestampLabel(entry.last_message_at)}</time></span><small>{entry.final_state||entry.exit_reason||'indexed'}{entry.external?' · external':''}{entry.compaction_count?` · compacted ${entry.compaction_count}×`:''}{formatBytes(transcriptBytes(entry))?` · ${formatBytes(transcriptBytes(entry))}`:''}</small>{entry.matches?.map(match=><span class={`history-match ${match.role}`}><b>{match.role==='assistant'?'agent':'you'}</b>{match.excerpt}</span>)}</button><button class={confirmDelete===entry.id?'danger confirming':'danger'} aria-label={`Delete history index entry ${historyName(entry)}`} onClick={()=>void remove(entry)}>{confirmDelete===entry.id?'✓':'×'}</button></article>)}</section>)}
        {loading&&<div class="history-inline-state">Searching agent history…</div>}
        {nextCursor&&!loading&&<button class="history-load-more" onClick={()=>void load(true)}>Load more</button>}
      </aside>
      <main>{transcript?<><div class="transcript-heading"><button class="history-back" onClick={()=>setTranscript(null)}>← Results</button><div><h3>[{transcript.entry.backend}] {historyName(transcript.entry)}</h3><span>{transcript.entry.project_label||'Unassigned'} · {transcript.entry.cwd}</span><small>Started {timestampLabel(historyStart(transcript.entry))} · last {transcript.entry.last_message_role==='assistant'?'agent':transcript.entry.last_message_role==='user'?'you':'message'} {timestampLabel(transcript.entry.last_message_at)}</small><small>{transcript.entry.exit_reason||transcript.entry.final_state||'indexed'} · {transcript.entry.model||'model unavailable'} · {transcript.entry.external?'external':'mux session'}</small><small>{transcript.entry.context_window?`context final ${Math.round((transcript.entry.final_context_pct||0)*100)}% · peak ${Math.round((transcript.entry.peak_context_pct||0)*100)}% · ${transcript.entry.measurement_source||'native observation'}`:'context unavailable'} · tokens in {transcript.entry.tokens_in||0} / out {transcript.entry.tokens_out||0}{formatBytes(transcriptBytes(transcript.entry))?` · transcript ${formatBytes(transcriptBytes(transcript.entry))}`:''}</small><small>{transcript.entry.compaction_count?`explicit compactions ${transcript.entry.compaction_count} · ${transcript.entry.compaction_capability||'native evidence'} · confidence ${transcript.entry.compaction_confidence||'unknown'}`:'compaction count unavailable — token drops are not treated as compaction evidence'}</small></div><button class="primary" onClick={()=>void onResume(transcript.entry)}>Resume as new</button></div>
        <div class="transcript-actions">{transcript.entry.project_id&&transcript.entry.note_id&&<button onClick={()=>onSessionNote(transcript.entry)}>Session note</button>}<button onClick={()=>void onHandoff(transcript.entry)}>Export handoff</button><button class="primary" onClick={()=>void onSecondOpinion(transcript.entry)}>Review with {transcript.entry.backend==='claude'?'Codex':'Claude'}</button>{transcript.matches.length>0&&<div class="transcript-match-nav"><button aria-label="Previous search match" onClick={()=>moveMatch(-1)}>↑</button><span>{activeMatch+1}/{transcript.matches.length}</span><button aria-label="Next search match" onClick={()=>moveMatch(1)}>↓</button></div>}</div>
        {lineage.length>0&&<section class="transcript-lineage"><h4>Work lineage</h4>{lineage.map(edge=><article><strong>{edge.relation}</strong><span>{edge.parent_run_id} → {edge.child_run_id}</span><small>{new Date(edge.created_at*1000).toLocaleString()}</small></article>)}</section>}{transcript.annotations.length>0&&<section class="transcript-annotations"><h4>Run notes</h4>{transcript.annotations.map(item=><details><summary>{item.tag} · {item.content}</summary><small>{new Date(item.created_at*1000).toLocaleString()} · {item.provenance} · model::{item.resolved_model||'deterministic'} · confidence::{item.confidence??'—'} · cost::{money.format(item.cost_usd||0)}</small></details>)}</section>}
        <div class="messages" ref={transcriptBody}>{transcript.messages.length?transcript.messages.map((message,ordinal)=><article data-message-ordinal={ordinal} class={`${message.role} ${transcript.matches.some(match=>match.ordinal===ordinal)?'search-match-message':''} ${activeOrdinal===ordinal?'active-search-match':''}`}><header><span>{message.role}</span><time dateTime={timestampIso(message.ts)}>{timestampLabel(message.ts)}</time></header>{message.content.map(block=>block.type==='text'?<p>{block.text}</p>:<pre>{block.type==='tool_use'?`${block.name}\n${JSON.stringify(block.input,null,2)}`:block.type}</pre>)}</article>):<div class="no-transcript">No native transcript is available for this session.</div>}</div></>:<div class="history-placeholder"><span>◷</span><strong>Select a session</strong><p>Search prompts and replies, then inspect the native transcript.</p></div>}</main>
    </div>
    </section>
    </section>
  </div>
}
