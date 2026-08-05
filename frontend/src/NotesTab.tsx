import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { ComponentChildren, JSX } from 'preact'
import { api } from './api'
import { clampContextMenuLeft, fitMenuInViewport } from './menuPosition'
import { useModalFocus } from './modalFocus'
import { OverflowRail } from './RailScroller'
import {
  SCRATCHPAD_TAB_ID,
  canonicalNoteTabId,
  fallbackNoteTab,
  noteTabAfterDelete,
  projectNoteTabId,
  stableProjectNoteTabs,
} from './noteTabs'
import type { Project } from './types'

// The drawer's persistent Notes workspace. Scratchpad and every note in the active Project
// are non-closeable tabs; the selected resource is remembered per Project by `App`.
//
// The two are mutually exclusive per browser and the drawer host enforces it; `drawerNotes.ts`
// explains why that is a correctness rule rather than tidiness (one save queue per note, so a
// second live editor silently overwrites the first).
//
// Every row is the same Project-owned Note type. Notes are created here rather than
// being coupled to terminal lifetime or history identity.

export type ProjectNoteSummary = {
  note_id:string;project_id:string;project_name:string
  title:string;created_at:number;updated_at:number;bytes:number;revision:string;excerpt:string
  origin_session_id?:string|null
}

/** Where a selected note should be opened. Both hosts are real placements, not a preview and
 *  a real one, and a note lives in exactly one of them at a time. */
export type NotePlacement='drawer'|'tab'

type Props={
  project?:Project
  allProjects:boolean
  onAllProjects:(value:boolean)=>void
  onOpenNote:(projectId:string,noteId:string,title:string,place:NotePlacement)=>void
  onOpenScratchpad:(place:NotePlacement)=>void
  onDone:()=>void
  selectedResourceId:string|null
  editor:ComponentChildren
  onPopSelected:()=>void
}

const sizeLabel=(bytes:number)=>bytes>=1024?`${Math.round(bytes/1024)} KiB`:`${bytes} B`
const LONG_PRESS_MS=550
const noteKey=(note:Pick<ProjectNoteSummary,'project_id'|'note_id'>)=>`${note.project_id}:${note.note_id}`
type NoteMenu={note:ProjectNoteSummary;x:number;y:number}
type NoteTitlePrompt={mode:'create'|'rename';title:string;note?:ProjectNoteSummary}

