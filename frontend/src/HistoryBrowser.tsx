import type { ComponentChildren } from 'preact'
import { memo } from 'preact/compat'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
import { withoutClipboardCapture } from './clipboardHistory'
import { Dropdown } from './Dropdown'
import { projectDropdownOptions } from './projectOptions'
import { useDismissLevel, useModalFocus } from './modalFocus'
import { copyPreparedText } from './terminalClipboard'
import { TranscriptToolCalls } from './TranscriptToolCalls'
import {
  transcriptClamped,
  transcriptSpeaker,
  transcriptTimestampIso,
  transcriptTimestampLabel,
  type TranscriptToolCall,
} from './transcriptView'
import type { Project } from './types'
import { runDisplayName } from './sessionNames'
import { transcriptHarnesses } from './harnessRegistry'
import {
  commitsSummary, countLabel, defaultHistorySections, historyKeyStats, sectionKeysVisible, speakerLabel, splitRecentScans,
  type HistorySectionKey, type HistorySectionState,
} from './historyDetail'
import { groupHistoryFeed, historySearchDelay } from './historyFeed'
import {
  addSpan, clipSpans, EMPTY_HISTORY_WINDOW, HISTORY_PAGE_SIZE, historyEdgeQuery, historyReaderRows, historyToolEntries,
  historyWindowFromPage, historyWindowHas, historyWindowSpan, mergeHistoryPage, missingSpans, toolCallsQuery,
  type HistoryIndexedMessage, type HistoryMessagesPage, type HistoryMessageWindow, type HistoryOrdinalSpan,
  type HistoryPageEdge, type HistoryToolCallsPage, type HistoryToolEntry,
} from './historyMessages'
import { ModelName } from './ModelName'
import { parseGitProvenance, provenanceRoleLabel, shortSha, type GitProvenance } from './gitWorktrees'
import { lineageCounterpart, lineageCutLabel, lineageDirection, lineageEndpointLabel, lineageVerb, orderedLineage, type LineageEdge } from './lineageView'

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
type ScanRecord={
  id:string;agent_run_id:string;t0:number;t1:number;work_phase?:string;intent?:string;summary?:string
  claim?:string;user_ask?:string;blocked_on?:string;novelty?:number;target?:string[]
}
/* What the daemon can say about a conversation's indexed copy without parsing it
   (`GET /api/history/{id}/detail`). `indexing` means a background reindex is running
   because the index is missing or behind the file; the reader shows what is indexed and
   asks again until it is not. `abandoned_messages` counts turns this conversation branched
   away from - retries, rewinds - which are not in the index because they are not what the
   conversation says; the count is here so a retried run does not read as one with pieces
   cut out. `null` is "not known yet", for an index written before the count existed. */
type TranscriptIndexState={
  available:boolean;indexed:boolean;current:boolean;indexing:boolean;message_count:number
  abandoned_messages:number|null;tool_calls:number|null;indexed_at:number|null
}
type HistoryDetailAnswer={entry:HistoryEntry;transcript:TranscriptIndexState}
type RunNotes={annotations:DerivedAnnotation[];scan_records:ScanRecord[]}
/** `abandoned` marks a search the daemon dropped because this client had already
 *  disconnected; its `items` are empty and mean nothing about the archive. */
type HistoryPage={items:HistoryEntry[];next_cursor:string|null;abandoned?:boolean}
type HistoryProject={project_id:string|null;label:string;root?:string;removed_at?:number;sessions:number;last_activity:number}
type BackfillJob={
  id:string;project_id:string;project_name:string;status:string;phase:string;scanned:number;total:number;processed:number
  discovered:number;indexed:number;indexed_messages:number;unchanged:number;ambiguous:number;unreadable:number
  error?:string;cancel_requested:boolean
}
type SearchScope='all'|'user'|'assistant'|'metadata'
/** The conversation being read. `entry` is the list row it was opened from, which is
 *  everything the header and Resume need, so they draw on the click; a conversation
 *  opened by id has no row until `/detail` answers. `query` and `scope` are the search
 *  that found it, which is what its matches are matches of. */
type Selection={id:string;entry?:HistoryEntry;query:string;scope:SearchScope}

type Props={
  projects:Project[]
  initialProjectId:string
  /** Open straight onto this conversation instead of the list. Empty means browse. */
  initialEntryId?:string
  onClose:()=>void
  onResume:(entry:HistoryEntry)=>void|Promise<void>
  /** Author a schedule that reopens this conversation later. Separate from `onResume`
   *  because it starts nothing now: it hands the conversation to the Schedule tab. */
  onScheduleResume:(entry:HistoryEntry)=>void
  onHandoff:(entry:HistoryEntry)=>void|Promise<void>
}

const money=new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',maximumFractionDigits:4})
const COPIED_FLASH_MS=1200
/** How soon an open conversation first asks whether its background reindex has finished,
 *  and the ceiling its backoff grows to. A first index of a long conversation is a parse
 *  of the whole file, so it can take seconds; nothing is drawn faster by asking faster. */
const INDEX_POLL_MS=800
const INDEX_POLL_MAX_MS=4000
/** How far from an edge of the loaded messages the next page is asked for, so a reader
 *  scrolling at an ordinal pace never reaches the edge before the page does. */
const PAGE_PREFETCH_MARGIN='600px 0px'
// One formatter for every local timestamp the browser draws. `Date.toLocaleString` built a
// new one per call, twice per results row and once per commit, note and timeline entry.
// These options are exactly the ones it defaults to, so no label changes.
const localStamp=new Intl.DateTimeFormat(undefined,{year:'numeric',month:'numeric',day:'numeric',hour:'numeric',minute:'numeric',second:'numeric'})
const historyName=(entry:HistoryEntry)=>runDisplayName(entry)
const timestampDate=(value?:string|number|null)=>{
  if(value===undefined||value===null||value==='')return null
  const numeric=typeof value==='number'?value:/^\d+(?:\.\d+)?$/.test(value)?Number(value):null
  const date=new Date(numeric===null?value:numeric>10_000_000_000?numeric:numeric*1000)
  return Number.isNaN(date.getTime())?null:date
}
const timestampLabel=(value?:string|number|null)=>{const date=timestampDate(value);return date?localStamp.format(date):'timestamp unavailable'}
const epochLabel=(seconds:number)=>localStamp.format(new Date(seconds*1000))
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
const errorText=(cause:unknown)=>cause instanceof Error?cause.message:String(cause)
const isAbort=(cause:unknown)=>cause instanceof Error&&cause.name==='AbortError'
const notFound=(cause:unknown)=>(cause as ApiError|undefined)?.status===404
const messageSelector=(ordinal:number)=>`[data-message-ordinal="${ordinal}"]`

