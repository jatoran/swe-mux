import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import type { Project } from './types'

// The drawer's Notes tab: an index of every note, not an editor.
//
// Notes are documents you author, so they stay ordinary pane tabs — the drawer
// unmounts a tab body on every switch, which would cost cursor/undo history and
// would break insert routing (switching to Clipboard would detach the very editor
// the insert was meant for). What belongs here is *finding* a note. Selecting a
// row opens it into the workspace through the ordinary companion placement rule.
//
// The Project note is pinned first and always present, even empty: it is the one
// note that exists for every Project. The focused terminal's note is pinned second
// when it holds text. Everything else is the filesystem-derived listing, which by
// design lists only notes that hold text, so a note started by a stray chip click
// never earns a row.

export type SessionNoteSummary = {
  note_id:string;project_id:string;project_name:string
  updated_at:number;bytes:number;excerpt:string
  owner_label:string;owner_backend?:string|null;owner_live:boolean;owner_known:boolean
}

type Props={
  project?:Project
  allProjects:boolean
  onAllProjects:(value:boolean)=>void
  /** The focused terminal's note, when it holds text. Pinned above the listing. */
  focusedNote:{projectId:string;noteId:string;label:string}|null
  onOpenProjectNote:(projectId:string)=>void
  onOpenSessionNote:(projectId:string,noteId:string)=>void
  onDone:()=>void
}

const sizeLabel=(bytes:number)=>bytes>=1024?`${Math.round(bytes/1024)} KiB`:`${bytes} B`

export function NotesTab({project,allProjects,onAllProjects,focusedNote,onOpenProjectNote,onOpenSessionNote,onDone}:Props){
  const [items,setItems]=useState<SessionNoteSummary[]|null>(null)
  const [query,setQuery]=useState('')
  const [error,setError]=useState('')
  const scopeId=allProjects?'':project?.id||''
  // A generation guard rather than a cancel flag: a refresh fired by a note-changed
  // event can land after a scope change, and the newest request must win.
  const generation=useRef(0)

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

  const openSession=(projectId:string,noteId:string)=>{onOpenSessionNote(projectId,noteId);onDone()}
  const noteRow=(note:SessionNoteSummary)=><article class="session-note-row" key={`${note.project_id}:${note.note_id}`}>
    <button onClick={()=>openSession(note.project_id,note.note_id)} title={`Open the session note for ${note.owner_label}`}>
      <strong>
        {note.owner_backend&&<span class={`agent-prefix ${note.owner_backend}`}>[{note.owner_backend}]</span>}
        {note.owner_label}
        {note.owner_live&&<em class="session-note-live" title="Its terminal is still running">live</em>}
      </strong>
      <span>{new Date(note.updated_at*1000).toLocaleString()} · {sizeLabel(note.bytes)}</span>
      <small>{note.excerpt}</small>
    </button>
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
          ?<button class="note-pin-row" title={`Open the Project note for ${project.name}`} onClick={()=>{onOpenProjectNote(project.id);onDone()}}>
            <span class="note-pin-kind">Project note</span><strong>{project.name}</strong>
          </button>
          :<p class="session-notes-state">Select a Project to see its note.</p>}
        {focusedNote&&<button class="note-pin-row" title={`Open the session note for ${focusedNote.label}`} onClick={()=>openSession(focusedNote.projectId,focusedNote.noteId)}>
          <span class="note-pin-kind">Session note</span><strong>{focusedNote.label}</strong>
        </button>}
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
    <p class="notes-footnote">Notes outlive their terminals. Opening one puts it in a pane.</p>
  </div>
}
