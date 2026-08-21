import type { JSX } from 'preact'
import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
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
import { useLandQueue } from './landState'
import { GitReviewModal } from './GitReviewModal'
import type { SendToAgentRequest } from './SendToAgentPicker'
import {
  comparisonSourceLabel, parseCommitChanges, parseGitOverview, sortWorktreesByActivity,
  type GitCommitChanges, type GitWorktreeOverview,
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
  const [overview,setOverview]=useState<GitWorktreeOverview|null>(null)
  const [graph,setGraph]=useState<GitGraph|null>(null)
  const [provenance,setProvenance]=useState<GitProvenance[]>([])
  const [provenanceGroups,setProvenanceGroups]=useState<ProvenanceGroup[]>([])
  const [refMoves,setRefMoves]=useState<GitRefMove[]>([])
  const [provenanceError,setProvenanceError]=useState('')
  const [graphLimit,setGraphLimit]=useState(GRAPH_STEP)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [expandedTree,setExpandedTree]=useState('')
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
      const raw=await api<unknown>('GET',`/api/git/worktrees?project_id=${encodeURIComponent(project.id)}`,undefined,{timeoutMs:20000})
      if(mine!==generation.current)return
      const parsed=parseGitOverview(raw)
      if(!parsed)throw new Error('The daemon returned an invalid Git overview.')
      setOverview(parsed);setError('');setPreview({});setNotRepository(false)
    }catch(cause){if(mine===generation.current){
      const missing=(cause as ApiError)?.detail?.code==='not_git_repository'
      setOverview(null);setNotRepository(missing)
      setError(missing?'':describeGitError(cause,'Reading the repository'))
    }}
    finally{if(mine===generation.current)setBusy(false)}
  },[project?.id])

  const refreshGraph=useCallback(async(limit:number)=>{
    if(!project)return
    const mine=++graphGeneration.current
    try{
      const raw=await api<unknown>('GET',`/api/git/graph?project_id=${encodeURIComponent(project.id)}&limit=${limit}`,undefined,{timeoutMs:20000})
      if(mine!==graphGeneration.current)return
      setGraph(parseGitGraph(raw));setError('')
    }catch(cause){if(mine===graphGeneration.current)setError(describeGitError(cause,'Reading the commit graph'))}
  },[project?.id])

  const refreshProvenance=useCallback(async()=>{
    if(!project){setProvenance([]);setProvenanceGroups([]);setRefMoves([]);return}
    const mine=++provenanceGeneration.current
    try{
      const raw=await api<unknown>('GET',`/api/git/provenance?project_id=${encodeURIComponent(project.id)}&limit=500`)
      if(mine!==provenanceGeneration.current)return
      setProvenance(parseGitProvenance(raw));setProvenanceGroups(groupProvenance(raw));setRefMoves(parseGitRefMoves(raw));setProvenanceError('')
    }catch(cause){if(mine===provenanceGeneration.current)setProvenanceError(cause instanceof Error?cause.message:String(cause))}
  },[project?.id])

  useEffect(()=>{
    setOverview(null);setGraph(null);setProvenance([]);setProvenanceGroups([]);setRefMoves([]);setProvenanceError('');setExpandedTree('');setExpandedCommit('');setCommitCache(new Map());setReview(null);setError('');setNotRepository(false);setInitNote('');setCompareOverride(project?.git_compare_ref||'')
    void refresh();void refreshProvenance()
    const changed=()=>{void refresh();void refreshProvenance()}
    window.addEventListener('mux:git-changed',changed);window.addEventListener('mux:events-connected',changed);window.addEventListener('mux:worktree-created',changed);window.addEventListener('mux:worktree-removed',changed)
    return()=>{window.removeEventListener('mux:git-changed',changed);window.removeEventListener('mux:events-connected',changed);window.removeEventListener('mux:worktree-created',changed);window.removeEventListener('mux:worktree-removed',changed)}
  },[refresh,refreshProvenance])
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
    if(!graph)void refreshGraph(graphLimit)
    const changed=()=>void refreshGraph(graphLimit)
    window.addEventListener('mux:git-changed',changed)
    window.addEventListener('mux:events-connected',changed)
    return()=>{window.removeEventListener('mux:git-changed',changed);window.removeEventListener('mux:events-connected',changed)}
  },[view,graphLimit,refreshGraph])

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
      await refresh();void refreshProvenance()
    }catch(cause){setError(describeGitError(cause,'Creating the repository',true))}
    finally{setBusy(false)}
  }
  const removeWorktree=async()=>{
    if(!remove)return
    setBusy(true)
    try{await api('DELETE','/api/git/worktrees',{cwd:project.root,path:remove.path,force:remove.force});setRemove(null);setExpandedTree('');await refresh()}
    catch(cause){const message=describeGitError(cause,'Removing the worktree',true);await refresh();setError(message)}finally{setBusy(false)}
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
      {/* Right-aligned as a group, under the host's segmented control rather than beside
          a toggle of this tab's own. The refresh control is its glyph alone, so it keeps
          an explicit accessible name. */}
      <div class="git-toolbar-actions">
        <button class="git-refresh" disabled={busy} aria-label="Refresh" title="Refresh" onClick={()=>{window.dispatchEvent(new Event('mux:git-review-refresh'));if(view==='map')void refresh();else if(view==='log')void refreshGraph(graphLimit);void refreshProvenance()}}>↻</button>
        {view==='map'&&<button onClick={()=>setAdding(value=>!value)}>+ worktree</button>}
      </div>
    </div>
    {overview&&<div class="git-compare"><label>COMPARE <select disabled={busy} value={compareOverride} onChange={event=>void saveComparison(event.currentTarget.value)}><option value="">Auto{overview.comparison.available?` (${overview.comparison.display})`:''}</option>{compareOverride&&!overview.comparison.candidates.includes(compareOverride)&&<option value={compareOverride}>{compareOverride} (unavailable)</option>}{overview.comparison.candidates.map(ref=><option value={ref}>{ref}</option>)}</select></label><small>{comparisonSourceLabel(overview.comparison)}</small></div>}
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
      {!overview&&!error&&<p class="git-state">Reading repository…</p>}
      {overview&&orderedWorktrees.map(tree=>{
        const expanded=expandedTree===tree.path,{measured:localMeasured,total}=localMeasurement(tree)
        const branchRef=overview.comparison.available?overview.comparison.display||overview.comparison.ref:null
        const attached=sessionsFor(tree.path),upstream=attached.find(session=>session.git?.ahead||session.git?.behind)?.git
        const removalBlocked=tree.locked!==null||attached.length>0
        const worktreeName=pathTail(tree.path),identityQualifier=tree.main?'main tree':worktreeName!==tree.branch?worktreeName:''
        return <article class="git-map-row" key={tree.path}>
          {/* The live count is a sibling of the expand button, not a span inside it: it is
              its own affordance now, and interactive content nested in a button is neither
              valid nor reliably clickable. */}
          <div class="git-map-head">
            <button class="git-map-summary" aria-expanded={expanded} onClick={()=>setExpandedTree(expanded?'':tree.path)}>
              <span class={`git-map-rail ${tree.main?'main':''}`} aria-hidden="true">{tree.main?'●':'○'}</span>
              <span class="git-map-identity"><strong class={tree.detached?'detached':''}>{tree.branch||`detached @ ${shortSha(tree.head)}`}</strong>{identityQualifier&&<small>{identityQualifier}</small>}</span>
              <span class="git-map-metrics">{localMeasured&&total===0&&<em class="clean">clean</em>}{localMeasured&&total>0&&<em class="local">{total} local</em>}{!localMeasured&&<em class="warn">unavailable</em>}{tree.comparisonCounts?.ahead?<em class="ahead">{tree.comparisonCounts.ahead} ahead</em>:null}{tree.comparisonCounts?.behind?<em>{tree.comparisonCounts.behind} behind</em>:null}{upstream&&<em class="diverged">upstream {upstream.ahead?`↑${upstream.ahead}`:''}{upstream.behind?` ↓${upstream.behind}`:''}</em>}{tree.locked!==null&&<em class="warn">locked</em>}{tree.prunable!==null&&<em class="warn">prunable</em>}</span>
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
            {tree.conflicted&&tree.conflicted.total>0&&<ReviewGroup id={`${tree.path}:conflicted`} title="CONFLICTS" summary={tree.conflicted} projectId={project.id} locator={{scope:'conflicted',worktree:tree.path,commit:null,parent:null,comparisonRef:null}} openRoot={tree.path} preview={preview[`${tree.path}:conflicted`]||''} onPreview={value=>setPreview(current=>({...current,[`${tree.path}:conflicted`]:value}))} onReview={file=>startReview(tree.conflicted!,{scope:'conflicted',worktree:tree.path,commit:null,parent:null,comparisonRef:null},file)} onOpen={file=>openFor(tree.path,file.path)}/>}
            {tree.unstaged&&tree.unstaged.total>0&&<ReviewGroup id={`${tree.path}:unstaged`} title="UNSTAGED" summary={tree.unstaged} projectId={project.id} locator={{scope:'unstaged',worktree:tree.path,commit:null,parent:null,comparisonRef:null}} openRoot={tree.path} preview={preview[`${tree.path}:unstaged`]||''} onPreview={value=>setPreview(current=>({...current,[`${tree.path}:unstaged`]:value}))} onReview={file=>startReview(tree.unstaged!,{scope:'unstaged',worktree:tree.path,commit:null,parent:null,comparisonRef:null},file)} onOpen={file=>openFor(tree.path,file.path)}/>}
            {tree.staged&&tree.staged.total>0&&<ReviewGroup id={`${tree.path}:staged`} title="STAGED" summary={tree.staged} projectId={project.id} locator={{scope:'staged',worktree:tree.path,commit:null,parent:null,comparisonRef:null}} openRoot={tree.path} preview={preview[`${tree.path}:staged`]||''} onPreview={value=>setPreview(current=>({...current,[`${tree.path}:staged`]:value}))} onReview={file=>startReview(tree.staged!,{scope:'staged',worktree:tree.path,commit:null,parent:null,comparisonRef:null},file)} onOpen={file=>openFor(tree.path,file.path)}/>}
            {branchRef&&tree.branchDelta&&tree.branchDelta.total>0&&<ReviewGroup id={`${tree.path}:branch`} title={`BRANCH - VS ${branchRef.toUpperCase()}`} summary={tree.branchDelta} projectId={project.id} locator={{scope:'branch',worktree:tree.path,commit:null,parent:null,comparisonRef:overview.comparison.ref}} openRoot={tree.path} preview={preview[`${tree.path}:branch`]||''} onPreview={value=>setPreview(current=>({...current,[`${tree.path}:branch`]:value}))} onReview={file=>startReview(tree.branchDelta!,{scope:'branch',worktree:tree.path,commit:null,parent:null,comparisonRef:overview.comparison.ref},file)} onOpen={file=>openFor(tree.path,file.path)}/>}
            {/* Landing is an act on the checkout in front of you, so the act is drawn on
                the row. Only the act: everything Project-wide is in the strip above, and
                a row that needs one sends the reader there rather than drawing a second
                copy under every worktree. The main tree is the trunk these land *onto*
                and is never a candidate. */}
            {!tree.main&&!tree.bare&&<GitLandRow project={project} worktreeRoot={tree.path} branch={tree.branch} detached={tree.detached} queue={landQueue} onChanged={refreshLand} onShowLanding={()=>setLandingOpen(true)}/>}
            {!tree.main&&!tree.bare&&<div class="git-map-actions">{removalBlocked?<p class="git-change-empty">{tree.locked!==null?'Git reports this worktree as locked.':`${attached.length} live session${attached.length===1?' uses':'s use'} this worktree.`}</p>:remove?.path===tree.path?<><button class="danger" disabled={busy} onClick={()=>void removeWorktree()}>{remove.force?'Force remove ✓':'Confirm remove ✓'}</button><label><input type="checkbox" checked={remove.force} onChange={event=>setRemove({path:tree.path,force:event.currentTarget.checked})}/> discard uncommitted files</label><button onClick={()=>setRemove(null)}>Cancel</button></>:<button onClick={()=>setRemove({path:tree.path,force:false})}>Remove worktree…</button>}</div>}
          </div>}
        </article>
      })}
    </>}
    {view==='log'&&<>{!graph&&!error&&<p class="git-state">Reading commit graph…</p>}{graph&&<><div class="git-graph-context" aria-label="Commit graph context">
      <span><b>MAIN TREE</b><strong>{mainTree?.branch||mainTree&&`detached @ ${shortSha(mainTree.head)}`||'unavailable'}</strong>{mainTree?.head&&<code>@ {shortSha(mainTree.head)}</code>}</span>
      <span><b>COMPARE</b><strong>{comparisonLabel||'unavailable'}</strong></span>
      <span><b>WORKTREES</b><strong>{linkedWorktrees.length} linked</strong></span>
      <span><b>SCOPE</b><strong>all refs</strong></span>
    </div><section class="git-graph" aria-label="Commit graph">{graph.lines.map((line,index)=>line.kind==='connector'?<div class="git-graph-connector" key={`c:${index}`}><GraphGlyph value={line.graph}/></div>:(()=>{
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
          {expanded&&<div class="git-commit-detail">{commitProvenance.length>0&&<div class="git-provenance-links">{commitProvenance.map(item=><p key={item.id}>{provenanceSessionButton(item)}<span class={`git-provenance-role ${item.role}`}>{provenanceRoleLabel(item)}</span><span class={`git-provenance-confidence ${item.confidence}`}>{item.confidence}</span>{item.contributedPaths.length>0&&<small title={item.contributedPaths.join('\n')}>{item.contributedPaths.slice(0,3).join(', ')}{item.contributedPaths.length>3?` +${item.contributedPaths.length-3}`:''}</small>}</p>)}</div>}{commitBusy&&!changes&&<p>Loading commit changes…</p>}{commitError&&!changes&&<p class="error">{commitError}</p>}{changes&&<>{changes.message&&<pre class="git-commit-message">{changes.message}</pre>}<div class="git-commit-parent"><span>{changes.parentLabel}</span>{changes.parents.length>1&&<select aria-label="Comparison parent" value={changes.parent||''} onChange={event=>changeParent(line.oid,event.currentTarget.value)}>{changes.parents.map((oid,index)=><option value={oid}>{index===0?`first parent ${shortSha(oid)}`:shortSha(oid)}</option>)}</select>}</div><ReviewGroup id={`commit:${key}`} title="COMMIT CHANGES" summary={changes.summary} projectId={project.id} locator={{scope:'commit',worktree:null,commit:changes.commit,parent:changes.parent,comparisonRef:null}} openRoot={project.root} preview={preview[`commit:${key}`]||''} onPreview={value=>setPreview(current=>({...current,[`commit:${key}`]:value}))} onReview={file=>startReview(changes.summary,{scope:'commit',worktree:null,commit:changes.commit,parent:changes.parent,comparisonRef:null},file,commitProvenance)} onOpen={file=>onOpenFile(file.path)}/></>}</div>}
        </article>
    })())}{graph.hasMore&&graphLimit<GRAPH_MAX&&<button class="git-load-more" onClick={()=>{const next=Math.min(GRAPH_MAX,graphLimit+GRAPH_STEP);setGraphLimit(next);void refreshGraph(next)}}>Load more commits</button>}</section></>}</>}
    {view==='provenance'&&<section class="git-provenance" aria-label="Session Git provenance">
      {provenanceError&&<p class="git-state error" role="alert">{provenanceError}</p>}
      {/* This read is written by the `provenance_graph` consumer, which is a per-Project
          opt-in that is off until someone turns it on. Without this the segment drew
          "No session-to-commit associations recorded yet" on a Project that will never
          record one — an inert surface rendering as merely empty, which is the single
          thing the setting-link rule forbids. */}
      {provenanceOff&&<GrantGate ids={['project.provenanceGraph']} projectId={project.id}
        heading="This Project has not permitted the provenance graph."
        onGranted={()=>{forgetProjectAutomations(project.id);void refreshProvenance()}}>
        <p>It records which session and which run produced each commit, from evidence
        swe-mux already captures. Commits made before it is on are not attributed
        retroactively.</p>
      </GrantGate>}
      {!provenanceError&&!provenanceOff&&provenance.length===0&&<p class="git-state">No session-to-commit associations recorded yet.</p>}
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
