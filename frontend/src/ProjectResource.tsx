import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { Editor, type ContinuityEditorElement, type RailAction } from '@continuity-editor/editor'
import { api } from './api'
import { insertEditorTab } from './editorText'
import { copyPreparedText } from './terminalClipboard'
import { DelimitedTextViewer } from './DelimitedTextViewer'
import { absoluteProjectPath, copySummary, FILE_COPY_MAX_LINES, truncateForClipboard } from './fileClipboard'
import { ImageViewer } from './ImageViewer'
import { clampContextMenuLeft, fitMenuInViewport } from './menuPosition'
import { useModalFocus } from './modalFocus'
import { composeAgentMessage, selectionText } from './noteSelection'
import type { EditorSnapshot } from './noteSelection'
import { findMatches, matchIndexAfter, stepMatchIndex, type FindRange } from './noteFind'
import type { SendToAgentRequest } from './SendToAgentPicker'
import { ProjectNoteEditor } from './ProjectNoteEditor'
import { projectResourceCreationParent } from './projectResourceCreate'
import { fileSaveTarget, noteQueueKey, noteSaveQueue, noteSaveTarget, type NoteSaveState, type ResourceSaveTarget } from './noteSaveQueue'
import { loadExpandedFolders, saveExpandedFolders } from './deviceSettings'
import { currentInsertTarget } from './insertTarget'
import { isFocusTraversalKey } from './keys'
import { REQUEST_TIMEOUT_MS, retryDelay, watchResume } from './liveness'
import type { Project } from './types'

export type ProjectResourceIdentity=
  | {kind:'note'|'session-note'|'files'|'file';id:string}
  | {kind:'worktree-file';id:string;worktree:string}

type NotePayload={revision:string;markdown:string;status:string;path:string}
type FilePresentation=
  | {kind:'text'}
  | {kind:'delimited';delimiter:','|'\t'}
  | {kind:'image';mime?:string;width?:number;height?:number;frames?:number;reason?:string}
  | {kind:'unsupported';reason:string}
type FilePayload={revision:string;status:string;path:string;size:number;text?:string;presentation?:FilePresentation}
type DirectoryItem={name:string;path:string;kind:'directory'|'file';size:number|null}
type DirectoryPayload={path:string;parent:string|null;items:DirectoryItem[];truncated:boolean}
type ResourceEvent={projectId:string;paths:string[];worktree?:string}
type NoteResourceEvent={projectId:string;kind:'note'|'session-note';noteId:string|null;revision:string}
type TreeMenu={item:DirectoryItem;x:number;y:number}
type SearchMode='names'|'contents'|'both'
type SearchHit={name:string;path:string;match:'name'|'content';line:number|null;snippet:string|null}
type SearchPayload={items:SearchHit[];truncated:boolean}
type CreateResourceKind='file'|'directory'
type CreateResourcePrompt={parent:string;kind:CreateResourceKind;name:string}
type CreatedResource=DirectoryItem&{parent:string;hidden:boolean}
type FileDraft={revision:string;text:string;baseline:string;status:string;size:number;presentation:FilePresentation|null;saveState:'idle'|'modified'|'saving'|'saved'|'error';error:string}
type BrowserState={directories:Record<string,DirectoryPayload>;expanded:Set<string>}

// Resource views can be reparented when a tab is dragged between panes. Keep
// their local working state outside the component so that reparenting never
// discards an unsaved file edit or collapses the file tree.
const fileDrafts=new Map<string,FileDraft>()
const browserStates=new Map<string,BrowserState>()

type Props={
  project:Project
  resource:ProjectResourceIdentity
  onOpenFile:(path:string)=>void
  onFileDragStart?:(path:string,event:JSX.TargetedPointerEvent<HTMLElement>)=>void
  /** Opens the send-to-agent dialog. Only the Continuity-backed views (notes and markdown
   *  files) offer it: they are the surfaces that own a real selection. */
  onSendToAgent?:(request:SendToAgentRequest)=>void
}

const parentPath=(path:string)=>path.includes('/')?path.slice(0,path.lastIndexOf('/')):''
const watchIdentity=()=>`resource-${Date.now()}-${Math.random().toString(36).slice(2)}`
const treeKey=(paths:Iterable<string>)=>[...paths].sort().join('\n')
const TREE_LONG_PRESS_MS=550

// Expand state persists to the shared server settings blob. Debounce the write at
// module scope (keyed by project) so a rapid burst of toggles collapses to one PUT
// and a save that is still pending survives the file tab unmounting mid-debounce.
const treeSaveTimers=new Map<string,number>()
function scheduleTreeSave(projectId:string,paths:string[]){
  const pending=treeSaveTimers.get(projectId)
  if(pending)clearTimeout(pending)
  treeSaveTimers.set(projectId,window.setTimeout(()=>{
    treeSaveTimers.delete(projectId)
    void saveExpandedFolders(projectId,paths)
  },400))
}

