import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { useModalFocus } from './modalFocus'
import type { Project } from './types'

export type SessionNoteSummary = {
  note_id:string;project_id:string;project_name:string
  updated_at:number;bytes:number;excerpt:string
  owner_label:string;owner_backend?:string|null;owner_live:boolean;owner_known:boolean
}

type Props={
  projects:Project[]
  initialProjectId?:string|null
  onClose:()=>void
  onOpen:(note:SessionNoteSummary)=>void
}

const sizeLabel=(bytes:number)=>bytes>=1024?`${Math.round(bytes/1024)} KiB`:`${bytes} B`

export function SessionNotesBrowser({projects,initialProjectId=null,onClose,onOpen}:Props){
  const [projectId,setProjectId]=useState(initialProjectId||'')
  const [items,setItems]=useState<SessionNoteSummary[]|null>(null)
  const [query,setQuery]=useState('')
  const [error,setError]=useState('')
  const panel=useRef<HTMLElement>(null)
  useModalFocus(panel,onClose)

  useEffect(()=>{
    let cancelled=false
    setItems(null);setError('')
    const path=projectId?`/api/session-notes?project_id=${encodeURIComponent(projectId)}`:'/api/session-notes'
    void api<{items:SessionNoteSummary[]}>('GET',path)
      .then(result=>{if(!cancelled)setItems(result.items)})
      .catch(cause=>{if(!cancelled){setItems([]);setError(cause instanceof Error?cause.message:String(cause))}})
    return()=>{cancelled=true}
  },[projectId])

  const shown=useMemo(()=>{
    const needle=query.trim().toLowerCase()
    if(!needle)return items||[]
    return (items||[]).filter(item=>
      item.owner_label.toLowerCase().includes(needle)
      ||item.project_name.toLowerCase().includes(needle)
      ||item.excerpt.toLowerCase().includes(needle))
  },[items,query])

  // Grouping by project keeps a multi-project listing readable; a single-project
  // filter still renders its one heading so the scope stays visible.
  const grouped=useMemo(()=>{
    const buckets=new Map<string,{label:string;notes:SessionNoteSummary[]}>()
    for(const item of shown){
      const bucket=buckets.get(item.project_id)||{label:item.project_name,notes:[]}
      bucket.notes.push(item)
      buckets.set(item.project_id,bucket)
    }
    return [...buckets.entries()]
  },[shown])

  const scope=projects.find(project=>project.id===projectId)
  return <div class="modal-layer session-notes-layer" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section ref={panel} class="modal session-notes-modal" role="dialog" aria-modal="true" aria-label="Session notes">
      <div class="modal-heading">
        <div><span>SESSION NOTES</span><h2>{scope?scope.name:'All projects'}</h2></div>
        <button type="button" aria-label="Close session notes" onClick={onClose}>×</button>
      </div>
      <div class="session-notes-filters">
        <input
          aria-label="Search session notes"
          placeholder="Search notes…"
          value={query}
          onInput={event=>setQuery(event.currentTarget.value)}
        />
        <select aria-label="Filter session notes by project" value={projectId} onChange={event=>setProjectId(event.currentTarget.value)}>
          <option value="">All projects</option>
          {projects.map(project=><option value={project.id}>{project.name}</option>)}
        </select>
      </div>
      <div class="session-notes-body">
        {error&&<p class="session-notes-state error" role="alert">{error}</p>}
        {!items&&!error&&<p class="session-notes-state">Reading session notes…</p>}
        {items&&!error&&!shown.length&&<p class="session-notes-state">
          {items.length
            ?'No session note matches this search.'
            :'No session notes with content yet. Use the note button on a terminal to start one.'}
        </p>}
        {grouped.map(([id,bucket])=><section class="session-notes-group" key={id}>
          <h3>project::{bucket.label}<span>{bucket.notes.length}</span></h3>
          {bucket.notes.map(note=><article class="session-note-row" key={`${note.project_id}:${note.note_id}`}>
            <button onClick={()=>onOpen(note)} title={`Open the session note for ${note.owner_label}`}>
              <strong>
                {note.owner_backend&&<span class={`agent-prefix ${note.owner_backend}`}>[{note.owner_backend}]</span>}
                {note.owner_label}
                {note.owner_live&&<em class="session-note-live" title="Its terminal is still running">live</em>}
              </strong>
              <span>{new Date(note.updated_at*1000).toLocaleString()} · {sizeLabel(note.bytes)}</span>
              <small>{note.excerpt}</small>
            </button>
          </article>)}
        </section>)}
      </div>
      <div class="modal-footer">
        <span>{items?`${shown.length} of ${items.length} note${items.length===1?'':'s'}`:'reading…'} · notes outlive their terminals</span>
        <button type="button" onClick={onClose}>Close</button>
      </div>
    </section>
  </div>
}