type RowProps={entry:HistoryEntry;active:boolean;confirming:boolean;onOpen:(entry:HistoryEntry)=>void;onRemove:(entry:HistoryEntry)=>void}

/** One result. Memoized and keyed by id, so a page of results appended by "Load more", a
 *  delete confirmation, or a conversation opened beside the list re-renders the rows it
 *  changed rather than every row on every keystroke. */
const HistoryRow=memo(function HistoryRow({entry,active,confirming,onOpen,onRemove}:RowProps){
  const bytes=formatBytes(transcriptBytes(entry))
  const held=heldLabel(entry)
  return <article class={`history-row ${active?'active':''}`}>
    <button onClick={()=>onOpen(entry)}><strong>[{entry.backend}] {historyName(entry)}</strong><span class="history-times"><time dateTime={timestampIso(historyStart(entry))}>Started {timestampLabel(historyStart(entry))}</time><time dateTime={timestampIso(entry.last_message_at)}>Last {entry.last_message_role==='assistant'?'agent':entry.last_message_role==='user'?'you':'message'} · {timestampLabel(entry.last_message_at)}</time></span><small>{entry.final_state||entry.exit_reason||'indexed'}{held?` · ${held}`:''}{entry.external?' · external':''}{entry.compaction_count?` · compacted ${entry.compaction_count}×`:''}{bytes?` · ${bytes}`:''}</small>{entry.matches?.map(match=><span class={`history-match ${match.role}`} key={match.ordinal}><b>{match.role==='assistant'?'agent':'you'}</b>{match.excerpt}</span>)}</button>
    <button class={confirming?'danger confirming':'danger'} aria-label={`Delete history index entry ${historyName(entry)}`} onClick={()=>onRemove(entry)}>{confirming?'✓':'×'}</button>
  </article>
})

type ListProps={
  groups:[string|null,{label:string;entries:HistoryEntry[]}][]
  activeId:string;confirmDelete:string|null
  onOpen:(entry:HistoryEntry)=>void;onRemove:(entry:HistoryEntry)=>void
}

const HistoryList=memo(function HistoryList({groups,activeId,confirmDelete,onOpen,onRemove}:ListProps){
  return <>{groups.map(([id,group])=><section class="history-project" key={id||'ungrouped'}>
    <div class="history-project-heading"><strong>▾ {group.label}</strong><span>{group.entries.length}</span></div>
    {group.entries.map(entry=><HistoryRow key={entry.id} entry={entry} active={entry.id===activeId} confirming={confirmDelete===entry.id} onOpen={onOpen} onRemove={onRemove}/>)}
  </section>)}</>
})

type MessageProps={
  message:HistoryIndexedMessage;toolCalls:TranscriptToolCall[];showToolCalls:boolean
  matched:boolean;active:boolean;expanded:boolean;copied:boolean
  onToggle:(ordinal:number)=>void;onCopy:(message:HistoryIndexedMessage)=>void
}

/** One message of the conversation. Every prop is a primitive or an object the window keeps
 *  across pages, so a page landing beside a message, or another message expanding, does not
 *  re-render it. */
const HistoryMessage=memo(function HistoryMessage({message,toolCalls,showToolCalls,matched,active,expanded,copied,onToggle,onCopy}:MessageProps){
  const text=message.text
  const foldable=transcriptClamped(text)
  const open=matched||expanded
  const speaker=transcriptSpeaker(message.role)
  const stamp=transcriptTimestampLabel(message.ts)
  return <article data-message-ordinal={message.ordinal} class={`transcript-message ${message.role} ${matched?'search-match-message':''} ${active?'active-search-match':''}`}>
    {text&&<div class="transcript-copy-anchor"><button class={copied?'transcript-copy copied':'transcript-copy'} aria-label={`Copy this ${speaker==='you'?'message':'reply'}`} title={`Copy this ${speaker==='you'?'message':'reply'}`} onClick={()=>onCopy(message)}>{copied?'Copied':'Copy'}</button></div>}
    <header><span class="transcript-speaker">{speaker}</span>{stamp&&<time dateTime={transcriptTimestampIso(message.ts)}>{stamp}</time>}</header>
    {text&&<div class={`history-transcript-content ${foldable&&!open?'clamped':''}`}><p>{text}</p></div>}
    {foldable&&!matched&&<button class="transcript-expand" onClick={()=>onToggle(message.ordinal)}>{open?'Show less':'Show more'}</button>}
    {showToolCalls&&<TranscriptToolCalls calls={toolCalls}/>}
  </article>
})

type DetailProps={
  selection:Selection
  showToolCalls:boolean
  onShowToolCalls:(show:boolean)=>void
  onBack:()=>void
  /** Open another conversation by id, for a lineage link. */
  onOpenRun:(historyId:string)=>void
  /** The conversation has no row to open; the browser closes the reader and says why. */
  onOpenFailed:(historyId:string,cause:unknown)=>void
  onResume:(entry:HistoryEntry)=>void|Promise<void>
  onScheduleResume:(entry:HistoryEntry)=>void
  onHandoff:(entry:HistoryEntry)=>void|Promise<void>
}

/**
 * One conversation, read out of the message index a page at a time.
 *
 * Mounted per conversation (`key` is the id), so opening another one starts from nothing:
 * nothing of the previous conversation can be drawn under the new heading, and unmounting
 * aborts every request the previous one still had in flight.
 *
 * Nothing here waits on anything else. The header and Resume draw from the row the reader
 * clicked; `/detail` refreshes them and says what the index holds; messages, matches,
 * notes, lineage and commits each arrive on their own. It used to be one request that
 * parsed the whole transcript before any of it - Resume included - could draw.
 */
