import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
import { withoutClipboardCapture } from './clipboardHistory'
import { useDismissLevel, useModalFocus } from './modalFocus'
import { copyPreparedText } from './terminalClipboard'
import { TranscriptToolCalls } from './TranscriptToolCalls'
import {
  transcriptClamped,
  transcriptSpeaker,
  transcriptTimestampIso,
  transcriptTimestampLabel,
  transcriptToolCallsFromBlocks,
  type TranscriptContentBlock,
} from './transcriptView'
import type { Project } from './types'
import { runDisplayName } from './sessionNames'
import { harnessDisplayName, promptDeliveryHarnesses, transcriptHarnesses } from './harnessRegistry'
import { ModelName } from './ModelName'
import { parseGitProvenance, provenanceRoleLabel, shortSha, type GitProvenance } from './gitWorktrees'

export type HistoryMatch={ordinal:number;role:'user'|'assistant';ts?:string;excerpt:string}
export type HistoryEntry = {
  id:string;native_id:string;backend:string;name:string;cwd:string
  spawned_at:number;exited_at?:number;exit_reason?:string;transcript_path?:string
  project_id?:string;project_label?:string;final_state?:string;external?:number
  project_scope_id?:string;repo_group_id?:string;project_root?:string
  context_window?:number;final_context_pct?:number;peak_context_pct?:number
  tokens_in?:number;tokens_out?:number;tokens_cache_read?:number;tokens_cache_write?:number;cost_usd?:number;provider?:string;provider_account_hashes?:Record<string,string>;model?:string;measurement_source?:string
  compaction_count?:number;last_compaction_at?:number;compaction_capability?:string;compaction_confidence?:string
  native_started_at?:number|null;last_message_at?:number|null;last_message_role?:'user'|'assistant'|null
  transcript_size?:number|null;time_summary_size?:number|null
  auto_named?:number;generated_title?:string;matches?:HistoryMatch[];match_count?:number
  // Present only while a live CLI process holds this conversation, which is what
  // makes Resume impossible: the CLI opens a conversation once and answers a
  // second opener by exiting. Read live by the daemon, never stored.
  held_by?:{kind:string;pid:number;job_id?:string;name?:string;detail:string}
}
type DerivedAnnotation={id:string;tag:string;content:string;provenance:string;resolved_model?:string;confidence?:number;cost_usd?:number;created_at:number}
type TranscriptMessage={role:'user'|'assistant';ts?:string;content:TranscriptContentBlock[]}
type ScanRecord={
  id:string;agent_run_id:string;t0:number;t1:number;work_phase?:string;intent?:string;summary?:string
  claim?:string;user_ask?:string;blocked_on?:string;novelty?:number;target?:string[]
}
type Transcript={entry:HistoryEntry;messages:TranscriptMessage[];annotations:DerivedAnnotation[];matches:HistoryMatch[];scan_records?:ScanRecord[]}
type LineageEdge={id:string;parent_run_id:string;child_run_id:string;relation:string;created_at:number}
type HistoryPage={items:HistoryEntry[];next_cursor:string|null}
type HistoryProject={project_id:string|null;label:string;root?:string;removed_at?:number;sessions:number;last_activity:number}
type BackfillJob={
  id:string;project_id:string;project_name:string;status:string;phase:string;scanned:number;total:number;processed:number
  discovered:number;indexed:number;indexed_messages:number;unchanged:number;ambiguous:number;unreadable:number
  error?:string;cancel_requested:boolean
}

type Props={
  projects:Project[]
  initialProjectId:string
  /** Open straight onto this conversation instead of the list. Empty means browse. */
  initialEntryId?:string
  onClose:()=>void
  onResume:(entry:HistoryEntry)=>void|Promise<void>
  onSecondOpinion:(entry:HistoryEntry)=>void|Promise<void>
  onHandoff:(entry:HistoryEntry)=>void|Promise<void>
}

