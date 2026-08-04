import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { api } from './api'
import { clampContextMenuLeft, fitMenuInViewport } from './menuPosition'
import type { Project } from './types'

// The drawer's Notes tab: the index half of the Notes surface.
//
// Selecting a row opens that note **in the drawer**, which is the point of the tab on a
// phone: reading or adding to a note without leaving the session you are watching. The `⇥`
// on each row is the other half, opening it as an ordinary workspace tab instead — better on
// desktop, where a pane is wider than a 380 px column and two notes can be on screen at once.
//
// The two are mutually exclusive per browser and the drawer host enforces it; `drawerNotes.ts`
// explains why that is a correctness rule rather than tidiness (one save queue per note, so a
// second live editor silently overwrites the first).
//
// The Project note is pinned first and always present, even empty: it is the one
// note that exists for every Project. The focused terminal's note is pinned second
// when it holds text. Everything else is the filesystem-derived listing, which by
// design lists only notes that hold text, so a note started by a stray chip click
// never earns a row.

export type SessionNoteSummary = {
  note_id:string;project_id:string;project_name:string
  updated_at:number;bytes:number;revision:string;excerpt:string
  owner_label:string;owner_backend?:string|null;owner_live:boolean;owner_known:boolean
}

/** Where a selected note should be opened. Both hosts are real placements, not a preview and
 *  a real one, and a note lives in exactly one of them at a time. */
export type NotePlacement='drawer'|'tab'

type Props={
  project?:Project
  allProjects:boolean
  onAllProjects:(value:boolean)=>void
  /** The focused terminal's note, when it holds text. Pinned above the listing. */
  focusedNote:{projectId:string;noteId:string;label:string}|null
  onOpenProjectNote:(projectId:string,place:NotePlacement)=>void
  onOpenSessionNote:(projectId:string,noteId:string,place:NotePlacement)=>void
  onDone:()=>void
}

const sizeLabel=(bytes:number)=>bytes>=1024?`${Math.round(bytes/1024)} KiB`:`${bytes} B`
const LONG_PRESS_MS=550
const noteKey=(note:Pick<SessionNoteSummary,'project_id'|'note_id'>)=>`${note.project_id}:${note.note_id}`
type NoteMenu={note:SessionNoteSummary;x:number;y:number}
type ProjectNoteMeta={bytes:number}