const HistoryDetail=memo(function HistoryDetail({selection,showToolCalls,onShowToolCalls,onBack,onOpenRun,onOpenFailed,onResume,onScheduleResume,onHandoff}:DetailProps){
  const historyId=selection.id
  const base=`/api/history/${encodeURIComponent(historyId)}`
  const [entry,setEntry]=useState<HistoryEntry|null>(selection.entry||null)
  const [index,setIndex]=useState<TranscriptIndexState|null>(null)
  const [messageWindow,setMessageWindow]=useState<HistoryMessageWindow>(EMPTY_HISTORY_WINDOW)
  const [windowLoading,setWindowLoading]=useState(true)
  const [edgeLoading,setEdgeLoading]=useState<Record<HistoryPageEdge,boolean>>({before:false,after:false})
  const [matches,setMatches]=useState<HistoryMatch[]>([])
  const [activeMatch,setActiveMatch]=useState(0)
  const [jumps,setJumps]=useState(0)
  const [notes,setNotes]=useState<RunNotes|null>(null)
  const [lineage,setLineage]=useState<LineageEdge[]>([])
  const [gitProvenance,setGitProvenance]=useState<GitProvenance[]>([])
  // Which bands are expanded, defaulted per conversation rather than watched on a media
  // query - see `historyDetail.ts` for why. A fresh mount is a fresh default.
  const [sections,setSections]=useState<HistorySectionState>(()=>defaultHistorySections())
  const [timelineExpanded,setTimelineExpanded]=useState(false)
  const [expandedMessages,setExpandedMessages]=useState<Set<number>>(()=>new Set())
  const [copiedMessage,setCopiedMessage]=useState<number|null>(null)
  const [tools,setTools]=useState<ReadonlyMap<number,HistoryToolEntry>>(()=>new Map())
  const [notice,setNotice]=useState('')

  const lifetime=useRef<AbortController|null>(null)
  lifetime.current??=new AbortController()
  const signal=lifetime.current.signal
  useEffect(()=>()=>lifetime.current?.abort(),[])
  const get=<T,>(path:string)=>api<T>('GET',path,undefined,{signal})

  // The window's single writer keeps this current between a write and the render it
  // causes, so two pages landing in one tick each join the window the other left.
  const windowRef=useRef<HistoryMessageWindow>(EMPTY_HISTORY_WINDOW)
  // Bumped by every whole-window replacement; an edge page or a replacement that was asked
  // for under an older number is answering a question nobody is asking any more.
  const windowSequence=useRef(0)
  const edgeBusy=useRef<Record<HistoryPageEdge,boolean>>({before:false,after:false})
  const toolSpans=useRef<HistoryOrdinalSpan[]>([])
  const toolEpoch=useRef(0)
  const anchor=useRef<{ordinal:number;offset:number}|null>(null)
  const pendingScroll=useRef<number|null>(null)
  const body=useRef<HTMLDivElement>(null)
  const topEdge=useRef<HTMLDivElement>(null)
  const bottomEdge=useRef<HTMLDivElement>(null)
  const manualCopyArea=useRef<HTMLTextAreaElement>(null)

  /** The first message at least partly in view, and how far below the column's top it sits. */
  const visibleAnchor=()=>{
    const container=body.current
    if(!container)return null
    const top=container.getBoundingClientRect().top
    for(const node of container.querySelectorAll<HTMLElement>('[data-message-ordinal]')){
      const rect=node.getBoundingClientRect()
      if(rect.bottom>top)return {ordinal:Number(node.dataset.messageOrdinal),offset:rect.top-top}
    }
    return null
  }
  /* Keep the reader's place across a change above it. Pages land above the first visible
     message and the far edge of an outgrown window is cut from above it too; either moves
     what the reader is looking at unless it is put back. Done by hand rather than left to
     the browser's scroll anchoring, which Safari does not implement and which Chromium
     skips when the column is scrolled to its very top - exactly where a fling lands. The
     first capture before a render wins: it is the one that describes the DOM on screen. */
  const keepPlace=()=>{anchor.current??=visibleAnchor()}
  const commitWindow=(next:HistoryMessageWindow,options:{keep?:boolean;scrollTo?:number}={})=>{
    if(next===windowRef.current)return
    if(options.keep)keepPlace()
    if(options.scrollTo!==undefined)pendingScroll.current=options.scrollTo
    windowRef.current=next
    setMessageWindow(next)
  }

  const loadDetail=async(first:boolean):Promise<HistoryDetailAnswer|null>=>{
    try{
      const answer=await get<HistoryDetailAnswer>(`${base}/detail`)
      if(signal.aborted)return null
      setEntry(answer.entry);setIndex(answer.transcript)
      return answer
    }catch(cause){
      if(signal.aborted||isAbort(cause))return null
      if(notFound(cause)){onOpenFailed(historyId,cause);return null}
      if(first)setNotice(errorText(cause))
      return null
    }
  }

  const replaceWindow=async(query:string,options:{keep?:boolean;scrollTo?:number}={})=>{
    const sequence=++windowSequence.current
    setWindowLoading(true)
    try{
      const page=await get<HistoryMessagesPage>(`${base}/messages?${query}`)
      if(signal.aborted||sequence!==windowSequence.current)return
      commitWindow(historyWindowFromPage(page),options)
    }catch(cause){
      // A missing row is `/detail`'s to report, and it closes the reader when it does.
      if(!signal.aborted&&!isAbort(cause)&&!notFound(cause))setNotice(errorText(cause))
    }finally{
      if(!signal.aborted&&sequence===windowSequence.current)setWindowLoading(false)
    }
  }

  const extend=async(edge:HistoryPageEdge)=>{
    const held=windowRef.current
    const query=historyEdgeQuery(held,edge)
    if(!query||edgeBusy.current[edge])return
    const requestedFrom=edge==='before'?held.messages[0].ordinal:held.messages[held.messages.length-1].ordinal
    const sequence=windowSequence.current
    edgeBusy.current[edge]=true
    setEdgeLoading(current=>({...current,[edge]:true}))
    try{
      const page=await get<HistoryMessagesPage>(`${base}/messages?${query}`)
      if(signal.aborted||sequence!==windowSequence.current)return
      commitWindow(mergeHistoryPage(windowRef.current,page,edge,requestedFrom),{keep:true})
    }catch(cause){
      if(!signal.aborted&&!isAbort(cause))setNotice(errorText(cause))
    }finally{
      edgeBusy.current[edge]=false
      if(!signal.aborted)setEdgeLoading(current=>({...current,[edge]:false}))
    }
  }

  // Everything that does not depend on the row arrives independently, from the moment the
  // conversation is opened.
  useEffect(()=>{
    void loadDetail(true)
    void replaceWindow(`limit=${HISTORY_PAGE_SIZE}`)
    void get<RunNotes>(`${base}/notes`).then(result=>{if(!signal.aborted)setNotes(result)}).catch(()=>undefined)
    void get<{items:LineageEdge[]}>(`/api/lineage?run_id=${encodeURIComponent(historyId)}`).then(result=>{if(!signal.aborted)setLineage(result.items)}).catch(()=>undefined)
  },[])

  /** `jump` takes the reader to the first match, which is what opening a conversation from
   *  a search is for. A reload under a reindex passes `false`: the reader has scrolled
   *  somewhere by then, and a background job must not move them back to the first match. */
  const loadMatches=async(jump:boolean)=>{
    const query=selection.query.trim()
    if(!query||selection.scope==='metadata'){setMatches([]);return}
    try{
      const result=await get<{matches:HistoryMatch[]}>(`${base}/matches?${new URLSearchParams({q:query,scope:selection.scope})}`)
      if(signal.aborted)return
      setMatches(result.matches)
      if(jump){setActiveMatch(0);setJumps(count=>count+1)}
      else setActiveMatch(current=>Math.min(current,Math.max(0,result.matches.length-1)))
    }catch{/* Matches decorate the conversation; without them it still reads. */}
  }
  useEffect(()=>{void loadMatches(true)},[selection.query,selection.scope])

  // Commits are found by Project, which a conversation opened by id learns from `/detail`.
  const provenanceProject=entry?.project_id
  useEffect(()=>{
    if(!provenanceProject)return
    void get<unknown>(`/api/git/provenance?project_id=${encodeURIComponent(provenanceProject)}&agent_run_id=${encodeURIComponent(historyId)}&limit=500`)
      .then(raw=>{if(!signal.aborted)setGitProvenance(parseGitProvenance(raw))}).catch(()=>undefined)
  },[provenanceProject])

  /* A stale or missing index is rebuilt in the background (`HistoryIndex.transcript_index_
     state`), and the reader polls `/detail` until it says so. When it does, whatever was
     drawn from the old index is reloaded where the reader is - the conversation may have
     grown under them, and the matches and tool calls described the old one. */
  const indexing=!!index?.indexing
  const wasIndexing=useRef(false)
  useEffect(()=>{
    if(!indexing){
      if(!wasIndexing.current)return
      wasIndexing.current=false
      toolEpoch.current++;toolSpans.current=[];setTools(new Map())
      void loadMatches(false)
      const place=visibleAnchor()?.ordinal??windowRef.current.messages[0]?.ordinal
      void replaceWindow(place===undefined?`limit=${HISTORY_PAGE_SIZE}`:`around=${place}&limit=${HISTORY_PAGE_SIZE}`,{keep:true})
      return
    }
    wasIndexing.current=true
    let delay=INDEX_POLL_MS
    let timer=0
    const poll=async()=>{
      const answer=await loadDetail(false)
      // An answer that is no longer indexing ends this effect through its own state change.
      if(signal.aborted||answer&&!answer.transcript.indexing)return
      delay=Math.min(delay*1.5,INDEX_POLL_MAX_MS)
      timer=window.setTimeout(()=>void poll(),delay)
    }
    timer=window.setTimeout(()=>void poll(),delay)
    return()=>window.clearTimeout(timer)
  },[indexing])

  const activeOrdinal=matches[activeMatch]?.ordinal
  // A match is somewhere in a conversation the reader holds a window of. One inside the
  // window is scrolled to; one outside it replaces the window with the page around it.
  // Driven by an explicit count of jumps asked for (opening on a search, stepping through
  // matches) rather than by the active match changing, so nothing else moves the reader.
  useEffect(()=>{
    if(!jumps||activeOrdinal===undefined)return
    if(historyWindowHas(windowRef.current,activeOrdinal)){
      body.current?.querySelector<HTMLElement>(messageSelector(activeOrdinal))?.scrollIntoView({block:'center',behavior:'smooth'})
    }else void replaceWindow(`around=${activeOrdinal}&limit=${HISTORY_PAGE_SIZE}`,{scrollTo:activeOrdinal})
  },[jumps])

  /* Tool calls are the one read that parses (their inputs are not indexed), so they are
     fetched only while the toggle is on, only for the span the window covers, and never
     twice for one span. Spans the window has moved away from are let go, so a reader who
     scrolls a whole long conversation with the toggle on is not left holding every input
     in it. */
  const available=!!index?.available
  useEffect(()=>{
    if(!showToolCalls||!available)return
    const want=historyWindowSpan(messageWindow)
    if(!want)return
    toolSpans.current=clipSpans(toolSpans.current,want)
    if([...tools.keys()].some(ordinal=>ordinal<want.from||ordinal>want.to)){
      setTools(current=>new Map([...current].filter(([ordinal])=>ordinal>=want.from&&ordinal<=want.to)))
    }
    for(const span of missingSpans(toolSpans.current,want)){
      toolSpans.current=addSpan(toolSpans.current,span)
      const epoch=toolEpoch.current
      void get<HistoryToolCallsPage>(`${base}/tool-calls?${toolCallsQuery(span)}`).then(page=>{
        if(signal.aborted||epoch!==toolEpoch.current)return
        const found=historyToolEntries(page,historyId)
        if(!found.size)return
        keepPlace()
        setTools(current=>new Map([...current,...found]))
      }).catch(cause=>{
        if(signal.aborted||isAbort(cause)||epoch!==toolEpoch.current)return
        // Forget what was asked for, so the next window change asks again.
        toolEpoch.current++;toolSpans.current=[]
        setNotice(errorText(cause))
      })
    }
  },[showToolCalls,available,messageWindow])

  const rows=useMemo(()=>historyReaderRows(messageWindow,tools,showToolCalls),[messageWindow,tools,showToolCalls])
  const matchedOrdinals=useMemo(()=>new Set(matches.map(match=>match.ordinal)),[matches])

  useLayoutEffect(()=>{
    const container=body.current
    const held=anchor.current
    anchor.current=null
    if(container&&held){
      const node=container.querySelector<HTMLElement>(messageSelector(held.ordinal))
      if(node)container.scrollTop+=node.getBoundingClientRect().top-container.getBoundingClientRect().top-held.offset
    }
    const target=pendingScroll.current
    const node=target===null?null:container?.querySelector<HTMLElement>(messageSelector(target))
    if(node){pendingScroll.current=null;node.scrollIntoView({block:'center'})}
  },[rows])

  // The next page is asked for as an edge comes within reach. Re-observed whenever the
  // window changes, because an observer only reports *changes* in intersection: an edge
  // still in reach after its page landed has to be noticed again.
  useEffect(()=>{
    const container=body.current
    if(!container||typeof IntersectionObserver==='undefined')return
    const observer=new IntersectionObserver(entries=>{
      for(const item of entries)if(item.isIntersecting)void extend(item.target===topEdge.current?'before':'after')
    },{root:container,rootMargin:PAGE_PREFETCH_MARGIN})
    for(const node of [topEdge.current,bottomEdge.current])if(node)observer.observe(node)
    return()=>observer.disconnect()
  },[messageWindow,sections.transcript])

  const toggleSection=useCallback((key:HistorySectionKey,open:boolean)=>setSections(current=>current[key]===open?current:{...current,[key]:open}),[])
  /* Stepping through matches unfolds the transcript first. The band is collapsible, and a
     scroll into a closed band is a silent no-op - the counter would advance while nothing
     moved. */
  const moveMatch=(offset:number)=>{
    if(!matches.length)return
    toggleSection('transcript',true)
    setActiveMatch(current=>(current+offset+matches.length)%matches.length)
    setJumps(count=>count+1)
  }
  const toggleMessage=useCallback((ordinal:number)=>setExpandedMessages(current=>{const next=new Set(current);if(next.has(ordinal))next.delete(ordinal);else next.add(ordinal);return next}),[])
  const copyMessage=useCallback(async(message:HistoryIndexedMessage)=>{
    const text=message.text
    if(manualCopyArea.current)manualCopyArea.current.value=text
    const ok=await withoutClipboardCapture(()=>copyPreparedText(text,manualCopyArea.current))
    if(!ok){setNotice('The browser refused the copy. Select the message and copy it manually.');return}
    setNotice('');setCopiedMessage(message.ordinal)
    window.setTimeout(()=>setCopiedMessage(current=>current===message.ordinal?null:current),COPIED_FLASH_MS)
  },[])
  const toggleToolCalls=(show:boolean)=>{keepPlace();onShowToolCalls(show)}

  const annotations=notes?.annotations||[]
  const scanRecords=splitRecentScans(notes?.scan_records||[])
  const scanCount=notes?.scan_records.length||0
  const keyStats=entry?historyKeyStats(entry,{timestamp:timestampLabel,money:value=>money.format(value)}):[]
  const toolCallsAvailable=available&&index?.tool_calls!==0
  const abandoned=index?.abandoned_messages||0
  const transcriptKeys=!index?'loading'
    :`${countLabel(index.message_count,'message')}${index.indexing?index.indexed?' · updating':' · indexing':''}`
  const emptyTranscript=windowLoading||!index?'Loading conversation…'
    :index.indexing&&!index.indexed?'Indexing this conversation for the first time…'
    :!index.available&&!index.indexed?'That conversation’s transcript is no longer on disk. The agent CLI prunes its own history on its own timer, and mux does not own that file.'
    :'No native transcript is available for this session.'
  /** One collapsible band: a title, what it says while closed, and its body.
   *
   *  `sectionKeysVisible` is what keeps a closed band one line high. Most summary lines are
   *  a count and stay; Commits carries a whole commit subject and is suppressed until the
   *  band is open, where its rows say the same thing per commit. */
  const section=(key:HistorySectionKey,title:string,keys:ComponentChildren,content:ComponentChildren)=>
    <details class={`transcript-section transcript-section-${key}`} open={sections[key]} onToggle={event=>toggleSection(key,event.currentTarget.open)}>
      <summary><span class="transcript-section-title">{title}</span>{sectionKeysVisible(key,sections[key])&&<span class="transcript-section-keys">{keys}</span>}</summary>
      <div class="transcript-section-body">{content}</div>
    </details>
  const scanRow=(item:ScanRecord)=><details class="transcript-row" key={item.id}>
    <summary><code>{item.work_phase||'unknown'}</code><span>{item.summary||item.intent||'—'}</span></summary>
    <div><small>{epochLabel(item.t0)}{item.blocked_on&&item.blocked_on!=='none'?` · blocked on ${item.blocked_on}`:''}{item.claim?` · claims: ${item.claim}`:''}{(item.target?.length??0)>0?` · ${item.target!.slice(0,4).join(', ')}`:''}</small></div>
  </details>
  /* Resume is drawn from the row the reader clicked, so it is there on the click. It is
     withheld for the two cases it would refuse: a live CLI holds the conversation, or the
     daemon looked for the transcript (`/detail` repairs one that only moved) and it is gone. */
  const resumeControl=(shown:HistoryEntry)=>shown.held_by
    ?<span class="transcript-held" role="note" title={shown.held_by.detail}>Cannot resume — {heldLabel(shown)}</span>
    :index&&!index.available
      ?<span class="transcript-held" role="note" title="The agent CLI prunes its own history; this is the copy mux indexed before it went.">Cannot resume - the transcript is no longer on disk</span>
      :<button class="primary" onClick={()=>void onResume(shown)}>Resume</button>
  return <><div class="transcript-heading">
      {/* Back belongs in this view's own top bar rather than above it: on a phone the
          detail view replaces the results list, and a full-width row of its own was a
          band of chrome subtracted from the transcript on every conversation opened. */}
      <button class="history-back" aria-label="Back to results" onClick={onBack}>← Results</button>
      {entry?<div><h3>[{entry.backend}] {historyName(entry)}</h3><span>{entry.project_label||'Unassigned'} · {entry.cwd}</span></div>:<div><h3>Opening conversation…</h3><span>{historyId}</span></div>}
    </div>
    {entry&&<div class="transcript-actions">{resumeControl(entry)}<button title="Schedule a session that reopens this conversation later" onClick={()=>onScheduleResume(entry)}>Schedule Resume</button><button onClick={()=>void onHandoff(entry)}>Export handoff</button><label class="transcript-tool-toggle"><input type="checkbox" checked={showToolCalls} disabled={!toolCallsAvailable} onChange={event=>toggleToolCalls(event.currentTarget.checked)}/>Tool calls</label>{matches.length>0&&<div class="transcript-match-nav"><button aria-label="Previous search match" onClick={()=>moveMatch(-1)}>↑</button><span>{activeMatch+1}/{matches.length}</span><button aria-label="Next search match" onClick={()=>moveMatch(1)}>↓</button></div>}</div>}
    {notice&&<div class="history-inline-state error" role="alert">{notice}</div>}
    {entry&&section('stats','Session',
      keyStats.map(stat=><span class="transcript-stat" key={stat.label}><b>{stat.label}</b>{stat.model?<ModelName model={stat.value}/>:stat.value}</span>),
      <>
        <small>Started {timestampLabel(historyStart(entry))} · last {speakerLabel(entry.last_message_role)} {timestampLabel(entry.last_message_at)} · {entry.external?'external':'mux session'}</small>
        {providerIdentity(entry)&&<small>{providerIdentity(entry)}</small>}
        <small>{entry.context_window?`context final ${Math.round((entry.final_context_pct||0)*100)}% · peak ${Math.round((entry.peak_context_pct||0)*100)}% · ${entry.measurement_source||'native observation'}`:'context unavailable'} · tokens in {entry.tokens_in||0} / out {entry.tokens_out||0} · cache read {entry.tokens_cache_read||0} / write {entry.tokens_cache_write||0}{formatBytes(transcriptBytes(entry))?` · transcript ${formatBytes(transcriptBytes(entry))}`:''}</small>
        <small>{entry.compaction_count?`explicit compactions ${entry.compaction_count} · ${entry.compaction_capability||'native evidence'} · confidence ${entry.compaction_confidence||'unknown'}`:'compaction count unavailable - token drops are not treated as compaction evidence'}</small>
        {abandoned>0&&<small title="Retries and rewinds append a new branch and leave the previous attempt in the transcript. Those turns were never sent to the model, so they are not shown here.">{abandoned} message{abandoned===1?'':'s'} from abandoned branches are not shown</small>}
      </>)}
    {/* Commits and lineage are lists, so they read as rows that open. They used to be a
        horizontal strip of fixed-width cards, which put the subject - the only part
        anyone scans for - behind a sideways scroll on every viewport. */}
    {gitProvenance.length>0&&section('commits','Commits',commitsSummary(gitProvenance,shortSha),
      gitProvenance.map(item=><details class="transcript-row" key={item.id}>
        <summary><code>{shortSha(item.commitOid)}</code><span>{item.subject||'subject unavailable'}</span></summary>
        <div><small>{provenanceRoleLabel(item)} · {item.confidence}</small><small>{epochLabel(item.observedAt)}</small></div>
      </details>))}
    {lineage.length>0&&section('lineage','Work lineage',countLabel(lineage.length,'link'),
      orderedLineage(lineage).map(edge=>{
        const direction=lineageDirection(edge,historyId)
        const {endpoint,runId}=lineageCounterpart(edge,historyId)
        const cut=edge.relation==='branch'?lineageCutLabel(edge.metadata):''
        const label=lineageEndpointLabel(endpoint,runId)
        /* Clickable only when the other end still has a row to open. A dead link on
           a conversation that has been deleted is worse than plain text: it looks
           like the lineage is broken rather than like the row is gone. */
        return <details class={`transcript-row ${endpoint.known?'':'lineage-missing'}`} key={edge.id}>
          <summary><code>{lineageVerb(edge.relation,direction)}</code><span>{label}{endpoint.live?<span class="lineage-live" title="a session is still open on this conversation"> · open</span>:null}</span></summary>
          <div>
            {endpoint.known&&<button type="button" class="lineage-open" title={`Open ${label}`} onClick={()=>onOpenRun(runId)}>Open {label}</button>}
            {cut?<small class="lineage-cut" title={cut}>{cut}</small>:null}
            <small>{epochLabel(edge.created_at)}</small>
          </div>
        </details>
      }))}
    {annotations.length>0&&section('notes','Run notes',countLabel(annotations.length,'note'),
      annotations.map(item=><details class="transcript-row" key={item.id}>
        <summary><code>{item.tag}</code><span>{item.content}</span></summary>
        <div><small>{epochLabel(item.created_at)} · {item.provenance} · model::<ModelName model={item.resolved_model} fallback="deterministic"/> · confidence::{item.confidence??'—'} · cost::{money.format(item.cost_usd||0)}</small></div>
      </details>))}
    {/* Two entries, newest first, and the rest behind a second disclosure. A run that
        scanned forty times is a wall of rows nobody reads past the top of. */}
    {scanCount>0&&section('timeline','Behavioral timeline',countLabel(scanCount,'entry','entries'),
      <>
        {scanRecords.recent.map(item=>scanRow(item))}
        {scanRecords.earlier.length>0&&<details class="transcript-more" open={timelineExpanded} onToggle={event=>setTimelineExpanded(event.currentTarget.open)}>
          <summary>{countLabel(scanRecords.earlier.length,'earlier entry','earlier entries')}</summary>
          {scanRecords.earlier.map(item=>scanRow(item))}
        </details>}
      </>)}
    {/* The transcript collapses like the bands above it, and is the only one open by
        default. Its control is a *sibling* of the conversation rather than a `<details>`
        wrapping it: a `<details>` puts a UA-owned content box between the element and
        its children, and that box is the flex item, so the transcript cannot be made to
        take the leftover height from inside one - it sizes to its text and the view ends
        in dead space. `aria-controls` is what ties the two back together. */}
    <button type="button" class="transcript-section-toggle" aria-expanded={sections.transcript} aria-controls="history-transcript-body" onClick={()=>toggleSection('transcript',!sections.transcript)}>
      <span class="transcript-section-title">Transcript</span><span class="transcript-section-keys">{transcriptKeys}</span>
    </button>
    {sections.transcript&&<div class="messages" id="history-transcript-body" ref={body}>
      {messageWindow.hasMoreBefore&&<div class="history-page-edge" ref={topEdge}>{edgeLoading.before?'Loading earlier messages…':''}</div>}
      {rows.length?rows.map(({message,toolCalls})=><HistoryMessage key={message.ordinal} message={message} toolCalls={toolCalls} showToolCalls={showToolCalls} matched={matchedOrdinals.has(message.ordinal)} active={activeOrdinal===message.ordinal} expanded={expandedMessages.has(message.ordinal)} copied={copiedMessage===message.ordinal} onToggle={toggleMessage} onCopy={copyMessage}/>)
        :<div class="no-transcript">{emptyTranscript}</div>}
      {messageWindow.hasMoreAfter&&<div class="history-page-edge" ref={bottomEdge}>{edgeLoading.after?'Loading later messages…':''}</div>}
    </div>}
    <textarea ref={manualCopyArea} class="transcript-manual" readOnly aria-hidden="true" tabIndex={-1}/></>
})

