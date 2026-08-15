import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
import {
  isAbsolutePath,
  localMeasurement,
  normalizePath,
  parseGitGraph,
  parseGitProvenance,
  pathTail,
  sessionGitCwd,
  shortSha,
  type GitGraph,
  type GitProvenance,
} from './gitWorktrees'
import type { Project, Session } from './types'
import { GitFileRow } from './GitFileRow'
import { GitReviewModal } from './GitReviewModal'
import type { SendToAgentRequest } from './SendToAgentPicker'
import {
  comparisonSourceLabel, parseCommitChanges, parseGitOverview,
  type GitCommitChanges, type GitWorktreeOverview,
  type ReviewChangeSummary, type ReviewFileChange,
} from './gitWorktrees'
import type { ReviewLocator } from './gitReview'

// One repository, three readings:
//
//  * Map is the operational projection: one row per worktree, with local files,
//    comparison-ref changes, and the live sessions using the directory.
//  * Log is the repository's real commit DAG. Git computes the ASCII lanes; the browser
//    styles them and attaches the structured commit metadata returned beside each prefix.
//  * Provenance is the durable evidence ledger connecting commits to sessions and runs.
//
// The tab remains deliberately read-mostly. Its only mutations are the worktree add/remove
// operations the Git feature already owns; it never stages, commits, merges, or checks out.

type GitView = 'map' | 'log' | 'provenance'
const GRAPH_STEP = 80
const GRAPH_MAX = 200

function describeGitError(cause: unknown, action: string): string {
  const error = cause as ApiError
  const message = cause instanceof Error ? cause.message : String(cause)
  if (error?.detail?.code !== 'git_timeout' && !error?.timeout) return message
  return `Git did not answer in time. ${action} may still have completed - refresh to see.`
}

function committedLabel(timestamp: number): string {
  if (!timestamp) return ''
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
    .format(new Date(timestamp * 1000))
}

function GraphGlyph({ value }: { value: string }) {
  return <span class="git-graph-glyph" aria-hidden="true">
    {[...value].map((character, index) =>
      <i class={`lane-${Math.floor(index / 2) % 5}`} key={`${index}:${character}`}>{character}</i>)}
  </span>
}