const money=new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',maximumFractionDigits:4})
const COPIED_FLASH_MS=1200
const historyName=(entry:HistoryEntry)=>runDisplayName(entry)
const timestampDate=(value?:string|number|null)=>{
  if(value===undefined||value===null||value==='')return null
  const numeric=typeof value==='number'?value:/^\d+(?:\.\d+)?$/.test(value)?Number(value):null
  const date=new Date(numeric===null?value:numeric>10_000_000_000?numeric:numeric*1000)
  return Number.isNaN(date.getTime())?null:date
}
const timestampLabel=(value?:string|number|null)=>timestampDate(value)?.toLocaleString()||'timestamp unavailable'
const timestampIso=(value?:string|number|null)=>timestampDate(value)?.toISOString()
const historyStart=(entry:HistoryEntry)=>entry.native_started_at??(entry.external?null:entry.spawned_at)
const heldLabel=(entry:HistoryEntry)=>!entry.held_by?null:entry.held_by.kind==='bg'?'running as a background agent':'open in another CLI'
const transcriptBytes=(entry:HistoryEntry)=>entry.transcript_size??entry.time_summary_size??null
const providerIdentity=(entry:HistoryEntry)=>{
  const pins=Object.entries(entry.provider_account_hashes||{})
  if(!entry.provider&&!pins.length)return null
  const accounts=pins.map(([provider,hash])=>`${provider} account sha256 ${hash}`).join(' · ')
  return [entry.provider?`provider ${entry.provider}`:'',accounts].filter(Boolean).join(' · ')
}
const formatBytes=(bytes?:number|null)=>{
  if(bytes===undefined||bytes===null||bytes<=0)return null
  const units=['B','KB','MB','GB'];let value=bytes,unit=0
  while(value>=1024&&unit<units.length-1){value/=1024;unit++}
  return `${value>=100||unit===0?Math.round(value):value.toFixed(1)} ${units[unit]}`
}
const historyMessageText=(message:TranscriptMessage)=>message.content.filter(block=>block.type==='text').map(block=>block.text||'').filter(Boolean).join('\n\n')

