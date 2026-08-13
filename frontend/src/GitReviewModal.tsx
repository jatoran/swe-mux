import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { withoutClipboardCapture } from './clipboardHistory'
import { LazyGitDiff } from './LazyGitDiff'
import { useModalFocus } from './modalFocus'
import { copyPreparedText } from './terminalClipboard'
import type { SendToAgentRequest } from './SendToAgentPicker'
import type { Project } from './types'
import { parsePatchSnapshot, type GitPatchSnapshot, type GitProvenance, type ReviewFileChange } from './gitWorktrees'
import {
  annotationKey, deleteAnnotation, effectiveDiffView, extendAnnotationRange, generateReviewPacket,
  markReviewStale, patchRequestQuery, upsertAnnotation,
  type AnnotationAnchor, type GitAnnotation, type ReviewLocator,
} from './gitReview'

type Props={
  project:Project
  repositoryRoot:string
  files:ReviewFileChange[]
  locator:ReviewLocator
  initialPath:string
  truncated:boolean
  provenance?:GitProvenance[]
  onClose:()=>void
  onOpenFile:(worktree:string,path:string)=>void
  onSendToAgent?:(request:SendToAgentRequest)=>void
}

const scopeTitle=(scope:ReviewLocator['scope'])=>({unstaged:'Unstaged changes',staged:'Staged changes',conflicted:'Conflicts',branch:'Branch comparison',commit:'Commit changes'})[scope]

