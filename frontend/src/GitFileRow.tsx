import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { LazyGitDiff } from './LazyGitDiff'
import { INLINE_DIFF_ROW_LIMIT, patchRequestQuery, type ReviewLocator } from './gitReview'
import { changeStatusLabel, fileStatLabel, parsePatchSnapshot, type GitPatchSnapshot, type ReviewFileChange } from './gitWorktrees'

type Props={
  projectId:string
  file:ReviewFileChange
  locator:ReviewLocator
  expanded:boolean
  onToggle:()=>void
  onReview:()=>void
  onOpenCurrent:()=>void
  openRoot:string
}

export function GitFileRow(props:Props) {
  const [snapshot,setSnapshot]=useState<GitPatchSnapshot|null>(null)
  const [error,setError]=useState('')
  const [loading,setLoading]=useState(false)
  const generation=useRef(0)
  const load=async()=>{
    const mine=++generation.current
    setLoading(true);setError('')
    try{
      const raw=await api<unknown>('GET',`/api/git/diff?${patchRequestQuery(props.projectId,props.locator,props.file.path)}`,undefined,{timeoutMs:20000})
      if(mine!==generation.current)return
      const parsed=parsePatchSnapshot(raw)
      if(!parsed)throw new Error('The daemon returned an invalid patch snapshot.')
      setSnapshot(parsed)
    }catch(cause){if(mine===generation.current)setError(cause instanceof Error?cause.message:String(cause))}
    finally{if(mine===generation.current)setLoading(false)}
  }
  useEffect(()=>{if(props.expanded&&!snapshot&&!loading)void load()},[props.expanded])
  useEffect(()=>()=>{generation.current+=1},[])
  useEffect(()=>{
    const invalidate=()=>{generation.current+=1;setSnapshot(null);setError('')}
    window.addEventListener('mux:git-changed',invalidate)
    window.addEventListener('mux:git-review-refresh',invalidate)
    return()=>{window.removeEventListener('mux:git-changed',invalidate);window.removeEventListener('mux:git-review-refresh',invalidate)}
  },[])
  const deleted=props.file.status[0]==='D'||props.file.status[1]==='D'
  const unavailable=deleted||props.file.currentExists===false
  const previewId=`git-inline-${encodeURIComponent(props.openRoot)}-${encodeURIComponent(props.file.path)}`
  return <div class={`git-review-file-row ${props.expanded?'expanded':''}`}>
    <div class="git-review-file-main">
      <button class="git-file-caret" aria-label={`${props.expanded?'Collapse':'Preview'} ${props.file.path}`} aria-expanded={props.expanded} aria-controls={previewId} onClick={event=>{event.stopPropagation();props.onToggle()}}>{props.expanded?'▾':'▸'}</button>
      <b class="git-file-status">{changeStatusLabel(props.file.status)}</b>
      <button class="git-file-name" title={props.file.oldPath?`${props.file.oldPath} -> ${props.file.path}`:props.file.path} onClick={event=>{event.stopPropagation();props.onReview()}}>
        {props.file.oldPath&&<del>{props.file.oldPath}</del>}{props.file.oldPath&&<span> → </span>}{props.file.path}
      </button>
      <span class="git-file-stat">{fileStatLabel(props.file)}</span>
      <button class="git-file-open" disabled={unavailable} title={deleted?'Deleted files have no current working copy.':props.file.currentExists===false?'This historical path is absent from the current working copy.':`Open current file in ${props.openRoot}`} onClick={event=>{event.stopPropagation();props.onOpenCurrent()}}>open</button>
    </div>
    {props.expanded&&<div class="git-inline-diff" id={previewId}>
      {loading&&<p class="git-diff-state">Loading preview…</p>}
      {error&&<div class="git-diff-state error"><p>{error}</p><button onClick={()=>void load()}>Retry</button></div>}
      {!loading&&!error&&snapshot?.binary&&<p class="git-diff-state">Binary file - no textual preview.</p>}
      {!loading&&!error&&snapshot?.tooLarge&&<p class="git-diff-state">Patch exceeds the preview limit.</p>}
      {!loading&&!error&&snapshot?.unavailableReason&&<p class="git-diff-state">{snapshot.unavailableReason}</p>}
      {!loading&&!error&&snapshot?.patch&&<LazyGitDiff patch={snapshot.patch} viewType="unified" wrap={false} rowLimit={INLINE_DIFF_ROW_LIMIT}/>}
      <button class="git-open-full-diff" onClick={props.onReview}>Open full diff</button>
    </div>}
  </div>
}
