import type { JSX } from 'preact'
import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
import { Dropdown } from './Dropdown'
import { GitSessionLinks, sessionLinkDestination, type SessionLinkItem, type SessionLinkMenu } from './GitSessionLinks'
import { GrantGate } from './GrantGate'
import {
  PROJECT_AUTOMATIONS_CHANGED,
  fetchProjectAutomations,
  forgetProjectAutomations,
} from './projectAutomations'
import { sessionDisplayName } from './sessionNames'
import {
  isAbsolutePath,
  graphDecorations,
  graphNodeLane,
  localMeasurement,
  normalizePath,
  groupProvenance,
  occupancyLabel,
  parseGitGraph,
  parseGitProvenance,
  parseGitRefMoves,
  pathTail,
  provenanceAmbiguityNote,
  provenanceRoleLabel,
  refMoveLabel,
  sessionGitCwd,
  shortSha,
  type GitGraph,
  type GitProvenance,
  type GitRefMove,
  type ProvenanceGroup,
} from './gitWorktrees'
import type { Project, Session } from './types'
import { GitFileRow } from './GitFileRow'
import { GitLandBar } from './GitLandBar'
import { GitLandRow } from './GitLandRow'
import {
  landGateNote,
  landKindNote,
  landedAtByBranch,
  landQueueOrder,
  landStateLabel,
  verifyProgressLabel,
} from './gitLand'
import { landErrorText, useLandQueue } from './landState'
import {
  assessRemoval,
  beginRemovals,
  forgetRemoval,
  isRemoving,
  landBlockLabel,
  planBulkLand,
  planBulkRemoval,
  removalBlockLabel,
  removalWarningLabel,
  settleRemovals,
  skippedLabel,
  type PendingRemovals,
  type RemovalAssessment,
} from './worktreeRemoval'
import { applySelectionClick, type SelectableRow } from './worktreeSelection'
import { GitReviewModal } from './GitReviewModal'
import type { SendToAgentRequest } from './SendToAgentPicker'
import {
  comparisonSourceLabel, parseCommitChanges, parseGitOverview, sortWorktreesByActivity,
  type GitCommitChanges, type GitOverviewWorktree, type GitWorktreeOverview,
  type ReviewChangeSummary, type ReviewFileChange,
} from './gitWorktrees'
import type { ReviewLocator } from './gitReview'

// One repository, three readings:
//
//  * Map is the operational projection: one row per worktree, with local files,
//    comparison-ref changes, the live sessions using the directory, and what it would
//    take to land that branch. Landing's Project-wide half - the verification command,
//    the agent grants, the queue - is one compact strip at the head of it
//    (`GitLandBar.tsx`), which is what lets each row carry only its own act.
//  * Log is the repository's real commit DAG. Git computes the ASCII lanes; the browser
//    styles them and attaches the structured commit metadata returned beside each prefix.
//  * Provenance is the durable evidence ledger connecting commits to sessions and runs.
//
// The tab remains deliberately read-mostly. Its only mutations are the worktree add/remove
// operations the Git feature already owns, and creating the repository itself for a Project
// that has none; it never stages, commits, merges, or checks out.
//
// That fourth state — a Project folder Git knows nothing about — is why the tab is not
// hidden on a Project without a repository. It used to answer with Git's own `fatal:`,
// which is the one reading here nobody can act on. It now offers the action that reading
// implies, and the daemon re-checks the folder before touching it (`git_init.py`).

// The view is owned by the drawer, not by this component. It is a registered segment
// (`drawerSegments.ts`), which is what gives "open Git Log" a palette entry and a voice
// phrase, and what persists the choice per Project — neither of which a local `useState`
// could do. The host draws the segmented control above this tab's toolbar; what is left
// here is the toolbar's actions.
// Land was a fourth reading and is not one any more. It answered "what is happening to
// this worktree" beside a Map that answered "what is in it", and the split cost more
// than it bought: the act sat on a surface that could not show the diff behind it, and
// once the act moved onto the row the segment was a second copy of one Project-wide
// block. Both now live on Map. The retired segment id, its palette command, and its
// voice phrases all migrate here rather than being dropped (`drawerSegments.ts`).
export type GitView = 'map' | 'log' | 'provenance'
const GRAPH_STEP = 80
const GRAPH_MAX = 200
/** `HistoryBrowser`'s own search cadence, so every search box in the app types alike. */
const SEARCH_DEBOUNCE_MS = 220
/**
 * How long a burst of `mux:git-changed` is allowed to coalesce.
 *
 * The event is raised by every session's five-second dirty tick, so ten sessions in one
 * repository is ten refetches of the same answer within a few hundred milliseconds. The
 * window is trailing and short: it must not make the tab feel stale after a real act
 * (a commit, a worktree removal), only stop the herd.
 */
const GIT_REFRESH_DEBOUNCE_MS = 350

function describeGitError(cause: unknown, action: string, mayHaveMutated=false): string {
  const error = cause as ApiError
  const message = cause instanceof Error ? cause.message : String(cause)
  if (error?.detail?.code !== 'git_timeout' && !error?.timeout) return message
  if(mayHaveMutated)return `Git did not answer in time. ${action} may still have completed - refresh to see.`
  return `Git did not answer in time while ${action.charAt(0).toLowerCase()}${action.slice(1)}. Refresh to retry.`
}

function committedLabel(timestamp: number): string {
  if (!timestamp) return ''
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
    .format(new Date(timestamp * 1000))
}

/** A landing's moment, to the minute: "when did this branch land" is a same-day question. */
function landedLabel(timestamp: number): string {
  if (!timestamp) return ''
  return new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  }).format(new Date(timestamp * 1000))
}

function GraphGlyph({ value, commit=false }: { value: string; commit?: boolean }) {
  return <span class="git-graph-glyph" aria-hidden="true">
    {[...value].map((character, index) => {
      const node=commit&&character==='*'
      return <i class={`lane-${Math.floor(index / 2) % 5}${node?' node':character.trim()?' edge':''}`} key={`${index}:${character}`}>{node?'●':character}</i>
    })}
  </span>
}

type Props={
  /** Which of the three readings to draw. Owned and persisted by the drawer host. */
  view:GitView
  /** Report an in-tab view change (the Log's own "provenance" links) back to the host. */
  onView:(view:GitView)=>void
  project?:Project
  sessions:Session[]
  onOpenFile:(path:string)=>void
  onOpenWorktreeFile:(worktree:string,path:string)=>void
  onSendToAgent?:(request:SendToAgentRequest)=>void
  onProjectUpdated:(project:Project)=>void
  /** Focus a live session: activates its open tab, or opens one in the focused pane. */
  onOpenSession:(sessionId:string)=>void
  /** Read an ended session's conversation, which is where its work now lives. */
  onOpenHistory:(historyId:string)=>void
}

type ReviewState={files:ReviewFileChange[];locator:ReviewLocator;initialPath:string;truncated:boolean;provenance:GitProvenance[]}

/** The last good overview per Project, for stale-while-revalidate rendering on mount.
 *  Bounded: a handful of Projects is the working set, and an evicted entry only costs
 *  the blank-first-paint this cache exists to remove. */
const OVERVIEW_CACHE=new Map<string,GitWorktreeOverview>()
const OVERVIEW_CACHE_LIMIT=8
function rememberOverview(projectId:string,overview:GitWorktreeOverview){
  OVERVIEW_CACHE.delete(projectId)
  OVERVIEW_CACHE.set(projectId,overview)
  while(OVERVIEW_CACHE.size>OVERVIEW_CACHE_LIMIT){
    const oldest=OVERVIEW_CACHE.keys().next().value
    if(oldest===undefined)break
    OVERVIEW_CACHE.delete(oldest)
  }
}

function SummaryHeader({title,summary}:{title:string;summary:ReviewChangeSummary}) {
  return <h4><span>{title}</span><small>{summary.total} file{summary.total===1?'':'s'} · +{summary.additions} -{summary.deletions}{summary.binaryFiles?` · ${summary.binaryFiles} binary`:''}</small></h4>
}

