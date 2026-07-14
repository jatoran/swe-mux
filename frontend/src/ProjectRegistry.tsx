import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import type { ProjectScope } from './types'

type SpaceNote={id:string;label:string;active:boolean;path:string;revision:string}
type Props={onOpenNote:(scope:ProjectScope)=>void;onOpenSpaceNote:(note:SpaceNote)=>void;onClose:()=>void}

export function ProjectRegistry({onOpenNote,onOpenSpaceNote,onClose}:Props){
  const [items,setItems]=useState<ProjectScope[]>([])
  const [spaceNotes,setSpaceNotes]=useState<SpaceNote[]>([])
  const [selected,setSelected]=useState<ProjectScope|null>(null)
  const [showHidden,setShowHidden]=useState(false)
  const [error,setError]=useState('')
  const load=async()=>{try{const [result,notes]=await Promise.all([api<{items:ProjectScope[]}>('GET',`/api/projects${showHidden?'?include_hidden=1':''}`),api<SpaceNote[]>('GET','/api/space-notes')]);setItems(result.items);setSpaceNotes(notes);setError('')}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  useEffect(()=>{void load()},[showHidden])
  const inspect=async(id:string)=>{try{setSelected(await api<ProjectScope>('GET',`/api/projects/${id}`));setError('')}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  const hide=async(item:ProjectScope)=>{await api('PATCH',`/api/projects/${item.id}`,{hidden:!item.hidden});setSelected(null);await load()}
  const forget=async(item:ProjectScope)=>{try{await api('DELETE',`/api/projects/${item.id}`);setSelected(null);await load()}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}}
  return <div class="projects-layer" role="dialog" aria-modal="true" aria-label="Projects and durable notes" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="projects-panel"><header><div><span>PROJECT SHELF</span><h2>Projects</h2></div><label><input type="checkbox" checked={showHidden} onChange={event=>setShowHidden(event.currentTarget.checked)}/>hidden</label><button onClick={onClose}>×</button></header>
      {error&&<div class="projects-error">{error}</div>}
      <div class="projects-body"><aside>
        <div class="context-subtitle">PROJECT-OWNED</div>
        {items.length?items.map(item=><button class={selected?.id===item.id?'active':''} onClick={()=>void inspect(item.id)}><span class={`state-dot ${item.root_exists?'idle':'crashed'}`}/><strong>{item.label}</strong><small>{item.repo_group_label||'standalone'} · {item.live_count} live · {item.history_count} history · {item.artifact_count} notes</small><em>{item.root}</em></button>):<p>No durable project scopes yet.</p>}
        {spaceNotes.length>0&&<><div class="context-subtitle">APP-OWNED SPACE NOTES</div>{spaceNotes.map(note=><button onClick={()=>onOpenSpaceNote(note)}><span class={`state-dot ${note.active?'idle':'exited'}`}/><strong>{note.label}</strong><small>{note.active?'live space':'archived space'} · stored by swe-mux</small></button>)}</>}
      </aside>
        <main>{selected?<><div class="project-detail-heading"><div><h3>{selected.label}</h3><span>{selected.root}</span><small>{selected.root_exists?'available':'missing root'} · project scope::{selected.id}</small></div><button class="primary" disabled={!selected.root_exists} onClick={()=>onOpenNote(selected)}>Project note</button><button onClick={()=>void hide(selected)}>{selected.hidden?'Unhide':'Hide'}</button><button class="danger" disabled={!!selected.blockers&&Object.values(selected.blockers).some(Boolean)} title="Forget removes only the swe-mux index after history and note bindings are gone; project files are never deleted" onClick={()=>void forget(selected)}>Forget index</button></div>
          <section><h4>Project-owned files</h4><p>config::{selected.inventory?.config_exists?'present':'missing'} · rules::{selected.inventory?.rules_present_inert?'present (inert)':'missing'}</p>{selected.artifacts?.length?selected.artifacts.map(item=><article><strong>{item.owner_type}::{item.owner_label||item.owner_id}</strong><span>{item.relative_path}</span></article>):<p>No indexed agent-run notes yet.</p>}</section>
          <section><h4>Recovered project files</h4>{selected.detached_artifacts?.map(item=><article><strong>detached::{item.owner_type}::{item.owner_label||item.owner_id}</strong><span>{item.relative_path}</span></article>)}{selected.inventory?.unlinked.length?selected.inventory.unlinked.map(item=><article><strong>{item.kind==='spaces'?'legacy space-note backup':selected.inventory?.conflicting?.some(conflict=>conflict.path===item.path)?'conflicting':'unlinked'}::{item.filename}</strong><span>{item.path}</span></article>):!selected.detached_artifacts?.length&&<p>No detached or unlinked files.</p>}</section>
        </>:<div class="projects-placeholder"><strong>Projects own project configuration, project notes, and agent-run notes.</strong><p>Spaces are independent workflow containers. Their notes live in swe-mux app data and are listed separately on the left.</p></div>}</main></div>
    </section>
  </div>
}
