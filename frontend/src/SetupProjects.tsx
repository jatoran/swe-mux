import { rich, t } from './localization/index.ts'
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
  return <section><h2 data-heading-message="SetupProjects.ec420f1206">{t("SetupProjects.ec420f1206")}</h2><p class="setup-lede">{t("SetupProjects.c66601e588")}</p>
    <div class="setup-inline-actions"><button class="primary" disabled={busy} onClick={()=>setBrowsing(true)}>{t("SetupProjects.d7fc3d7eaf")}</button><button disabled={busy} onClick={()=>setManual(value=>!value)}>{t("SetupProjects.c04ed055c5")}</button></div>
    {browsing && <DirectoryPicker initialPath={draft.project_path||''} onCancel={()=>setBrowsing(false)} onSelect={path=>{onChange({project_path:path});setManual(true);setBrowsing(false)}}/>}
    {manual && <div class="setup-manual">
      <label>{rich("SetupProjects.3efd97d51b", { value1: <input value={draft.project_path||''} placeholder={t("SetupProjects.1673623dff")} onInput={event=>onChange({project_path:event.currentTarget.value})}/> })}</label>
      <label>{rich("SetupProjects.dd1c4301df", { value1: <input value={draft.project_name||''} placeholder={t("SetupProjects.5cd827fd44")} onInput={event=>onChange({project_name:event.currentTarget.value})}/> })}</label>
      <button disabled={busy||!draft.project_path?.trim()} onClick={()=>void perform(async()=>{await add([{root:draft.project_path!.trim(),name:draft.project_name?.trim()||''}]);onChange({project_path:'',project_name:''})})}>{t("SetupProjects.5bbfc5a69b")}</button>
    </div>}
    <div class="setup-project-heading"><h3 data-heading-message="SetupProjects.5c72d4ba00">{t("SetupProjects.5c72d4ba00")}</h3><span role="status">{discovery.scanning ? t("SetupProjects.1795f96fc3") : t("SetupProjects.3e79cf143c", { length: discovery.items.length })}</span></div>
    <input type="search" class="setup-search" aria-label={t("SetupProjects.7c4e46b7cc")} placeholder={t("SetupProjects.cdb82f96a3")} value={draft.project_filter||''} onInput={event=>onChange({project_filter:event.currentTarget.value})}/>
    {!!discovery.items.length && <div class="setup-inline-actions"><button disabled={busy||!items.some(item=>item.available)} onClick={()=>onChange({selected_projects:[...new Set([...selected,...items.filter(item=>item.available).map(item=>item.root)])]})}>{t("SetupProjects.70f58736b6")}</button><button disabled={busy||!selected.size} onClick={()=>onChange({selected_projects:[]})}>{t("SetupProjects.cea4d2e010")}</button><span class="setup-hint">{rich("SetupProjects.b95cdbd130", { size: selected.size })}</span></div>}
    <div class="setup-project-list">{items.map(item=>{
      const existing=registered.current.has(setupPathKey(item.root))
      return <label class={`setup-project-row ${!item.available?'unavailable':''}`} key={item.root}><input type="checkbox" disabled={busy||!item.available} checked={selected.has(item.root)} onChange={event=>{const next=new Set(selected);if(event.currentTarget.checked)next.add(item.root);else next.delete(item.root);onChange({selected_projects:[...next]})}}/><span><strong>{item.name}</strong><small>{item.root}</small><small>{existing ? t("SetupProjects.3f7d552f08") : ''}{item.harnesses.join(', ')}{!item.available ? t("SetupProjects.f37031f9d7") : ''}</small></span></label>
    })}</div>
    {!items.length && <p class="setup-hint">{query ? t("SetupProjects.3db75001e8") : discovery.scanning ? t("SetupProjects.b9946b64cf") : t("SetupProjects.15401e9c17")}</p>}
    {discovery.limited && <p class="setup-hint">{t("SetupProjects.f78f4fd364")}</p>}
    {!!discovery.errors.length && <div role="alert">{rich("SetupProjects.03c58c4a32", { value1: <button disabled={discovery.scanning} onClick={discovery.retry}>{t("SetupProjects.942087cc2d")}</button>, value2: <details><summary>{t("SetupProjects.45989de49f")}</summary>{discovery.errors.map(message=><p key={message}>{message}</p>)}</details> })}</div>}
    {projects.length>0 && <label class="setup-select">{rich("SetupProjects.443610e14b", { value1: <select value={draft.project_id||projects[0].id} onChange={event=>onChange({project_id:event.currentTarget.value})}>{projects.map(project=><option key={project.id} value={project.id}>{project.name}</option>)}</select> })}</label>}
    {error && <p role="alert">{error} <button onClick={()=>void load().catch(cause=>setError(cause.message))}>{t("SetupProjects.b2f9313077")}</button></p>}
    <footer class="setup-step-actions"><button class="primary" disabled={busy} onClick={()=>void continueSetup()}>{selected.size ? t("SetupProjects.f28074b9cc", { size: selected.size }) : projects.length ? t("SetupProjects.31fbef1625") : t("SetupProjects.8e4eedccd3")}</button></footer>
  </section>
}