export function NotesTab({project,allProjects,onAllProjects,onOpenNote,onOpenScratchpad,onDone,selectedResourceId,editor,onPopSelected}:Props){
  const [items,setItems]=useState<ProjectNoteSummary[]|null>(null)
  const [query,setQuery]=useState('')
  const [error,setError]=useState('')
  const [deleteConfirm,setDeleteConfirm]=useState('')
  const [deleting,setDeleting]=useState('')
  const [menu,setMenu]=useState<NoteMenu|null>(null)
  const [titlePrompt,setTitlePrompt]=useState<NoteTitlePrompt|null>(null)
  const [titleBusy,setTitleBusy]=useState(false)
  const [titleError,setTitleError]=useState('')
  const [browseOpen,setBrowseOpen]=useState(false)
  const scopeId=allProjects?'':project?.id||''
  // A generation guard rather than a cancel flag: a refresh fired by a note-changed
  // event can land after a scope change, and the newest request must win.
  const generation=useRef(0)
  const menuPanel=useRef<HTMLDivElement>(null)
  const longPress=useRef<number|null>(null)
  const pressOrigin=useRef<{x:number;y:number}|null>(null)
  const touchPress=useRef(false)
  const suppressClick=useRef(false)
  const menuOpenedAt=useRef(0)
  const titlePanel=useRef<HTMLFormElement>(null)
  useModalFocus(titlePanel,()=>{if(!titleBusy){setTitlePrompt(null);setTitleError('')}},!!titlePrompt)

  const load=async()=>{
    const mine=++generation.current
    const path=scopeId?`/api/notes?project_id=${encodeURIComponent(scopeId)}`:'/api/notes'
    try{
      const result=await api<{items:ProjectNoteSummary[]}>('GET',path)
      if(mine!==generation.current)return
      setItems(result.items);setError('')
    }catch(cause){
      if(mine!==generation.current)return
      setItems([]);setError(cause instanceof Error?cause.message:String(cause))
    }
  }

  useEffect(()=>{setItems(null);void load()},[scopeId])
  useEffect(()=>{
    const changed=()=>void load()
    window.addEventListener('mux:note-changed',changed)
    return()=>window.removeEventListener('mux:note-changed',changed)
  },[scopeId])

  useEffect(()=>()=>{if(longPress.current!==null)window.clearTimeout(longPress.current)},[])

  useEffect(()=>{
    if(!menu)return
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:null
    const frame=requestAnimationFrame(()=>menuPanel.current?.querySelector<HTMLButtonElement>('button')?.focus())
    const dismiss=(event:Event)=>{
      const target=event.target
      if(target instanceof Element&&target.closest('.note-row-menu'))return
      setMenu(null);setDeleteConfirm('')
    }
    const key=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){
        event.preventDefault();event.stopImmediatePropagation();setMenu(null);setDeleteConfirm('');return
      }
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return
      const buttons=[...menuPanel.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')||[]]
      if(!buttons.length)return
      event.preventDefault()
      const current=buttons.indexOf(document.activeElement as HTMLButtonElement)
      const next=event.key==='Home'?0:event.key==='End'?buttons.length-1
        :(Math.max(current,0)+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length
      buttons[next].focus()
    }
    document.addEventListener('pointerdown',dismiss)
    window.addEventListener('blur',dismiss)
    window.addEventListener('keydown',key,true)
    return()=>{
      cancelAnimationFrame(frame)
      document.removeEventListener('pointerdown',dismiss)
      window.removeEventListener('blur',dismiss)
      window.removeEventListener('keydown',key,true)
      previous?.focus()
    }
  },[menu])

  const needle=query.trim().toLowerCase()
  const shown=useMemo(()=>{
    const listed=items||[]
    if(!needle)return listed
    return listed.filter(item=>
      item.title.toLowerCase().includes(needle)
      ||item.project_name.toLowerCase().includes(needle)
      ||item.excerpt.toLowerCase().includes(needle))
  },[items,needle])

  // Grouping by project keeps an all-projects listing readable. A scoped listing is
  // already one project, so it renders flat.
  const grouped=useMemo(()=>{
    if(!allProjects)return [] as Array<[string,{label:string;notes:ProjectNoteSummary[]}]>
    const buckets=new Map<string,{label:string;notes:ProjectNoteSummary[]}>()
    for(const item of shown){
      const bucket=buckets.get(item.project_id)||{label:item.project_name,notes:[]}
      bucket.notes.push(item)
      buckets.set(item.project_id,bucket)
    }
    return [...buckets.entries()]
  },[shown,allProjects])

  const projectItems=useMemo(()=>stableProjectNoteTabs(
    (items||[]).filter(item=>item.project_id===project?.id)
  ),[items,project?.id])
  const selectedTabId=canonicalNoteTabId(selectedResourceId)

  // A stale remembered id can follow a deletion or an older on-disk build. Resolve it only
  // after the scoped collection loads, then persist the replacement through the same callback
  // as an explicit tab click.
  useEffect(()=>{
    if(allProjects||!project||!items)return
    const fallback=fallbackNoteTab(selectedResourceId,projectItems)
    if(fallback===selectedTabId)return
    if(fallback===SCRATCHPAD_TAB_ID)onOpenScratchpad('drawer')
    else{
      const note=projectItems.find(item=>projectNoteTabId(item.note_id)===fallback)
      if(note)onOpenNote(note.project_id,note.note_id,note.title,'drawer')
    }
  },[allProjects,items,project?.id,projectItems,selectedResourceId,selectedTabId])

  // Selecting a drawer sub-tab keeps the panel visible. Moving a note to a workspace tab
  // hands the screen back on mobile.
  const openNote=(note:ProjectNoteSummary,place:NotePlacement)=>{
    setBrowseOpen(false)
    if(allProjects)onAllProjects(false)
    onOpenNote(note.project_id,note.note_id,note.title,place)
    if(place==='tab')onDone()
  }
  const openScratchpad=(place:NotePlacement)=>{
    setBrowseOpen(false)
    if(allProjects)onAllProjects(false)
    onOpenScratchpad(place)
    if(place==='tab')onDone()
  }

  const cancelLongPress=()=>{
    if(longPress.current!==null)window.clearTimeout(longPress.current)
    longPress.current=null;pressOrigin.current=null
  }
  const openMenu=(note:ProjectNoteSummary,x:number,y:number)=>{
    setDeleteConfirm('')
    menuOpenedAt.current=performance.now()
    setMenu({note,x,y})
  }
  const beginLongPress=(note:ProjectNoteSummary,event:JSX.TargetedPointerEvent<HTMLElement>)=>{
    touchPress.current=event.pointerType==='touch'
    if(!touchPress.current)return
    cancelLongPress()
    const {clientX,clientY}=event
    pressOrigin.current={x:clientX,y:clientY}
    longPress.current=window.setTimeout(()=>{
      longPress.current=null;suppressClick.current=true
      navigator.vibrate?.(20)
      openMenu(note,clientX,clientY)
    },LONG_PRESS_MS)
  }
  const trackLongPress=(event:JSX.TargetedPointerEvent<HTMLElement>)=>{
    const origin=pressOrigin.current
    if(origin&&(Math.abs(event.clientX-origin.x)>10||Math.abs(event.clientY-origin.y)>10))cancelLongPress()
  }
  const openContextMenu=(note:ProjectNoteSummary,event:JSX.TargetedMouseEvent<HTMLElement>)=>{
    event.preventDefault();event.stopPropagation();cancelLongPress()
    if(touchPress.current)suppressClick.current=true
    openMenu(note,event.clientX,event.clientY)
  }
  const suppressLongPressClick=(event:JSX.TargetedMouseEvent<HTMLElement>)=>{
    if(!suppressClick.current)return
    suppressClick.current=false;event.preventDefault();event.stopPropagation()
  }
  const deleteProjectNote=async(note:ProjectNoteSummary)=>{
    const key=noteKey(note)
    if(deleteConfirm!==key){setDeleteConfirm(key);return}
    const fallback=note.project_id===project?.id
      ?noteTabAfterDelete(selectedResourceId,note.note_id,projectItems)
      :null
    setDeleting(key)
    try{
      await api('DELETE',`/api/projects/${note.project_id}/notes/${encodeURIComponent(note.note_id)}`,{revision:note.revision})
      setItems(current=>current?.filter(item=>noteKey(item)!==key)||current)
      setDeleteConfirm('');setMenu(null);setError('')
      if(fallback===SCRATCHPAD_TAB_ID)openScratchpad('drawer')
      else if(fallback){
        const next=projectItems.find(item=>projectNoteTabId(item.note_id)===fallback)
        if(next)openNote(next,'drawer')
      }
      window.dispatchEvent(new CustomEvent('mux:note-changed',{detail:{scope:'project',projectId:note.project_id,kind:'note',noteId:note.note_id,revision:'missing'}}))
    }catch(cause){
      setDeleteConfirm('');setError(cause instanceof Error?cause.message:String(cause));void load()
    }finally{setDeleting('')}
  }
  const noteActions=(note:ProjectNoteSummary)=>{
    const key=noteKey(note)
    const confirming=deleteConfirm===key
    const busy=deleting===key
    return <>
      <button
        class={`note-delete ${confirming?'confirming':''}`}
        aria-label={confirming?`Confirm deletion of ${note.title}`:`Delete ${note.title}`}
        title={confirming?'Click again to permanently delete this note':'Delete note'}
        disabled={busy}
        onBlur={()=>setDeleteConfirm(current=>current===key?'':current)}
        onClick={()=>void deleteProjectNote(note)}
      >{busy?'…':confirming?'delete?':'×'}</button>
      <button class="note-open-as-tab" aria-label={`Open ${note.title} as a workspace tab`} title="Open as a workspace tab instead" onClick={()=>openNote(note,'tab')}>⇥</button>
    </>
  }
  const submitTitle=async(event:SubmitEvent)=>{
    event.preventDefault()
    if(!titlePrompt||!project)return
    const title=titlePrompt.title.trim()
    if(!title){setTitleError('Enter a title.');return}
    setTitleBusy(true);setTitleError('')
    try{
      if(titlePrompt.mode==='create'){
        const created=await api<{id:string}>('POST',`/api/projects/${project.id}/notes`,{title})
        setTitlePrompt(null);await load();onOpenNote(project.id,created.id,title,'drawer')
      }else if(titlePrompt.note){
        const note=titlePrompt.note
        const renamed=await api<ProjectNoteSummary>('PATCH',`/api/projects/${note.project_id}/notes/${encodeURIComponent(note.note_id)}`,{title,revision:note.revision})
        setItems(current=>current?.map(item=>noteKey(item)===noteKey(note)?{...item,...renamed,title}:item)||current)
        setTitlePrompt(null);setMenu(null)
        window.dispatchEvent(new CustomEvent('mux:note-changed',{detail:{scope:'project',projectId:note.project_id,kind:'note',noteId:note.note_id,revision:renamed.revision}}))
      }
    }catch(cause){setTitleError(cause instanceof Error?cause.message:String(cause))}
    finally{setTitleBusy(false)}
  }
  const noteRow=(note:ProjectNoteSummary)=><article
    class="project-note-row"
    key={noteKey(note)}
    onContextMenu={event=>openContextMenu(note,event)}
    onPointerDown={event=>beginLongPress(note,event)}
    onPointerMove={trackLongPress}
    onPointerUp={cancelLongPress}
    onPointerCancel={cancelLongPress}
    onPointerLeave={cancelLongPress}
    onClickCapture={suppressLongPressClick}
  >
    <button onClick={()=>openNote(note,'drawer')} title={`Open ${note.title} in this panel · right-click or hold for actions`}>
      <strong>{note.title}</strong>
      <span>{new Date(note.updated_at*1000).toLocaleString()} · {sizeLabel(note.bytes)}</span>
      <small>{note.excerpt}</small>
    </button>
    {noteActions(note)}
  </article>

  const focusAdjacentTab=(event:JSX.TargetedKeyboardEvent<HTMLButtonElement>,offset:number)=>{
    const buttons=[...event.currentTarget.closest('[role="tablist"]')?.querySelectorAll<HTMLButtonElement>('[role="tab"]')||[]]
    const index=buttons.indexOf(event.currentTarget)
    const next=buttons[(index+offset+buttons.length)%buttons.length]
    if(!next)return
    event.preventDefault();next.click();next.focus()
  }
  const browserVisible=allProjects||browseOpen

  return <div class="notes-tab">
    <div class="notes-subtabs-row">
      <OverflowRail
        className="notes-subtabs"
        itemLabel="notes"
        wrapperClassName="notes-subtabs-rail"
        activeKey={selectedTabId||''}
        touchDrag
        stripProps={{role:'tablist','aria-label':'Notes in this Project'}}
      >
        <button
          role="tab"
          aria-selected={selectedTabId===SCRATCHPAD_TAB_ID}
          tabIndex={selectedTabId===SCRATCHPAD_TAB_ID?0:-1}
          class={selectedTabId===SCRATCHPAD_TAB_ID?'active':''}
          title="Global Scratchpad"
          disabled={!project}
          onClick={()=>openScratchpad('drawer')}
          onKeyDown={event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight')focusAdjacentTab(event,event.key==='ArrowLeft'?-1:1)}}
        >Scratchpad</button>
        {projectItems.map(note=>{
          const resourceId=projectNoteTabId(note.note_id)
          const active=selectedTabId===resourceId
          return <button
            key={resourceId}
            role="tab"
            aria-selected={active}
            tabIndex={active?0:-1}
            class={active?'active':''}
            title={note.title}
            onClick={()=>openNote(note,'drawer')}
            onKeyDown={event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight')focusAdjacentTab(event,event.key==='ArrowLeft'?-1:1)}}
          >{note.title}</button>
        })}
      </OverflowRail>
      <button class="notes-browse" aria-label="Search and manage notes" aria-expanded={browserVisible} title="Search and manage notes" onClick={()=>setBrowseOpen(value=>!value)}>⌕</button>
      <button class="notes-new" aria-label="Create a Project note" disabled={!project} title="Create a Project note" onClick={()=>{setTitleError('');setTitlePrompt({mode:'create',title:'Untitled note'})}}>+</button>
      <button class="notes-pop" aria-label="Move the selected note into a workspace tab" disabled={!selectedResourceId} title="Move the selected note into a workspace tab" onClick={onPopSelected}>⇥</button>
    </div>
    <div class="notes-editor-host">
      {editor||<p class="notes-state">{project?'Reading notes…':'Select a Project to open Notes.'}</p>}
    </div>
    {browserVisible&&<section class="notes-browser" aria-label="Browse notes">
      <div class="notes-browser-heading">
        <strong>{allProjects?'All Project notes':project?.name||'Project notes'}</strong>
        <button onClick={()=>{setBrowseOpen(false);if(allProjects)onAllProjects(false)}}>Done</button>
      </div>
      <div class="notes-filters">
        <input
          autoFocus
          aria-label="Search notes"
          placeholder="Search notes…"
          value={query}
          spellcheck={false}
          onInput={event=>setQuery(event.currentTarget.value)}
        />
        <select aria-label="Filter notes by project" value={allProjects?'':scopeId} onChange={event=>onAllProjects(!event.currentTarget.value)}>
          <option value={project?.id||''} disabled={!project}>{project?project.name:'No project'}</option>
          <option value="">All projects</option>
        </select>
      </div>
      <div class="notes-body notes-tab-body">
        {error&&<p class="notes-state error" role="alert">{error}</p>}
        {!items&&!error&&<p class="notes-state">Reading notes…</p>}
        {items&&!error&&!shown.length&&<p class="notes-state">
          {items.length?'No note matches this search.':'No notes yet. Create one for this Project.'}
        </p>}
        {!!shown.length&&<div class="notes-listing">
          {allProjects
            ?grouped.map(([id,bucket])=><section class="notes-group" key={id}>
              <h3>project::{bucket.label}<span>{bucket.notes.length}</span></h3>
              {bucket.notes.map(noteRow)}
            </section>)
            :<section class="notes-group">
              <h3>notes<span>{shown.length}</span></h3>
              {shown.map(noteRow)}
            </section>}
        </div>}
      </div>
      <p class="notes-footnote">Scratchpad is global. Project notes stay in creation order and remain in the tab rail.</p>
    </section>}
    {menu&&<div
      class="context-menu note-row-menu"
      ref={el=>{menuPanel.current=el;fitMenuInViewport(el)}}
      role="menu"
      aria-label={`Actions for ${menu.note.title}`}
      style={{left:clampContextMenuLeft(menu.x,window.innerWidth),top:Math.max(4,menu.y)}}
      onClickCapture={event=>{if(performance.now()-menuOpenedAt.current<250){event.preventDefault();event.stopPropagation()}}}
    >
      <div class="context-title"><strong>{menu.note.title}</strong></div>
      <button role="menuitem" onClick={()=>{const note=menu.note;setMenu(null);openNote(note,'tab')}}>Open in workspace tab</button>
      <button role="menuitem" onClick={()=>{setTitleError('');setTitlePrompt({mode:'rename',title:menu.note.title,note:menu.note})}}>Rename…</button>
      <div class="context-rule" />
      <button
        role="menuitem"
        class={`danger ${deleteConfirm===noteKey(menu.note)?'confirming':''}`}
        disabled={deleting===noteKey(menu.note)}
        onClick={()=>void deleteProjectNote(menu.note)}
      >{deleting===noteKey(menu.note)?'Deleting…':deleteConfirm===noteKey(menu.note)?'Confirm delete':'Delete note'}</button>
    </div>}
    {titlePrompt&&<div class="modal-layer note-title-layer" onPointerDown={event=>{if(event.target===event.currentTarget&&!titleBusy){setTitlePrompt(null);setTitleError('')}}}>
      <form ref={titlePanel} class="modal note-title-modal" role="dialog" aria-modal="true" aria-label={titlePrompt.mode==='create'?'Create note':'Rename note'} onPointerDown={event=>event.stopPropagation()} onSubmit={event=>void submitTitle(event)}>
        <div class="modal-heading"><div><span>NOTES::{titlePrompt.mode==='create'?'NEW':'RENAME'}</span><h2>{titlePrompt.mode==='create'?'New note':'Rename note'}</h2></div><button type="button" disabled={titleBusy} aria-label="Cancel" onClick={()=>{setTitlePrompt(null);setTitleError('')}}>×</button></div>
        <label>Title<input autoFocus value={titlePrompt.title} maxLength={160} disabled={titleBusy} onInput={event=>setTitlePrompt(current=>current?{...current,title:event.currentTarget.value}:current)}/></label>
        {titleError&&<p class="resource-create-error" role="alert">{titleError}</p>}
        <div class="modal-footer"><button type="button" disabled={titleBusy} onClick={()=>{setTitlePrompt(null);setTitleError('')}}>Cancel</button><button class="primary" disabled={titleBusy||!titlePrompt.title.trim()} type="submit">{titleBusy?'Saving…':titlePrompt.mode==='create'?'Create':'Rename'}</button></div>
      </form>
    </div>}
  </div>
}
