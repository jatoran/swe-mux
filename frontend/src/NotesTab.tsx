import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { ComponentChildren, JSX } from 'preact'
import { api } from './api'
import { Dropdown } from './Dropdown'
import { clampContextMenuLeft, fitMenuInViewport } from './menuPosition'
import { useDismissLevel, useModalFocus } from './modalFocus'
import { dismissStack } from './dismissStack.ts'
import { OverflowRail } from './RailScroller'
import {
  SCRATCHPAD_TAB_ID,
  canonicalNoteTabId,
  fallbackNoteTab,
  lastNoteInProject,
  noteTabAfterDelete,
  projectNoteCounts,
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
//
// A rail tab and a browser row open the same actions menu (right-click, touch long-press,
// or the keyboard's context-menu key), so managing a note never requires finding it in the
// browser first. Deleting is recoverable — the daemon moves the file to the Project's note
// trash — which is what lets that menu carry a delete at all; the one thing it refuses is
// emptying a Project's collection.

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
// Deletion is recoverable, so the confirmation says where the note goes rather than
// claiming a permanence the daemon no longer implements.
const DELETE_HINT='Click again to move this note to the Project note trash'
const PROTECTED_HINT='A Project keeps at least one note. Rename this one, or create another note first.'
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
  // The row menu and its inline delete confirmation close together, because the
  // confirmation has no existence outside the menu that hosts it.
  useDismissLevel(()=>{setMenu(null);setDeleteConfirm('')},!!menu,'note-row-menu')
  const [titlePrompt,setTitlePrompt]=useState<NoteTitlePrompt|null>(null)
  const [titleBusy,setTitleBusy]=useState(false)
  const [titleError,setTitleError]=useState('')
  const [browseOpen,setBrowseOpen]=useState(false)
  const scopeId=allProjects?'':project?.id||''
  // Desktop keeps this component mounted while Projects switch. Tag the loaded
  // collection with its request scope so the new Project cannot validate its
  // remembered sub-tab against the previous Project's notes for one render.
  const loadedScope=useRef<string|null>(null)
  const scopedItems=loadedScope.current===scopeId?items:null
  const scopedError=loadedScope.current===scopeId?error:''
  // A generation guard rather than a cancel flag: a refresh fired by a note-changed
  // event can land after a scope change, and the newest request must win.
  const generation=useRef(0)
  const menuPanel=useRef<HTMLDivElement>(null)
  const longPress=useRef<number|null>(null)
  const pressWatch=useRef<(()=>void)|null>(null)
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
      loadedScope.current=scopeId
      setItems(result.items);setError('')
    }catch(cause){
      if(mine!==generation.current)return
      loadedScope.current=scopeId
      setItems([]);setError(cause instanceof Error?cause.message:String(cause))
    }
  }

  useEffect(()=>{loadedScope.current=null;setItems(null);void load()},[scopeId])
  useEffect(()=>{
    const changed=()=>void load()
    const reconnected=(event:Event)=>{
      if((event as CustomEvent<{resumed?:boolean}>).detail?.resumed)void load()
    }
    window.addEventListener('mux:note-changed',changed)
    window.addEventListener('mux:events-connected',reconnected)
    return()=>{
      window.removeEventListener('mux:note-changed',changed)
      window.removeEventListener('mux:events-connected',reconnected)
    }
  },[scopeId])

  useEffect(()=>()=>{
    if(longPress.current!==null)window.clearTimeout(longPress.current)
    pressWatch.current?.()
  },[])

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
        event.preventDefault();event.stopImmediatePropagation();dismissStack.pop();return
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
    const listed=scopedItems||[]
    if(!needle)return listed
    return listed.filter(item=>
      item.title.toLowerCase().includes(needle)
      ||item.project_name.toLowerCase().includes(needle)
      ||item.excerpt.toLowerCase().includes(needle))
  },[scopedItems,needle])

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
    (scopedItems||[]).filter(item=>item.project_id===project?.id)
  ),[scopedItems,project?.id])
  // Counted over the loaded collection rather than the search results, so filtering to one
  // row never makes an ordinary note look like a Project's last one.
  const noteCounts=useMemo(()=>projectNoteCounts(scopedItems||[]),[scopedItems])
  const selectedTabId=canonicalNoteTabId(selectedResourceId)

  // A stale remembered id can follow a deletion or an older on-disk build. Resolve it only
  // after the scoped collection loads, then persist the replacement through the same callback
  // as an explicit tab click.
  useEffect(()=>{
    if(allProjects||!project||!scopedItems)return
    const fallback=fallbackNoteTab(selectedResourceId,projectItems)
    if(fallback===selectedTabId)return
    if(fallback===SCRATCHPAD_TAB_ID)onOpenScratchpad('drawer')
    else{
      const note=projectItems.find(item=>projectNoteTabId(item.note_id)===fallback)
      if(note)onOpenNote(note.project_id,note.note_id,note.title,'drawer')
    }
  },[allProjects,scopedItems,project?.id,projectItems,selectedResourceId,selectedTabId])

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
    longPress.current=null
    pressWatch.current?.();pressWatch.current=null
  }
  const openMenu=(note:ProjectNoteSummary,x:number,y:number)=>{
    setDeleteConfirm('')
    menuOpenedAt.current=performance.now()
    setMenu({note,x,y})
  }
  // The pending press is watched on `window`, not on the pressed element. A rail tab sits
  // inside `OverflowRail`, which captures the pointer for its own horizontal pan the moment
  // a touch lands, retargeting every later pointer event to the rail — an element-local
  // move/up handler would then never fire, and this timer would open a menu partway through
  // someone's scroll.
  const beginLongPress=(note:ProjectNoteSummary,event:JSX.TargetedPointerEvent<HTMLElement>)=>{
    touchPress.current=event.pointerType==='touch'
    if(!touchPress.current)return
    cancelLongPress()
    const {clientX,clientY,pointerId}=event
    const moved=(later:PointerEvent)=>{
      if(later.pointerId!==pointerId)return
      if(Math.hypot(later.clientX-clientX,later.clientY-clientY)>10)cancelLongPress()
    }
    const ended=(later:PointerEvent)=>{if(later.pointerId===pointerId)cancelLongPress()}
    window.addEventListener('pointermove',moved,true)
    window.addEventListener('pointerup',ended,true)
    window.addEventListener('pointercancel',ended,true)
    pressWatch.current=()=>{
      window.removeEventListener('pointermove',moved,true)
      window.removeEventListener('pointerup',ended,true)
      window.removeEventListener('pointercancel',ended,true)
    }
    longPress.current=window.setTimeout(()=>{
      longPress.current=null;suppressClick.current=true
      cancelLongPress()
      navigator.vibrate?.(20)
      openMenu(note,clientX,clientY)
    },LONG_PRESS_MS)
  }
  const openContextMenu=(note:ProjectNoteSummary,event:JSX.TargetedMouseEvent<HTMLElement>)=>{
    event.preventDefault();event.stopPropagation();cancelLongPress()
    if(touchPress.current)suppressClick.current=true
    openMenu(note,event.clientX,event.clientY)
  }
  // Right-click and long-press are pointer gestures; a keyboard needs its own way in, and
  // the rail tabs have no inline actions to fall back on the way browser rows do.
  const openMenuFromKeyboard=(note:ProjectNoteSummary,event:JSX.TargetedKeyboardEvent<HTMLButtonElement>)=>{
    if(event.key!=='ContextMenu'&&!(event.key==='F10'&&event.shiftKey))return false
    event.preventDefault()
    const box=event.currentTarget.getBoundingClientRect()
    openMenu(note,box.left,box.bottom)
    return true
  }
  const suppressLongPressClick=(event:JSX.TargetedMouseEvent<HTMLElement>)=>{
    if(!suppressClick.current)return
    suppressClick.current=false;event.preventDefault();event.stopPropagation()
  }
  const deleteProjectNote=async(note:ProjectNoteSummary)=>{
    const key=noteKey(note)
    if(lastNoteInProject(note,noteCounts))return
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
    const guarded=lastNoteInProject(note,noteCounts)
    return <>
      <button
        class={`note-delete ${confirming?'confirming':''} ${guarded?'protected':''}`}
        aria-label={guarded
          ?`${note.title} is the only note in ${note.project_name} and cannot be deleted`
          :confirming?`Confirm deletion of ${note.title}`:`Delete ${note.title}`}
        title={guarded?PROTECTED_HINT:confirming?DELETE_HINT:'Delete note'}
        disabled={busy||guarded}
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
    onClickCapture={suppressLongPressClick}
  >
    <button
      onClick={()=>openNote(note,'drawer')}
      onKeyDown={event=>{openMenuFromKeyboard(note,event)}}
      title={`Open ${note.title} in this panel · right-click or hold for actions`}
    >
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
            title={`${note.title} · right-click or hold for actions`}
            onClick={()=>openNote(note,'drawer')}
            onContextMenu={event=>openContextMenu(note,event)}
            onPointerDown={event=>beginLongPress(note,event)}
            onClickCapture={suppressLongPressClick}
            onKeyDown={event=>{
              if(openMenuFromKeyboard(note,event))return
              if(event.key==='ArrowLeft'||event.key==='ArrowRight')focusAdjacentTab(event,event.key==='ArrowLeft'?-1:1)
            }}
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
        <Dropdown ariaLabel="Filter notes by project" value={allProjects?'':scopeId} onChange={value=>onAllProjects(!value)} options={[
          {value:project?.id||'',label:project?project.name:'No project',disabled:!project},
          {value:'',label:'All projects'},
        ]}/>
      </div>
      <div class="notes-body notes-tab-body">
        {scopedError&&<p class="notes-state error" role="alert">{scopedError}</p>}
        {!scopedItems&&!scopedError&&<p class="notes-state">Reading notes…</p>}
        {scopedItems&&!scopedError&&!shown.length&&<p class="notes-state">
          {scopedItems.length?'No note matches this search.':'No notes yet. Create one for this Project.'}
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
      <p class="notes-footnote">Scratchpad is global. Project notes stay in creation order and remain in the tab rail. Deleting one moves it to <code>.swe-mux/notes/trash/</code>; a Project always keeps its last note.</p>
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
        disabled={deleting===noteKey(menu.note)||lastNoteInProject(menu.note,noteCounts)}
        title={lastNoteInProject(menu.note,noteCounts)?PROTECTED_HINT:DELETE_HINT}
        onClick={()=>void deleteProjectNote(menu.note)}
      >{deleting===noteKey(menu.note)?'Deleting…':deleteConfirm===noteKey(menu.note)?'Confirm delete':'Delete note'}</button>
      <p class="context-note">{lastNoteInProject(menu.note,noteCounts)
        ?PROTECTED_HINT
        :'Deleted notes move to the Project note trash and can be restored from disk.'}</p>
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
