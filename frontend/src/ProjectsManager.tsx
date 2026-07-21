import { useEffect, useMemo, useState } from 'preact/hooks'
import type { Project, ProjectGroup, Session } from './types'

type Props = {
  projects:Project[]
  groups:ProjectGroup[]
  sessions:Session[]
  onClose:()=>void
  onAdd:()=>void
  onAddGroup:()=>void
  onOpen:(project:Project)=>void
  onSettings:(project:Project)=>void
  onNote:(project:Project)=>void
  onFiles:(project:Project)=>void
  onPatch:(project:Project,changes:Partial<Pick<Project,'name'|'group_id'|'sidebar_visible'>>)=>Promise<Project>
  onDelete:(project:Project)=>Promise<void>
}

export function ProjectsManager({projects,groups,sessions,onClose,onAdd,onAddGroup,onOpen,onSettings,onNote,onFiles,onPatch,onDelete}:Props){
  const isVisible=(project:Project)=>project.sidebar_visible!==false
  const ordered=useMemo(()=>[...projects].sort((a,b)=>a.position-b.position||a.name.localeCompare(b.name)),[projects])
  const [selectedId,setSelectedId]=useState(ordered[0]?.id||'')
  const [query,setQuery]=useState('')
  const [filter,setFilter]=useState<'all'|'visible'|'hidden'>('all')
  const [name,setName]=useState('')
  const [groupId,setGroupId]=useState('')
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [confirmDelete,setConfirmDelete]=useState(false)
  const selected=projects.find(project=>project.id===selectedId)||ordered[0]||null
  const shown=ordered.filter(project=>{
    if(filter==='visible'&&!isVisible(project))return false
    if(filter==='hidden'&&isVisible(project))return false
    const needle=query.trim().toLowerCase()
    return !needle||project.name.toLowerCase().includes(needle)||project.root.toLowerCase().includes(needle)
  })
  useEffect(()=>{if(!selected&&ordered[0])setSelectedId(ordered[0].id)},[selected,ordered])
  useEffect(()=>{setName(selected?.name||'');setGroupId(selected?.group_id||'');setConfirmDelete(false);setError('')},[selected?.id,selected?.name,selected?.group_id])
  const save=async()=>{
    if(!selected||!name.trim())return
    setBusy(true);setError('')
    try{await onPatch(selected,{name:name.trim(),group_id:groupId||null})}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const toggleVisible=async()=>{
    if(!selected)return
    setBusy(true);setError('')
    try{await onPatch(selected,{sidebar_visible:!isVisible(selected)})}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const remove=async()=>{
    if(!selected)return
    if(!confirmDelete){setConfirmDelete(true);return}
    setBusy(true);setError('')
    try{await onDelete(selected);setSelectedId(ordered.find(project=>project.id!==selected.id)?.id||'')}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause));setConfirmDelete(false)}
    finally{setBusy(false)}
  }
  const dirty=!!selected&&(name.trim()!==selected.name||(groupId||null)!==(selected.group_id||null))
  const liveCount=selected?sessions.filter(session=>session.project_id===selected.id&&!['exited','crashed'].includes(session.state)).length:0
  return <div class="modal-layer projects-manager-layer" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="projects-manager" role="dialog" aria-modal="true" aria-label="Manage projects">
      <header><div><span>PROJECTS::REGISTRY</span><h2>Projects</h2><small>Configured Projects keep their notes, files, settings, and history even when hidden from the sidebar.</small></div><div class="projects-manager-header-actions"><button onClick={onAddGroup}>New group</button><button data-tutorial="add-project" class="primary" onClick={onAdd}>+ Add project</button><button class="icon" aria-label="Close Projects" onClick={onClose}>×</button></div></header>
      <div class="projects-manager-body">
        <aside><div class="projects-manager-filter"><input aria-label="Search projects" placeholder="Search projects…" value={query} onInput={event=>setQuery(event.currentTarget.value)}/><select aria-label="Filter projects" value={filter} onChange={event=>setFilter(event.currentTarget.value as typeof filter)}><option value="all">All</option><option value="visible">In sidebar</option><option value="hidden">Hidden</option></select></div>
          <div data-tutorial="project-list" class="projects-manager-list">{shown.map(project=><button class={project.id===selected?.id?'active':''} onClick={()=>setSelectedId(project.id)}><span class={`project-visibility-dot ${isVisible(project)?'visible':'hidden'}`} aria-hidden="true"/><strong>{project.name}</strong><small>{project.root}</small><em>{isVisible(project)?'sidebar':'hidden'}</em></button>)}{!shown.length&&<p>No Projects match this view.</p>}</div>
        </aside>
        <main>{selected?<>
          <div class="projects-manager-title"><div><span>PROJECT::{selected.id.slice(0,8)}</span><h3>{selected.name}</h3><small>{liveCount} live session{liveCount===1?'':'s'} · {isVisible(selected)?'shown in sidebar':'configured, hidden from sidebar'}</small></div><button class={`sidebar-visibility-toggle ${isVisible(selected)?'active':''}`} disabled={busy} onClick={()=>void toggleVisible()}><span aria-hidden="true">{isVisible(selected)?'◉':'○'}</span>{isVisible(selected)?'Shown in sidebar':'Show in sidebar'}</button></div>
          <div class="projects-manager-actions"><button data-tutorial="open-project" class="primary" onClick={()=>onOpen(selected)}>Open workspace</button><button onClick={()=>onSettings(selected)}>Project settings</button><button onClick={()=>onNote(selected)}>Project note</button><button onClick={()=>onFiles(selected)}>Files</button></div>
          <div class="projects-manager-form"><label>Name<input value={name} onInput={event=>setName(event.currentTarget.value)}/></label><label>Folder<input value={selected.root} readOnly/></label><label>Group<div><select value={groupId} onChange={event=>setGroupId(event.currentTarget.value)}><option value="">Ungrouped</option>{groups.map(group=><option value={group.id}>{group.name}</option>)}</select><button onClick={onAddGroup}>+</button></div></label></div>
          {error&&<p class="projects-manager-error" role="alert">{error}</p>}
          <footer><button class={confirmDelete?'danger confirming':'danger'} disabled={busy||liveCount>0} title={liveCount>0?'Stop this Project’s live sessions before deleting it.':undefined} onClick={()=>void remove()}>{confirmDelete?'Confirm delete':'Delete project'}</button><span>{liveCount>0?'Deletion is locked while sessions are live.':'Hiding is non-destructive; deletion may also be blocked by history.'}</span><button class="primary" disabled={!dirty||busy||!name.trim()} onClick={()=>void save()}>{busy?'Saving…':'Save changes'}</button></footer>
        </>:<div class="projects-manager-empty"><strong>No Projects configured</strong><p>Add a folder-backed Project to begin.</p><button data-tutorial="add-project" class="primary" onClick={onAdd}>+ Add project</button></div>}</main>
      </div>
    </section>
  </div>
}
