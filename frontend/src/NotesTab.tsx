import { isComposingKey } from './inputComposition.ts'
import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { ComponentChildren, JSX } from 'preact'
import { api } from './api'
import { Dropdown } from './Dropdown'
import { byProjectName } from './projectOptions'
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
  scratchpadEnabled:boolean
  onScratchpadEnabled:(enabled:boolean)=>Promise<void>
  onDone:()=>void
  selectedResourceId:string|null
  editor:ComponentChildren
  onPopSelected:()=>void
}

const sizeLabel=(bytes:number)=>bytes>=1024?t("NotesTab.e419c42726", { value1: Math.round(bytes/1024) }):t("NotesTab.276b4e09cd", { value1: bytes })
const LONG_PRESS_MS=550
// Deletion is recoverable, so the confirmation says where the note goes rather than
// claiming a permanence the daemon no longer implements.
const deleteHint=() => (t("NotesTab.b13c6d8b78"))
const protectedHint=() => (t("NotesTab.a24ef7d6bf"))
const noteKey=(note:Pick<ProjectNoteSummary,'project_id'|'note_id'>)=>`${note.project_id}:${note.note_id}`
type NoteMenu=
  |{kind:'project';note:ProjectNoteSummary;x:number;y:number}
  |{kind:'scratchpad';x:number;y:number}
type NoteTitlePrompt={mode:'create'|'rename';title:string;note?:ProjectNoteSummary}