export function ProjectResource({project,resource,onOpenFile,onFileDragStart,onSendToAgent}:Props){
  const isNote=resource.kind==='note'||resource.kind==='session-note'
  const isFile=resource.kind==='file'||resource.kind==='worktree-file'
  const worktree=resource.kind==='worktree-file'?resource.worktree:undefined
  const fileQuery=(path=resource.id)=>{
    const params=new URLSearchParams({path})
    if(worktree)params.set('worktree',worktree)
    return params.toString()
  }
  // Markdown files opened from the browser render in Continuity and autosave through the same
  // resource-scoped queue as notes; only their save target (endpoint/body) differs.
  const isMarkdownFile=isFile&&/\.(md|markdown|mdx)$/i.test(resource.id)
  const autosaved=isNote||isMarkdownFile
  const saveTarget=():ResourceSaveTarget=>isNote
    ?noteSaveTarget(project.id,resource.kind==='session-note'?resource.id:null)
    :fileSaveTarget(project.id,resource.id,worktree)
  const noteEndpoint=resource.kind==='session-note'
    ?`/api/projects/${project.id}/session-notes/${encodeURIComponent(resource.id)}`
    :`/api/projects/${project.id}/note`
  const noteLabel=resource.kind==='session-note'?'Session note':'Project note'
  const resourceKey=`${project.id}\0${resource.kind}\0${worktree||''}\0${resource.id}`
  const cachedFile=isFile?fileDrafts.get(resourceKey):undefined
  const cachedBrowser=resource.kind==='files'?browserStates.get(resourceKey):undefined
  const [revision,setRevision]=useState(cachedFile?.revision||'missing')
  const [text,setText]=useState(cachedFile?.text||'')
  const [baseline,setBaseline]=useState(cachedFile?.baseline||'')
  const [status,setStatus]=useState(cachedFile?.status||'loading')
  const [fileSize,setFileSize]=useState(cachedFile?.size||0)
  const [presentation,setPresentation]=useState<FilePresentation|null>(cachedFile?.presentation||null)
  const [fileViewMode,setFileViewMode]=useState<'preview'|'raw'>('preview')
  const [saveState,setSaveState]=useState<'idle'|'modified'|'saving'|'saved'|'error'>(cachedFile?.saveState||'idle')
  const [error,setError]=useState(cachedFile?.error||'')
  const [directories,setDirectories]=useState<Record<string,DirectoryPayload>>(cachedBrowser?.directories||{})
  // Seed from the in-memory cache first (survives tab reparent); otherwise from
  // the shared server-persisted set so a refresh or a different device restores it.
  const [expanded,setExpanded]=useState<Set<string>>(()=>cachedBrowser?.expanded||new Set(resource.kind==='files'?loadExpandedFolders(project.id):[]))
  const [treeMenu,setTreeMenu]=useState<TreeMenu|null>(null)
  const [createPrompt,setCreatePrompt]=useState<CreateResourcePrompt|null>(null)
  const [creating,setCreating]=useState(false)
  const [createError,setCreateError]=useState('')
  // Transient "copied" confirmation, and the text of a copy the browser refused to write.
  const [notice,setNotice]=useState('')
  const [pendingCopy,setPendingCopy]=useState<string|null>(null)
  const [query,setQuery]=useState('')
  const [searchMode,setSearchMode]=useState<SearchMode>('names')
  const [filtersOpen,setFiltersOpen]=useState(false)
  const [results,setResults]=useState<SearchHit[]|null>(null)
  const [searching,setSearching]=useState(false)
  const [searchTruncated,setSearchTruncated]=useState(false)
  const searchGeneration=useRef(0)
  // A fresh note/markdown-file load remounts the editor so a new Continuity engine is built.
  const [loadGeneration,setLoadGeneration]=useState(0)
  const [noteSave,setNoteSave]=useState<NoteSaveState>({status:'idle',storageRevision:'missing',banner:null})
  const noteKey=noteQueueKey(project.id,`${resource.kind}:${resource.id}`)
  const watchId=useRef(watchIdentity())
  // Baseline of what expand state is already persisted, so only genuine changes
  // write back; `interacted` stops a remote/late settings load from clobbering a
  // tree the user has started opening by hand.
  const lastSavedTree=useRef<string|undefined>(undefined)
  const interacted=useRef(false)
  const treeMenuPanel=useRef<HTMLDivElement>(null)
  const createPanel=useRef<HTMLFormElement>(null)
  const createNameInput=useRef<HTMLInputElement>(null)
  const treeLongPress=useRef<number|null>(null)
  const treePressOrigin=useRef<{x:number;y:number}|null>(null)
  const treeTouchPress=useRef(false)
  const suppressTreeClick=useRef(false)
  const treeMenuOpenedAt=useRef(0)
  const treeMenuOpenedByTouch=useRef(false)
  const copyFallback=useRef<HTMLTextAreaElement>(null)
  const noticeTimer=useRef<number|undefined>(undefined)
  const noteLoadGeneration=useRef(0)
  const textRef=useRef(text)
  const baselineRef=useRef(baseline)
  textRef.current=text;baselineRef.current=baseline
  // Recovery bookkeeping (see the retry machinery below): whether a first load has ever
  // succeeded, the pending backoff retry, and refs the timers/listeners read at call time
  // so they never run against a stale render's closure.
  const loadedOnce=useRef(false)
  const retryTimer=useRef<number|undefined>(undefined)
  const retryAttempt=useRef(0)
  const recoveryEpoch=useRef(0)
  const inFlightEpoch=useRef<number|null>(null)
  const pendingRetryRef=useRef(false)
  const recoverRef=useRef<()=>Promise<boolean>>(async()=>true)
  const retryNowRef=useRef<()=>void>(()=>{})
  const [pendingRetry,setPendingRetry]=useState(false)
  const expandedRef=useRef(expanded)
  expandedRef.current=expanded
  useModalFocus(createPanel,()=>{if(!creating){setCreatePrompt(null);setCreateError('')}},!!createPrompt)
  useEffect(()=>{
    if(!createPrompt)return
    const frame=requestAnimationFrame(()=>createNameInput.current?.focus())
    return()=>cancelAnimationFrame(frame)
  },[!!createPrompt])

  const loadDirectory=async(folder:string):Promise<boolean>=>{
    try{
      const payload=await api<DirectoryPayload>('GET',`/api/projects/${project.id}/files?path=${encodeURIComponent(folder)}`,undefined,{timeoutMs:REQUEST_TIMEOUT_MS})
      setDirectories(current=>({...current,[folder]:payload}));setError('')
      return true
    }catch(cause){
      setError(cause instanceof Error?cause.message:String(cause))
      // One folder failing gets the same treatment as a failed initial load: the retry
      // reloads the whole saved tree, which includes this folder.
      settleRecovery(false)
      return false
    }
  }

  // Restore a saved tree in one round trip: the root plus every expanded folder.
  // Folders that come back missing (deleted/renamed) are pruned so a stale saved
  // set self-heals; folders expanded while the request was in flight are kept.
  const loadTree=async(want:string[]):Promise<boolean>=>{
    try{
      const params=new URLSearchParams()
      for(const folder of ['',...want])params.append('path',folder)
      const payload=await api<{directories:Record<string,DirectoryPayload>}>('GET',`/api/projects/${project.id}/files/tree?${params.toString()}`,undefined,{timeoutMs:REQUEST_TIMEOUT_MS})
      const loaded=payload.directories||{}
      setDirectories(current=>({...current,...loaded}))
      const requested=new Set(['',...want])
      setExpanded(current=>new Set([...current].filter(folder=>!requested.has(folder)||folder in loaded)))
      setError('')
      loadedOnce.current=true
      return true
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause));return false}
  }

  const loadText=async():Promise<boolean>=>{
    if(resource.kind==='files')return true
    const requestGeneration=isNote?++noteLoadGeneration.current:0
    setStatus('loading');setError('')
    const path=isNote
      ?noteEndpoint
      :`/api/projects/${project.id}/file?${fileQuery()}`
    try{
      const payload=await api<NotePayload|FilePayload>('GET',path,undefined,{timeoutMs:REQUEST_TIMEOUT_MS})
      // A newer request owns the outcome; this one is neither a success nor a failure.
      if(isNote&&requestGeneration!==noteLoadGeneration.current)return true
      const stored='markdown' in payload?payload.markdown:payload.text||''
      // Local work the daemon may not have yet outranks what it just sent us. This is the
      // handoff case: a note moving between the drawer and a pane unmounts one editor and
      // mounts this one against the same save-queue entry, and the flush that unmount fired
      // can still be in flight while this GET is answered from the older file. Without this,
      // arriving would silently show — and then save — the pre-move text.
      const unsaved=autosaved?noteSaveQueue.pendingText(noteKey):null
      const next=unsaved??stored
      setRevision(payload.revision);setText(next);setBaseline(stored);setStatus(payload.status)
      if(!('markdown' in payload)){
        setFileSize(payload.size)
        setPresentation(payload.presentation||(
          payload.text!==undefined?{kind:'text'}:{kind:'unsupported',reason:payload.status}
        ))
      }
      setSaveState(unsaved===null?'idle':'modified')
      if(autosaved){
        // The queue owns the daemon storage revision; the editor starts fresh at 0. Remounting
        // it on a fresh load shows the new text while ordinary edits keep cursor/undo history.
        noteSaveQueue.reset(noteKey,saveTarget(),payload.revision)
        // `reset` clears the queue deliberately (it is the "this is now the truth" signal), so
        // carried-over work has to be re-armed after it or it would never commit.
        if(unsaved!==null)noteSaveQueue.submit(noteKey,unsaved)
        setLoadGeneration(generation=>generation+1)
      }
      loadedOnce.current=true
      return true
    }catch(cause){
      if(isNote&&requestGeneration!==noteLoadGeneration.current)return true
      setError(cause instanceof Error?cause.message:String(cause));setStatus('error')
      return false
    }
  }

  // Adopt a note edited elsewhere (another device, the agent, the file on disk). Yields to
  // an in-progress local edit, which keeps the revision-conflict path in charge instead.
  const followNote=async(remoteRevision=''):Promise<boolean>=>{
    if(!isNote)return true
    if(!noteSaveQueue.canFollowRemote(noteKey,remoteRevision))return true
    const requestGeneration=++noteLoadGeneration.current
    try{
      const payload=await api<NotePayload>('GET',noteEndpoint,undefined,{timeoutMs:REQUEST_TIMEOUT_MS})
      if(requestGeneration!==noteLoadGeneration.current)return true
      // Typing may have started while the GET was in flight. In that case the
      // local editor wins and the existing revision-conflict path stays armed.
      if(!noteSaveQueue.canFollowRemote(noteKey,payload.revision))return true
      setRevision(payload.revision);setText(payload.markdown);setBaseline(payload.markdown)
      setStatus(payload.status);setSaveState('idle');setError('')
      noteSaveQueue.reset(noteKey,saveTarget(),payload.revision)
      setLoadGeneration(generation=>generation+1)
      loadedOnce.current=true
      return true
    }catch(cause){
      if(requestGeneration!==noteLoadGeneration.current)return true
      setError(cause instanceof Error?cause.message:String(cause))
      return false
    }
  }

  // Whatever this tab needs in order to be current again. A note that already has content
  // refreshes through `followNote` so a retry can never discard unsaved typing.
  const recover=async():Promise<boolean>=>{
    if(resource.kind==='files')return loadTree([...expandedRef.current])
    if(isNote&&loadedOnce.current)return followNote()
    return loadText()
  }
  recoverRef.current=recover

  // Load failures are never terminal. A dormant PWA resumes with a network stack that can
  // hang a request outright (the api timeout turns that into a failure), and before this the
  // only way out was closing the tab and reopening it. Failures retry on a backoff, and any
  // resume signal retries at once.
  const clearRetry=()=>{
    if(retryTimer.current===undefined)return
    window.clearTimeout(retryTimer.current)
    retryTimer.current=undefined
  }
  const scheduleRetry=()=>{
    if(retryTimer.current!==undefined)return
    setPendingRetry(true);pendingRetryRef.current=true
    retryTimer.current=window.setTimeout(()=>{
      retryTimer.current=undefined
      // No point re-fetching for a tab nobody is looking at (and mobile throttles the timer
      // anyway); the resume watcher drives the retry the moment it is visible again.
      if(document.hidden){scheduleRetry();return}
      void runRecovery()
    },retryDelay(retryAttempt.current))
  }
  const settleRecovery=(ok:boolean)=>{
    if(ok){retryAttempt.current=0;pendingRetryRef.current=false;setPendingRetry(false);return}
    retryAttempt.current+=1
    scheduleRetry()
  }
  // One attempt at a time per resource, and an attempt started for a resource this tab has
  // since moved off can neither block the new one nor settle its retry state.
  const runRecovery=async()=>{
    const epoch=recoveryEpoch.current
    if(inFlightEpoch.current===epoch)return
    clearRetry()
    inFlightEpoch.current=epoch
    let ok=true
    try{ok=await recoverRef.current()}
    finally{if(inFlightEpoch.current===epoch)inFlightEpoch.current=null}
    if(epoch!==recoveryEpoch.current)return
    settleRecovery(ok)
  }
  const retryNow=()=>{retryAttempt.current=0;clearRetry();void runRecovery()}
  retryNowRef.current=retryNow

  useEffect(()=>{
    setTreeMenu(null)
    setFileViewMode('preview')
    recoveryEpoch.current+=1
    clearRetry();retryAttempt.current=0;pendingRetryRef.current=false;setPendingRetry(false)
    loadedOnce.current=false
    if(isFile&&cachedFile&&cachedFile.text!==cachedFile.baseline){
      setError(cachedFile.error)
      loadedOnce.current=true
      return
    }
    void runRecovery()
  },[project.id,resource.kind,resource.id])

  // Retry on resume, and drop the retry timer when the tab goes away.
  useEffect(()=>{
    const stop=watchResume(()=>{
      if(!pendingRetryRef.current&&retryTimer.current===undefined)return
      retryNowRef.current()
    })
    return()=>{stop();clearRetry()}
  },[])

  useEffect(()=>{
    if(!isFile)return
    // Retained so reparenting a tab between panes never discards an unsaved
    // edit — but *only* while there is something to protect. A clean draft is
    // identical to what a refetch would produce, and keeping every file ever
    // opened accumulated megabytes of strings for the page's lifetime (this app
    // is designed to stay open for days).
    if(text===baseline&&saveState!=='error'){fileDrafts.delete(resourceKey);return}
    fileDrafts.set(resourceKey,{revision,text,baseline,status,size:fileSize,presentation,saveState,error})
  },[resource.kind,resourceKey,revision,text,baseline,status,fileSize,presentation,saveState,error])

  useEffect(()=>{
    if(resource.kind==='files')browserStates.set(resourceKey,{directories,expanded:new Set(expanded)})
  },[resource.kind,resourceKey,directories,expanded])

  // Persist expand changes to the shared server blob. The first pass only records
  // the seeded/restored baseline; after that, user toggles and the self-healing
  // prune write back through the module-level debounce.
  useEffect(()=>{
    if(resource.kind!=='files')return
    const key=treeKey(expanded)
    if(lastSavedTree.current===undefined){lastSavedTree.current=key;return}
    if(key===lastSavedTree.current)return
    lastSavedTree.current=key
    scheduleTreeSave(project.id,[...expanded])
  },[resource.kind,project.id,expanded])

  // The settings cache loads asynchronously at boot and refreshes when another
  // device edits it. Adopt the persisted set whenever it arrives, unless the user
  // has already started opening folders here (then their in-progress tree wins).
  useEffect(()=>{
    if(resource.kind!=='files')return
    const adopt=()=>{
      if(interacted.current)return
      const persisted=loadExpandedFolders(project.id)
      const key=treeKey(persisted)
      if(key===lastSavedTree.current)return
      lastSavedTree.current=key
      setExpanded(new Set(persisted))
      void loadTree(persisted)
    }
    window.addEventListener('mux:settings-changed',adopt)
    return()=>window.removeEventListener('mux:settings-changed',adopt)
  },[resource.kind,project.id])

  useEffect(()=>{
    if(!treeMenu)return
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:null
    const frame=requestAnimationFrame(()=>treeMenuPanel.current?.querySelector<HTMLButtonElement>('button')?.focus())
    const dismiss=()=>setTreeMenu(null)
    const key=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){
        event.preventDefault();event.stopImmediatePropagation();dismiss();return
      }
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return
      const buttons=[...treeMenuPanel.current?.querySelectorAll<HTMLButtonElement>('button')||[]]
      if(!buttons.length)return
      event.preventDefault()
      const current=buttons.indexOf(document.activeElement as HTMLButtonElement)
      const next=event.key==='Home'?0:event.key==='End'?buttons.length-1
        :(Math.max(current,0)+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length
      buttons[next].focus()
    }
    window.addEventListener('mousedown',dismiss)
    window.addEventListener('blur',dismiss)
    window.addEventListener('keydown',key,true)
    return()=>{
      cancelAnimationFrame(frame)
      window.removeEventListener('mousedown',dismiss)
      window.removeEventListener('blur',dismiss)
      window.removeEventListener('keydown',key,true)
      previous?.focus()
    }
  },[treeMenu])

  // Debounced recursive search. An empty query restores the tree; a generation guard drops a
  // slow response that arrives after the query already moved on.
  useEffect(()=>{
    if(resource.kind!=='files')return
    const q=query.trim()
    if(!q){searchGeneration.current++;setResults(null);setSearching(false);setSearchTruncated(false);return}
    setSearching(true)
    const generation=++searchGeneration.current
    const handle=window.setTimeout(()=>{
      void api<SearchPayload>('GET',`/api/projects/${project.id}/search?q=${encodeURIComponent(q)}&mode=${searchMode}`)
        .then(payload=>{if(generation!==searchGeneration.current)return;setResults(payload.items);setSearchTruncated(payload.truncated);setError('')})
        .catch(cause=>{if(generation!==searchGeneration.current)return;setError(cause instanceof Error?cause.message:String(cause));setResults([])})
        .finally(()=>{if(generation===searchGeneration.current)setSearching(false)})
    },250)
    return()=>window.clearTimeout(handle)
  },[resource.kind,project.id,query,searchMode])

  const watchedPaths=resource.kind==='files'
    ?['',...expanded]
    :isFile?[parentPath(resource.id)]:[]
  const watchKey=watchedPaths.sort().join('\n')
  useEffect(()=>{
    if(!watchedPaths.length)return
    const leaseId=`${watchId.current}-${Math.random().toString(36).slice(2)}`
    const renew=()=>void api('PUT',`/api/projects/${project.id}/watch`,{watch_id:leaseId,paths:watchedPaths,worktree}).catch(()=>{})
    renew()
    const timer=window.setInterval(renew,30000)
    return()=>{clearInterval(timer);void api('DELETE',`/api/projects/${project.id}/watch/${encodeURIComponent(leaseId)}`).catch(()=>{})}
  },[project.id,resource.kind,resource.id,watchKey])

  useEffect(()=>{
    const changed=(event:Event)=>{
      const detail=(event as CustomEvent<ResourceEvent>).detail
      if(detail.projectId!==project.id)return
      if(resource.kind==='files'){
        const affected=new Set(detail.paths.map(parentPath))
        for(const folder of Object.keys(directories))if(affected.has(folder))void loadDirectory(folder)
      }else if(isFile&&detail.paths.includes(resource.id)&&(!worktree||detail.worktree===worktree)){
        // A markdown file autosaves through the queue, whose own event carries no revision to
        // distinguish our own echoed write. Rather than reload (and disrupt an active editor)
        // on every file-changed event, leave the local editor authoritative; a genuine
        // out-of-band edit surfaces as a 409 conflict banner on the next save.
        if(isMarkdownFile)return
        if(textRef.current===baselineRef.current)void loadText()
        else setError('File changed externally while this tab has unsaved edits.')
      }
    }
    window.addEventListener('mux:project-files-changed',changed)
    return()=>window.removeEventListener('mux:project-files-changed',changed)
  },[project.id,resource.kind,resource.id,directories])

  useEffect(()=>{
    if(!isNote)return
    // A failed refresh feeds the same retry machinery as a failed load: the events socket
    // reconnecting is exactly when the network is least likely to be ready.
    const follow=async(remoteRevision='')=>settleRecovery(await followNote(remoteRevision))
    const changed=(event:Event)=>{
      const detail=(event as CustomEvent<NoteResourceEvent>).detail
      if(detail.projectId!==project.id||detail.kind!==resource.kind)return
      if(resource.kind==='session-note'&&detail.noteId!==resource.id)return
      void follow(detail.revision)
    }
    const reconnected=()=>void follow()
    window.addEventListener('mux:note-changed',changed)
    window.addEventListener('mux:events-connected',reconnected)
    return()=>{
      window.removeEventListener('mux:note-changed',changed)
      window.removeEventListener('mux:events-connected',reconnected)
      noteLoadGeneration.current++
    }
  },[isNote,noteKey,noteEndpoint,project.id,resource.kind,resource.id])

  // Files save on demand; notes persist through the resource-scoped queue.
  const save=async(content=text,expectedRevision=revision)=>{
    setSaveState('saving')
    try{
      const payload=await api<FilePayload>('PUT',`/api/projects/${project.id}/file`,{path:resource.id,text:content,revision:expectedRevision,worktree})
      setRevision(payload.revision);setBaseline(content);setStatus(payload.status);setFileSize(payload.size);setPresentation(payload.presentation||{kind:'text'});setError('');setSaveState('saved')
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause));setSaveState('error')}
  }

  // Send-to-agent. The Continuity engine (not DOM focus) owns the selection, so a header button
  // can still read what is highlighted after the click has taken focus away from the editor. On
  // touch the same action is registered on Continuity's own rail, where a tap keeps the soft
  // keyboard and the selection where they are.
  const editorElement=useRef<ContinuityEditorElement|null>(null)
  const sourceLabel=resource.kind==='note'?`project note (${project.name})`
    :resource.kind==='session-note'?`session note (${project.name})`
    :resource.id
  const requestSendToAgent=()=>{
    if(!onSendToAgent)return
    let snapshot:EditorSnapshot|null=null
    try{snapshot=editorElement.current?.snapshot()??null}catch{snapshot=null}
    const selected=selectionText(snapshot)
    // An empty selection is not an error: the whole document is the useful fallback, and the
    // dialog says which of the two it is about to send.
    const body=selected||snapshot?.text||textRef.current
    if(!body.trim()){setError('There is nothing to send yet.');return}
    const scope=selected?'selection':'document'
    const slice=truncateForClipboard(body,sourceLabel)
    // Only an actual note selection can be consumed. Whole documents and file selections stay
    // source material, and a truncated payload must never delete text that was not handed off.
    const removeSelectionAfterSend=isNote&&selected&&snapshot&&!slice.truncated?()=>{
      const element=editorElement.current
      if(element){
        try{
          if(element.snapshot().text!==snapshot.text){
            setError('The selection was sent, but the note changed before it could be removed.')
            return
          }
          element.setSelections(snapshot.selections)
          if(!element.insertText(''))setError('The selection was sent, but it could not be removed from the note.')
        }catch{
          setError('The selection was sent, but it could not be removed from the note.')
        }
        return
      }
      // A mobile drawer closes before the send finishes, so its editor is already detached.
      // Reproduce Continuity's exact multi/line/block-selection edit in a short-lived engine,
      // then hand the resulting snapshot to the resource-scoped queue that survives unmounts.
      let scratch:Editor|null=null
      try{
        scratch=new Editor(snapshot.text)
        scratch.setSelections(snapshot.selections)
        if(scratch.insertText(''))noteSaveQueue.submit(noteKey,scratch.snapshot().text)
      }catch{
        // The handoff is already accepted. Preserve the note if the captured engine state
        // cannot be replayed instead of risking a broader text edit.
      }finally{scratch?.destroy()}
    }:undefined
    onSendToAgent({
      projectId:project.id,
      label:sourceLabel,
      scope,
      message:composeAgentMessage(slice.text,{label:sourceLabel,scope}),
      removeSelectionAfterSend,
    })
  }
  // Find in this note. Continuity paints the matches (0.2.17 `setDecorations`) but does not
  // locate them, and the browser's own find cannot stand in: the projection realizes only
  // the lines in the viewport, so an offscreen match is not in the DOM to be found.
  //
  // The ranges live in a ref rather than state. Only the count and the current index are
  // rendered, and re-rendering the whole resource for every match of a broad query on every
  // keystroke would be waste. `findIndexRef` shadows `findIndex` for the same reason the
  // ranges do: the keyboard handlers need the current value synchronously.
  const [findOpen,setFindOpen]=useState(false)
  const [findQuery,setFindQuery]=useState('')
  const [findCase,setFindCase]=useState(false)
  const [findCount,setFindCount]=useState(0)
  const [findIndex,setFindIndex]=useState(0)
  const findRanges=useRef<readonly FindRange[]>([])
  const findIndexRef=useRef(0)
  const findInput=useRef<HTMLInputElement|null>(null)

  /** Paint the set and the current match, and bring that one into view. `active` is
   *  Continuity's own decoration id, so its stronger tint needs no theming from us. */
  const showMatch=(index:number)=>{
    findIndexRef.current=index
    setFindIndex(index)
    const element=editorElement.current
    if(!element)return
    const ranges=findRanges.current
    element.setDecorations('find',ranges)
    const current=ranges[index]
    element.setDecorations('active',current?[current]:[])
    if(current)element.revealRange(current,{align:'nearest'})
  }
  const stepFind=(backwards:boolean)=>{
    if(!findRanges.current.length)return
    showMatch(stepMatchIndex(findRanges.current.length,findIndexRef.current,backwards))
  }
  const openFind=()=>{
    setFindOpen(true)
    requestAnimationFrame(()=>{findInput.current?.focus();findInput.current?.select()})
  }
  /** Leaving the bar selects the match the user stopped on, so the next edit happens where
   *  they were looking rather than wherever the caret sat before the search started. */
  const closeFind=()=>{
    const element=editorElement.current
    const current=findRanges.current[findIndexRef.current]
    findRanges.current=[]
    findIndexRef.current=0
    setFindOpen(false)
    setFindCount(0)
    setFindIndex(0)
    if(!element)return
    element.clearDecorations('find')
    element.clearDecorations('active')
    if(current){
      // A range can go stale if the note changed between the last recompute and this
      // click; landing the caret is a convenience, never worth an error.
      try{element.setSelections([{anchor:current.start,head:current.end,kind:'caret'}],{reveal:true})}catch{/* ignored */}
    }
    element.focus()
  }
  const findRef=useRef(openFind)
  findRef.current=openFind

  // Recompute on every input, and on every edit to the note itself: decorations are
  // positions rather than anchors, so an edit underneath a live set would leave it marking
  // the wrong bytes. Re-running on `continuity-change` is what keeps them honest.
  useEffect(()=>{
    const element=editorElement.current
    if(!findOpen||!element)return
    const recompute=(anchorToCaret:boolean)=>{
      let snapshot:EditorSnapshot|null=null
      try{snapshot=element.snapshot()}catch{snapshot=null}
      const ranges=snapshot?findMatches(snapshot.text,findQuery,{matchCase:findCase}):[]
      findRanges.current=ranges
      setFindCount(ranges.length)
      showMatch(anchorToCaret
        ?matchIndexAfter(ranges,snapshot?.selections[0]?.head??null)
        :Math.min(findIndexRef.current,Math.max(ranges.length-1,0)))
    }
    // Opening (or retyping) resumes from the caret; an edit keeps the user's place.
    recompute(true)
    const onChange=()=>recompute(false)
    element.addEventListener('continuity-change',onChange)
    return()=>element.removeEventListener('continuity-change',onChange)
  },[findOpen,findQuery,findCase])

  // The command dispatches to every mounted resource at once; the one holding the focused
  // editor claims it by cancelling, which is also how the command learns whether any note
  // was focused at all.
  useEffect(()=>{
    if(!autosaved)return
    const onFind=(event:Event)=>{
      const element=editorElement.current
      const target=currentInsertTarget()
      if(!element||target?.kind!=='editor'||target.editor!==element)return
      event.preventDefault()
      openFind()
    }
    window.addEventListener('mux:note-find',onFind)
    return()=>window.removeEventListener('mux:note-find',onFind)
  },[autosaved])

  // A tab pointed at a different note keeps this component, so the bar and its ranges have
  // to go with the old document rather than describing it over the new one.
  useEffect(()=>{
    findRanges.current=[]
    findIndexRef.current=0
    setFindOpen(false)
    setFindQuery('')
    setFindCount(0)
    setFindIndex(0)
  },[project.id,resource.kind,resource.id])

  /**
   * Ctrl+F is handled here rather than through the app's global keybindings on purpose. The
   * ask is "the currently focused note", and a global chord would have to swallow the
   * browser's own find everywhere else in the app to get it. Continuity's `browser-safe`
   * policy leaves the chord alone, so it reaches this handler from inside the editor.
   */
  const handleFindKey=(event:KeyboardEvent&{currentTarget:HTMLElement})=>{
    if(!autosaved||!editable)return
    if(event.key!=='f'&&event.key!=='F')return
    if(!(event.ctrlKey||event.metaKey)||event.altKey)return
    event.preventDefault()
    event.stopPropagation()
    openFind()
  }

  // Assigning `railActions` replaces Continuity's whole host set, so the array is built once
  // (stable identity) and each button reads its current handler through a ref.
  const sendRef=useRef(requestSendToAgent)
  sendRef.current=requestSendToAgent
  const railActions=useMemo<RailAction[]>(()=>{
    const actions:RailAction[]=[]
    if(onSendToAgent)actions.push({
      id:'mux:send-to-agent',
      label:'Send to an agent session',
      glyph:'→',
      run:()=>sendRef.current(),
    })
    // On touch there is no Ctrl+F and no menu bar, so the rail is the only way in.
    actions.push({id:'mux:find',label:'Find in this note',glyph:'⌕',run:()=>findRef.current()})
    return actions
  },[!!onSendToAgent])

  const editable=status==='ready'||status==='missing'
  const isDelimitedFile=isFile&&presentation?.kind==='delimited'
  const imagePresentation=presentation?.kind==='image'?presentation:null
  const isImageFile=isFile&&!!imagePresentation
  const readableFile=isFile
    &&(presentation?.kind==='text'||presentation?.kind==='delimited')
    &&(status==='ready'||status==='read-only')
  const canSendText=!!onSendToAgent&&(isNote?editable:readableFile)
  const imageReady=isImageFile&&status==='viewable'
    &&!!imagePresentation?.mime
    &&typeof imagePresentation.width==='number'
    &&typeof imagePresentation.height==='number'
    &&typeof imagePresentation.frames==='number'
  // Notes and markdown files persist through the resource-scoped save queue (outside this
  // component so a pane close cannot cancel a save); we only mirror its state for display.
  useEffect(()=>{
    if(!autosaved)return
    const unsubscribe=noteSaveQueue.subscribe(noteKey,setNoteSave)
    // The header no longer carries a close button, so flush any pending edit when the
    // resource unmounts (tab close or reparent) instead of relying on that button.
    return()=>{unsubscribe();noteSaveQueue.flush(noteKey)}
  },[autosaved,noteKey])

  const resolveKeepMine=async()=>{
    try{
      const endpoint=isNote?noteEndpoint:`/api/projects/${project.id}/file?${fileQuery()}`
      const payload=await api<{revision:string}>('GET',endpoint,undefined,{timeoutMs:REQUEST_TIMEOUT_MS})
      noteSaveQueue.overwrite(noteKey,payload.revision)
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }

  const toggleDirectory=(path:string)=>{
    interacted.current=true
    if(expanded.has(path)){
      setExpanded(current=>new Set([...current].filter(item=>item!==path&&!item.startsWith(`${path}/`))))
      return
    }
    setExpanded(current=>new Set([...current,path]))
    if(!directories[path])void loadDirectory(path)
  }

  const openTreeMenuAt=(item:DirectoryItem,x:number,y:number)=>{
    treeMenuOpenedAt.current=performance.now()
    treeMenuOpenedByTouch.current=treeTouchPress.current
    setTreeMenu({item,x,y})
  }

  const cancelTreeLongPress=()=>{
    if(treeLongPress.current!==null)window.clearTimeout(treeLongPress.current)
    treeLongPress.current=null
    treePressOrigin.current=null
  }

  const openTreeMenu=(item:DirectoryItem,event:MouseEvent)=>{
    event.preventDefault();event.stopPropagation()
    cancelTreeLongPress()
    if(treeTouchPress.current)suppressTreeClick.current=true
    openTreeMenuAt(item,event.clientX,event.clientY)
  }

  const beginTreePress=(item:DirectoryItem,event:JSX.TargetedPointerEvent<HTMLElement>)=>{
    treeTouchPress.current=event.pointerType==='touch'
    if(!treeTouchPress.current){
      if(item.kind==='file'&&event.button===0)onFileDragStart?.(item.path,event)
      return
    }
    cancelTreeLongPress()
    const {clientX,clientY}=event
    treePressOrigin.current={x:clientX,y:clientY}
    treeLongPress.current=window.setTimeout(()=>{
      treeLongPress.current=null
      treePressOrigin.current=null
      suppressTreeClick.current=true
      navigator.vibrate?.(20)
      openTreeMenuAt(item,clientX,clientY)
    },TREE_LONG_PRESS_MS)
  }

  const trackTreePress=(event:JSX.TargetedPointerEvent<HTMLElement>)=>{
    const origin=treePressOrigin.current
    if(!origin)return
    if(Math.abs(event.clientX-origin.x)>10||Math.abs(event.clientY-origin.y)>10)cancelTreeLongPress()
  }

  const finishTreePress=()=>cancelTreeLongPress()
  const runTreeClick=(action:()=>void)=>{
    if(suppressTreeClick.current){suppressTreeClick.current=false;return}
    action()
  }

  useEffect(()=>()=>cancelTreeLongPress(),[])

  const revealResource=async(item:DirectoryItem)=>{
    setTreeMenu(null)
    try{
      await api('POST',`/api/projects/${project.id}/reveal`,{path:item.path})
      setError('')
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }

  const showNotice=(message:string)=>{
    setNotice(message)
    if(noticeTimer.current)clearTimeout(noticeTimer.current)
    noticeTimer.current=window.setTimeout(()=>{setNotice('');noticeTimer.current=undefined},2600)
  }
  useEffect(()=>()=>{if(noticeTimer.current)clearTimeout(noticeTimer.current)},[])

  const beginCreateResource=(kind:CreateResourceKind)=>{
    if(!treeMenu)return
    const parent=projectResourceCreationParent(treeMenu.item.path,treeMenu.item.kind)
    setTreeMenu(null)
    setCreateError('')
    setCreatePrompt({parent,kind,name:''})
  }

  const submitCreateResource=async(event:Event)=>{
    event.preventDefault()
    if(!createPrompt||creating)return
    setCreating(true);setCreateError('')
    try{
      const created=await api<CreatedResource>('POST',`/api/projects/${project.id}/resources`,{
        parent:createPrompt.parent,
        name:createPrompt.name,
        kind:createPrompt.kind,
      },{timeoutMs:REQUEST_TIMEOUT_MS})
      interacted.current=true
      if(created.kind==='directory'){
        setQuery('')
        setExpanded(current=>{
          const next=new Set(current)
          if(created.parent)next.add(created.parent)
          if(!created.hidden)next.add(created.path)
          return next
        })
      }
      await loadDirectory(created.parent)
      if(created.kind==='directory'&&!created.hidden)await loadDirectory(created.path)
      setCreatePrompt(null);setCreateError('')
      if(created.hidden)setError(`${created.path} was created, but it is hidden by the current ignore rules.`)
      else showNotice(`Created ${created.kind==='directory'?'folder':'file'} ${created.path}`)
      if(created.kind==='file')onOpenFile(created.path)
    }catch(cause){setCreateError(cause instanceof Error?cause.message:String(cause))}
    finally{setCreating(false)}
  }

  // The offscreen textarea gives `copyPreparedText` its synchronous execCommand path, which is
  // what carries the write on mobile and in non-secure contexts where the async Clipboard API
  // is refused. If both routes fail the text is parked in `pendingCopy`, so a blocked copy
  // costs one more tap instead of losing the payload.
  const writeClipboard=async(text:string,summary:string)=>{
    if(await copyPreparedText(text,copyFallback.current)){
      setPendingCopy(null);setError('');showNotice(summary);return true
    }
    setPendingCopy(text)
    setError('Clipboard write was blocked. Copy the prepared text below.')
    return false
  }

  const copyPath=async(item:DirectoryItem,form:'absolute'|'relative')=>{
    setTreeMenu(null)
    const path=form==='absolute'?absoluteProjectPath(project.root,item.path):item.path
    await writeClipboard(path,`Copied ${form} path`)
  }

  // Contents are fetched rather than read from any open editor draft: the tree menu can target
  // a file that is not open, and copying what is on disk is the predictable behaviour.
  const copyContents=async(item:DirectoryItem)=>{
    setTreeMenu(null)
    try{
      const payload=await api<FilePayload>('GET',`/api/projects/${project.id}/file?path=${encodeURIComponent(item.path)}`)
      if(payload.status==='too-large'){setError(`${item.name} is above the 2 MiB read limit and cannot be copied.`);return}
      if(payload.status==='binary'||payload.text===undefined){setError(`${item.name} is not text, so there is nothing to copy.`);return}
      const slice=truncateForClipboard(payload.text,item.path)
      await writeClipboard(slice.text,copySummary(slice))
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }

  // Phase 4 sender coverage: the tree menu can hand any text file to the send-to-agent
  // dialog. Same fetch and gating as the copy action — the file on disk, not an open draft.
  const sendFileToAgent=async(item:DirectoryItem)=>{
    setTreeMenu(null)
    if(!onSendToAgent)return
    try{
      const payload=await api<FilePayload>('GET',`/api/projects/${project.id}/file?path=${encodeURIComponent(item.path)}`)
      if(payload.status==='too-large'){setError(`${item.name} is above the 2 MiB read limit and cannot be sent.`);return}
      if(payload.status==='binary'||payload.text===undefined){setError(`${item.name} is not text, so there is nothing to send.`);return}
      const slice=truncateForClipboard(payload.text,item.path)
      onSendToAgent({
        projectId:project.id,
        label:item.path,
        scope:'document',
        message:composeAgentMessage(slice.text,{label:item.path,scope:'document'}),
      })
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }

  const ignoreResource=async(item:DirectoryItem,scope:'global'|'project')=>{
    setTreeMenu(null)
    try{
      await api('POST',`/api/projects/${project.id}/ignore`,{path:item.path,scope})
      setExpanded(current=>new Set([...current].filter(path=>path!==item.path&&!path.startsWith(`${item.path}/`))))
      setError('')
      await Promise.all(Object.keys(directories).map(loadDirectory))
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }

  const tree=(folder:string,depth=0)=>{
    const directory=directories[folder]
    if(!directory)return expanded.has(folder)?<p class="file-tree-loading" style={{paddingLeft:`${12+depth*15}px`}}>loading…</p>:null
    return <>{directory.items.map(item=>item.kind==='directory'?<div class="file-tree-branch" key={item.path}>
      <button class="file-tree-row directory" style={{paddingLeft:`${9+depth*15}px`}} title="Open folder · right-click or long-press for actions" onPointerDown={event=>beginTreePress(item,event)} onPointerMove={trackTreePress} onPointerUp={finishTreePress} onPointerCancel={finishTreePress} onPointerLeave={finishTreePress} onClick={()=>runTreeClick(()=>toggleDirectory(item.path))} onContextMenu={event=>openTreeMenu(item,event)} aria-expanded={expanded.has(item.path)}><span>{expanded.has(item.path)?'▾':'▸'}</span><strong>{item.name}/</strong></button>
      {expanded.has(item.path)&&tree(item.path,depth+1)}
    </div>:<button class="file-tree-row file" key={item.path} style={{paddingLeft:`${9+depth*15}px`}} title="Open file · right-click or long-press for actions" onPointerDown={event=>beginTreePress(item,event)} onPointerMove={trackTreePress} onPointerUp={finishTreePress} onPointerCancel={finishTreePress} onPointerLeave={finishTreePress} onClick={()=>runTreeClick(()=>onOpenFile(item.path))} onContextMenu={event=>openTreeMenu(item,event)}><span>·</span><strong>{item.name}</strong></button>)}{directory.truncated&&<p class="file-tree-loading" style={{paddingLeft:`${12+depth*15}px`}}>Showing the first 2,000 entries.</p>}</>
  }

  const searchModes:SearchMode[]=['names','contents','both']
  const searchModeLabel:Record<SearchMode,string>={names:'Names',contents:'Contents',both:'Both'}
  const projectRootItem:DirectoryItem={name:'Project root',path:'',kind:'directory',size:null}
  const backgroundTreePress=(event:JSX.TargetedPointerEvent<HTMLElement>)=>{
    if(event.target instanceof Element&&event.target.closest('.file-tree-row'))return
    beginTreePress(projectRootItem,event)
  }
  const backgroundTreeMenu=(event:JSX.TargetedMouseEvent<HTMLElement>)=>{
    if(event.target instanceof Element&&event.target.closest('.file-tree-row'))return
    openTreeMenu(projectRootItem,event)
  }
  // Any error with a retry queued behind it says so, and offers to skip the wait.
  const errorLine=error?<p class="resource-error">{error}{pendingRetry&&<> <button class="resource-retry" onClick={()=>retryNowRef.current()}>Retry now</button></>}</p>:null
  if(resource.kind==='files')return <section class="project-resource file-browser">
    <header><div><strong>{project.root}</strong></div></header>
    <div class="file-browser-body">
      <div class="file-search">
        <div class="file-search-field">
          <div class="file-search-inputwrap">
            <input class="file-search-input" value={query} onInput={event=>setQuery(event.currentTarget.value)} placeholder="Search files…" aria-label="Search files" spellcheck={false} autoCapitalize="off" autoCorrect="off"/>
            {query&&<button class="file-search-clear" aria-label="Clear search" title="Clear search" onClick={()=>setQuery('')}>×</button>}
          </div>
          <button class={`file-search-filter${filtersOpen?' active':''}`} aria-label="Search scope" aria-expanded={filtersOpen} title={`Search scope: ${searchModeLabel[searchMode]}`} onClick={()=>setFiltersOpen(open=>!open)}>
            <svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 5h18l-7 8v6l-4 2v-8z"/></svg>
          </button>
        </div>
        {filtersOpen&&<div class="file-search-modes" role="group" aria-label="Search scope">
          {searchModes.map(mode=><button key={mode} class={searchMode===mode?'active':''} aria-pressed={searchMode===mode} title={`Match file ${searchModeLabel[mode].toLowerCase()}`} onClick={()=>setSearchMode(mode)}>{searchModeLabel[mode]}</button>)}
        </div>}
      </div>
      {errorLine}
      {notice&&<p class="resource-notice" role="status" aria-live="polite">{notice}</p>}
      {results!==null
        ?<div class="file-results" role="list" aria-busy={searching} onPointerDown={backgroundTreePress} onPointerMove={trackTreePress} onPointerUp={finishTreePress} onPointerCancel={finishTreePress} onPointerLeave={finishTreePress} onContextMenu={backgroundTreeMenu}>
          {searching&&!results.length?<p class="file-tree-loading">Searching…</p>
            :!results.length?<p class="file-tree-loading">No matches.</p>
            :<>{results.map(hit=>{const item:DirectoryItem={name:hit.name,path:hit.path,kind:'file',size:null};return <button class="file-tree-row file file-result-row" key={hit.path} title="Open file · right-click or long-press for actions" onPointerDown={event=>beginTreePress(item,event)} onPointerMove={trackTreePress} onPointerUp={finishTreePress} onPointerCancel={finishTreePress} onPointerLeave={finishTreePress} onClick={()=>runTreeClick(()=>onOpenFile(hit.path))} onContextMenu={event=>openTreeMenu(item,event)}>
                <span class="file-result-line"><span>·</span><strong>{hit.name}</strong><small>{hit.path}</small></span>
                {hit.match==='content'&&hit.snippet&&<em class="file-result-snippet">{hit.line!=null?`${hit.line}: `:''}{hit.snippet}</em>}
              </button>})}
              {searchTruncated&&<p class="file-tree-loading">Showing the first {results.length} matches; refine to narrow.</p>}</>}
        </div>
        :<div class="file-tree" role="tree" onPointerDown={backgroundTreePress} onPointerMove={trackTreePress} onPointerUp={finishTreePress} onPointerCancel={finishTreePress} onPointerLeave={finishTreePress} onContextMenu={backgroundTreeMenu}>{tree('')}</div>}
    </div>
    {treeMenu&&<div class="context-menu project-file-menu" ref={el=>{treeMenuPanel.current=el;fitMenuInViewport(el)}} role="menu" aria-label={`File actions for ${treeMenu.item.name}`} style={{left:clampContextMenuLeft(treeMenu.x,window.innerWidth),top:Math.max(4,Math.min(treeMenu.y,window.innerHeight-(treeMenu.item.kind==='file'?320:290)))}} onMouseDown={event=>event.stopPropagation()} onClickCapture={event=>{if(treeMenuOpenedByTouch.current&&performance.now()-treeMenuOpenedAt.current<250){event.preventDefault();event.stopPropagation()}}}>
      <div class="context-title"><strong>{treeMenu.item.name}</strong></div>
      <button role="menuitem" onClick={()=>beginCreateResource('file')}>New file…</button>
      <button role="menuitem" onClick={()=>beginCreateResource('directory')}>New folder…</button>
      <div class="context-rule"/>
      <button role="menuitem" onClick={()=>void revealResource(treeMenu.item)}>Open in default explorer</button>
      <div class="context-rule"/>
      <button role="menuitem" title={absoluteProjectPath(project.root,treeMenu.item.path)} onClick={()=>void copyPath(treeMenu.item,'absolute')}>Copy full path</button>
      {treeMenu.item.path&&<button role="menuitem" title={treeMenu.item.path} onClick={()=>void copyPath(treeMenu.item,'relative')}>Copy path from project root</button>}
      {treeMenu.item.kind==='file'&&<button role="menuitem" title={`Copy the file's text, capped at ${FILE_COPY_MAX_LINES.toLocaleString()} lines`} onClick={()=>void copyContents(treeMenu.item)}>Copy file contents</button>}
      {treeMenu.item.kind==='file'&&onSendToAgent&&<button role="menuitem" title="Open the send-to-agent dialog with this file's contents" onClick={()=>void sendFileToAgent(treeMenu.item)}>Send to an agent session</button>}
      {treeMenu.item.path&&<><div class="context-rule"/>
        <button role="menuitem" onClick={()=>void ignoreResource(treeMenu.item,'global')}>Add pattern to global ignores</button>
        <button role="menuitem" onClick={()=>void ignoreResource(treeMenu.item,'project')}>Add pattern to project ignores</button>
      </>}
    </div>}
    {createPrompt&&<div class="modal-layer resource-create-layer" onPointerDown={event=>{if(event.target===event.currentTarget&&!creating){setCreatePrompt(null);setCreateError('')}}}>
      <form ref={createPanel} class="modal resource-create-modal" role="dialog" aria-modal="true" aria-label={`Create ${createPrompt.kind==='directory'?'folder':'file'}`} onPointerDown={event=>event.stopPropagation()} onSubmit={event=>void submitCreateResource(event)}>
        <div class="modal-heading"><div><span>FILES::{createPrompt.kind==='directory'?'NEW_FOLDER':'NEW_FILE'}</span><h2>New {createPrompt.kind==='directory'?'folder':'file'}</h2></div><button type="button" disabled={creating} aria-label="Cancel creation" onClick={()=>{setCreatePrompt(null);setCreateError('')}}>×</button></div>
        <label>Name<input ref={createNameInput} value={createPrompt.name} disabled={creating} autoComplete="off" spellcheck={false} onInput={event=>setCreatePrompt(current=>current?{...current,name:event.currentTarget.value}:current)}/></label>
        <p class="resource-create-parent" title={createPrompt.parent||project.root}>Create in <strong>{createPrompt.parent||'Project root'}</strong></p>
        {createError&&<p class="resource-create-error" role="alert">{createError}</p>}
        <div class="modal-footer"><button type="button" disabled={creating} onClick={()=>{setCreatePrompt(null);setCreateError('')}}>Cancel</button><button class="primary" disabled={creating||!createPrompt.name} type="submit">{creating?'Creating…':'Create'}</button></div>
      </form>
    </div>}
    <textarea ref={copyFallback} class="resource-copy-relay" tabIndex={-1} aria-hidden="true" readOnly/>
    {pendingCopy!==null&&<div class="resource-copy-fallback" role="dialog" aria-label="Copy blocked text">
      <span>Your browser blocked the write. Copy it once from here.</span>
      <button onClick={()=>void writeClipboard(pendingCopy,'Copied')}>Copy</button>
      <button aria-label="Dismiss blocked copy" onClick={()=>{setPendingCopy(null);setError('')}}>×</button>
      <textarea readOnly value={pendingCopy} aria-label="Text waiting to be copied" onFocus={event=>event.currentTarget.select()}/>
    </div>}
  </section>

  const handleEditorKey=(event:KeyboardEvent&{currentTarget:HTMLTextAreaElement})=>{
    if(!isFocusTraversalKey(event))return
    event.preventDefault()
    const editor=event.currentTarget
    const result=insertEditorTab(text,editor.selectionStart,editor.selectionEnd)
    setText(result.text)
    requestAnimationFrame(()=>editor.setSelectionRange(result.caret,result.caret))
  }
  const stateLabel=autosaved?(noteSave.status==='idle'?status:noteSave.status):(saveState==='idle'?status:saveState)
  const unavailableMessage=status==='binary'?'This file is not UTF-8 text and has no safe viewer.'
    :status==='too-large'&&presentation?.kind==='image'?'This image exceeds the 16 MiB viewer limit.'
    :status==='too-large'?'This file exceeds the 2 MiB text and table limit.'
    :status==='unsupported'?'This file is not a valid allowlisted PNG, JPEG, GIF, or WebP image.'
    :status==='loading'?'Loading…'
    :status==='error'?null
    :'This resource is read-only.'
  return <section class="project-resource file-editor" onKeyDown={handleFindKey}>
    <header><div>{!isNote&&<strong>{resource.id}</strong>}<span>{project.name} · {stateLabel}</span></div>{autosaved||onSendToAgent||isDelimitedFile||(isFile&&!isMarkdownFile&&presentation?.kind==='text')?<div class="resource-actions">
      {/* Continuity-backed views send the live selection (or the document); a plain-text
          editor owns no selection engine, so its send is always the whole document. */}
      {canSendText&&<button class="resource-send" title={autosaved?'Send the selection (or the whole document) to an agent session':'Send the whole document to an agent session'} onClick={requestSendToAgent}>→ agent</button>}
      {autosaved&&<button disabled={!editable} title="Find in this note" aria-label="Find in this note" onClick={openFind}>⌕</button>}
      {isDelimitedFile&&<><button class={fileViewMode==='preview'?'active':''} onClick={()=>setFileViewMode('preview')}>Table</button><button class={fileViewMode==='raw'?'active':''} onClick={()=>setFileViewMode('raw')}>Raw</button></>}
      {isFile&&!isMarkdownFile&&presentation?.kind!=='image'&&presentation?.kind!=='unsupported'&&(!isDelimitedFile||fileViewMode==='raw')&&<button disabled={!editable||text===baseline||saveState==='saving'} onClick={()=>void save()}>Save</button>}
    </div>:null}</header>
    {errorLine}
    {findOpen&&<div class="note-find" role="search">
      <input ref={findInput} value={findQuery} spellcheck={false} placeholder="find in note" aria-label="Find in note"
        onInput={event=>setFindQuery(event.currentTarget.value)}
        onKeyDown={event=>{
          // Escape is stopped here rather than left to bubble: the app's global handler
          // treats it as "close every overlay", which would shut the whole pane's menus
          // when the user only meant to leave the find bar.
          if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeFind()}
          if(event.key==='Enter'){event.preventDefault();stepFind(event.shiftKey)}
        }}/>
      <button title="Previous match" aria-label="Previous match" onClick={()=>stepFind(true)}>↑</button>
      <button title="Next match" aria-label="Next match" onClick={()=>stepFind(false)}>↓</button>
      <button class={findCase?'active':''} title="Match case" aria-pressed={findCase} onClick={()=>setFindCase(value=>!value)}>Aa</button>
      <span class={findQuery&&!findCount?'missing':''} aria-live="polite">{!findQuery?'':findCount?`${findIndex+1}/${findCount}`:'no match'}</span>
      <button title="Close find" aria-label="Close find" onClick={closeFind}>×</button>
    </div>}
    {autosaved&&noteSave.banner&&<p class="resource-error note-conflict"><span>{noteSave.banner}</span>{noteSave.status==='conflict'&&<span class="note-conflict-actions"><button onClick={()=>void loadText()}>Reload from disk</button><button onClick={()=>void resolveKeepMine()}>Overwrite disk</button></span>}</p>}
    {imageReady?<ImageViewer key={revision} projectId={project.id} path={resource.id} revision={revision} mime={imagePresentation!.mime!} width={imagePresentation!.width!} height={imagePresentation!.height!} frames={imagePresentation!.frames!} size={fileSize} worktree={worktree}/>
      :isDelimitedFile&&readableFile&&fileViewMode==='preview'?<DelimitedTextViewer text={text} delimiter={presentation.delimiter}/>
      :editable?(autosaved?<ProjectNoteEditor key={`${project.id}:${resource.kind}:${resource.id}:${loadGeneration}`} projectId={project.id} resourceId={`${resource.kind}:${resource.id}`} initialText={text} label={isNote?noteLabel:resource.id} railActions={railActions} elementRef={editorElement}/>:<textarea value={text} onInput={event=>setText(event.currentTarget.value)} onKeyDown={handleEditorKey} spellcheck={false}/>)
      :isDelimitedFile&&readableFile&&fileViewMode==='raw'?<textarea readOnly value={text} spellcheck={false}/>
      :<div class="resource-unavailable">{status==='error'?<><span>{isNote?'This note could not be loaded.':'This file could not be loaded.'}</span> <button class="resource-retry" onClick={()=>retryNowRef.current()}>Retry now</button></>
        :unavailableMessage
        // Reachable while a retry is still pending, so it says what is happening rather
        // than claiming the resource is read-only.
        }</div>}
  </section>
}