function ReviewGroup(props:{
  id:string;title:string;summary:ReviewChangeSummary;projectId:string;locator:ReviewLocator;openRoot:string;
  preview:string;onPreview:(value:string)=>void;onReview:(file:ReviewFileChange)=>void;onOpen:(file:ReviewFileChange)=>void
}) {
  return <section class="git-change-group git-review-group">
    <SummaryHeader title={props.title} summary={props.summary}/>
    {props.summary.files.map(file=><GitFileRow key={`${props.id}:${file.status}:${file.oldPath}:${file.path}`} projectId={props.projectId} file={file} locator={props.locator} expanded={props.preview===file.path} onToggle={()=>props.onPreview(props.preview===file.path?'':file.path)} onReview={()=>props.onReview(file)} onOpenCurrent={()=>props.onOpen(file)} openRoot={props.openRoot}/>)}
    {props.summary.truncated&&<p class="git-change-empty">Showing the first {props.summary.files.length} files of {props.summary.total}.</p>}
  </section>
}

export function GitTab({view,onView,project,sessions,onOpenFile,onOpenWorktreeFile,onSendToAgent,onProjectUpdated,onOpenSession,onOpenHistory}:Props) {
  const [links,setLinks]=useState<SessionLinkMenu|null>(null)
  // Stale-while-revalidate: the tab is not `keepMounted`, so every open is a cold mount,
  // and without this the map is blank for a full network+git round trip that usually
  // returns what the reader was just looking at. The last good overview per Project is
  // rendered immediately and `refresh()` still runs underneath; `busy` tells the truth
  // about the revalidation, and everything derived from a *changed* tree (detail rows,
  // pending removals, selections) is reconciled by the refresh exactly as before.
  // Same shape as `projectAutomations.ts`; bounded, module-scoped, dropped on daemon
  // restart with the page.
  const [overview,setOverview]=useState<GitWorktreeOverview|null>(()=>project?OVERVIEW_CACHE.get(project.id)??null:null)
  const [graph,setGraph]=useState<GitGraph|null>(null)
  const [provenance,setProvenance]=useState<GitProvenance[]>([])
  const [provenanceGroups,setProvenanceGroups]=useState<ProvenanceGroup[]>([])
  const [refMoves,setRefMoves]=useState<GitRefMove[]>([])
  const [provenanceError,setProvenanceError]=useState('')
  const [graphLimit,setGraphLimit]=useState(GRAPH_STEP)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [expandedTree,setExpandedTree]=useState('')
  // Full-detail rows, one per checkout the reader has expanded. The Map itself is read
  // with `detail=summary`, which withholds every per-file list: four lists of up to two
  // hundred records per worktree, served so a badge can say "12 local".
  const [treeDetail,setTreeDetail]=useState<Record<string,GitOverviewWorktree>>({})
  const [detailBusy,setDetailBusy]=useState('')
  const [detailError,setDetailError]=useState('')
  // Client-side only, and deliberately: every branch and path this filters on is already
  // in the payload, so asking the daemon would be a round trip to re-send what is on
  // screen.
  const [treeFilter,setTreeFilter]=useState('')
  const [logQuery,setLogQuery]=useState('')
  const [logField,setLogField]=useState<'message'|'author'>('message')
  const [logRegex,setLogRegex]=useState(false)
  const [provenanceQuery,setProvenanceQuery]=useState('')
  const [preview,setPreview]=useState<Record<string,string>>({})
  const [expandedCommit,setExpandedCommit]=useState('')
  const [commitCache,setCommitCache]=useState<Map<string,GitCommitChanges>>(()=>new Map())
  const [commitError,setCommitError]=useState('')
  const [commitBusy,setCommitBusy]=useState(false)
  const [parentByCommit,setParentByCommit]=useState<Record<string,string>>({})
  const [review,setReview]=useState<ReviewState|null>(null)
  const [adding,setAdding]=useState(false)
  const [addForm,setAddForm]=useState({path:'',branch:'',start:''})
  const [remove,setRemove]=useState<{path:string;force:boolean}|null>(null)
  // Removals in flight, owned by the list rather than by any row. A row that held its
  // own "removing" state stopped saying it the moment it was collapsed - or the moment
  // the API answered, before the next inventory arrived - and the worktree then sat
  // there looking exactly like the ones that were not being deleted.
  const [pendingRemovals,setPendingRemovals]=useState<PendingRemovals>({})
  // Bulk acts, off by default: checkboxes on every row are weight on a surface people
  // open to read. One toolbar press is what turns fifty worktrees into a work list.
  const [selecting,setSelecting]=useState(false)
  const [selected,setSelected]=useState<Record<string,true>>({})
  // The origin a Shift-click extends from: the last row checked by a plain click,
  // normalized. Held next to the selection because it is only ever read together with
  // it, and cleared wherever the selection is.
  const [selectAnchor,setSelectAnchor]=useState('')
  const [bulkRemoving,setBulkRemoving]=useState(false)
  const [bulkForce,setBulkForce]=useState(false)
  const [bulkBusy,setBulkBusy]=useState(false)
  const [bulkNote,setBulkNote]=useState('')
  const [compareOverride,setCompareOverride]=useState(project?.git_compare_ref||'')
  // Set only from the daemon's own `not_git_repository`, never inferred from a message.
  const [notRepository,setNotRepository]=useState(false)
  const [initNote,setInitNote]=useState('')
  const generation=useRef(0)
  const graphGeneration=useRef(0)
  const provenanceGeneration=useRef(0)
  // Three-valued: `null` while unread, so an unreadable opt-in table never renders as
  // "this Project said no".
  const [provenancePermitted,setProvenancePermitted]=useState<boolean|null>(null)
  // One read, two consumers: the landing strip at the head of Map and the Land control
  // inside each expanded row. Owned here rather than by either of them so they cannot
  // show two different answers about the same request, and so Log and Provenance pay
  // nothing for a five-second poll neither of them draws.
  const {queue:landQueue,error:landError,refresh:refreshLand}=useLandQueue(project?.id,view==='map')
  // `null` until the reader touches it, so the strip's own blocked-gate default still
  // applies. Lifted out of the strip because a blocked row opens it (`GitLandRow.tsx`),
  // which is what lets a row name a Project-wide control without drawing one.
  const [landingOpen,setLandingOpen]=useState<boolean|null>(null)

  const refresh=useCallback(async()=>{
    if(!project){setOverview(null);return}
    const mine=++generation.current
    setBusy(true)
    try{
      // `detail=summary`: counts for every row, file lists for none. The daemon serves
      // this with an `ETag` and `Cache-Control: no-cache`, so a poll that finds nothing
      // changed costs a conditional request and no body at all.
      const raw=await api<unknown>('GET',`/api/git/worktrees?project_id=${encodeURIComponent(project.id)}&detail=summary`,undefined,{timeoutMs:20000})
      if(mine!==generation.current)return
      const parsed=parseGitOverview(raw)
      if(!parsed)throw new Error('The daemon returned an invalid Git overview.')
      setOverview(parsed);rememberOverview(project.id,parsed);setError('');setPreview({});setNotRepository(false)
      // Every expanded row's file lists are now a statement about a tree that has moved
      // (that is what brought us here), so they are dropped rather than redrawn. The
      // effect below re-reads whichever one is still open.
      setTreeDetail({})
      // The inventory is the only thing that ends a removing indication, and the only
      // thing that can: the daemon answers a fast removal before Git has finished
      // deleting anything, and a slow one while it still is.
      setPendingRemovals(current=>settleRemovals(current,parsed.worktrees))
      setSelected(current=>{
        const listed=new Set(parsed.worktrees.map(tree=>normalizePath(tree.path)))
        const next=Object.fromEntries(Object.entries(current).filter(([key])=>listed.has(key)))
        return Object.keys(next).length===Object.keys(current).length?current:next as Record<string,true>
      })
    }catch(cause){if(mine===generation.current){
      const missing=(cause as ApiError)?.detail?.code==='not_git_repository'
      setOverview(null);setNotRepository(missing)
      setError(missing?'':describeGitError(cause,'Reading the repository'))
    }}
    finally{if(mine===generation.current)setBusy(false)}
  },[project?.id])

  const refreshGraph=useCallback(async(limit:number,search:{query:string;field:'message'|'author';regex:boolean})=>{
    if(!project)return
    const mine=++graphGeneration.current
    try{
      const query=new URLSearchParams({project_id:project.id,limit:String(limit)})
      // Only sent when there is something to search for. An empty `grep` would ask Git
      // to match every commit against nothing, and - because filtering drops
      // `--graph` - would silently retire the lane drawing for no reason.
      if(search.query.trim()){
        query.set(search.field==='author'?'author':'grep',search.query.trim())
        if(search.regex)query.set('regex','1')
      }
      const raw=await api<unknown>('GET',`/api/git/graph?${query}`,undefined,{timeoutMs:20000})
      if(mine!==graphGeneration.current)return
      setGraph(parseGitGraph(raw));setError('')
    }catch(cause){if(mine===graphGeneration.current)setError(describeGitError(cause,'Reading the commit graph'))}
  },[project?.id])

  const refreshProvenance=useCallback(async(search='')=>{
    if(!project){setProvenance([]);setProvenanceGroups([]);setRefMoves([]);return}
    const mine=++provenanceGeneration.current
    try{
      const query=new URLSearchParams({project_id:project.id,limit:'500'})
      if(search.trim())query.set('subject',search.trim())
      const raw=await api<unknown>('GET',`/api/git/provenance?${query}`)
      if(mine!==provenanceGeneration.current)return
      setProvenance(parseGitProvenance(raw));setProvenanceGroups(groupProvenance(raw));setRefMoves(parseGitRefMoves(raw));setProvenanceError('')
    }catch(cause){if(mine===provenanceGeneration.current)setProvenanceError(cause instanceof Error?cause.message:String(cause))}
  },[project?.id])

  /**
   * One checkout's full reading, for the row that just opened.
   *
   * The Map is read with `detail=summary`, so an expanded row has counts and no files.
   * This asks the daemon for that one worktree - which is one worktree's worth of Git,
   * not the Project's - and the row draws its answer.
   */
  const loadTreeDetail=useCallback(async(path:string)=>{
    if(!project)return
    setDetailBusy(path);setDetailError('')
    try{
      const query=new URLSearchParams({project_id:project.id,detail:'full',worktree:path})
      const raw=await api<unknown>('GET',`/api/git/worktrees?${query}`,undefined,{timeoutMs:20000})
      const parsed=parseGitOverview(raw)
      const row=parsed?.worktrees.find(item=>normalizePath(item.path)===normalizePath(path))
      if(!row)throw new Error('The daemon returned no reading for this worktree.')
      setTreeDetail(current=>({...current,[path]:row}))
    }catch(cause){setDetailError(describeGitError(cause,'Reading the worktree'))}
    finally{setDetailBusy(current=>current===path?'':current)}
  },[project?.id])

  useEffect(()=>{
    // On a Project switch the map seeds from the cache instead of blanking, for the same
    // reason the mount does; `refresh()` below revalidates it immediately.
    setOverview(project?OVERVIEW_CACHE.get(project.id)??null:null);setGraph(null);setProvenance([]);setProvenanceGroups([]);setRefMoves([]);setProvenanceError('');setExpandedTree('');setTreeDetail({});setDetailError('');setTreeFilter('');setLogQuery('');setProvenanceQuery('');setExpandedCommit('');setCommitCache(new Map());setReview(null);setError('');setNotRepository(false);setInitNote('');setCompareOverride(project?.git_compare_ref||'')
    void refresh()
    // Two filters on one listener, and both are the same defect: work done for a
    // repository nobody is looking at. `git_changed` is raised by *every* session's
    // five-second dirty tick, so an unfiltered handler re-read this Project's whole
    // worktree map on another Project's poll - and ten sessions in this one repository
    // raised it ten times inside a few hundred milliseconds for one answer.
    let timer:number|undefined
    const changed=(event:Event)=>{
      const detail=(event as CustomEvent<{projectId?:string}>).detail
      // An event with no Project named is not filtered out: `mux:events-connected` and
      // the worktree acts carry none, and treating "unknown" as "not mine" would stop
      // the tab refreshing after a reconnect.
      if(detail?.projectId&&detail.projectId!==project?.id)return
      window.clearTimeout(timer)
      timer=window.setTimeout(()=>{void refresh()},GIT_REFRESH_DEBOUNCE_MS)
    }
    window.addEventListener('mux:git-changed',changed);window.addEventListener('mux:events-connected',changed);window.addEventListener('mux:worktree-created',changed);window.addEventListener('mux:worktree-removed',changed)
    return()=>{window.clearTimeout(timer);window.removeEventListener('mux:git-changed',changed);window.removeEventListener('mux:events-connected',changed);window.removeEventListener('mux:worktree-created',changed);window.removeEventListener('mux:worktree-removed',changed)}
  },[refresh,project?.id])

  // The provenance ledger is read by Log (its per-commit session links) and by
  // Provenance. Map draws none of it, and used to fetch five hundred rows of it on
  // every one of the refreshes above.
  useEffect(()=>{
    if(view==='map')return
    const timer=window.setTimeout(()=>{void refreshProvenance(provenanceQuery)},provenanceQuery?SEARCH_DEBOUNCE_MS:0)
    return()=>window.clearTimeout(timer)
  },[view,provenanceQuery,refreshProvenance])

  // The expanded row's own file lists. Re-runs after a refresh drops them, which is what
  // keeps an open row current without the Map paying for every row's lists.
  useEffect(()=>{
    if(view!=='map'||!expandedTree||treeDetail[expandedTree])return
    void loadTreeDetail(expandedTree)
  },[view,expandedTree,treeDetail,loadTreeDetail])
  useEffect(()=>setCompareOverride(project?.git_compare_ref||''),[project?.git_compare_ref])
  useEffect(()=>{
    const id=project?.id
    if(!id){setProvenancePermitted(null);return}
    let stale=false
    const read=()=>{fetchProjectAutomations(id)
      .then(state=>{if(!stale)setProvenancePermitted(state.enabled.includes('provenance_graph'))})
      .catch(()=>{if(!stale)setProvenancePermitted(null)})}
    read()
    window.addEventListener(PROJECT_AUTOMATIONS_CHANGED,read)
    return()=>{stale=true;window.removeEventListener(PROJECT_AUTOMATIONS_CHANGED,read)}
  },[project?.id])
  useEffect(()=>{
    if(view!=='log')return
    const search={query:logQuery,field:logField,regex:logRegex}
    // Debounced only while typing. A refetch caused by the repository moving, or by
    // loading more commits, is not a keystroke and waits for nothing.
    const timer=window.setTimeout(()=>{void refreshGraph(graphLimit,search)},logQuery?SEARCH_DEBOUNCE_MS:0)
    const changed=()=>void refreshGraph(graphLimit,search)
    window.addEventListener('mux:git-changed',changed)
    window.addEventListener('mux:events-connected',changed)
    return()=>{window.clearTimeout(timer);window.removeEventListener('mux:git-changed',changed);window.removeEventListener('mux:events-connected',changed)}
  },[view,graphLimit,logQuery,logField,logRegex,refreshGraph])

  const provenanceByCommit=new Map<string,GitProvenance[]>()
  for(const item of provenance){const entries=provenanceByCommit.get(item.commitOid)||[];entries.push(item);provenanceByCommit.set(item.commitOid,entries)}

  if(!project)return <><p class="drawer-status">no Project selected</p><p class="drawer-empty">Select a Project to inspect its repository.</p></>

  const provenanceOff=provenancePermitted===false
  // Most recently committed branch first. This is now the *only* list of checkouts in
  // the tab - the retired Land segment used to draw a second one in the same order, a
  // standing invitation for the two to drift - so the order matters for the same reason
  // it always did and has one owner again. Parsing stays faithful to the payload;
  // presentation is what reorders (`sortWorktreesByActivity` says why the key is the
  // branch tip's date rather than the directory's mtime).
  const orderedWorktrees=sortWorktreesByActivity(overview?.worktrees||[])
  const mainTree=orderedWorktrees.find(tree=>tree.main)
  const linkedWorktrees=orderedWorktrees.filter(tree=>!tree.main)
  const comparisonLabel=overview?.comparison.available?(overview.comparison.display||overview.comparison.ref):null
  // Substring, case-insensitive, over the two things a reader has to go on at fifty
  // checkouts: the branch and the directory. Not a fuzzy match - a filter that matches
  // things the reader cannot see the reason for is worse than one that matches less.
  const filterText=treeFilter.trim().toLowerCase()
  const shownWorktrees=filterText
    ? orderedWorktrees.filter(tree=>`${tree.branch||''} ${tree.path}`.toLowerCase().includes(filterText))
    : orderedWorktrees
  // When this queue last landed each branch. A floor, and drawn only where it exists:
  // `GET /api/land` returns the newest hundred rows, so a branch that landed long enough
  // ago says nothing rather than something invented (`landedAtByBranch`).
  const landedAt=landedAtByBranch(landQueue?.requests||[])
  // One ordered projection for both the strip and every collapsed Map row. Re-sorting
  // inside each row would obscure that its number is its actual place in this queue.
  const activeLands=landQueueOrder(landQueue?.requests||[])

  const saveComparison=async(value:string)=>{
    setBusy(true)
    try{const updated=await api<Project>('PATCH',`/api/projects/${project.id}`,{git_compare_ref:value||null});onProjectUpdated(updated);setCompareOverride(value);setReview(null);await refresh()}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const openFor=(root:string,path:string)=>normalizePath(root)===normalizePath(project.root)?onOpenFile(path):onOpenWorktreeFile(root,path)
  // An ended session is not an occupant: its process is gone, so it neither uses the
  // checkout nor belongs under a "live" count, and it cannot block a worktree removal.
  // A pending one does occupy it — it is starting up in that directory.
  const isEnded=(session:Session)=>session.state==='exited'||session.state==='crashed'
  const sessionsFor=(root:string)=>sessions.filter(session=>{
    if(session.project_id!==project.id||isEnded(session))return false
    const cwd=normalizePath(sessionGitCwd(session)),worktree=normalizePath(root)
    return cwd===worktree||cwd.startsWith(`${worktree}/`)
  })
  // Live sessions win over the row's own name even though the daemon already resolved
  // one: a rename lands in this array immediately, while provenance is refetched only
  // when Git changes, and the drawer would otherwise show the old name until it did.
  const liveById=new Map(sessions.map(session=>[session.id,session]))
  const provenanceName=(item:GitProvenance)=>{
    const live=liveById.get(item.sessionId)
    return live?sessionDisplayName(live):item.displayName||item.sessionName
  }
  const provenanceLinks=(entries:GitProvenance[]):SessionLinkItem[]=>entries.map(item=>({
    key:item.id,
    label:provenanceName(item),
    detail:provenanceRoleLabel(item),
    session:liveById.get(item.sessionId)||null,
    // The daemon resolves the exact History row; the run/session id is the fallback for
    // a row it could not resolve, and History reports a miss rather than failing silently.
    historyId:item.historyId||item.agentRunId||item.sessionId,
  }))
  const worktreeLinks=(occupants:Session[]):SessionLinkItem[]=>occupants.map(session=>({
    key:session.id,
    label:sessionDisplayName(session)||session.id,
    detail:session.git?.branch||undefined,
    session,
    historyId:session.agent_run_id||session.id,
  }))
  /** Open the list at the pointer, or under the control when a keyboard opened it. */
  const openLinks=(event:JSX.TargetedMouseEvent<HTMLElement>,title:string,items:SessionLinkItem[])=>{
    event.stopPropagation()
    const rect=event.currentTarget.getBoundingClientRect()
    setLinks({title,x:event.clientX||rect.left,y:event.clientY||rect.bottom,items})
  }
  /** One rule for every session link here: a running session is a pane, anything else
   *  is the conversation it left behind. */
  const followLink=(item:SessionLinkItem)=>{
    const destination=sessionLinkDestination(item)
    if(destination==='session'&&item.session)onOpenSession(item.session.id)
    else if(destination==='history'&&item.historyId)onOpenHistory(item.historyId)
  }
  /** The session's name in a provenance row, as the link to that session. */
  const provenanceSessionButton=(item:GitProvenance)=>{
    const link=provenanceLinks([item])[0]
    const destination=sessionLinkDestination(link)
    return <button
      class="git-session-open"
      disabled={destination==='none'}
      title={destination==='session'?`Open ${link.label}`:destination==='history'?`Read ${link.label} in History`:'This session left no conversation behind'}
      onClick={()=>followLink(link)}
    >{link.label}</button>
  }
  const startReview=(summary:ReviewChangeSummary,locator:ReviewLocator,file:ReviewFileChange,commitProvenance:GitProvenance[]=[])=>setReview({files:summary.files,locator,initialPath:file.path,truncated:summary.truncated,provenance:commitProvenance})
  const loadCommit=async(oid:string,parent?:string)=>{
    const key=`${oid}:${parent||''}`
    if(commitCache.has(key))return
    setCommitBusy(true);setCommitError('')
    try{
      const query=new URLSearchParams({project_id:project.id});if(parent)query.set('parent',parent)
      const raw=await api<unknown>('GET',`/api/git/commits/${oid}/changes?${query}`,undefined,{timeoutMs:20000})
      const parsed=parseCommitChanges(raw);if(!parsed)throw new Error('The daemon returned invalid commit changes.')
      setCommitCache(current=>new Map(current).set(`${oid}:${parsed.parent||''}`,parsed))
      setParentByCommit(current=>({...current,[oid]:parsed.parent||''}))
    }catch(cause){setCommitError(cause instanceof Error?cause.message:String(cause))}
    finally{setCommitBusy(false)}
  }
  const toggleCommit=(oid:string)=>{
    if(expandedCommit===oid){setExpandedCommit('');return}
    setExpandedCommit(oid);void loadCommit(oid,parentByCommit[oid])
  }
  const changeParent=(oid:string,parent:string)=>{setParentByCommit(current=>({...current,[oid]:parent}));void loadCommit(oid,parent)}
  const create=async()=>{
    if(!isAbsolutePath(addForm.path)){setError('Worktree path must be absolute.');return}
    setBusy(true)
    try{await api('POST','/api/git/worktrees',{cwd:project.root,path:addForm.path,branch:addForm.branch||undefined,start_point:addForm.start||undefined});setAdding(false);setAddForm({path:'',branch:'',start:''});await refresh()}
    catch(cause){setError(describeGitError(cause,'Creating the worktree',true))}finally{setBusy(false)}
  }
  const initialize=async()=>{
    setBusy(true);setError('');setInitNote('')
    try{
      const result=await api<{branch:string;gitignore:string}>('POST','/api/git/init',{project_id:project.id},{timeoutMs:60000})
      setInitNote(result.gitignore==='created'
        ?`Repository created on ${result.branch}, with a starter .gitignore. Nothing is staged yet.`
        :`Repository created on ${result.branch}. The folder already had a .gitignore, which was left alone.`)
      // Everything else in the tab reads through the same refresh path, and other clients
      // learn about it from the daemon's `git_changed` event rather than from this one.
      await refresh()
    }catch(cause){setError(describeGitError(cause,'Creating the repository',true))}
    finally{setBusy(false)}
  }
  /**
   * Remove one or many, by exactly one path.
   *
   * The pending set is written before the first request and only ever *narrowed* here,
   * on a refusal: what clears an entry on success is the refreshed inventory not
   * listing it. That is what makes the fast path (the tree is renamed away and gone
   * from the next list within a second) and the slow one (Git is still unlinking
   * thirty thousand files) render as the same sentence.
   */
  const runRemovals=async(targets:readonly {path:string;force:boolean}[])=>{
    if(!targets.length)return
    setPendingRemovals(current=>beginRemovals(current,targets.map(item=>item.path)))
    setBusy(true);setError('')
    const failures:string[]=[]
    for(const target of targets){
      try{await api('DELETE','/api/git/worktrees',{cwd:project.root,path:target.path,force:target.force})}
      catch(cause){
        failures.push(`${pathTail(target.path)}: ${describeGitError(cause,'Removing the worktree',true)}`)
        setPendingRemovals(current=>forgetRemoval(current,target.path))
      }
    }
    setBusy(false)
    // The refresh first, then the message: a refusal may have been preceded by a repair
    // or an interrupted Git command that changed the row, and `refresh` clears the error
    // on success - so setting it before would show the new inventory with no reason
    // beside it.
    await refresh()
    if(failures.length)setError(failures.join(' · '))
  }
  const removeWorktree=async()=>{
    if(!remove)return
    const target={path:remove.path,force:remove.force}
    setRemove(null);setExpandedTree('')
    await runRemovals([target])
  }

  // One assessment per linked worktree, in map order, so the bulk bar and each row are
  // reading the same answer about the same checkout rather than two similar ones.
  const assessments=new Map<string,RemovalAssessment>(
    orderedWorktrees.map(tree=>[normalizePath(tree.path),assessRemoval(tree,sessionsFor(tree.path).length)]),
  )
  const selectedTrees=orderedWorktrees.filter(tree=>selected[normalizePath(tree.path)])
  const selectedAssessments=selectedTrees
    .map(tree=>assessments.get(normalizePath(tree.path))!)
    .filter(Boolean)
  const removalPlan=planBulkRemoval(selectedAssessments)
  const landPlan=planBulkLand(selectedTrees)
  const safeRemovals=removalPlan.removable.filter(item=>item.warnings.length===0)
  const removalTargets=bulkForce?removalPlan.removable:safeRemovals
  const clearSelection=()=>{setSelected({});setSelectAnchor('');setBulkRemoving(false);setBulkForce(false);setBulkNote('')}
  /** The rows a range may sweep, in the order they are drawn - so the search box's
   *  filtering is what a Shift-click ranges over, and a row whose checkbox is disabled
   *  is stepped over rather than pressed. Both conditions are the checkbox's own. */
  const selectableRows:SelectableRow[]=shownWorktrees.map(row=>({
    path:row.path,
    selectable:!isRemoving(pendingRemovals,row.path)&&(assessments.get(normalizePath(row.path))?.blocks.length??1)===0,
  }))
  const clickSelected=(path:string,extend:boolean)=>{
    const result=applySelectionClick(selected,selectableRows,path,{extend,anchor:selectAnchor})
    setSelected(result.selected)
    setSelectAnchor(result.anchor)
  }
  /** Everything the reader could act on: a blocked checkout is not a candidate, so
   *  selecting it would only be something to un-select before pressing anything. */
  const selectAllRemovable=()=>{
    setSelected(Object.fromEntries(
      orderedWorktrees
        .filter(tree=>!tree.main&&(assessments.get(normalizePath(tree.path))?.blocks.length??1)===0)
        .map(tree=>[normalizePath(tree.path),true as const]),
    ))
    // A sweep of the whole list is not an origin anyone pointed at, so the next
    // Shift-click has nothing to extend from and is a plain click.
    setSelectAnchor('')
  }
  /** One `request_land` per branch, in map order. The queue serializes them - that is
   *  the queue doing its job, and nothing here waits for or reorders a landing. */
  const runBulkLand=async()=>{
    if(!landPlan.landable.length)return
    setBulkBusy(true)
    const failures:string[]=[]
    for(const item of landPlan.landable){
      try{await api('POST','/api/land',{project_id:project.id,worktree_root:item.path})}
      catch(cause){failures.push(`${item.branch}: ${landErrorText(cause)}`)}
    }
    setBulkBusy(false)
    const queued=landPlan.landable.length-failures.length
    setBulkNote(failures.length
      ?`${queued} queued · ${failures.join(' · ')}`
      :`${queued} branch${queued===1?'':'es'} queued to land.`)
    await refreshLand()
  }
  const runBulkRemove=async()=>{
    const targets=removalTargets.map(item=>({path:item.path,force:item.needsForce}))
    setBulkRemoving(false);setBulkForce(false);setBulkNote('');setSelected({})
    await runRemovals(targets)
  }

  // Nothing here has a repository to read, so the three views, the compare control, and
  // the worktree form are all absent rather than present-and-empty: the only decision
  // available is whether to create one.
  if(notRepository)return <div class="git-tab git-init">
    <p class="git-state">This Project's folder is not a Git repository.</p>
    <p class="drawer-empty"><code>{project.root}</code></p>
    <p class="drawer-empty">Initializing creates a repository here and writes a starter <code>.gitignore</code> matched to what the folder already contains. Nothing is staged and no commit is made, so history starts empty and the files are exactly as they are now. An existing <code>.gitignore</code> is left alone.</p>
    <div class="git-map-actions"><button disabled={busy} onClick={()=>void initialize()}>{busy?'Initializing…':'Initialize repository'}</button></div>
    {initNote&&<p class="git-state" role="status">{initNote}</p>}
    {error&&<p class="git-state error" role="alert">{error}</p>}
  </div>

  return <div class="git-tab git-review-tab">
    <div class="git-toolbar">
      {/* Each reading searches the thing it is a reading *of*, and each searches it where
          that search is cheapest. Map filters the payload it already has - every branch
          and path is on screen. Log asks Git, because a message body is not in any
          payload here and `git log --grep` is the only thing that has read them all.
          Provenance asks SQLite, over an indexed column, which is instant and covers
          exactly the commits this daemon observed - a strictly smaller set than Git's,
          and complementary rather than a substitute for it. */}
      {view==='map'&&<label class="git-search">
        <span class="sr-only">Filter worktrees</span>
        <input type="search" value={treeFilter} placeholder="filter branch or path"
          onInput={event=>setTreeFilter(event.currentTarget.value)}/>
      </label>}
      {view==='log'&&<label class="git-search">
        <span class="sr-only">Search commits</span>
        <input type="search" value={logQuery} placeholder={logField==='author'?'search author':'search commit messages'}
          onInput={event=>setLogQuery(event.currentTarget.value)}/>
      </label>}
      {view==='log'&&<Dropdown ariaLabel="Search field" value={logField}
        onChange={value=>setLogField(value==='author'?'author':'message')}
        options={[{value:'message',label:'message'},{value:'author',label:'author'}]}/>}
      {view==='log'&&<label class="git-search-regex" title="Treat the search as a regular expression rather than literal text">
        <input type="checkbox" checked={logRegex} onChange={event=>setLogRegex(event.currentTarget.checked)}/>
        {' '}regex
      </label>}
      {view==='provenance'&&<label class="git-search">
        <span class="sr-only">Search recorded commit subjects</span>
        <input type="search" value={provenanceQuery} placeholder="search recorded subjects"
          onInput={event=>setProvenanceQuery(event.currentTarget.value)}/>
      </label>}
      {/* Right-aligned as a group, under the host's segmented control rather than beside
          a toggle of this tab's own. The refresh control is its glyph alone, so it keeps
          an explicit accessible name. */}
      <div class="git-toolbar-actions">
        <button class="git-refresh" disabled={busy} aria-label="Refresh" title="Refresh" onClick={()=>{window.dispatchEvent(new Event('mux:git-review-refresh'));if(view==='map'){void refresh();if(expandedTree)void loadTreeDetail(expandedTree)}else if(view==='log')void refreshGraph(graphLimit,{query:logQuery,field:logField,regex:logRegex});else void refreshProvenance(provenanceQuery)}}>↻</button>
        {/* Bulk is a mode, not a permanent column. Fifty accumulated worktrees is what
            makes it necessary; a checkbox under every branch name on a surface people
            open to read a diff is what makes it a mode. */}
        {view==='map'&&<button aria-pressed={selecting} onClick={()=>{setSelecting(value=>!value);clearSelection()}}>Select</button>}
        {view==='map'&&<button onClick={()=>setAdding(value=>!value)}>+ worktree</button>}
      </div>
    </div>
    {overview&&<div class="git-compare"><label>COMPARE <Dropdown disabled={busy} value={compareOverride} onChange={value=>void saveComparison(value)} options={[
      {value:'',label:`Auto${overview.comparison.available?` (${overview.comparison.display})`:''}`},
      ...(compareOverride&&!overview.comparison.candidates.includes(compareOverride)?[{value:compareOverride,label:`${compareOverride} (unavailable)`}]:[]),
      ...overview.comparison.candidates.map(ref=>({value:ref,label:ref})),
    ]}/></label><small>{comparisonSourceLabel(overview.comparison)}</small></div>}
    {error&&<p class="git-state error" role="alert">{error}</p>}
    {adding&&<div class="git-add-form"><label>Absolute path<input value={addForm.path} onInput={event=>setAddForm(value=>({...value,path:event.currentTarget.value}))}/></label><label>New branch<input value={addForm.branch} onInput={event=>setAddForm(value=>({...value,branch:event.currentTarget.value}))}/></label><label>Start point<input value={addForm.start} onInput={event=>setAddForm(value=>({...value,start:event.currentTarget.value}))}/></label><div><button disabled={busy} onClick={()=>void create()}>Create</button><button onClick={()=>setAdding(false)}>Cancel</button></div></div>}
    {view==='map'&&<>
      {/* Landing, once, at the head of the map: the verification command and its
          approval, who besides you may start a land, and the queue. Everything here is
          Project-wide, which is exactly why it is not on a row - drawn per row it was
          the same paragraph about approved bytes under each of eight worktrees. It is a
          compact strip rather than a panel so the tab still opens on a map. */}
      <GitLandBar project={project} queue={landQueue} error={landError} onChanged={refreshLand}
        open={landingOpen} onOpen={setLandingOpen}/>
      {/* The bulk bar states what it will *not* touch before it states what it will.
          "Remove 30" is not a sentence anyone can check by reading it, so the counts
          that make it checkable - what is in use, what holds uncommitted or unlanded
          work - are the sentence instead. */}
      {selecting&&overview&&<div class="git-map-bulk" aria-label="Selected worktrees">
        <div class="git-map-bulk-head">
          <strong>{selectedTrees.length} selected</strong>
          <button disabled={bulkBusy||busy} onClick={selectAllRemovable}>All removable</button>
          <button disabled={bulkBusy||busy||!selectedTrees.length} onClick={clearSelection}>None</button>
        </div>
        {selectedTrees.length>0&&<div class="git-map-bulk-badges">
          {removalPlan.blocked.length>0&&<em class="warn">{skippedLabel(removalPlan.blocked.reduce<Record<string,number>>((counts,item)=>{for(const block of item.blocks)counts[block]=(counts[block]||0)+1;return counts},{}))}</em>}
          {removalPlan.warned.length>0&&<em class="warn">{removalPlan.warned.length} with {[...new Set(removalPlan.warned.flatMap(item=>item.warnings))].map(removalWarningLabel).join(' / ')} work</em>}
          {landPlan.blocked.length>0&&<em>{landPlan.blocked.length} cannot land ({[...new Set(landPlan.blocked.map(item=>landBlockLabel(item.reason)))].join(', ')})</em>}
        </div>}
        {!bulkRemoving&&<div class="git-map-actions">
          <button disabled={bulkBusy||busy||!landPlan.landable.length} onClick={()=>void runBulkLand()}>
            {bulkBusy?'Queueing…':`Land ${landPlan.landable.length}`}
          </button>
          <button disabled={bulkBusy||busy||!removalPlan.removable.length} onClick={()=>setBulkRemoving(true)}>
            Remove {removalPlan.removable.length}…
          </button>
          <small>one land request per branch · the queue runs them one at a time</small>
        </div>}
        {bulkRemoving&&<div class="git-map-actions">
          <button class="danger" disabled={bulkBusy||busy||!removalTargets.length} onClick={()=>void runBulkRemove()}>
            Remove {removalTargets.length} ✓
          </button>
          {removalPlan.warned.length>0&&<label>
            <input type="checkbox" checked={bulkForce} onChange={event=>setBulkForce(event.currentTarget.checked)}/>
            {' '}also remove {removalPlan.warned.length} with uncommitted or unlanded work
          </label>}
          <button onClick={()=>{setBulkRemoving(false);setBulkForce(false)}}>Cancel</button>
        </div>}
        {bulkNote&&<p class="git-state" role="status">{bulkNote}</p>}
      </div>}
      {!overview&&!error&&<p class="git-state">Reading repository…</p>}
      {overview&&filterText&&<p class="git-state" role="status">
        {shownWorktrees.length} of {orderedWorktrees.length} worktrees match “{treeFilter.trim()}”.
      </p>}
      {overview&&shownWorktrees.map(row=>{
        // The row as the Map read it, or - once expanded - the full reading fetched for
        // this one checkout. The two carry the same fields; only the file lists differ.
        const tree=treeDetail[row.path]||row
        const expanded=expandedTree===tree.path,{measured:localMeasured,total}=localMeasurement(tree)
        const landed=tree.branch?landedAt.get(tree.branch):undefined
        const branchRef=overview.comparison.available?overview.comparison.display||overview.comparison.ref:null
        const attached=sessionsFor(tree.path),upstream=attached.find(session=>session.git?.ahead||session.git?.behind)?.git
        const removalBlocked=tree.locked!==null||attached.length>0
        const worktreeName=pathTail(tree.path),identityQualifier=tree.main?'main tree':worktreeName!==tree.branch?worktreeName:''
        // Live landing state belongs on the collapsed row: requiring an expansion to
        // learn which checkout is queued or running turns the map into a search task.
        // Match on the same two coordinates as `GitLandRow`, because the daemon's root
        // can differ from Git's presentation in separator and case, and a removed
        // worktree can still be identified by its branch.
        const activeLandIndex=tree.main?-1:activeLands.findIndex(request=>
          normalizePath(request.worktreeRoot)===normalizePath(tree.path)
          || (!!tree.branch&&request.branch===tree.branch))
        const activeLand=activeLandIndex>=0?activeLands[activeLandIndex]:null
        const activeLandProgress=activeLand?verifyProgressLabel(activeLand.verifyProgress):''
        const activeLandGate=activeLand?landGateNote(activeLand):''
        const activeLandDetail=activeLand
          ? activeLand.state==='queued' ? `#${activeLandIndex+1} in queue`
            : activeLandProgress||activeLandGate||activeLand.reason
          : ''
        const activeLandKind=activeLand?landKindNote(activeLand):''
        // Removing is a property of the *list*, so it survives this row being collapsed
        // and it is the same reading on the fast and the slow path.
        const removing=isRemoving(pendingRemovals,tree.path)
        const assessment=assessments.get(normalizePath(tree.path))
        const blocks=assessment?.blocks||[]
        return <article class={`git-map-row${activeLand?` land-active land-${activeLand.state}`:''}${removing?' removing':''}`} key={tree.path} aria-busy={removing?'true':undefined}>
          {/* The live count is a sibling of the expand button, not a span inside it: it is
              its own affordance now, and interactive content nested in a button is neither
              valid nor reliably clickable. */}
          <div class={`git-map-head${selecting?' selecting':''}`}>
            {selecting&&<label class="git-map-select" title={blocks.length?`Cannot be removed: ${blocks.map(removalBlockLabel).join(', ')}`:`Select ${tree.branch||worktreeName} · Shift-click to select through here`}>
              {/* `onClick`, not `onChange`: a `change` event is not a mouse event and
                  carries no `shiftKey`, so the range press would be indistinguishable
                  from an ordinary one. Click fires for a press on the label and for
                  Space on a focused box alike, so nothing is lost by reading it here. */}
              <input
                type="checkbox"
                checked={!!selected[normalizePath(tree.path)]}
                disabled={removing||blocks.length>0}
                aria-label={`Select ${tree.branch||worktreeName}`}
                onClick={event=>clickSelected(tree.path,event.shiftKey)}
              />
            </label>}
            <button class={`git-map-summary${activeLand?' has-land-status':''}`} aria-expanded={expanded} onClick={()=>setExpandedTree(expanded?'':tree.path)}>
              <span class={`git-map-rail ${tree.main?'main':''}`} aria-hidden="true">{tree.main?'●':'○'}</span>
              <span class="git-map-identity"><strong class={tree.detached?'detached':''}>{tree.branch||`detached @ ${shortSha(tree.head)}`}</strong>{identityQualifier&&<small>{identityQualifier}</small>}</span>
              <span class="git-map-metrics">{localMeasured&&total===0&&<em class="clean">clean</em>}{localMeasured&&total>0&&<em class="local">{total} local</em>}{!localMeasured&&<em class="warn">unavailable</em>}{tree.comparisonCounts?.ahead?<em class="ahead">{tree.comparisonCounts.ahead} ahead</em>:null}{tree.comparisonCounts?.behind?<em>{tree.comparisonCounts.behind} behind</em>:null}{upstream&&<em class="diverged">upstream {upstream.ahead?`↑${upstream.ahead}`:''}{upstream.behind?` ↓${upstream.behind}`:''}</em>}{tree.locked!==null&&<em class="warn">locked</em>}{tree.prunable!==null&&<em class="warn">prunable</em>}{landed!==undefined&&<em class="landed" title={`This queue landed ${tree.branch} at ${new Date(landed*1000).toLocaleString()}`}>landed {landedLabel(landed)}</em>}{removing&&<em class="removing"><i class="git-map-spinner" aria-hidden="true"/>removing…</em>}</span>
              {activeLand&&<span class={`git-map-land-status state-${activeLand.state}`}>
                <i aria-hidden="true"/>
                <strong>{activeLandKind&&`${activeLandKind} · `}{landStateLabel(activeLand.state)}</strong>
                {activeLandDetail&&<small>{activeLandDetail}</small>}
              </span>}
              <span class="git-map-chevron" aria-hidden="true">{expanded?'−':'+'}</span>
            </button>
            {attached.length>0&&<button
              class="git-map-live"
              aria-haspopup="menu"
              title={`${attached.length} live session${attached.length===1?'':'s'} in this worktree`}
              onClick={event=>openLinks(event,`${pathTail(tree.path)} · live sessions`,worktreeLinks(attached))}
            >{attached.length} live</button>}
          </div>
          {expanded&&<div class="git-map-detail"><p class="git-map-path">{tree.path}</p>
            {tree.prunable!==null&&<p class="git-change-empty">Git cannot use this checkout: {tree.prunable||'the worktree registration is prunable'}.</p>}
            {/* The Map's own reading has counts and no files, so a row that says "12
                local" over nothing is waiting for its own read rather than reporting an
                empty change set. Stated, because the two look identical. */}
            {detailBusy===tree.path&&!treeDetail[tree.path]&&<p class="git-state">Reading this worktree…</p>}
            {detailError&&expandedTree===tree.path&&!treeDetail[tree.path]&&<p class="git-state error" role="alert">{detailError}</p>}
            {/* Landing is an act on the checkout in front of you, so the act is drawn on
                the row. Only the act: everything Project-wide is in the strip above, and
                a row that needs one sends the reader there rather than drawing a second
                copy under every worktree. The main tree is the trunk these land *onto*
                and is never a candidate.
                Above the change groups rather than below them (operator decision
                2026-08-22): those groups are unbounded - a branch with sixty changed
                files pushed Land, and the live land state it reports, off the bottom of a
                scroller, so the row's one action was reachable only by scrolling past the
                thing it acts on. The removal control stays at the bottom, where a
                destructive act is not the first thing under the cursor. */}
            {!tree.main&&!tree.bare&&!removing&&<GitLandRow project={project} worktreeRoot={tree.path} branch={tree.branch} detached={tree.detached} queue={landQueue} onChanged={refreshLand} onShowLanding={()=>setLandingOpen(true)}/>}
            {tree.conflicted&&tree.conflicted.total>0&&<ReviewGroup id={`${tree.path}:conflicted`} title="CONFLICTS" summary={tree.conflicted} projectId={project.id} locator={{scope:'conflicted',worktree:tree.path,commit:null,parent:null,comparisonRef:null}} openRoot={tree.path} preview={preview[`${tree.path}:conflicted`]||''} onPreview={value=>setPreview(current=>({...current,[`${tree.path}:conflicted`]:value}))} onReview={file=>startReview(tree.conflicted!,{scope:'conflicted',worktree:tree.path,commit:null,parent:null,comparisonRef:null},file)} onOpen={file=>openFor(tree.path,file.path)}/>}
            {tree.unstaged&&tree.unstaged.total>0&&<ReviewGroup id={`${tree.path}:unstaged`} title="UNSTAGED" summary={tree.unstaged} projectId={project.id} locator={{scope:'unstaged',worktree:tree.path,commit:null,parent:null,comparisonRef:null}} openRoot={tree.path} preview={preview[`${tree.path}:unstaged`]||''} onPreview={value=>setPreview(current=>({...current,[`${tree.path}:unstaged`]:value}))} onReview={file=>startReview(tree.unstaged!,{scope:'unstaged',worktree:tree.path,commit:null,parent:null,comparisonRef:null},file)} onOpen={file=>openFor(tree.path,file.path)}/>}
            {tree.staged&&tree.staged.total>0&&<ReviewGroup id={`${tree.path}:staged`} title="STAGED" summary={tree.staged} projectId={project.id} locator={{scope:'staged',worktree:tree.path,commit:null,parent:null,comparisonRef:null}} openRoot={tree.path} preview={preview[`${tree.path}:staged`]||''} onPreview={value=>setPreview(current=>({...current,[`${tree.path}:staged`]:value}))} onReview={file=>startReview(tree.staged!,{scope:'staged',worktree:tree.path,commit:null,parent:null,comparisonRef:null},file)} onOpen={file=>openFor(tree.path,file.path)}/>}
            {branchRef&&tree.branchDelta&&tree.branchDelta.total>0&&<ReviewGroup id={`${tree.path}:branch`} title={`BRANCH - VS ${branchRef.toUpperCase()}`} summary={tree.branchDelta} projectId={project.id} locator={{scope:'branch',worktree:tree.path,commit:null,parent:null,comparisonRef:overview.comparison.ref}} openRoot={tree.path} preview={preview[`${tree.path}:branch`]||''} onPreview={value=>setPreview(current=>({...current,[`${tree.path}:branch`]:value}))} onReview={file=>startReview(tree.branchDelta!,{scope:'branch',worktree:tree.path,commit:null,parent:null,comparisonRef:overview.comparison.ref},file)} onOpen={file=>openFor(tree.path,file.path)}/>}
            {/* A checkout being deleted is not something to land or to remove again, and
                its own row is not where that is decided any more - the list said it. */}
            {!tree.main&&!tree.bare&&removing&&<p class="git-change-empty">This worktree is being removed.</p>}
            {!tree.main&&!tree.bare&&!removing&&<div class="git-map-actions">{removalBlocked?<p class="git-change-empty">{tree.locked!==null?'Git reports this worktree as locked.':`${attached.length} live session${attached.length===1?' uses':'s use'} this worktree.`}</p>:remove?.path===tree.path?<><button class="danger" disabled={busy} onClick={()=>void removeWorktree()}>{remove.force?'Force remove ✓':'Confirm remove ✓'}</button><label><input type="checkbox" checked={remove.force} onChange={event=>setRemove({path:tree.path,force:event.currentTarget.checked})}/> discard uncommitted files</label><button onClick={()=>setRemove(null)}>Cancel</button></>:<button onClick={()=>setRemove({path:tree.path,force:false})}>Remove worktree…</button>}</div>}
          </div>}
        </article>
      })}
    </>}
    {view==='log'&&<>{!graph&&!error&&<p class="git-state">Reading commit graph…</p>}{graph&&<><div class="git-graph-context" aria-label="Commit graph context">
      <span><b>MAIN TREE</b><strong>{mainTree?.branch||mainTree&&`detached @ ${shortSha(mainTree.head)}`||'unavailable'}</strong>{mainTree?.head&&<code>@ {shortSha(mainTree.head)}</code>}</span>
      <span><b>COMPARE</b><strong>{comparisonLabel||'unavailable'}</strong></span>
      <span><b>WORKTREES</b><strong>{linkedWorktrees.length} linked</strong></span>
      <span><b>SCOPE</b><strong>{graph.filtered?`matching ${logField==='author'?'author':'message'}`:'all refs'}</strong></span>
    </div>{graph.filtered&&<p class="git-state">
      Searching all refs, so the lane drawing is off: Git draws lanes for a continuous
      walk, and over a filtered set they would connect commits that are not connected.
    </p>}{graph.filtered&&graph.lines.length===0&&<p class="git-change-empty">
      No commit {logField==='author'?'author':'message'} matches “{logQuery.trim()}”.
    </p>}<section class="git-graph" aria-label="Commit graph">{graph.lines.map((line,index)=>line.kind==='connector'?<div class="git-graph-connector" key={`c:${index}`}><GraphGlyph value={line.graph}/></div>:(()=>{
         const parent=parentByCommit[line.oid]??line.parents[0]??'',key=`${line.oid}:${parent}`,changes=commitCache.get(key),expanded=expandedCommit===line.oid
         const commitProvenance=provenanceByCommit.get(line.oid)||[]
         const lane=graphNodeLane(line.graph),decorations=graphDecorations(line,overview)
         return <article class="git-graph-row git-review-commit" key={line.oid}><div class="git-commit-head"><button class="git-commit-summary" aria-expanded={expanded} onClick={()=>toggleCommit(line.oid)}><GraphGlyph value={line.graph} commit/><span class="git-commit"><span class="git-commit-title"><strong>{shortSha(line.oid)}</strong><span>{line.subject}</span></span>{decorations.length>0&&<span class="git-commit-refs">{decorations.map((item,refIndex)=><em class={`${item.kind} lane-${lane}`} title={item.title} key={`${item.kind}:${item.label}:${refIndex}`}>{item.label}</em>)}</span>}<small>{line.author}{line.committedAt?` · ${committedLabel(line.committedAt)}`:''}</small></span><span>{expanded?'−':'+'}</span></button>
          {/* Outside the expand button on purpose: a commit's sessions are reachable
              without first expanding it, and a button cannot contain another. */}
          {commitProvenance.length>0&&<button
            class="git-commit-links"
            aria-haspopup="menu"
            title={`${commitProvenance.length} session${commitProvenance.length===1?'':'s'} linked to ${shortSha(line.oid)}`}
            onClick={event=>openLinks(event,`${shortSha(line.oid)} · session links`,provenanceLinks(commitProvenance))}
          >{commitProvenance.length} session link{commitProvenance.length===1?'':'s'}</button>}
          </div>
          {/* The summary row can only ever show one elided line of the subject, so the whole
              message - subject, blank line, body - is reproduced here. `pre-wrap` because a
              commit message is pre-formatted prose: its own hard wraps and paragraph breaks
              are part of what was written, and only the over-long line needs the browser. */}
          {expanded&&<div class="git-commit-detail">{commitProvenance.length>0&&<div class="git-provenance-links">{commitProvenance.map(item=><p key={item.id}>{provenanceSessionButton(item)}<span class={`git-provenance-role ${item.role}`}>{provenanceRoleLabel(item)}</span><span class={`git-provenance-confidence ${item.confidence}`}>{item.confidence}</span>{item.contributedPaths.length>0&&<small title={item.contributedPaths.join('\n')}>{item.contributedPaths.slice(0,3).join(', ')}{item.contributedPaths.length>3?` +${item.contributedPaths.length-3}`:''}</small>}</p>)}</div>}{commitBusy&&!changes&&<p>Loading commit changes…</p>}{commitError&&!changes&&<p class="error">{commitError}</p>}{changes&&<>{changes.message&&<pre class="git-commit-message">{changes.message}</pre>}<div class="git-commit-parent"><span>{changes.parentLabel}</span>{changes.parents.length>1&&<Dropdown ariaLabel="Comparison parent" value={changes.parent||''} onChange={value=>changeParent(line.oid,value)} options={changes.parents.map((oid,index)=>({value:oid,label:index===0?`first parent ${shortSha(oid)}`:shortSha(oid)}))}/>}</div><ReviewGroup id={`commit:${key}`} title="COMMIT CHANGES" summary={changes.summary} projectId={project.id} locator={{scope:'commit',worktree:null,commit:changes.commit,parent:changes.parent,comparisonRef:null}} openRoot={project.root} preview={preview[`commit:${key}`]||''} onPreview={value=>setPreview(current=>({...current,[`commit:${key}`]:value}))} onReview={file=>startReview(changes.summary,{scope:'commit',worktree:null,commit:changes.commit,parent:changes.parent,comparisonRef:null},file,commitProvenance)} onOpen={file=>onOpenFile(file.path)}/></>}</div>}
        </article>
    })())}{graph.hasMore&&graphLimit<GRAPH_MAX&&<button class="git-load-more" onClick={()=>{const next=Math.min(GRAPH_MAX,graphLimit+GRAPH_STEP);setGraphLimit(next);void refreshGraph(next,{query:logQuery,field:logField,regex:logRegex})}}>Load more commits</button>}</section></>}</>}
    {view==='provenance'&&<section class="git-provenance" aria-label="Session Git provenance">
      {provenanceError&&<p class="git-state error" role="alert">{provenanceError}</p>}
      {/* This read is written by the `provenance_graph` consumer, which is a per-Project
          opt-in that is off until someone turns it on. Without this the segment drew
          "No session-to-commit associations recorded yet" on a Project that will never
          record one — an inert surface rendering as merely empty, which is the single
          thing the setting-link rule forbids. */}
      {provenanceOff&&<GrantGate ids={['project.provenanceGraph']} projectId={project.id}
        heading="This Project has not permitted the provenance graph."
        onGranted={()=>{forgetProjectAutomations(project.id);void refreshProvenance(provenanceQuery)}}>
        <p>It records which session and which run produced each commit, from evidence
        swe-mux already captures. Commits made before it is on are not attributed
        retroactively.</p>
      </GrantGate>}
      {!provenanceError&&!provenanceOff&&provenance.length===0&&(provenanceQuery.trim()
        ? <p class="git-change-empty">No recorded commit subject matches “{provenanceQuery.trim()}”.
          This ledger holds only commits swe-mux observed, so Log's own search reaches further.</p>
        : <p class="git-state">No session-to-commit associations recorded yet.</p>)}
      {/* One card per commit, not one per row. The ledger stores a row per
          session because that is what each piece of evidence is about; read back
          flatly, ten occupancy rows bury the one naming who made the commit. */}
      {provenanceGroups.map(group=>{
        const claim=(item:GitProvenance)=><p key={item.id}>{provenanceSessionButton(item)}{item.agentRunId&&<code title={`Agent run ${item.agentRunId}`}>{item.agentRunId.slice(0,8)}</code>}<span class={`git-provenance-role ${item.role}`}>{provenanceRoleLabel(item)}</span><span class={`git-provenance-confidence ${item.confidence}`}>{item.confidence}</span>{item.contributedPaths.length>0&&<small class="git-provenance-paths" title={item.contributedPaths.join('\n')}>{item.contributedPaths.slice(0,4).join(', ')}{item.contributedPaths.length>4?` +${item.contributedPaths.length-4} more`:''}</small>}{item.ambiguous&&<em>{provenanceAmbiguityNote(item)}</em>}</p>
        return <article key={group.commitOid}>
          <div><strong>{shortSha(group.commitOid)}</strong><span>{group.subject||'Commit observed without readable metadata'}</span></div>
          {group.committer&&claim(group.committer)}
          {/* A landing merge names two sessions in two roles: the one that ran
              the merge, and the one whose branch it carries. Both are true, and
              drawing only the first is what made a land read as authorship. */}
          {group.integrator&&claim(group.integrator)}
          {group.branchAuthors.map(claim)}
          {group.contributors.filter(item=>item.id!==group.integrator?.id).map(claim)}
          {group.observers.length>0&&<p class="git-provenance-occupancy"><span class="git-provenance-role observer">{occupancyLabel(group)}</span>{group.observers.slice(0,6).map(item=>provenanceSessionButton(item))}{group.observers.length>6&&<small>{`+${group.observers.length-6} more`}</small>}</p>}
          <small>{group.worktreeRoot} · observed {new Date(group.observedAt*1000).toLocaleString()}</small>
        </article>
      })}
      {/* Checkout facts, kept apart from session claims on purpose. A branch
          landing moves every attached session's HEAD and says nothing about any
          of them, so it belongs here rather than on each of their ledgers. */}
      {refMoves.length>0&&<div class="git-ref-moves"><h4>Reference movements</h4>{refMoves.map(move=><article key={move.id}><div><strong>{shortSha(move.commitOid)}</strong><span>{move.subject||'Moved to a commit without readable metadata'}</span></div><p><span class={`git-ref-move-kind ${move.kind}`}>{refMoveLabel(move)}</span></p><small>{move.worktreeRoot} · from {shortSha(move.previousHead)} · observed {new Date(move.observedAt*1000).toLocaleString()}</small></article>)}</div>}
    </section>}
    {review&&<GitReviewModal project={project} repositoryRoot={overview?.repository.root||project.root} files={review.files} locator={review.locator} initialPath={review.initialPath} truncated={review.truncated} provenance={review.provenance} onClose={()=>setReview(null)} onOpenFile={openFor} onSendToAgent={onSendToAgent}/>}
    {links&&<GitSessionLinks menu={links} onClose={()=>setLinks(null)} onFollow={followLink}/>}
  </div>
}