export function NotesTab({project,allProjects,onAllProjects,onOpenNote,onOpenScratchpad,scratchpadEnabled,onScratchpadEnabled,onDone,selectedResourceId,editor,onPopSelected}:Props){
  const [items,setItems]=useState<ProjectNoteSummary[]|null>(null)
  const [query,setQuery]=useState('')
  const [error,setError]=useState('')
  const [deleteConfirm,setDeleteConfirm]=useState('')
  const [deleting,setDeleting]=useState('')
  const [scratchpadBusy,setScratchpadBusy]=useState(false)
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
      if (isComposingKey(event)) return

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
  }, [scopedItems, needle, uiLocale.value, catalogRevision.value])

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
    // By Project name. The buckets arrive in whatever order the notes happened to, which
    // is not an order anybody can predict or scan; the notes *within* a bucket keep theirs.
    return byProjectName([...buckets.entries()],([,bucket])=>bucket.label)
  }, [shown, allProjects, uiLocale.value, catalogRevision.value])

  const projectItems=useMemo(()=>stableProjectNoteTabs(
    (scopedItems||[]).filter(item=>item.project_id===project?.id)
  ), [scopedItems, project?.id, uiLocale.value, catalogRevision.value])
  // Counted over the loaded collection rather than the search results, so filtering to one
  // row never makes an ordinary note look like a Project's last one.
  const noteCounts=useMemo(()=>projectNoteCounts(scopedItems||[]), [scopedItems, uiLocale.value, catalogRevision.value])
  const selectedTabId=canonicalNoteTabId(selectedResourceId)

  // A stale remembered id can follow a deletion or an older on-disk build. Resolve it only
  // after the scoped collection loads, then persist the replacement through the same callback
  // as an explicit tab click.
  useEffect(()=>{
    if(allProjects||!project||!scopedItems)return
    const fallback=fallbackNoteTab(selectedResourceId,projectItems,scratchpadEnabled)
    if(fallback===selectedTabId)return
    if(fallback===SCRATCHPAD_TAB_ID)onOpenScratchpad('drawer')
    else{
      const note=projectItems.find(item=>projectNoteTabId(item.note_id)===fallback)
      if(note)onOpenNote(note.project_id,note.note_id,note.title,'drawer')
    }
  },[allProjects,scopedItems,project?.id,projectItems,selectedResourceId,selectedTabId,scratchpadEnabled])

  // Selecting a drawer sub-tab keeps the panel visible. Moving a note to a workspace tab
  // hands the screen back on mobile.
  const openNote=(note:ProjectNoteSummary,place:NotePlacement)=>{
    setBrowseOpen(false)
    if(allProjects)onAllProjects(false)
    onOpenNote(note.project_id,note.note_id,note.title,place)
    if(place==='tab')onDone()
  }
  const openScratchpad=(place:NotePlacement)=>{
    if(!scratchpadEnabled)return
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
    setMenu({kind:'project',note,x,y})
  }
  const openScratchpadMenu=(x:number,y:number)=>{
    setDeleteConfirm('')
    touchPress.current=false
    menuOpenedAt.current=performance.now()
    setMenu({kind:'scratchpad',x,y})
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
  const openScratchpadMenuFromKeyboard=(event:JSX.TargetedKeyboardEvent<HTMLButtonElement>)=>{
    if(event.key!=='ContextMenu'&&!(event.key==='F10'&&event.shiftKey))return false
    event.preventDefault()
    const box=event.currentTarget.getBoundingClientRect()
    openScratchpadMenu(box.left,box.bottom)
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
      ?noteTabAfterDelete(selectedResourceId,note.note_id,projectItems,scratchpadEnabled)
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
      window.dispatchEvent(new CustomEvent('mux:note-changed',{detail:{scope:'project',projectId:note.project_id,kind:'note',noteId:note.note_id,get revision() { return t("NotesTab.ffa63583df") }}}))
    }catch(cause){
      setDeleteConfirm('');setError(cause instanceof Error?cause.message:String(cause));void load()
    }finally{setDeleting('')}
  }
  const toggleScratchpad=async()=>{
    setScratchpadBusy(true)
    try{await onScratchpadEnabled(!scratchpadEnabled);setMenu(null)}
    catch{/* App reports the durable save failure. */}
    finally{setScratchpadBusy(false)}
  }
  const noteActions=(note:ProjectNoteSummary)=>{
    const key=noteKey(note)
    const confirming=deleteConfirm===key
    const busy=deleting===key
    const guarded=lastNoteInProject(note,noteCounts)
    return <>
      <button
        class={`note-delete ${confirming?'confirming':''} ${guarded?'protected':''}`}
        aria-label={guarded ? t("NotesTab.0ec4ac4fed", { title: note.title, project_name: note.project_name }) : confirming ? t("NotesTab.e5abe44a66", { title: note.title }) : t("NotesTab.b4c7358981", { title: note.title })}
        title={guarded ? protectedHint() : confirming ? deleteHint() : t("NotesTab.b80e4f4423")}
        disabled={busy||guarded}
        onBlur={()=>setDeleteConfirm(current=>current===key?'':current)}
        onClick={()=>void deleteProjectNote(note)}
      >{busy ? '…' : confirming ? t("NotesTab.0f24da4cd0") : '×'}</button>
      <button class="note-open-as-tab" aria-label={t("NotesTab.2e4e65a278", { title: note.title })} title={t("NotesTab.de8c86d8b5")} onClick={()=>openNote(note,'tab')}>⇥</button>
    </>
  }
  const submitTitle=async(event:SubmitEvent)=>{
    event.preventDefault()
    if(!titlePrompt||!project)return
    const title=titlePrompt.title.trim()
    if(!title){setTitleError(t("NotesTab.8b33880c49"));return}
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
      onKeyDown={event=>{
      if (isComposingKey(event)) return
openMenuFromKeyboard(note,event)}}
      title={t("NotesTab.8268f4fbeb", { title: note.title })}
    >
      <strong>{note.title}</strong>
      <span>{new Date(note.updated_at*1000).toLocaleString(uiLocale.value)} · {sizeLabel(note.bytes)}</span>
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
        wrapperClassName="notes-subtabs-rail"
        activeKey={selectedTabId||''}
        touchDrag
        stripProps={{
          role:'tablist',get 'aria-label'() { return t("NotesTab.1a23cfed9e") },
          onContextMenu:event=>{
            if(event.target instanceof Element&&event.target.closest('button'))return
            event.preventDefault();event.stopPropagation()
            openScratchpadMenu(event.clientX,event.clientY)
          },
        }}
      >
        {scratchpadEnabled && <button
          role="tab"
          aria-selected={selectedTabId===SCRATCHPAD_TAB_ID}
          tabIndex={selectedTabId===SCRATCHPAD_TAB_ID?0:-1}
          class={selectedTabId===SCRATCHPAD_TAB_ID?'active':''}
          title={t("NotesTab.b9ac955c99")}
          disabled={!project}
          onClick={()=>openScratchpad('drawer')}
          onContextMenu={event=>{event.preventDefault();event.stopPropagation();openScratchpadMenu(event.clientX,event.clientY)}}
          onKeyDown={event=>{
      if (isComposingKey(event)) return

            if(openScratchpadMenuFromKeyboard(event))return
            if(event.key==='ArrowLeft'||event.key==='ArrowRight')focusAdjacentTab(event,event.key==='ArrowLeft'?-1:1)
          }}
        >{t("NotesTab.17bd998ee0")}</button>}
        {projectItems.map(note=>{
          const resourceId=projectNoteTabId(note.note_id)
          const active=selectedTabId===resourceId
          return <button data-tab-id={resourceId}
            key={resourceId}
            role="tab"
            aria-selected={active}
            tabIndex={active?0:-1}
            class={active?'active':''}
            title={t("NotesTab.9cb00ec966", { title: note.title })}
            onClick={()=>openNote(note,'drawer')}
            onContextMenu={event=>openContextMenu(note,event)}
            onPointerDown={event=>beginLongPress(note,event)}
            onClickCapture={suppressLongPressClick}
            onKeyDown={event=>{
      if (isComposingKey(event)) return

              if(openMenuFromKeyboard(note,event))return
              if(event.key==='ArrowLeft'||event.key==='ArrowRight')focusAdjacentTab(event,event.key==='ArrowLeft'?-1:1)
            }}
          >{note.title}</button>
        })}
      </OverflowRail>
      <button class="notes-browse" aria-label={t("NotesTab.a2991c109f")} aria-expanded={browserVisible} title={t("NotesTab.a2991c109f")} onClick={()=>setBrowseOpen(value=>!value)}>⌕</button>
      <button class="notes-new" aria-label={t("NotesTab.7c08a13d2e")} disabled={!project} title={t("NotesTab.7c08a13d2e")} onClick={()=>{setTitleError('');setTitlePrompt({mode:'create',get title() { return t("NotesTab.e93736573a") }})}}>+</button>
      <button class="notes-pop" aria-label={t("NotesTab.042c188f8e")} disabled={!selectedResourceId} title={t("NotesTab.042c188f8e")} onClick={onPopSelected}>⇥</button>
    </div>
    <div class="notes-editor-host">
      {editor || <p class="notes-state">{project ? t("NotesTab.a75e11122d") : t("NotesTab.348ec955e9")}</p>}
    </div>
    {browserVisible && <section class="notes-browser" aria-label={t("NotesTab.98968523eb")}>
      <div class="notes-browser-heading">
        <strong>{allProjects ? t("NotesTab.26d231e2b8") : project?.name || t("NotesTab.6bffbde03e")}</strong>
        <button onClick={()=>{setBrowseOpen(false);if(allProjects)onAllProjects(false)}}>{t("NotesTab.11a6767d56")}</button>
      </div>
      <div class="notes-filters">
        <input
          autoFocus
          aria-label={t("NotesTab.6e7a2179a3")}
          placeholder={t("NotesTab.d95d6ace9e")}
          value={query}
          spellcheck={false}
          onInput={event=>setQuery(event.currentTarget.value)}
        />
        <Dropdown ariaLabel={t("NotesTab.e770faa33e")} value={allProjects?'':scopeId} onChange={value=>onAllProjects(!value)} options={[
          {value:project?.id||'',label:project?project.name:t("NotesTab.f34c2be0d1"),disabled:!project},
          {value:'',get label() { return t("NotesTab.4b87271b6b") }},
        ]}/>
      </div>
      <div class="notes-body notes-tab-body">
        {scopedError && <p class="notes-state error" role="alert">{scopedError}</p>}
        {!scopedItems&&!scopedError && <p class="notes-state">{t("NotesTab.a75e11122d")}</p>}
        {scopedItems&&!scopedError&&!shown.length && <p class="notes-state">
          {scopedItems.length ? t("NotesTab.c314c24a94") : t("NotesTab.0f85f7aac7")}
        </p>}
        {!!shown.length && <div class="notes-listing">
          {allProjects ? grouped.map(([id,bucket])=><section class="notes-group" key={id}>
              <h3>{rich("NotesTab.f8d078029d", { label: bucket.label, value2: <span>{bucket.notes.length}</span> })}</h3>
              {bucket.notes.map(noteRow)}
            </section>) : <section class="notes-group">
              <h3>{rich("NotesTab.755e505612", { value1: <span>{shown.length}</span> })}</h3>
              {shown.map(noteRow)}
            </section>}
        </div>}
      </div>
      <p class="notes-footnote">{rich("NotesTab.d7ecee4711", { value1: (scratchpadEnabled) ? 'yes' : 'no', value2: <code>{t("NotesTab.9bd9029ed6")}</code> })}</p>
    </section>}
    {menu && <div
      class="context-menu note-row-menu"
      ref={el=>{menuPanel.current=el;fitMenuInViewport(el)}}
      role="menu"
      aria-label={menu.kind==='project' ? t("NotesTab.bc0d30cf9f", { title: menu.note.title }) : t("NotesTab.32c1bd13a6")}
      style={{left:clampContextMenuLeft(menu.x,window.innerWidth),top:Math.max(4,menu.y)}}
      onClickCapture={event=>{if(touchPress.current&&performance.now()-menuOpenedAt.current<250){event.preventDefault();event.stopPropagation()}}}
    >
      {menu.kind==='scratchpad' ? <>
        <div class="context-title"><strong>{t("NotesTab.a6a0bc74a1")}</strong></div>
        <button role="menuitem" disabled={scratchpadBusy} onClick={()=>void toggleScratchpad()}>{scratchpadBusy ? t("NotesTab.23e39291d6") : scratchpadEnabled ? t("NotesTab.c079bbe107") : t("NotesTab.a3b9f7ffd8")}</button>
        <p class="context-note">{t("NotesTab.d0f392e5f3")}</p>
      </> : <>
        <div class="context-title"><strong>{menu.note.title}</strong></div>
        <button role="menuitem" onClick={()=>{const note=menu.note;setMenu(null);openNote(note,'tab')}}>{t("NotesTab.320462920d")}</button>
        <button role="menuitem" onClick={()=>{setTitleError('');setTitlePrompt({mode:'rename',title:menu.note.title,note:menu.note})}}>{t("NotesTab.6fa62b3dba")}</button>
        <div class="context-rule" />
        <button
          role="menuitem"
          class={`danger ${deleteConfirm===noteKey(menu.note)?'confirming':''}`}
          disabled={deleting===noteKey(menu.note)||lastNoteInProject(menu.note,noteCounts)}
          title={lastNoteInProject(menu.note,noteCounts) ? protectedHint() : deleteHint()}
          onClick={()=>void deleteProjectNote(menu.note)}
        >{deleting===noteKey(menu.note) ? t("NotesTab.43b5894c3e") : deleteConfirm===noteKey(menu.note) ? t("NotesTab.da00fa968b") : t("NotesTab.b80e4f4423")}</button>
        <p class="context-note">{lastNoteInProject(menu.note,noteCounts) ? protectedHint() : t("NotesTab.868fe2637e")}</p>
      </>}
    </div>}
    {titlePrompt && <div class="modal-layer note-title-layer" onPointerDown={event=>{if(event.target===event.currentTarget&&!titleBusy){setTitlePrompt(null);setTitleError('')}}}>
      <form ref={titlePanel} class="modal note-title-modal" role="dialog" aria-modal="true" aria-label={titlePrompt.mode==='create' ? t("NotesTab.27ecd163ae") : t("NotesTab.cb9d97d7fd")} onPointerDown={event=>event.stopPropagation()} onSubmit={event=>void submitTitle(event)}>
        <div class="modal-heading"><div><span>{t("NotesTab.2f72f70002", { value1: (titlePrompt.mode==='create') ? 'yes' : 'no' })}</span><h2>{titlePrompt.mode==='create' ? t("NotesTab.76ea482f84") : t("NotesTab.cb9d97d7fd")}</h2></div><button type="button" disabled={titleBusy} aria-label={t("NotesTab.19766ed6cc")} onClick={()=>{setTitlePrompt(null);setTitleError('')}}>×</button></div>
        <label>{rich("NotesTab.a3799a0eb4", { value1: <input autoFocus value={titlePrompt.title} maxLength={160} disabled={titleBusy} onInput={event=>setTitlePrompt(current=>current?{...current,title:event.currentTarget.value}:current)}/> })}</label>
        {titleError && <p class="resource-create-error" role="alert">{titleError}</p>}
        <div class="modal-footer"><button type="button" disabled={titleBusy} onClick={()=>{setTitlePrompt(null);setTitleError('')}}>{t("NotesTab.19766ed6cc")}</button><button class="primary" disabled={titleBusy||!titlePrompt.title.trim()} type="submit">{titleBusy ? t("NotesTab.23e39291d6") : titlePrompt.mode==='create' ? t("NotesTab.4759498ac2") : t("NotesTab.3064d79a29")}</button></div>
      </form>
    </div>}
  </div>
}
