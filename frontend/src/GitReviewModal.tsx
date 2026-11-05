import { rich, t } from './localization/index.ts'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { withoutClipboardCapture } from './clipboardHistory'
import { Dropdown } from './Dropdown'
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

const scopeTitle=(scope:ReviewLocator['scope'])=>({get unstaged() { return t("GitReviewModal.05b0213bff") },get staged() { return t("GitReviewModal.88b4f86927") },conflicted:'Conflicts',get branch() { return t("GitReviewModal.410844e68e") },get commit() { return t("GitReviewModal.c137a48499") }})[scope]

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
      setError(t("GitReviewModal.baf6ef069e"))
      return
    }
    const mine=++generation.current
    setLoading(true);setError('')
    try{
      const raw=await api<unknown>('GET',`/api/git/diff?${patchRequestQuery(props.project.id,props.locator,path)}`,undefined,{timeoutMs:20000})
      if(mine!==generation.current)return
      const parsed=parsePatchSnapshot(raw)
      if(!parsed)throw new Error(t("GitReviewModal.ef475920ea"))
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
    {items.map(item=><article class="git-annotation" key={item.key}><p>{item.text}</p><div><button onClick={()=>edit(item)}>{t("GitReviewModal.464c4ffd01")}</button><button onClick={()=>remove(item.key)}>{t("GitReviewModal.e2d0a54968")}</button></div></article>)}
    {anchor && <div class="git-annotation-composer"><label>{rich("GitReviewModal.4281d0d108", { value1: <textarea value={draft} onInput={event=>setDraft(event.currentTarget.value)} autofocus/> })}</label><div><button disabled={!draft.trim()} onClick={save}>{t("GitReviewModal.1509f561f2")}</button><button onClick={()=>{setSelection(null);setDraft('')}}>{t("GitReviewModal.19766ed6cc")}</button></div></div>}
  </div>
  const packet=()=>generateReviewPacket({
    projectName:props.project.name,projectId:props.project.id,repositoryRoot:props.repositoryRoot,locator:props.locator,
    headOid:[...patches.values()].find(item=>item.headOid)?.headOid||null,stale,files:props.files,fileListTruncated:props.truncated,snapshots:patches,annotations,includeFullPatches:includePatches,provenance:props.provenance,
  }).text
  const copy=async(text:string,label:string)=>{
    setRecovery('')
    const ok=await withoutClipboardCapture(()=>copyPreparedText(text))
    if(ok)setNotice(t("GitReviewModal.80a056eab2", { label: label }))
    else{setRecovery(text);setNotice(t("GitReviewModal.3471e28389"))}
  }
  const send=()=>props.onSendToAgent?.({projectId:props.project.id,get label() { return t("GitReviewModal.851515b21d", { value1: scopeTitle(props.locator.scope) }) },scope:'document',message:packet()})
  const currentFileAllowed=!!selected&&selected.status[0]!=='D'&&selected.status[1]!=='D'&&selected.currentExists!==false

  const headingDetail=props.locator.scope==='commit'
    ? `${props.locator.commit?.slice(0,8)} ${props.locator.parent?`vs ${props.locator.parent.slice(0,8)}`:t("GitReviewModal.2135c38eb7")}`
    : `${props.locator.worktree}${props.locator.comparisonRef?` vs ${props.locator.comparisonRef}`:''}`
  return <div class="modal-backdrop git-review-backdrop" role="presentation">
    <section ref={panel} class="modal git-review-modal" role="dialog" aria-modal="true" aria-labelledby="git-review-title">
      <header class="git-review-header">
        <div><h2 id="git-review-title">{props.project.name} - {scopeTitle(props.locator.scope)}</h2><p title={headingDetail}>{headingDetail}</p></div>
        <button aria-label={t("GitReviewModal.baf9d406af")} onClick={props.onClose}>×</button>
      </header>
      {stale && <p class="git-review-stale" role="status">{t("GitReviewModal.002ac5bd2d")}</p>}
      <div class="git-review-layout">
        <nav class="git-review-files" aria-label={t("GitReviewModal.5d4041aa7a")}>
          {props.files.map(file=>{const count=annotations.filter(item=>item.anchor.path===file.path).length;return <button class={file.path===selected?.path?'active':''} title={file.path} onClick={()=>choose(file.path)}><span>{file.path}</span>{count>0 && <b>{count}</b>}</button>})}
          {props.truncated && <p>{t("GitReviewModal.9e2f48fd8a")}</p>}
        </nav>
        <main ref={content} class={`git-review-content ${view} ${wrap?'wrap':'nowrap'}`}>
          <div class="git-review-filebar">
            <button disabled={index<=0} aria-label={t("GitReviewModal.55f8f16499")} onClick={()=>move(-1)}>‹</button>
            <Dropdown ariaLabel={t("GitReviewModal.809052fa4e")} value={selected?.path||''} onChange={choose} options={props.files.map(file=>({value:file.path,label:file.path}))}/>
            <button disabled={index<0||index>=props.files.length-1} aria-label={t("GitReviewModal.123245f14b")} onClick={()=>move(1)}>›</button>
            <button class={view==='unified'?'active':''} onClick={()=>setManualView('unified')}>{t("GitReviewModal.ad6a950b46")}</button>
            <button class={view==='split'?'active':''} onClick={()=>setManualView('split')}>{t("GitReviewModal.32afaa7843")}</button>
            <button class={wrap?'active':''} aria-pressed={wrap} onClick={()=>setWrap(value=>!value)}>{t("GitReviewModal.70bfd7fbcd")}</button>
            <button disabled={!currentFileAllowed} title={currentFileAllowed ? t("GitReviewModal.f7d530a671", { value1: props.locator.worktree||props.project.root }) : selected?.currentExists===false ? t("GitReviewModal.5980a2d722") : t("GitReviewModal.78d2001d3f")} onClick={()=>selected&&props.onOpenFile(props.locator.worktree||props.project.root,selected.path)}>{t("GitReviewModal.1c10e9e29d")}</button>
          </div>
          {loading && <p class="git-diff-state">{t("GitReviewModal.290aeae431")}</p>}
          {error && <div class="git-diff-state error" role="alert"><p>{error}</p><button onClick={()=>selected&&void load(selected.path)}>{t("GitReviewModal.942087cc2d")}</button></div>}
          {!loading&&!error&&snapshot?.binary && <p class="git-diff-state">{t("GitReviewModal.49100f7a7a")}</p>}
          {!loading&&!error&&snapshot?.tooLarge && <p class="git-diff-state">{t("GitReviewModal.e917e21aa4")}</p>}
          {!loading&&!error&&snapshot?.unavailableReason && <p class="git-diff-state">{snapshot.unavailableReason}</p>}
          {!loading&&!error&&snapshot?.patch && <LazyGitDiff patch={snapshot.patch} viewType={view} wrap={wrap} annotations={annotations.filter(item=>item.anchor.path===selected.path)} selectedAnchor={selection} onGutter={gutter} renderWidget={widgets}/>}
        </main>
      </div>
      <footer class="git-review-footer">
        <span aria-live="polite">{rich("GitReviewModal.8ccb225e01", { length: annotations.length, value2: (annotations.length===1) ? 'yes' : 'no', value3: notice ? t("GitReviewModal.3acf01eae0", { notice: notice }) : '' })}</span>
        <label>{rich("GitReviewModal.6628fc2d46", { value1: <input type="checkbox" checked={includePatches} onChange={event=>setIncludePatches(event.currentTarget.checked)}/> })}</label>
        <button disabled={!annotations.length} onClick={()=>setAnnotations([])}>{t("GitReviewModal.6ebb5eccfe")}</button>
        <button onClick={()=>void copy(packet(),t("GitReviewModal.c06840d76f"))}>{t("GitReviewModal.cbe511df4b")}</button>
        <button disabled={!snapshot?.patch} onClick={()=>snapshot?.patch&&void copy(snapshot.patch,t("GitReviewModal.a0e7d0efb9"))}>{t("GitReviewModal.d1aaef5898")}</button>
        <button disabled={!props.onSendToAgent} onClick={send}>{t("GitReviewModal.74915d54b9")}</button>
      </footer>
      {recovery && <label class="git-review-recovery">{rich("GitReviewModal.e12b96cb2e", { value1: <textarea readOnly value={recovery} onFocus={event=>event.currentTarget.select()}/> })}</label>}
    </section>
  </div>
}
