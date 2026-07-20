import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { insertEditorTab } from './editorText'
import { clampContextMenuLeft } from './menuPosition'
import { ProjectNoteEditor } from './ProjectNoteEditor'
import { noteQueueKey, noteSaveQueue, type NoteSaveState } from './noteSaveQueue'
import type { Project } from './types'

export type ProjectResourceIdentity={kind:'note'|'session-note'|'files'|'file';id:string}

type NotePayload={revision:string;markdown:string;status:string;path:string}
type FilePayload={revision:string;status:string;path:string;size:number;text?:string}
type DirectoryItem={name:string;path:string;kind:'directory'|'file';size:number|null}
type DirectoryPayload={path:string;parent:string|null;items:DirectoryItem[];truncated:boolean}
type ResourceEvent={projectId:string;paths:string[]}
type NoteResourceEvent={projectId:string;kind:'note'|'session-note';noteId:string|null;revision:string}
type TreeMenu={item:DirectoryItem;x:number;y:number}
type FileDraft={revision:string;text:string;baseline:string;status:string;saveState:'idle'|'modified'|'saving'|'saved'|'error';error:string}
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
  onClose:()=>void
}

const parentPath=(path:string)=>path.includes('/')?path.slice(0,path.lastIndexOf('/')):''
const watchIdentity=()=>`resource-${Date.now()}-${Math.random().toString(36).slice(2)}`