export function GitReviewModal(props:Props) {
  const panel=useRef<HTMLElement>(null)
  const content=useRef<HTMLDivElement>(null)
  const generation=useRef(0)
  const [selectedPath,setSelectedPath]=useState(props.initialPath)
  const [patches,setPatches]=useState<Map<string,GitPatchSnapshot>>(()=>new Map())
  const [loading,setLoading]=useState(false)
  const [error,setError]=useState('')
  const [width,setWidth]=useState(0)
  const [manualView,setManualView]=useState<'split'|'unified'|null>(null)
  const [wrap,setWrap]=useState(false)
  const [annotations,setAnnotations]=useState<GitAnnotation[]>([])
  const [selection,setSelection]=useState<AnnotationAnchor|null>(null)
  const [draft,setDraft]=useState('')
  const [stale,setStale]=useState(false)
  const [includePatches,setIncludePatches]=useState(false)
  const [notice,setNotice]=useState('')
  const [recovery,setRecovery]=useState('')
  useModalFocus(panel,props.onClose)

  const selected=props.files.find(file=>file.path===selectedPath)||props.files[0]
  const snapshot=selected?patches.get(selected.path):undefined
  const view=effectiveDiffView(width,manualView)
  const index=selected?props.files.indexOf(selected):-1

  const load=async(path:string)=>{
    if(patches.has(path))return
    if(stale&&props.locator.scope!=='commit'&&patches.size){
      setError('This local review is stale. Close and reopen it before loading another file.')
      return
    }
    const mine=++generation.current
    setLoading(true);setError('')
    try{
      const raw=await api<unknown>('GET',`/api/git/diff?${patchRequestQuery(props.project.id,props.locator,path)}`,undefined,{timeoutMs:20000})
      if(mine!==generation.current)return
      const parsed=parsePatchSnapshot(raw)
      if(!parsed)throw new Error('The daemon returned an invalid patch snapshot.')
      setPatches(current=>new Map(current).set(path,parsed))
    }catch(cause){if(mine===generation.current)setError(cause instanceof Error?cause.message:String(cause))}
    finally{if(mine===generation.current)setLoading(false)}
  }

  useEffect(()=>{if(selected)void load(selected.path)},[selected?.path])
  useEffect(()=>{
    if(!content.current||typeof ResizeObserver==='undefined')return
    const observer=new ResizeObserver(entries=>setWidth(entries[0]?.contentRect.width||0))
    observer.observe(content.current)
    return()=>observer.disconnect()
  },[])
  useEffect(()=>{
    if(props.locator.scope==='commit')return
    const changed=()=>setStale(current=>markReviewStale(current,props.locator.scope))
    window.addEventListener('mux:git-changed',changed)
    window.addEventListener('mux:git-review-refresh',changed)
    window.addEventListener('mux:worktree-created',changed)
    window.addEventListener('mux:worktree-removed',changed)
    return()=>{
      window.removeEventListener('mux:git-changed',changed)
      window.removeEventListener('mux:git-review-refresh',changed)
      window.removeEventListener('mux:worktree-created',changed)
      window.removeEventListener('mux:worktree-removed',changed)
    }
  },[props.locator.scope])

  const choose=(path:string)=>{setSelectedPath(path);setSelection(null);setDraft('');setError('');setRecovery('')}
  const move=(delta:number)=>{const next=props.files[index+delta];if(next)choose(next.path)}
  const gutter=(side:'old'|'new',line:number,shift:boolean)=>{
    if(!snapshot)return
    const next:AnnotationAnchor={path:selected.path,side,start:line,end:line,patchHash:snapshot.patchSha256}
    const anchor=shift&&selection?extendAnnotationRange(selection,next)||next:next
    setSelection(anchor);setDraft(annotations.find(item=>item.key===`${encodeURIComponent(anchor.path)}:${anchor.side}:${anchor.start}-${anchor.end}:${anchor.patchHash}`)?.text||'')
  }
  const save=()=>{if(!selection)return;setAnnotations(items=>upsertAnnotation(items,selection,draft));setSelection(null);setDraft('')}
  const edit=(annotation:GitAnnotation)=>{setSelection(annotation.anchor);setDraft(annotation.text)}
  const remove=(key:string)=>{setAnnotations(items=>deleteAnnotation(items,key));if(selection&&key===annotationKey(selection)){setSelection(null);setDraft('')}}
  const widgets=(items:GitAnnotation[],anchor:AnnotationAnchor|null)=><div class="git-review-widgets">
    {items.map(item=><article class="git-annotation" key={item.key}><p>{item.text}</p><div><button onClick={()=>edit(item)}>Edit</button><button onClick={()=>remove(item.key)}>Delete</button></div></article>)}
    {anchor&&<div class="git-annotation-composer"><label>Review comment<textarea value={draft} onInput={event=>setDraft(event.currentTarget.value)} autofocus/></label><div><button disabled={!draft.trim()} onClick={save}>Save</button><button onClick={()=>{setSelection(null);setDraft('')}}>Cancel</button></div></div>}
  </div>
  const packet=()=>generateReviewPacket({
    projectName:props.project.name,projectId:props.project.id,repositoryRoot:props.repositoryRoot,locator:props.locator,
    headOid:[...patches.values()].find(item=>item.headOid)?.headOid||null,stale,files:props.files,fileListTruncated:props.truncated,snapshots:patches,annotations,includeFullPatches:includePatches,provenance:props.provenance,
  }).text
  const copy=async(text:string,label:string)=>{
    setRecovery('')
    const ok=await withoutClipboardCapture(()=>copyPreparedText(text))
    if(ok)setNotice(`${label} copied.`)
    else{setRecovery(text);setNotice('Clipboard write was blocked. Copy from the recovery field.')}
  }
  const send=()=>props.onSendToAgent?.({projectId:props.project.id,label:`Git review: ${scopeTitle(props.locator.scope)}`,scope:'document',message:packet()})
  const currentFileAllowed=!!selected&&selected.status[0]!=='D'&&selected.status[1]!=='D'&&selected.currentExists!==false

  const headingDetail=props.locator.scope==='commit'
    ? `${props.locator.commit?.slice(0,8)} ${props.locator.parent?`vs ${props.locator.parent.slice(0,8)}`:'initial commit'}`
    : `${props.locator.worktree}${props.locator.comparisonRef?` vs ${props.locator.comparisonRef}`:''}`
  return <div class="modal-backdrop git-review-backdrop" role="presentation">
    <section ref={panel} class="modal git-review-modal" role="dialog" aria-modal="true" aria-labelledby="git-review-title">
      <header class="git-review-header">
        <div><h2 id="git-review-title">{props.project.name} - {scopeTitle(props.locator.scope)}</h2><p title={headingDetail}>{headingDetail}</p></div>
        <button aria-label="Close Git review" onClick={props.onClose}>×</button>
      </header>
      {stale&&<p class="git-review-stale" role="status">Changes updated - reload review. This frozen snapshot and its annotations were not replaced.</p>}
      <div class="git-review-layout">
        <nav class="git-review-files" aria-label="Changed files">
          {props.files.map(file=>{const count=annotations.filter(item=>item.anchor.path===file.path).length;return <button class={file.path===selected?.path?'active':''} title={file.path} onClick={()=>choose(file.path)}><span>{file.path}</span>{count>0&&<b>{count}</b>}</button>})}
          {props.truncated&&<p>File list truncated.</p>}
        </nav>
        <main ref={content} class={`git-review-content ${view} ${wrap?'wrap':'nowrap'}`}>
          <div class="git-review-filebar">
            <button disabled={index<=0} aria-label="Previous changed file" onClick={()=>move(-1)}>‹</button>
            <select aria-label="Selected changed file" value={selected?.path} onChange={event=>choose(event.currentTarget.value)}>{props.files.map(file=><option value={file.path}>{file.path}</option>)}</select>
            <button disabled={index<0||index>=props.files.length-1} aria-label="Next changed file" onClick={()=>move(1)}>›</button>
            <button class={view==='unified'?'active':''} onClick={()=>setManualView('unified')}>Unified</button>
            <button class={view==='split'?'active':''} onClick={()=>setManualView('split')}>Split</button>
            <button class={wrap?'active':''} aria-pressed={wrap} onClick={()=>setWrap(value=>!value)}>Wrap</button>
            <button disabled={!currentFileAllowed} title={currentFileAllowed?`Open current file in ${props.locator.worktree||props.project.root}`:selected?.currentExists===false?'This historical path is absent from the current working copy.':'Deleted files have no current working copy.'} onClick={()=>selected&&props.onOpenFile(props.locator.worktree||props.project.root,selected.path)}>Open current file</button>
          </div>
          {loading&&<p class="git-diff-state">Loading patch…</p>}
          {error&&<div class="git-diff-state error" role="alert"><p>{error}</p><button onClick={()=>selected&&void load(selected.path)}>Retry</button></div>}
          {!loading&&!error&&snapshot?.binary&&<p class="git-diff-state">Binary file - textual diff unavailable.</p>}
          {!loading&&!error&&snapshot?.tooLarge&&<p class="git-diff-state">Patch exceeds the 1 MiB or 10,000-line review limit.</p>}
          {!loading&&!error&&snapshot?.unavailableReason&&<p class="git-diff-state">{snapshot.unavailableReason}</p>}
          {!loading&&!error&&snapshot?.patch&&<LazyGitDiff patch={snapshot.patch} viewType={view} wrap={wrap} annotations={annotations.filter(item=>item.anchor.path===selected.path)} selectedAnchor={selection} onGutter={gutter} renderWidget={widgets}/>}
        </main>
      </div>
      <footer class="git-review-footer">
        <span aria-live="polite">{annotations.length} annotation{annotations.length===1?'':'s'}{notice?` - ${notice}`:''}</span>
        <label><input type="checkbox" checked={includePatches} onChange={event=>setIncludePatches(event.currentTarget.checked)}/> Include full loaded patches</label>
        <button disabled={!annotations.length} onClick={()=>setAnnotations([])}>Clear annotations</button>
        <button onClick={()=>void copy(packet(),'Review packet')}>Copy review packet</button>
        <button disabled={!snapshot?.patch} onClick={()=>snapshot?.patch&&void copy(snapshot.patch,'Raw patch')}>Copy raw patch</button>
        <button disabled={!props.onSendToAgent} onClick={send}>Send to agent…</button>
      </footer>
      {recovery&&<label class="git-review-recovery">Clipboard recovery<textarea readOnly value={recovery} onFocus={event=>event.currentTarget.select()}/></label>}
    </section>
  </div>
}