export function NotesTab({project,allProjects,onAllProjects,focusedNote,onOpenProjectNote,onOpenSessionNote,onDone}:Props){
  const [items,setItems]=useState<SessionNoteSummary[]|null>(null)
  const [query,setQuery]=useState('')
  const [error,setError]=useState('')
  const [projectNoteBytes,setProjectNoteBytes]=useState<number|null>(null)
  const [deleteConfirm,setDeleteConfirm]=useState('')
  const [deleting,setDeleting]=useState('')
  const [menu,setMenu]=useState<NoteMenu|null>(null)
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

  const load=async()=>{
    const mine=++generation.current
    const path=scopeId?`/api/session-notes?project_id=${encodeURIComponent(scopeId)}`:'/api/session-notes'
    try{
      const result=await api<{items:SessionNoteSummary[]}>('GET',path)
      if(mine!==generation.current)return
      setItems(result.items);setError('')
    }catch(cause){
      if(mine!==generation.current)return
      setItems([]);setError(cause instanceof Error?cause.message:String(cause))
    }
  }

  useEffect(()=>{setItems(null);void load()},[scopeId])
  // A note earns its row by holding text, so a first save has to reach this listing.
  useEffect(()=>{
    const changed=()=>void load()
    window.addEventListener('mux:note-changed',changed)
    return()=>window.removeEventListener('mux:note-changed',changed)
  },[scopeId])

  useEffect(()=>{
    const projectId=project?.id||''
    if(!projectId){setProjectNoteBytes(null);return}
    let current=true
    const loadProjectNote=()=>void api<ProjectNoteMeta>('GET',`/api/projects/${projectId}/note`)
      .then(note=>{if(current)setProjectNoteBytes(note.bytes)})
      .catch(()=>{if(current)setProjectNoteBytes(null)})
    const changed=(event:Event)=>{
      const detail=(event as CustomEvent<{projectId:string;kind:string}>).detail
      if(detail.projectId===projectId&&detail.kind==='note')loadProjectNote()
    }
    setProjectNoteBytes(null)
    loadProjectNote()
    window.addEventListener('mux:note-changed',changed)
    return()=>{current=false;window.removeEventListener('mux:note-changed',changed)}
  },[project?.id])

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
    const listed=(items||[]).filter(item=>item.note_id!==focusedNote?.noteId||item.project_id!==focusedNote.projectId)
    if(!needle)return listed
    return listed.filter(item=>
      item.owner_label.toLowerCase().includes(needle)
      ||item.project_name.toLowerCase().includes(needle)
      ||item.excerpt.toLowerCase().includes(needle))
  },[items,needle,focusedNote?.noteId,focusedNote?.projectId])

  // Grouping by project keeps an all-projects listing readable. A scoped listing is
  // already one project, so it renders flat.
  const grouped=useMemo(()=>{
    if(!allProjects)return [] as Array<[string,{label:string;notes:SessionNoteSummary[]}]>
    const buckets=new Map<string,{label:string;notes:SessionNoteSummary[]}>()
    for(const item of shown){
      const bucket=buckets.get(item.project_id)||{label:item.project_name,notes:[]}
      bucket.notes.push(item)
      buckets.set(item.project_id,bucket)
    }
    return [...buckets.entries()]
  },[shown,allProjects])
  const focusedSummary=useMemo(()=>focusedNote
    ?(items||[]).find(item=>item.project_id===focusedNote.projectId&&item.note_id===focusedNote.noteId)||null
    :null,[items,focusedNote?.projectId,focusedNote?.noteId])

  // Opening into the drawer replaces this index in place, so `onDone` (which closes the whole
  // panel on mobile) would hide the note it was just asked to show. Only a pane placement
  // hands the screen back.
  const openSession=(projectId:string,noteId:string,place:NotePlacement)=>{
    onOpenSessionNote(projectId,noteId,place)
    if(place==='tab')onDone()
  }
  const openProject=(projectId:string,place:NotePlacement)=>{
    onOpenProjectNote(projectId,place)
    if(place==='tab')onDone()
  }

  const cancelLongPress=()=>{
    if(longPress.current!==null)window.clearTimeout(longPress.current)
    longPress.current=null;pressOrigin.current=null
  }
  const openMenu=(note:SessionNoteSummary,x:number,y:number)=>{
    setDeleteConfirm('')
    menuOpenedAt.current=performance.now()
    setMenu({note,x,y})
  }
  const beginLongPress=(note:SessionNoteSummary,event:JSX.TargetedPointerEvent<HTMLElement>)=>{
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
  const openContextMenu=(note:SessionNoteSummary,event:JSX.TargetedMouseEvent<HTMLElement>)=>{
    event.preventDefault();event.stopPropagation();cancelLongPress()
    if(touchPress.current)suppressClick.current=true
    openMenu(note,event.clientX,event.clientY)
  }
  const suppressLongPressClick=(event:JSX.TargetedMouseEvent<HTMLElement>)=>{
    if(!suppressClick.current)return
    suppressClick.current=false;event.preventDefault();event.stopPropagation()
  }
  const deleteSession=async(note:SessionNoteSummary)=>{
    const key=noteKey(note)
    if(deleteConfirm!==key){setDeleteConfirm(key);return}
    setDeleting(key)
    try{
      await api('DELETE',`/api/projects/${note.project_id}/session-notes/${encodeURIComponent(note.note_id)}`,{revision:note.revision})
      setItems(current=>current?.filter(item=>noteKey(item)!==key)||current)
      setDeleteConfirm('');setMenu(null);setError('')
      window.dispatchEvent(new CustomEvent('mux:note-changed',{detail:{projectId:note.project_id,kind:'session-note',noteId:note.note_id,revision:'missing'}}))
    }catch(cause){
      setDeleteConfirm('');setError(cause instanceof Error?cause.message:String(cause));void load()
    }finally{setDeleting('')}
  }
  const noteActions=(note:SessionNoteSummary)=>{
    const key=noteKey(note)
    const confirming=deleteConfirm===key
    const busy=deleting===key
    return <>
      <button
        class={`note-delete ${confirming?'confirming':''}`}
        aria-label={confirming?`Confirm deletion of the session note for ${note.owner_label}`:`Delete the session note for ${note.owner_label}`}
        title={confirming?'Click again to permanently delete this session note':'Delete session note'}
        disabled={busy}
        onBlur={()=>setDeleteConfirm(current=>current===key?'':current)}
        onClick={()=>void deleteSession(note)}
      >{busy?'…':confirming?'delete?':'×'}</button>
      <button class="note-open-as-tab" aria-label={`Open the session note for ${note.owner_label} as a workspace tab`} title="Open as a workspace tab instead" onClick={()=>openSession(note.project_id,note.note_id,'tab')}>⇥</button>
    </>
  }
  const noteRow=(note:SessionNoteSummary)=><article
    class="session-note-row"
    key={noteKey(note)}
    onContextMenu={event=>openContextMenu(note,event)}
    onPointerDown={event=>beginLongPress(note,event)}
    onPointerMove={trackLongPress}
    onPointerUp={cancelLongPress}
    onPointerCancel={cancelLongPress}
    onPointerLeave={cancelLongPress}
    onClickCapture={suppressLongPressClick}
  >
    <button onClick={()=>openSession(note.project_id,note.note_id,'drawer')} title={`Open the session note for ${note.owner_label} in this panel · right-click or hold for actions`}>
      <strong>
        {note.owner_backend&&<span class={`agent-prefix ${note.owner_backend}`}>[{note.owner_backend}]</span>}
        {note.owner_label}
        {note.owner_live&&<em class="session-note-live" title="Its terminal is still running">live</em>}
      </strong>
      <span>{new Date(note.updated_at*1000).toLocaleString()} · {sizeLabel(note.bytes)}</span>
      <small>{note.excerpt}</small>
    </button>
    {noteActions(note)}
  </article>

  return <div class="notes-tab">
    <div class="session-notes-filters">
      <input
        aria-label="Search session notes"
        placeholder="Search notes…"
        value={query}
        spellcheck={false}
        onInput={event=>setQuery(event.currentTarget.value)}
      />
      <select aria-label="Filter session notes by project" value={allProjects?'':scopeId} onChange={event=>onAllProjects(!event.currentTarget.value)}>
        <option value={project?.id||''} disabled={!project}>{project?project.name:'No project'}</option>
        <option value="">All projects</option>
      </select>
    </div>
    <div class="session-notes-body notes-tab-body">
      {error&&<p class="session-notes-state error" role="alert">{error}</p>}
      {/* Pinned rows are not part of the search: they are the two notes tied to
          where you are right now, and hiding them behind a query would be a trap. */}
      <div class="notes-pinned">
        {project
          ?<div class="note-pin-shell project-note-shell">
            <button class="note-pin-row project-note-pin" title={`Open the Project note for ${project.name} in this panel`} onClick={()=>openProject(project.id,'drawer')}>
              <span class="note-pin-kind">Project note</span><strong>{project.name}</strong>
              <span class="note-pin-size">{projectNoteBytes===null?'size …':sizeLabel(projectNoteBytes)}</span>
            </button>
            <button class="note-open-as-tab" aria-label={`Open the Project note for ${project.name} as a workspace tab`} title="Open as a workspace tab instead" onClick={()=>openProject(project.id,'tab')}>⇥</button>
          </div>
          :<p class="session-notes-state">Select a Project to see its note.</p>}
        {focusedNote&&focusedSummary&&<article
          class="note-pin-shell session-note-pin-shell"
          onContextMenu={event=>openContextMenu(focusedSummary,event)}
          onPointerDown={event=>beginLongPress(focusedSummary,event)}
          onPointerMove={trackLongPress}
          onPointerUp={cancelLongPress}
          onPointerCancel={cancelLongPress}
          onPointerLeave={cancelLongPress}
          onClickCapture={suppressLongPressClick}
        >
          <button class="note-pin-row" title={`Open the session note for ${focusedNote.label} in this panel · right-click or hold for actions`} onClick={()=>openSession(focusedNote.projectId,focusedNote.noteId,'drawer')}>
            <span class="note-pin-kind">Session note</span><strong>{focusedNote.label}</strong><span class="note-pin-size">{sizeLabel(focusedSummary.bytes)}</span>
          </button>
          {noteActions(focusedSummary)}
        </article>}
      </div>
      {!items&&!error&&<p class="session-notes-state">Reading session notes…</p>}
      {items&&!error&&!shown.length&&<p class="session-notes-state">
        {items.length
          ?'No other session note matches this search.'
          :'No session notes with content yet. Use the note button on a terminal to start one.'}
      </p>}
      {!!shown.length&&<div class="notes-listing">
        {allProjects
          ?grouped.map(([id,bucket])=><section class="session-notes-group" key={id}>
            <h3>project::{bucket.label}<span>{bucket.notes.length}</span></h3>
            {bucket.notes.map(noteRow)}
          </section>)
          :<section class="session-notes-group">
            <h3>session notes<span>{shown.length}</span></h3>
            {shown.map(noteRow)}
          </section>}
      </div>}
    </div>
    <p class="notes-footnote">Notes outlive their terminals. Select to open here; <b>⇥</b> opens a pane. Right-click or hold for actions.</p>
    {menu&&<div
      class="context-menu note-row-menu"
      ref={el=>{menuPanel.current=el;fitMenuInViewport(el)}}
      role="menu"
      aria-label={`Actions for the session note owned by ${menu.note.owner_label}`}
      style={{left:clampContextMenuLeft(menu.x,window.innerWidth),top:Math.max(4,menu.y)}}
      onClickCapture={event=>{if(performance.now()-menuOpenedAt.current<250){event.preventDefault();event.stopPropagation()}}}
    >
      <div class="context-title"><strong>{menu.note.owner_label}</strong></div>
      <button role="menuitem" onClick={()=>{const note=menu.note;setMenu(null);openSession(note.project_id,note.note_id,'tab')}}>Open in workspace tab</button>
      <div class="context-rule" />
      <button
        role="menuitem"
        class={`danger ${deleteConfirm===noteKey(menu.note)?'confirming':''}`}
        disabled={deleting===noteKey(menu.note)}
        onClick={()=>void deleteSession(menu.note)}
      >{deleting===noteKey(menu.note)?'Deleting…':deleteConfirm===noteKey(menu.note)?'Confirm delete':'Delete session note'}</button>
    </div>}
  </div>
}