export function ProjectResource({project,resource,onOpenFile,onClose}:Props){
  const isNote=resource.kind==='note'||resource.kind==='session-note'
  const noteEndpoint=resource.kind==='session-note'
    ?`/api/projects/${project.id}/session-notes/${encodeURIComponent(resource.id)}`
    :`/api/projects/${project.id}/note`
  const noteLabel=resource.kind==='session-note'?'Session note':'Project note'
  const resourceKey=`${project.id}\0${resource.kind}\0${resource.id}`
  const cachedFile=resource.kind==='file'?fileDrafts.get(resourceKey):undefined
  const cachedBrowser=resource.kind==='files'?browserStates.get(resourceKey):undefined
  const [revision,setRevision]=useState(cachedFile?.revision||'missing')
  const [text,setText]=useState(cachedFile?.text||'')
  const [baseline,setBaseline]=useState(cachedFile?.baseline||'')
  const [status,setStatus]=useState(cachedFile?.status||'loading')
  const [saveState,setSaveState]=useState<'idle'|'modified'|'saving'|'saved'|'error'>(cachedFile?.saveState||'idle')
  const [error,setError]=useState(cachedFile?.error||'')
  const [directories,setDirectories]=useState<Record<string,DirectoryPayload>>(cachedBrowser?.directories||{})
  const [expanded,setExpanded]=useState<Set<string>>(cachedBrowser?.expanded||new Set())
  const [treeMenu,setTreeMenu]=useState<TreeMenu|null>(null)
  // A fresh note load remounts the editor so a new Continuity engine is built.
  const [loadGeneration,setLoadGeneration]=useState(0)
  const [noteSave,setNoteSave]=useState<NoteSaveState>({status:'idle',storageRevision:'missing',banner:null})
  const noteKey=noteQueueKey(project.id,`${resource.kind}:${resource.id}`)
  const watchId=useRef(watchIdentity())
  const treeMenuPanel=useRef<HTMLDivElement>(null)
  const noteLoadGeneration=useRef(0)
  const textRef=useRef(text)
  const baselineRef=useRef(baseline)
  textRef.current=text;baselineRef.current=baseline

  const loadDirectory=async(folder:string)=>{
    try{
      const payload=await api<DirectoryPayload>('GET',`/api/projects/${project.id}/files?path=${encodeURIComponent(folder)}`)
      setDirectories(current=>({...current,[folder]:payload}));setError('')
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }

  const loadText=async()=>{
    if(resource.kind==='files')return
    const requestGeneration=isNote?++noteLoadGeneration.current:0
    setStatus('loading');setError('')
    const path=isNote
      ?noteEndpoint
      :`/api/projects/${project.id}/file?path=${encodeURIComponent(resource.id)}`
    try{
      const payload=await api<NotePayload|FilePayload>('GET',path)
      if(isNote&&requestGeneration!==noteLoadGeneration.current)return
      const next='markdown' in payload?payload.markdown:payload.text||''
      setRevision(payload.revision);setText(next);setBaseline(next);setStatus(payload.status);setSaveState('idle')
      if(isNote){
        // The queue owns the daemon storage revision; the editor starts fresh at 0.
        noteSaveQueue.reset(noteKey,project.id,payload.revision,resource.kind==='session-note'?resource.id:null)
        setLoadGeneration(generation=>generation+1)
      }
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause));setStatus('error')}
  }

  useEffect(()=>{
    setTreeMenu(null)
    if(resource.kind==='files')void loadDirectory('')
    else if(resource.kind==='file'&&cachedFile&&cachedFile.text!==cachedFile.baseline){
      setError(cachedFile.error)
    }else void loadText()
  },[project.id,resource.kind,resource.id])

  useEffect(()=>{
    if(resource.kind==='file')fileDrafts.set(resourceKey,{revision,text,baseline,status,saveState,error})
  },[resource.kind,resourceKey,revision,text,baseline,status,saveState,error])

  useEffect(()=>{
    if(resource.kind==='files')browserStates.set(resourceKey,{directories,expanded:new Set(expanded)})
  },[resource.kind,resourceKey,directories,expanded])

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

  const watchedPaths=resource.kind==='files'
    ?['',...expanded]
    :resource.kind==='file'?[parentPath(resource.id)]:[]
  const watchKey=watchedPaths.sort().join('\n')
  useEffect(()=>{
    if(!watchedPaths.length)return
    const leaseId=`${watchId.current}-${Math.random().toString(36).slice(2)}`
    const renew=()=>void api('PUT',`/api/projects/${project.id}/watch`,{watch_id:leaseId,paths:watchedPaths}).catch(()=>{})
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
      }else if(resource.kind==='file'&&detail.paths.includes(resource.id)){
        if(textRef.current===baselineRef.current)void loadText()
        else setError('File changed externally while this tab has unsaved edits.')
      }
    }
    window.addEventListener('mux:project-files-changed',changed)
    return()=>window.removeEventListener('mux:project-files-changed',changed)
  },[project.id,resource.kind,resource.id,directories])

  useEffect(()=>{
    if(!isNote)return
    const follow=async(remoteRevision='')=>{
      if(!noteSaveQueue.canFollowRemote(noteKey,remoteRevision))return
      const requestGeneration=++noteLoadGeneration.current
      try{
        const payload=await api<NotePayload>('GET',noteEndpoint)
        if(requestGeneration!==noteLoadGeneration.current)return
        // Typing may have started while the GET was in flight. In that case the
        // local editor wins and the existing revision-conflict path stays armed.
        if(!noteSaveQueue.canFollowRemote(noteKey,payload.revision))return
        setRevision(payload.revision);setText(payload.markdown);setBaseline(payload.markdown)
        setStatus(payload.status);setSaveState('idle');setError('')
        noteSaveQueue.reset(noteKey,project.id,payload.revision,resource.kind==='session-note'?resource.id:null)
        setLoadGeneration(generation=>generation+1)
      }catch(cause){
        if(requestGeneration===noteLoadGeneration.current)setError(cause instanceof Error?cause.message:String(cause))
      }
    }
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
      const payload=await api<FilePayload>('PUT',`/api/projects/${project.id}/file`,{path:resource.id,text:content,revision:expectedRevision})
      setRevision(payload.revision);setBaseline(content);setStatus(payload.status);setError('');setSaveState('saved')
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause));setSaveState('error')}
  }

  const editable=status==='ready'||status==='missing'
  // Notes persist through the resource-scoped save queue (outside this component
  // so a pane close cannot cancel a save); we only mirror its state for display.
  useEffect(()=>{
    if(!isNote)return
    return noteSaveQueue.subscribe(noteKey,setNoteSave)
  },[isNote,noteKey])

  const resolveKeepMine=async()=>{
    try{
      const payload=await api<NotePayload>('GET',noteEndpoint)
      noteSaveQueue.overwrite(noteKey,payload.revision)
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
  }

  const toggleDirectory=(path:string)=>{
    if(expanded.has(path)){
      setExpanded(current=>new Set([...current].filter(item=>item!==path&&!item.startsWith(`${path}/`))))
      return
    }
    setExpanded(current=>new Set([...current,path]))
    if(!directories[path])void loadDirectory(path)
  }

  const openTreeMenu=(item:DirectoryItem,event:MouseEvent)=>{
    event.preventDefault();event.stopPropagation()
    setTreeMenu({item,x:event.clientX,y:event.clientY})
  }

  const revealResource=async(item:DirectoryItem)=>{
    setTreeMenu(null)
    try{
      await api('POST',`/api/projects/${project.id}/reveal`,{path:item.path})
      setError('')
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
      <button class="file-tree-row directory" style={{paddingLeft:`${9+depth*15}px`}} onClick={()=>toggleDirectory(item.path)} onContextMenu={event=>openTreeMenu(item,event)} aria-expanded={expanded.has(item.path)}><span>{expanded.has(item.path)?'▾':'▸'}</span><strong>{item.name}</strong><small>folder</small></button>
      {expanded.has(item.path)&&tree(item.path,depth+1)}
    </div>:<button class="file-tree-row file" key={item.path} style={{paddingLeft:`${9+depth*15}px`}} onClick={()=>onOpenFile(item.path)} onContextMenu={event=>openTreeMenu(item,event)}><span>·</span><strong>{item.name}</strong><small>{item.size!==null?`${item.size.toLocaleString()} B`:''}</small></button>)}{directory.truncated&&<p class="file-tree-loading" style={{paddingLeft:`${12+depth*15}px`}}>Showing the first 2,000 entries.</p>}</>
  }

  if(resource.kind==='files')return <section class="project-resource file-browser">
    <header><div><strong>{project.name} / Files</strong><span>{project.root}</span></div><div class="resource-actions"><button aria-label="Close Files tab" title="Close tab" onClick={()=>{browserStates.delete(resourceKey);onClose()}}>×</button></div></header>
    {error&&<p class="resource-error">{error}</p>}
    <div class="file-tree" role="tree">{tree('')}</div>
    {treeMenu&&<div class="context-menu project-file-menu" ref={treeMenuPanel} role="menu" aria-label={`File actions for ${treeMenu.item.name}`} style={{left:clampContextMenuLeft(treeMenu.x,window.innerWidth),top:Math.max(4,Math.min(treeMenu.y,window.innerHeight-140))}} onMouseDown={event=>event.stopPropagation()}>
      <div class="context-title"><strong>{treeMenu.item.name}</strong></div>
      <button role="menuitem" onClick={()=>void revealResource(treeMenu.item)}>Open in default explorer</button>
      <button role="menuitem" onClick={()=>void ignoreResource(treeMenu.item,'global')}>Add pattern to global ignores</button>
      <button role="menuitem" onClick={()=>void ignoreResource(treeMenu.item,'project')}>Add pattern to project ignores</button>
    </div>}
  </section>

  const handleEditorKey=(event:KeyboardEvent&{currentTarget:HTMLTextAreaElement})=>{
    if(event.key!=='Tab')return
    event.preventDefault()
    const editor=event.currentTarget
    const result=insertEditorTab(text,editor.selectionStart,editor.selectionEnd)
    setText(result.text)
    requestAnimationFrame(()=>editor.setSelectionRange(result.caret,result.caret))
  }
  const stateLabel=isNote?(noteSave.status==='idle'?status:noteSave.status):(saveState==='idle'?status:saveState)
  const closeResource=()=>{if(isNote)noteSaveQueue.flush(noteKey);else fileDrafts.delete(resourceKey);onClose()}
  return <section class="project-resource file-editor">
    <header><div><strong>{isNote?noteLabel:resource.id}</strong><span>{project.name} · {stateLabel}</span></div><div class="resource-actions">{resource.kind==='file'&&<button disabled={!editable||text===baseline||saveState==='saving'} onClick={()=>void save()}>Save</button>}<button aria-label={`Close ${isNote?noteLabel:resource.id} tab`} title="Close tab" onClick={closeResource}>×</button></div></header>
    {error&&<p class="resource-error">{error}</p>}
    {isNote&&noteSave.banner&&<p class="resource-error note-conflict"><span>{noteSave.banner}</span>{noteSave.status==='conflict'&&<span class="note-conflict-actions"><button onClick={()=>void loadText()}>Reload from disk</button><button onClick={()=>void resolveKeepMine()}>Overwrite disk</button></span>}</p>}
    {editable?(isNote?<ProjectNoteEditor key={`${project.id}:${resource.kind}:${resource.id}:${loadGeneration}`} projectId={project.id} resourceId={`${resource.kind}:${resource.id}`} initialText={text} label={noteLabel}/>:<textarea value={text} onInput={event=>setText(event.currentTarget.value)} onKeyDown={handleEditorKey} spellcheck={false}/>):<div class="resource-unavailable">{status==='binary'?'Binary files cannot be edited here.':status==='too-large'?'This file exceeds the 2 MiB editor limit.':'This resource is read-only.'}</div>}
  </section>
}