function HistoryBrowserImpl({projects,initialProjectId,initialEntryId,onClose,onResume,onScheduleResume,onHandoff}:Props){
  const [items,setItems]=useState<HistoryEntry[]>([])
  const [historyProjects,setHistoryProjects]=useState<HistoryProject[]>([])
  const [nextCursor,setNextCursor]=useState<string|null>(null)
  const [loading,setLoading]=useState(false)
  const [error,setError]=useState('')
  const [query,setQuery]=useState('')
  const [scope,setScope]=useState<SearchScope>('all')
  const [backend,setBackend]=useState('')
  const [project,setProject]=useState(initialProjectId)
  const [state,setState]=useState('')
  const [external,setExternal]=useState('')
  const [dateFrom,setDateFrom]=useState('')
  const [dateTo,setDateTo]=useState('')
  // Last activity, not session start: History opens on "what was I just doing", and a
  // long conversation spawned yesterday outranks a short one spawned an hour ago and
  // abandoned. This one control both orders the list and scopes the date range below it.
  const [timeBasis,setTimeBasis]=useState<'started'|'last_message'>('last_message')
  const [selected,setSelected]=useState<Selection|null>(null)
  const [confirmDelete,setConfirmDelete]=useState<string|null>(null)
  // Outlives any one conversation: a reader who wants tool calls wants them in the next one.
  const [showToolCalls,setShowToolCalls]=useState(false)
  const [job,setJob]=useState<BackfillJob|null>(null)
  const requestSequence=useRef(0)
  const inFlight=useRef<AbortController|null>(null)
  const panel=useRef<HTMLElement>(null)
  // Read by the stable callbacks the memoized list and reader are handed, so typing in the
  // search box re-renders neither.
  const search=useRef({query,scope})
  search.current={query,scope}
  const confirming=useRef(confirmDelete)
  confirming.current=confirmDelete
  useModalFocus(panel,onClose)
  const closeDetail=useCallback(()=>setSelected(null),[])
  // Reading a transcript is a level of its own, so back returns to the results that found
  // it instead of discarding the search. Registered after the modal and therefore above
  // it: `← Results` and back now agree, where Escape used to close the whole browser.
  useDismissLevel(closeDetail,!!selected,'history-transcript')
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

  // Aborting a superseded search is not cosmetic. The daemon runs one search at a
  // time on the thread every history read shares, and a search box types faster
  // than a large archive can answer - so five keystroke pauses used to leave five
  // searches queued, of which four were already stale when they started. The
  // abort closes the socket, and the daemon drops a queued search whose reader has
  // gone (`routes/history._search_turn`). The sequence guard stays: it is what
  // makes a late answer harmless, and an abort is not delivered everywhere.
  const load=async(append=false)=>{
    const sequence=++requestSequence.current
    inFlight.current?.abort()
    const controller=new AbortController()
    inFlight.current=controller
    setLoading(true);setError('')
    try{
      const page=await api<HistoryPage>('GET',`/api/history?${parameters(append?nextCursor||undefined:undefined)}`,undefined,{signal:controller.signal})
      if(sequence!==requestSequence.current)return
      // The daemon answers a search it dropped rather than raising, and that answer
      // is an empty page. It is addressed to a socket that has already gone, so this
      // should never see one - and rendering "no matches" if it ever did would be a
      // lie about the archive rather than about this request.
      if(page.abandoned)return
      setItems(current=>append?[...current,...page.items]:page.items)
      setNextCursor(page.next_cursor)
    }catch(cause){
      if(sequence!==requestSequence.current)return
      if(isAbort(cause))return
      // The budget refusal says what to do about it; anything else is reported as
      // itself. A search that outran the daemon's ceiling is not a broken History.
      const failure=cause as ApiError
      setError(failure?.detail?.code==='search_budget_exceeded'
        ? 'That search took too long and was stopped. Narrow it - a longer phrase, one Project, or a date range.'
        : errorText(cause))
    }
    finally{if(sequence===requestSequence.current)setLoading(false)}
  }

  const refreshProjects=()=>api<{items:HistoryProject[]}>('GET','/api/history/projects').then(result=>setHistoryProjects(result.items)).catch(()=>undefined)

  useEffect(()=>{void refreshProjects()},[])
  useEffect(()=>{
    if(!project||project==='__ungrouped__'){setJob(null);return}
    void api<{items:BackfillJob[]}>('GET',`/api/history/backfills?project_id=${encodeURIComponent(project)}`).then(result=>setJob(result.items[0]||null)).catch(()=>undefined)
  },[project])
  // Longer while the word being typed is one letter long (`historySearchDelay`): that is
  // the broadest search a typist sends, and the one most certain to be replaced.
  useEffect(()=>{
    const timer=window.setTimeout(()=>void load(false),historySearchDelay(query))
    return()=>window.clearTimeout(timer)
  },[query,scope,backend,project,state,external,dateFrom,dateTo,timeBasis])

  useEffect(()=>{
    if(!job||!['queued','running'].includes(job.status))return
    const timer=window.setInterval(()=>{
      void api<{job:BackfillJob}>('GET',`/api/history/backfills/${job.id}`).then(result=>{
        setJob(result.job)
        if(!['queued','running'].includes(result.job.status)){void load(false);void refreshProjects()}
      }).catch(cause=>setError(errorText(cause)))
    },700)
    return()=>window.clearInterval(timer)
  },[job?.id,job?.status])

  // Both levels keep the feed's order (`historyFeed.ts`): a heading appears where its most
  // recent conversation does, so changing the sort moves the top of the list.
  const grouped=useMemo(()=>groupHistoryFeed(items,historyProjects,projects),[items,historyProjects,projects])

  const openEntry=useCallback((entry:HistoryEntry)=>{
    setError('')
    setSelected({id:entry.id,entry,query:search.current.query,scope:search.current.scope})
  },[])
  /** Open one conversation by id, for a caller that named a session instead of browsing.
   *
   *  `/detail` carries the row, so nothing about the entry has to be known in advance, and
   *  it answers for a conversation whose file is gone too - from what was indexed, with
   *  Resume withheld. A row that has been deleted is the one miss left, and the browser
   *  stays open on its list for it, because the caller's intent - find this session - is
   *  still servable by hand. */
  const openById=useCallback((historyId:string)=>{setError('');setSelected({id:historyId,query:'',scope:'all'})},[])
  const openFailed=useCallback((historyId:string,cause:unknown)=>{
    setSelected(current=>current?.id===historyId?null:current)
    setError(notFound(cause)
      ? 'That session has no History row any more, so its conversation cannot be opened. Search below for it by name.'
      : errorText(cause))
  },[])

  // A deep link is the reason this overlay opened, so it runs once per requested id and
  // never re-fires when the list underneath reloads.
  useEffect(()=>{if(initialEntryId)openById(initialEntryId)},[initialEntryId])

  const remove=useCallback(async(entry:HistoryEntry)=>{
    if(confirming.current!==entry.id){setConfirmDelete(entry.id);return}
    try{await api('DELETE',`/api/history/${entry.id}`)}
    catch(cause){setError(errorText(cause));return}
    setItems(current=>current.filter(item=>item.id!==entry.id));setConfirmDelete(null)
    setSelected(current=>current?.id===entry.id?null:current)
  },[])

  const startBackfill=async()=>{
    if(!project||project==='__ungrouped__')return
    setError('')
    try{setJob((await api<{job:BackfillJob}>('POST','/api/history/backfills',{project_id:project})).job)}
    catch(cause){setError(errorText(cause))}
  }

  const cancelBackfill=()=>job&&api<{job:BackfillJob}>('DELETE',`/api/history/backfills/${job.id}`).then(result=>setJob(result.job)).catch(cause=>setError(errorText(cause)))

  const scopeProject=projects.find(item=>item.id===project)
  const historyHarnesses=transcriptHarnesses()
  return <div class="modal-layer history-layer" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section ref={panel} class="modal history-modal" role="dialog" aria-modal="true" aria-label="Agent session history">
    <div class="modal-heading"><div><span>SESSION HISTORY</span><h2>{scopeProject?scopeProject.name:'All projects'}</h2></div><button type="button" aria-label="Close session history" onClick={onClose}>×</button></div>
    <section class="history-workspace" aria-label="Agent session history">
    <div class="history-body">
      <aside>
        <div class="history-search">
          <div class="history-query-row"><input aria-label="Search session history" placeholder="Search prompts, replies, titles, and paths…" value={query} onInput={event=>setQuery(event.currentTarget.value)}/><Dropdown ariaLabel="Search within" value={scope} onChange={value=>setScope(value as SearchScope)} options={[{value:'all',label:'All content'},{value:'user',label:'User prompts'},{value:'assistant',label:'Agent replies'},{value:'metadata',label:'Titles + paths'}]}/></div>
          <Dropdown ariaLabel="Filter history project" value={project} onChange={value=>{setProject(value);setSelected(null)}} filter filterPlaceholder="Filter Projects…" options={[{value:'',label:'All projects'},...projectDropdownOptions(projects,item=>({value:item.id,label:item.name,detail:item.root})),{value:'__ungrouped__',label:'Unassigned'}]}/>
          <Dropdown ariaLabel="Filter history provider" value={backend} onChange={setBackend} options={[{value:'',label:'All agents'},...historyHarnesses.map(harness=>({value:harness.name,label:harness.display_name}))]}/>
          <Dropdown ariaLabel="Filter history state" value={state} onChange={setState} options={[{value:'',label:'All states'},{value:'idle',label:'Completed'},{value:'exited',label:'Exited'},{value:'crashed',label:'Crashed'}]}/>
          <Dropdown ariaLabel="Filter history origin" value={external} onChange={setExternal} options={[{value:'',label:'Mux + external'},{value:'false',label:'Mux sessions'},{value:'true',label:'External sessions'}]}/>
          {/* Named for what it does. It reads as a sort because it is one, and the date
              range under it narrows the same timestamp - one field, two uses, so a
              conversation can never sort by one clock and be filtered by another. */}
          <Dropdown ariaLabel="Sort and date-filter history by" value={timeBasis} onChange={value=>setTimeBasis(value as typeof timeBasis)} options={[{value:'last_message',label:'Sort: last activity'},{value:'started',label:'Sort: session started'}]}/>
          <label>from<input type="datetime-local" value={dateFrom} onChange={event=>setDateFrom(event.currentTarget.value)}/></label><label>to<input type="datetime-local" value={dateTo} onChange={event=>setDateTo(event.currentTarget.value)}/></label>
          <div class="history-backfill-control"><button disabled={!project||project==='__ungrouped__'||!!job&&['queued','running'].includes(job.status)} onClick={()=>void startBackfill()}>Scan historical sessions</button><small>{project&&project!=='__ungrouped__'?'Discovers and indexes all native history for this Project.':'Select one Project to scan its complete native history.'}</small></div>
          {job&&<div class={`history-backfill-status ${job.status}`}><div><strong>{job.phase}</strong><span>{job.total?`${job.processed}/${job.total}`:`${job.scanned} scanned`}</span></div>{['queued','running'].includes(job.status)&&<progress max={Math.max(1,job.total)} value={job.processed}/>}<small>{job.discovered} discovered · {job.indexed} indexed ({job.indexed_messages} messages) · {job.unchanged} unchanged · {job.unreadable} unreadable{job.ambiguous?` · ${job.ambiguous} ambiguous`:''}</small>{job.error&&<small class="error">{job.error}</small>}{['queued','running'].includes(job.status)&&<button onClick={()=>void cancelBackfill()}>Cancel scan</button>}</div>}
        </div>
        {error&&<div class="history-inline-state error" role="alert">{error}</div>}
        {!loading&&!error&&!items.length&&<div class="history-inline-state">No agent history matches these filters.</div>}
        <HistoryList groups={grouped} activeId={selected?.id||''} confirmDelete={confirmDelete} onOpen={openEntry} onRemove={remove}/>
        {loading&&<div class="history-inline-state">Searching agent history…</div>}
        {nextCursor&&!loading&&<button class="history-load-more" onClick={()=>void load(true)}>Load more</button>}
      </aside>
      <main>{selected
        ?<HistoryDetail key={selected.id} selection={selected} showToolCalls={showToolCalls} onShowToolCalls={setShowToolCalls} onBack={closeDetail} onOpenRun={openById} onOpenFailed={openFailed} onResume={onResume} onScheduleResume={onScheduleResume} onHandoff={onHandoff}/>
        :<div class="history-placeholder"><span>◷</span><strong>Select a session</strong><p>Search prompts and replies, then inspect the native transcript.</p></div>}</main>
    </div>
    </section>
    </section>
  </div>
}

/** Memoized because it is mounted inside the app's root, which re-renders on every session
 *  status event; with stable props (`App.tsx` passes ref-backed handlers) an open browser
 *  re-renders only when something it draws has changed. */
export const HistoryBrowser=memo(HistoryBrowserImpl)