type Props={
  project?:Project
  sessions:Session[]
  onOpenFile:(path:string)=>void
  onOpenWorktreeFile:(worktree:string,path:string)=>void
  onSendToAgent?:(request:SendToAgentRequest)=>void
  onProjectUpdated:(project:Project)=>void
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

export function GitTab({project,sessions,onOpenFile,onOpenWorktreeFile,onSendToAgent,onProjectUpdated}:Props) {
  const [view,setView]=useState<GitView>('map')
  const [overview,setOverview]=useState<GitWorktreeOverview|null>(null)
  const [graph,setGraph]=useState<GitGraph|null>(null)
  const [provenance,setProvenance]=useState<GitProvenance[]>([])
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
  const generation=useRef(0)
  const graphGeneration=useRef(0)
  const provenanceGeneration=useRef(0)

  const refresh=useCallback(async()=>{
    if(!project){setOverview(null);return}
    const mine=++generation.current
    setBusy(true)
    try{
      const raw=await api<unknown>('GET',`/api/git/worktrees?project_id=${encodeURIComponent(project.id)}`,undefined,{timeoutMs:20000})
      if(mine!==generation.current)return
      const parsed=parseGitOverview(raw)
      if(!parsed)throw new Error('The daemon returned an invalid Git overview.')
      setOverview(parsed);setError('');setPreview({})
    }catch(cause){if(mine===generation.current){setOverview(null);setError(describeGitError(cause,'Reading the repository'))}}
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
    if(!project){setProvenance([]);return}
    const mine=++provenanceGeneration.current
    try{
      const raw=await api<unknown>('GET',`/api/git/provenance?project_id=${encodeURIComponent(project.id)}&limit=500`)
      if(mine!==provenanceGeneration.current)return
      setProvenance(parseGitProvenance(raw));setProvenanceError('')
    }catch(cause){if(mine===provenanceGeneration.current)setProvenanceError(cause instanceof Error?cause.message:String(cause))}
  },[project?.id])

  useEffect(()=>{
    setOverview(null);setGraph(null);setProvenance([]);setProvenanceError('');setExpandedTree('');setExpandedCommit('');setCommitCache(new Map());setReview(null);setError('');setCompareOverride(project?.git_compare_ref||'')
    void refresh();void refreshProvenance()
    const changed=()=>{void refresh();void refreshProvenance()}
    window.addEventListener('mux:git-changed',changed);window.addEventListener('mux:events-connected',changed);window.addEventListener('mux:worktree-created',changed);window.addEventListener('mux:worktree-removed',changed)
    return()=>{window.removeEventListener('mux:git-changed',changed);window.removeEventListener('mux:events-connected',changed);window.removeEventListener('mux:worktree-created',changed);window.removeEventListener('mux:worktree-removed',changed)}
  },[refresh,refreshProvenance])
  useEffect(()=>setCompareOverride(project?.git_compare_ref||''),[project?.git_compare_ref])
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

  const saveComparison=async(value:string)=>{
    setBusy(true)
    try{const updated=await api<Project>('PATCH',`/api/projects/${project.id}`,{git_compare_ref:value||null});onProjectUpdated(updated);setCompareOverride(value);setReview(null);await refresh()}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const openFor=(root:string,path:string)=>normalizePath(root)===normalizePath(project.root)?onOpenFile(path):onOpenWorktreeFile(root,path)
  const sessionsFor=(root:string)=>sessions.filter(session=>{
    if(session.project_id!==project.id)return false
    const cwd=normalizePath(sessionGitCwd(session)),worktree=normalizePath(root)
    return cwd===worktree||cwd.startsWith(`${worktree}/`)
  })
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
    catch(cause){setError(describeGitError(cause,'Creating the worktree'))}finally{setBusy(false)}
  }
  const removeWorktree=async()=>{
    if(!remove)return
    setBusy(true)
    try{await api('DELETE','/api/git/worktrees',{cwd:project.root,path:remove.path,force:remove.force});setRemove(null);setExpandedTree('');await refresh()}
    catch(cause){const message=describeGitError(cause,'Removing the worktree');await refresh();setError(message)}finally{setBusy(false)}
  }

  return <div class="git-tab git-review-tab">
    <div class="git-toolbar">
      <div class="git-view-toggle" role="tablist" aria-label="Git view"><button class={view==='map'?'active':''} onClick={()=>setView('map')}>Map</button><button class={view==='log'?'active':''} onClick={()=>setView('log')}>Log</button><button class={view==='provenance'?'active':''} onClick={()=>setView('provenance')}>Provenance</button></div>
      <button disabled={busy} onClick={()=>{window.dispatchEvent(new Event('mux:git-review-refresh'));if(view==='map')void refresh();else if(view==='log')void refreshGraph(graphLimit);void refreshProvenance()}}>↻ Refresh</button>
      {view==='map'&&<button onClick={()=>setAdding(value=>!value)}>+ worktree</button>}
    </div>
    {overview&&<div class="git-compare"><label>COMPARE <select disabled={busy} value={compareOverride} onChange={event=>void saveComparison(event.currentTarget.value)}><option value="">Auto{overview.comparison.available?` (${overview.comparison.display})`:''}</option>{compareOverride&&!overview.comparison.candidates.includes(compareOverride)&&<option value={compareOverride}>{compareOverride} (unavailable)</option>}{overview.comparison.candidates.map(ref=><option value={ref}>{ref}</option>)}</select></label><small>{comparisonSourceLabel(overview.comparison)}</small></div>}
    {error&&<p class="git-state error" role="alert">{error}</p>}
    {adding&&<div class="git-add-form"><label>Absolute path<input value={addForm.path} onInput={event=>setAddForm(value=>({...value,path:event.currentTarget.value}))}/></label><label>New branch<input value={addForm.branch} onInput={event=>setAddForm(value=>({...value,branch:event.currentTarget.value}))}/></label><label>Start point<input value={addForm.start} onInput={event=>setAddForm(value=>({...value,start:event.currentTarget.value}))}/></label><div><button disabled={busy} onClick={()=>void create()}>Create</button><button onClick={()=>setAdding(false)}>Cancel</button></div></div>}
    {view==='map'&&<>
      {!overview&&!error&&<p class="git-state">Reading repository…</p>}
      {overview?.worktrees.map(tree=>{
        const expanded=expandedTree===tree.path,{measured:localMeasured,total}=localMeasurement(tree)
        const branchRef=overview.comparison.available?overview.comparison.display||overview.comparison.ref:null
        const attached=sessionsFor(tree.path),upstream=attached.find(session=>session.git?.ahead||session.git?.behind)?.git
        const removalBlocked=tree.locked!==null||attached.length>0
        const worktreeName=pathTail(tree.path),identityQualifier=tree.main?'main tree':worktreeName!==tree.branch?worktreeName:''
        return <article class="git-map-row" key={tree.path}>
          <button class="git-map-summary" aria-expanded={expanded} onClick={()=>setExpandedTree(expanded?'':tree.path)}>
            <span class={`git-map-rail ${tree.main?'main':''}`} aria-hidden="true">{tree.main?'●':'○'}</span>
            <span class="git-map-identity"><strong class={tree.detached?'detached':''}>{tree.branch||`detached @ ${shortSha(tree.head)}`}</strong>{identityQualifier&&<small>{identityQualifier}</small>}</span>
            <span class="git-map-metrics">{localMeasured&&total===0&&<em class="clean">clean</em>}{localMeasured&&total>0&&<em class="local">{total} local</em>}{!localMeasured&&<em class="warn">unavailable</em>}{tree.comparisonCounts?.ahead?<em>{tree.comparisonCounts.ahead} ahead</em>:null}{tree.comparisonCounts?.behind?<em>{tree.comparisonCounts.behind} behind</em>:null}{upstream&&<em class="diverged">upstream {upstream.ahead?`↑${upstream.ahead}`:''}{upstream.behind?` ↓${upstream.behind}`:''}</em>}{attached.length>0&&<em class="live">{attached.length} live</em>}{tree.locked!==null&&<em class="warn">locked</em>}{tree.prunable!==null&&<em class="warn">prunable</em>}</span>
            <span class="git-map-chevron" aria-hidden="true">{expanded?'−':'+'}</span>
          </button>
          {expanded&&<div class="git-map-detail"><p class="git-map-path">{tree.path}</p>
            {tree.prunable!==null&&<p class="git-change-empty">Git cannot use this checkout: {tree.prunable||'the worktree registration is prunable'}.</p>}
            {tree.conflicted&&tree.conflicted.total>0&&<ReviewGroup id={`${tree.path}:conflicted`} title="CONFLICTS" summary={tree.conflicted} projectId={project.id} locator={{scope:'conflicted',worktree:tree.path,commit:null,parent:null,comparisonRef:null}} openRoot={tree.path} preview={preview[`${tree.path}:conflicted`]||''} onPreview={value=>setPreview(current=>({...current,[`${tree.path}:conflicted`]:value}))} onReview={file=>startReview(tree.conflicted!,{scope:'conflicted',worktree:tree.path,commit:null,parent:null,comparisonRef:null},file)} onOpen={file=>openFor(tree.path,file.path)}/>}
            {tree.unstaged&&tree.unstaged.total>0&&<ReviewGroup id={`${tree.path}:unstaged`} title="UNSTAGED" summary={tree.unstaged} projectId={project.id} locator={{scope:'unstaged',worktree:tree.path,commit:null,parent:null,comparisonRef:null}} openRoot={tree.path} preview={preview[`${tree.path}:unstaged`]||''} onPreview={value=>setPreview(current=>({...current,[`${tree.path}:unstaged`]:value}))} onReview={file=>startReview(tree.unstaged!,{scope:'unstaged',worktree:tree.path,commit:null,parent:null,comparisonRef:null},file)} onOpen={file=>openFor(tree.path,file.path)}/>}
            {tree.staged&&tree.staged.total>0&&<ReviewGroup id={`${tree.path}:staged`} title="STAGED" summary={tree.staged} projectId={project.id} locator={{scope:'staged',worktree:tree.path,commit:null,parent:null,comparisonRef:null}} openRoot={tree.path} preview={preview[`${tree.path}:staged`]||''} onPreview={value=>setPreview(current=>({...current,[`${tree.path}:staged`]:value}))} onReview={file=>startReview(tree.staged!,{scope:'staged',worktree:tree.path,commit:null,parent:null,comparisonRef:null},file)} onOpen={file=>openFor(tree.path,file.path)}/>}
            {branchRef&&tree.branchDelta&&tree.branchDelta.total>0&&<ReviewGroup id={`${tree.path}:branch`} title={`BRANCH - VS ${branchRef.toUpperCase()}`} summary={tree.branchDelta} projectId={project.id} locator={{scope:'branch',worktree:tree.path,commit:null,parent:null,comparisonRef:overview.comparison.ref}} openRoot={tree.path} preview={preview[`${tree.path}:branch`]||''} onPreview={value=>setPreview(current=>({...current,[`${tree.path}:branch`]:value}))} onReview={file=>startReview(tree.branchDelta!,{scope:'branch',worktree:tree.path,commit:null,parent:null,comparisonRef:overview.comparison.ref},file)} onOpen={file=>openFor(tree.path,file.path)}/>}
            {!tree.main&&!tree.bare&&<div class="git-map-actions">{removalBlocked?<p class="git-change-empty">{tree.locked!==null?'Git reports this worktree as locked.':`${attached.length} live session${attached.length===1?' uses':'s use'} this worktree.`}</p>:remove?.path===tree.path?<><button class="danger" disabled={busy} onClick={()=>void removeWorktree()}>{remove.force?'Force remove ✓':'Confirm remove ✓'}</button><label><input type="checkbox" checked={remove.force} onChange={event=>setRemove({path:tree.path,force:event.currentTarget.checked})}/> discard uncommitted files</label><button onClick={()=>setRemove(null)}>Cancel</button></>:<button onClick={()=>setRemove({path:tree.path,force:false})}>Remove worktree…</button>}</div>}
          </div>}
        </article>
      })}
    </>}
    {view==='log'&&<>{!graph&&!error&&<p class="git-state">Reading commit graph…</p>}{graph&&<section class="git-graph" aria-label="Commit graph">{graph.lines.map((line,index)=>line.kind==='connector'?<div class="git-graph-connector" key={`c:${index}`}><GraphGlyph value={line.graph}/></div>:(()=>{
        const parent=parentByCommit[line.oid]??line.parents[0]??'',key=`${line.oid}:${parent}`,changes=commitCache.get(key),expanded=expandedCommit===line.oid
        const commitProvenance=provenanceByCommit.get(line.oid)||[]
        return <article class="git-graph-row git-review-commit" key={line.oid}><button class="git-commit-summary" aria-expanded={expanded} onClick={()=>toggleCommit(line.oid)}><GraphGlyph value={line.graph}/><span class="git-commit"><span class="git-commit-title"><strong>{shortSha(line.oid)}</strong><span>{line.subject}</span></span><small>{line.author}{line.committedAt?` · ${committedLabel(line.committedAt)}`:''}{commitProvenance.length?` · ${commitProvenance.length} session link${commitProvenance.length===1?'':'s'}`:''}</small></span><span>{expanded?'−':'+'}</span></button>
          {/* The summary row can only ever show one elided line of the subject, so the whole
              message - subject, blank line, body - is reproduced here. `pre-wrap` because a
              commit message is pre-formatted prose: its own hard wraps and paragraph breaks
              are part of what was written, and only the over-long line needs the browser. */}
          {expanded&&<div class="git-commit-detail">{commitProvenance.length>0&&<div class="git-provenance-links">{commitProvenance.map(item=><p key={item.id}><strong>{item.sessionName}</strong><span class={`git-provenance-confidence ${item.confidence}`}>{item.relationship} · {item.confidence}</span></p>)}</div>}{commitBusy&&!changes&&<p>Loading commit changes…</p>}{commitError&&!changes&&<p class="error">{commitError}</p>}{changes&&<>{changes.message&&<pre class="git-commit-message">{changes.message}</pre>}<div class="git-commit-parent"><span>{changes.parentLabel}</span>{changes.parents.length>1&&<select aria-label="Comparison parent" value={changes.parent||''} onChange={event=>changeParent(line.oid,event.currentTarget.value)}>{changes.parents.map((oid,index)=><option value={oid}>{index===0?`first parent ${shortSha(oid)}`:shortSha(oid)}</option>)}</select>}</div><ReviewGroup id={`commit:${key}`} title="COMMIT CHANGES" summary={changes.summary} projectId={project.id} locator={{scope:'commit',worktree:null,commit:changes.commit,parent:changes.parent,comparisonRef:null}} openRoot={project.root} preview={preview[`commit:${key}`]||''} onPreview={value=>setPreview(current=>({...current,[`commit:${key}`]:value}))} onReview={file=>startReview(changes.summary,{scope:'commit',worktree:null,commit:changes.commit,parent:changes.parent,comparisonRef:null},file,commitProvenance)} onOpen={file=>onOpenFile(file.path)}/></>}</div>}
        </article>
    })())}{graph.hasMore&&graphLimit<GRAPH_MAX&&<button class="git-load-more" onClick={()=>{const next=Math.min(GRAPH_MAX,graphLimit+GRAPH_STEP);setGraphLimit(next);void refreshGraph(next)}}>Load more commits</button>}</section>}</>}
    {view==='provenance'&&<section class="git-provenance" aria-label="Session Git provenance">{provenanceError&&<p class="git-state error" role="alert">{provenanceError}</p>}{!provenanceError&&provenance.length===0?<p class="git-state">No session-to-commit associations recorded yet.</p>:provenance.map(item=><article key={item.id}><div><strong>{shortSha(item.commitOid)}</strong><span>{item.subject||'Commit observed without readable metadata'}</span></div><p><strong>{item.sessionName}</strong>{item.agentRunId&&<code title={`Agent run ${item.agentRunId}`}>{item.agentRunId.slice(0,8)}</code>}<span class={`git-provenance-confidence ${item.confidence}`}>{item.relationship} · {item.confidence}</span></p><small>{item.worktreeRoot} · observed {new Date(item.observedAt*1000).toLocaleString()}</small>{item.ambiguous&&<em>Several live sessions shared this checkout, so swe-mux cannot identify the author.</em>}</article>)}</section>}
    {review&&<GitReviewModal project={project} repositoryRoot={overview?.repository.root||project.root} files={review.files} locator={review.locator} initialPath={review.initialPath} truncated={review.truncated} provenance={review.provenance} onClose={()=>setReview(null)} onOpenFile={openFor} onSendToAgent={onSendToAgent}/>}
  </div>
}