export function HistoryBrowser({projects,initialProjectId,initialEntryId,onClose,onResume,onSecondOpinion,onHandoff}:Props){
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
  const [expandedMessages,setExpandedMessages]=useState<Set<number>>(()=>new Set())
  const [copiedMessage,setCopiedMessage]=useState<number|null>(null)
  const [showToolCalls,setShowToolCalls]=useState(false)
  const [lineage,setLineage]=useState<LineageEdge[]>([])
  const [gitProvenance,setGitProvenance]=useState<GitProvenance[]>([])
  const [job,setJob]=useState<BackfillJob|null>(null)
  const requestSequence=useRef(0)
  const transcriptBody=useRef<HTMLDivElement>(null)
  const manualCopyArea=useRef<HTMLTextAreaElement>(null)
  const panel=useRef<HTMLElement>(null)
  useModalFocus(panel,onClose)
  // Reading a transcript is a level of its own, so back returns to the results that found
  // it instead of discarding the search. Registered after the modal and therefore above
  // it: `← Results` and back now agree, where Escape used to close the whole browser.
  useDismissLevel(()=>setTranscript(null),!!transcript,'history-transcript')
  useDismissLevel(()=>setConfirmDelete(null),!!confirmDelete,'history-delete-confirm')

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
      const baseLabel=known?.label||configured?.name||entry.project_label||'Unassigned'
      const group=groups.get(key)||{label:known?.removed_at?`${baseLabel} (removed)`:baseLabel,entries:[]}
      group.entries.push(entry);groups.set(key,group)
    }
    return [...groups.entries()]
  },[items,historyProjects,projects])

  const view=async(entry:HistoryEntry)=>{
    setError('');setActiveMatch(0);setLineage([]);setGitProvenance([]);setExpandedMessages(new Set());setCopiedMessage(null)
    try{
      const search=new URLSearchParams({scope});if(query)search.set('q',query)
      const provenance=entry.project_id
        ? api<unknown>('GET',`/api/git/provenance?project_id=${encodeURIComponent(entry.project_id)}&agent_run_id=${encodeURIComponent(entry.id)}&limit=500`).then(parseGitProvenance).catch(()=>[])
        : Promise.resolve([])
      const [nextTranscript,commits]=await Promise.all([
        api<Transcript>('GET',`/api/history/${entry.id}/transcript?${search}`),
        provenance,
      ])
      setTranscript(nextTranscript);setGitProvenance(commits)
      void api<{items:LineageEdge[]}>('GET',`/api/lineage?run_id=${encodeURIComponent(entry.id)}`).then(result=>setLineage(result.items)).catch(()=>setLineage([]))
    }catch(cause){setTranscript(null);setError(cause instanceof Error?cause.message:String(cause))}
  }

  /** Open one conversation by id, for a caller that named a session instead of browsing.
   *
   *  The transcript response carries the row, so nothing about the entry has to be known
   *  in advance. Two misses are expected rather than exceptional and are reported as
   *  themselves: the row can have been deleted, and a harness that keeps no readable
   *  transcript answers 409. Either way the browser stays open on its list, because the
   *  caller's intent - find this session - is still servable by hand. */
  const viewById=async(historyId:string)=>{
    setError('');setActiveMatch(0);setLineage([]);setGitProvenance([]);setExpandedMessages(new Set());setCopiedMessage(null)
    try{
      const nextTranscript=await api<Transcript>('GET',`/api/history/${historyId}/transcript`)
      setTranscript(nextTranscript)
      const projectId=nextTranscript.entry.project_id
      if(projectId){
        void api<unknown>('GET',`/api/git/provenance?project_id=${encodeURIComponent(projectId)}&agent_run_id=${encodeURIComponent(nextTranscript.entry.id)}&limit=500`)
          .then(raw=>setGitProvenance(parseGitProvenance(raw))).catch(()=>setGitProvenance([]))
      }
      void api<{items:LineageEdge[]}>('GET',`/api/lineage?run_id=${encodeURIComponent(nextTranscript.entry.id)}`).then(result=>setLineage(result.items)).catch(()=>setLineage([]))
    }catch(cause){
      const error=cause as ApiError
      setTranscript(null)
      setError(error?.status===404
        ? 'That session has no History row any more, so its conversation cannot be opened. Search below for it by name.'
        : error?.detail?.code==='transcript_unavailable'
          ? 'That session kept no readable transcript, so there is no conversation to open.'
          : cause instanceof Error?cause.message:String(cause))
    }
  }

  // A deep link is the reason this overlay opened, so it runs once per requested id and
  // never re-fires when the list underneath reloads.
  useEffect(()=>{if(initialEntryId)void viewById(initialEntryId)},[initialEntryId])

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
  const toggleMessage=(ordinal:number)=>setExpandedMessages(current=>{const next=new Set(current);if(next.has(ordinal))next.delete(ordinal);else next.add(ordinal);return next})
  const copyMessage=async(message:TranscriptMessage,ordinal:number)=>{
    const text=historyMessageText(message)
    if(manualCopyArea.current)manualCopyArea.current.value=text
    const ok=await withoutClipboardCapture(()=>copyPreparedText(text,manualCopyArea.current))
    if(!ok){setError('The browser refused the copy. Select the message and copy it manually.');return}
    setError('');setCopiedMessage(ordinal)
    window.setTimeout(()=>setCopiedMessage(current=>current===ordinal?null:current),COPIED_FLASH_MS)
  }
  const activeOrdinal=transcript?.matches[activeMatch]?.ordinal
  const historyRows=(transcript?.messages||[]).map((message,ordinal)=>({
    message,
    ordinal,
    text:historyMessageText(message),
    toolCalls:transcriptToolCallsFromBlocks(message.content,`history:${transcript?.entry.id||'unknown'}:${ordinal}`),
  }))
  const availableToolCalls=historyRows.reduce((total,row)=>total+row.toolCalls.length,0)
  const visibleHistoryRows=historyRows.filter(row=>row.text||(showToolCalls&&row.toolCalls.length))

  const scopeProject=projects.find(item=>item.id===project)
  const historyHarnesses=transcriptHarnesses()
  const reviewTarget=(source:string)=>promptDeliveryHarnesses().find(harness=>harness.name!==source)
  return <div class="modal-layer history-layer" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section ref={panel} class="modal history-modal" role="dialog" aria-modal="true" aria-label="Agent session history">
    <div class="modal-heading"><div><span>SESSION HISTORY</span><h2>{scopeProject?scopeProject.name:'All projects'}</h2></div><button type="button" aria-label="Close session history" onClick={onClose}>×</button></div>
    <section class="history-workspace" aria-label="Agent session history">
    <div class="history-body">
      <aside>
        <div class="history-search">
          <div class="history-query-row"><input aria-label="Search session history" placeholder="Search prompts, replies, titles, and paths…" value={query} onInput={event=>setQuery(event.currentTarget.value)}/><select aria-label="Search within" value={scope} onChange={event=>setScope(event.currentTarget.value as typeof scope)}><option value="all">All content</option><option value="user">User prompts</option><option value="assistant">Agent replies</option><option value="metadata">Titles + paths</option></select></div>
          <select aria-label="Filter history project" value={project} onChange={event=>{setProject(event.currentTarget.value);setTranscript(null)}}><option value="">All projects</option>{projects.map(item=><option value={item.id}>{item.name}</option>)}<option value="__ungrouped__">Unassigned</option></select>
          <select aria-label="Filter history provider" value={backend} onChange={event=>setBackend(event.currentTarget.value)}><option value="">All agents</option>{historyHarnesses.map(harness=><option value={harness.name}>{harness.display_name}</option>)}</select>
          <select aria-label="Filter history state" value={state} onChange={event=>setState(event.currentTarget.value)}><option value="">All states</option><option value="idle">Completed</option><option value="exited">Exited</option><option value="crashed">Crashed</option></select>
          <select aria-label="Filter history origin" value={external} onChange={event=>setExternal(event.currentTarget.value)}><option value="">Mux + external</option><option value="false">Mux sessions</option><option value="true">External sessions</option></select>
          <select aria-label="Filter history time field" value={timeBasis} onChange={event=>setTimeBasis(event.currentTarget.value as typeof timeBasis)}><option value="started">Time: session started</option><option value="last_message">Time: last message</option></select>
          <label>from<input type="datetime-local" value={dateFrom} onChange={event=>setDateFrom(event.currentTarget.value)}/></label><label>to<input type="datetime-local" value={dateTo} onChange={event=>setDateTo(event.currentTarget.value)}/></label>
          <div class="history-backfill-control"><button disabled={!project||project==='__ungrouped__'||!!job&&['queued','running'].includes(job.status)} onClick={()=>void startBackfill()}>Scan historical sessions</button><small>{project&&project!=='__ungrouped__'?'Discovers and indexes all native history for this Project.':'Select one Project to scan its complete native history.'}</small></div>
          {job&&<div class={`history-backfill-status ${job.status}`}><div><strong>{job.phase}</strong><span>{job.total?`${job.processed}/${job.total}`:`${job.scanned} scanned`}</span></div>{['queued','running'].includes(job.status)&&<progress max={Math.max(1,job.total)} value={job.processed}/>}<small>{job.discovered} discovered · {job.indexed} indexed ({job.indexed_messages} messages) · {job.unchanged} unchanged · {job.unreadable} unreadable{job.ambiguous?` · ${job.ambiguous} ambiguous`:''}</small>{job.error&&<small class="error">{job.error}</small>}{['queued','running'].includes(job.status)&&<button onClick={()=>void cancelBackfill()}>Cancel scan</button>}</div>}
        </div>
        {error&&<div class="history-inline-state error" role="alert">{error}</div>}
        {!loading&&!error&&!items.length&&<div class="history-inline-state">No agent history matches these filters.</div>}
        {grouped.map(([id,group])=><section class="history-project" key={id||'ungrouped'}><div class="history-project-heading"><strong>▾ {group.label}</strong><span>{group.entries.length}</span></div>{group.entries.map(entry=><article class={`history-row ${transcript?.entry.id===entry.id?'active':''}`}><button onClick={()=>void view(entry)}><strong>[{entry.backend}] {historyName(entry)}</strong><span class="history-times"><time dateTime={timestampIso(historyStart(entry))}>Started {timestampLabel(historyStart(entry))}</time><time dateTime={timestampIso(entry.last_message_at)}>Last {entry.last_message_role==='assistant'?'agent':entry.last_message_role==='user'?'you':'message'} · {timestampLabel(entry.last_message_at)}</time></span><small>{entry.final_state||entry.exit_reason||'indexed'}{heldLabel(entry)?` · ${heldLabel(entry)}`:''}{entry.external?' · external':''}{entry.compaction_count?` · compacted ${entry.compaction_count}×`:''}{formatBytes(transcriptBytes(entry))?` · ${formatBytes(transcriptBytes(entry))}`:''}</small>{entry.matches?.map(match=><span class={`history-match ${match.role}`}><b>{match.role==='assistant'?'agent':'you'}</b>{match.excerpt}</span>)}</button><button class={confirmDelete===entry.id?'danger confirming':'danger'} aria-label={`Delete history index entry ${historyName(entry)}`} onClick={()=>void remove(entry)}>{confirmDelete===entry.id?'✓':'×'}</button></article>)}</section>)}
        {loading&&<div class="history-inline-state">Searching agent history…</div>}
        {nextCursor&&!loading&&<button class="history-load-more" onClick={()=>void load(true)}>Load more</button>}
      </aside>
      <main>{transcript?<><div class="transcript-heading"><button class="history-back" onClick={()=>setTranscript(null)}>← Results</button><div><h3>[{transcript.entry.backend}] {historyName(transcript.entry)}</h3><span>{transcript.entry.project_label||'Unassigned'} · {transcript.entry.cwd}</span></div></div>
        <div class="transcript-actions">{transcript.entry.held_by?<span class="transcript-held" role="note" title={transcript.entry.held_by.detail}>Cannot resume — {heldLabel(transcript.entry)}</span>:<button class="primary" onClick={()=>void onResume(transcript.entry)}>Resume as new</button>}<button onClick={()=>void onHandoff(transcript.entry)}>Export handoff</button>{reviewTarget(transcript.entry.backend)&&<button onClick={()=>void onSecondOpinion(transcript.entry)}>Review with {harnessDisplayName(reviewTarget(transcript.entry.backend)!.name)}</button>}<label class="transcript-tool-toggle"><input type="checkbox" checked={showToolCalls} disabled={!availableToolCalls} onChange={event=>setShowToolCalls(event.currentTarget.checked)}/>Tool calls</label>{transcript.matches.length>0&&<div class="transcript-match-nav"><button aria-label="Previous search match" onClick={()=>moveMatch(-1)}>↑</button><span>{activeMatch+1}/{transcript.matches.length}</span><button aria-label="Next search match" onClick={()=>moveMatch(1)}>↓</button></div>}</div>
        <div class="transcript-metadata"><small>Started {timestampLabel(historyStart(transcript.entry))} · last {transcript.entry.last_message_role==='assistant'?'agent':transcript.entry.last_message_role==='user'?'you':'message'} {timestampLabel(transcript.entry.last_message_at)}</small><small>{transcript.entry.exit_reason||transcript.entry.final_state||'indexed'} · <ModelName model={transcript.entry.model}/> · {transcript.entry.external?'external':'mux session'}</small>{providerIdentity(transcript.entry)&&<small>{providerIdentity(transcript.entry)}</small>}<small>{transcript.entry.context_window?`context final ${Math.round((transcript.entry.final_context_pct||0)*100)}% · peak ${Math.round((transcript.entry.peak_context_pct||0)*100)}% · ${transcript.entry.measurement_source||'native observation'}`:'context unavailable'} · tokens in {transcript.entry.tokens_in||0} / out {transcript.entry.tokens_out||0} · cache read {transcript.entry.tokens_cache_read||0} / write {transcript.entry.tokens_cache_write||0} · cost {money.format(transcript.entry.cost_usd||0)}{formatBytes(transcriptBytes(transcript.entry))?` · transcript ${formatBytes(transcriptBytes(transcript.entry))}`:''}</small><small>{transcript.entry.compaction_count?`explicit compactions ${transcript.entry.compaction_count} · ${transcript.entry.compaction_capability||'native evidence'} · confidence ${transcript.entry.compaction_confidence||'unknown'}`:'compaction count unavailable - token drops are not treated as compaction evidence'}</small></div>
        {gitProvenance.length>0&&<section class="transcript-lineage"><h4>Commits from this run</h4>{gitProvenance.map(item=><article key={item.id}><strong>{shortSha(item.commitOid)} · {item.subject||'subject unavailable'}</strong><span>{provenanceRoleLabel(item)} · {item.confidence}</span><small>{new Date(item.observedAt*1000).toLocaleString()}</small></article>)}</section>}{lineage.length>0&&<section class="transcript-lineage"><h4>Work lineage</h4>{lineage.map(edge=><article><strong>{edge.relation}</strong><span>{edge.parent_run_id} → {edge.child_run_id}</span><small>{new Date(edge.created_at*1000).toLocaleString()}</small></article>)}</section>}{transcript.annotations.length>0&&<section class="transcript-annotations"><h4>Run notes</h4>{transcript.annotations.map(item=><details><summary>{item.tag} · {item.content}</summary><small>{new Date(item.created_at*1000).toLocaleString()} · {item.provenance} · model::<ModelName model={item.resolved_model} fallback="deterministic"/> · confidence::{item.confidence??'—'} · cost::{money.format(item.cost_usd||0)}</small></details>)}</section>}{(transcript.scan_records?.length??0)>0&&<section class="transcript-annotations"><h4>Behavioral timeline</h4>{transcript.scan_records!.map(item=><details key={item.id}><summary>{item.work_phase||'unknown'} · {item.summary||item.intent||'—'}</summary><small>{new Date(item.t0*1000).toLocaleString()}{item.blocked_on&&item.blocked_on!=='none'?` · blocked on ${item.blocked_on}`:''}{item.claim?` · claims: ${item.claim}`:''}{(item.target?.length??0)>0?` · ${item.target!.slice(0,4).join(', ')}`:''}</small></details>)}</section>}
        <div class="messages" ref={transcriptBody}>{visibleHistoryRows.length?visibleHistoryRows.map(({message,ordinal,text,toolCalls})=>{
          const matched=transcript.matches.some(match=>match.ordinal===ordinal)
          const foldable=transcriptClamped(text)
          const expanded=matched||expandedMessages.has(ordinal)
          const speaker=transcriptSpeaker(message.role)
          const stamp=transcriptTimestampLabel(message.ts)
          return <article data-message-ordinal={ordinal} class={`transcript-message ${message.role} ${matched?'search-match-message':''} ${activeOrdinal===ordinal?'active-search-match':''}`}>
            {text&&<div class="transcript-copy-anchor"><button class={copiedMessage===ordinal?'transcript-copy copied':'transcript-copy'} aria-label={`Copy this ${speaker==='you'?'message':'reply'}`} title={`Copy this ${speaker==='you'?'message':'reply'}`} onClick={()=>void copyMessage(message,ordinal)}>{copiedMessage===ordinal?'Copied':'Copy'}</button></div>}
            <header><span class="transcript-speaker">{speaker}</span>{stamp&&<time dateTime={transcriptTimestampIso(message.ts)}>{stamp}</time>}</header>
            {text&&<div class={`history-transcript-content ${foldable&&!expanded?'clamped':''}`}><p>{text}</p></div>}
            {foldable&&!matched&&<button class="transcript-expand" onClick={()=>toggleMessage(ordinal)}>{expanded?'Show less':'Show more'}</button>}
            {showToolCalls&&<TranscriptToolCalls calls={toolCalls}/>}
          </article>
        }):<div class="no-transcript">No native transcript is available for this session.</div>}</div><textarea ref={manualCopyArea} class="transcript-manual" readOnly aria-hidden="true" tabIndex={-1}/></>:<div class="history-placeholder"><span>◷</span><strong>Select a session</strong><p>Search prompts and replies, then inspect the native transcript.</p></div>}</main>
    </div>
    </section>
    </section>
  </div>
}
