import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { DirectoryPicker } from './DirectoryPicker'
import { useDismissLevel } from './modalFocus'
import { emitTutorialAction } from './tutorial'
import { setupPathKey, type ProjectDiscovery } from './setupDiscovery'
import type { SetupDraft } from './onboarding'

type Project={id:string;root:string;name:string}
type Props={draft:SetupDraft;onChange:(patch:Partial<SetupDraft>)=>void;discovery:ProjectDiscovery;onContinue:()=>Promise<void>;onBusy:(busy:boolean)=>void;onProjectsChanged:()=>void}
export function SetupProjects({draft,onChange,discovery,onContinue,onBusy,onProjectsChanged}:Props) {
  const [projects,setProjects]=useState<Project[]>([])
  const registered=useRef(new Map<string,Project>())
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [browsing,setBrowsing]=useState(false)
  const [manual,setManual]=useState(false)
  useDismissLevel(()=>setBrowsing(false),browsing,'setup-folder-picker')
  const load=async()=>{
    const values=await api<Project[]>('GET','/api/projects',undefined,{timeoutMs:10000})
    registered.current=new Map(values.map(item=>[setupPathKey(item.root),item]));setProjects(values)
  }
  useEffect(()=>{void load().catch(cause=>setError(cause.message))},[])
  useEffect(()=>{onBusy(busy);return()=>onBusy(false)},[busy])
  const selected=new Set(draft.selected_projects||[])
  const query=(draft.project_filter||'').trim().toLocaleLowerCase()
  const items=discovery.items.filter(item=>[item.name,item.root,...item.harnesses].some(text=>text.toLocaleLowerCase().includes(query)))
  const add=async(paths:{root:string;name:string}[])=>{
    for(const item of paths){
      let project=registered.current.get(setupPathKey(item.root))
      if(!project){
        project=await api<Project>('POST','/api/projects',item,{timeoutMs:15000})
        registered.current.set(setupPathKey(project.root),project)
        setProjects([...registered.current.values()])
        emitTutorialAction({action:'project-created'});onProjectsChanged()
      }
      onChange({project_id:project.id})
    }
  }
  const perform=async(action:()=>Promise<void>)=>{
    setBusy(true);setError('')
    try{await action()}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
  }
  const continueSetup=()=>perform(async()=>{
    await add(discovery.items.filter(item=>item.available&&selected.has(item.root)))
    onChange({selected_projects:[]})
    await onContinue()
  })
  return <section><h2>Where do you want to work?</h2><p class="setup-lede">A Project keeps your sessions and files together.</p>
    <div class="setup-inline-actions"><button class="primary" disabled={busy} onClick={()=>setBrowsing(true)}>Browse folders…</button><button disabled={busy} onClick={()=>setManual(value=>!value)}>Enter a path</button></div>
    {browsing&&<DirectoryPicker initialPath={draft.project_path||''} onCancel={()=>setBrowsing(false)} onSelect={path=>{onChange({project_path:path});setManual(true);setBrowsing(false)}}/>}
    {manual&&<div class="setup-manual">
      <label>Folder path<input value={draft.project_path||''} placeholder="Full path to an existing folder" onInput={event=>onChange({project_path:event.currentTarget.value})}/></label>
      <label>Project name (optional)<input value={draft.project_name||''} placeholder="Use folder name" onInput={event=>onChange({project_name:event.currentTarget.value})}/></label>
      <button disabled={busy||!draft.project_path?.trim()} onClick={()=>void perform(async()=>{await add([{root:draft.project_path!.trim(),name:draft.project_name?.trim()||''}]);onChange({project_path:'',project_name:''})})}>Add folder</button>
    </div>}
    <div class="setup-project-heading"><h3>Recent folders</h3><span role="status">{discovery.scanning?'Finding folders…':`${discovery.items.length} found`}</span></div>
    <input type="search" class="setup-search" aria-label="Filter project folders" placeholder="Filter by name, path, or agent…" value={draft.project_filter||''} onInput={event=>onChange({project_filter:event.currentTarget.value})}/>
    {!!discovery.items.length&&<div class="setup-inline-actions"><button disabled={busy||!items.some(item=>item.available)} onClick={()=>onChange({selected_projects:[...new Set([...selected,...items.filter(item=>item.available).map(item=>item.root)])]})}>Select visible</button><button disabled={busy||!selected.size} onClick={()=>onChange({selected_projects:[]})}>Clear selection</button><span class="setup-hint">{selected.size} selected</span></div>}
    <div class="setup-project-list">{items.map(item=>{
      const existing=registered.current.has(setupPathKey(item.root))
      return <label class={`setup-project-row ${!item.available?'unavailable':''}`} key={item.root}><input type="checkbox" disabled={busy||!item.available} checked={selected.has(item.root)} onChange={event=>{const next=new Set(selected);if(event.currentTarget.checked)next.add(item.root);else next.delete(item.root);onChange({selected_projects:[...next]})}}/><span><strong>{item.name}</strong><small>{item.root}</small><small>{existing?'Already added · ':''}{item.harnesses.join(', ')}{!item.available?' · Folder unavailable':''}</small></span></label>
    })}</div>
    {!items.length&&<p class="setup-hint">{query?'No matching folders. Try another search or add a folder manually.':discovery.scanning?'You can browse or add a folder while discovery runs.':'No recent folders found. Browse or enter a path to add one.'}</p>}
    {discovery.limited&&<p class="setup-hint">Showing recent history. Add any other folder manually.</p>}
    {!!discovery.errors.length&&<div role="alert">Some history could not be read. <button disabled={discovery.scanning} onClick={discovery.retry}>Retry</button><details><summary>Details</summary>{discovery.errors.map(message=><p key={message}>{message}</p>)}</details></div>}
    {projects.length>0&&<label class="setup-select">First session Project<select value={draft.project_id||projects[0].id} onChange={event=>onChange({project_id:event.currentTarget.value})}>{projects.map(project=><option key={project.id} value={project.id}>{project.name}</option>)}</select></label>}
    {error&&<p role="alert">{error} <button onClick={()=>void load().catch(cause=>setError(cause.message))}>Refresh projects</button></p>}
    <footer class="setup-step-actions"><button class="primary" disabled={busy} onClick={()=>void continueSetup()}>{selected.size?`Add ${selected.size} and continue`:projects.length?'Continue':'Add a Project later'}</button></footer>
  </section>
}
