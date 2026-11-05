import { rich, t } from './localization/index.ts'
import { useEffect, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
import { DirectoryPicker } from './DirectoryPicker'
import { Dropdown } from './Dropdown'
import { useDismissLevel, useModalFocus } from './modalFocus'
import { folderNameFromPath } from './pathNames'
import { defaultInitScriptSelection, emptyProjectCreateDraft, projectCreateFolder, projectCreateReady, projectCreateRequest, suggestFolderName, type InitScript } from './projectCreate'
import type { Project, ProjectGroup } from './types'

type SetupResult={errors:{script:string;error:string}[];started:boolean}
type Props={groups:ProjectGroup[];onClose:()=>void;onCreated:(project:Project)=>void;onSetupComplete:(project:Project,result:SetupResult)=>void}

/** Creation selects a folder and identity. Project policy is inherited, never copied or granted here. */
export function ProjectCreateDialog({groups,onClose,onCreated,onSetupComplete}:Props) {
  const [draft,setDraft]=useState(emptyProjectCreateDraft)
  const [scripts,setScripts]=useState<InitScript[]>([])
  const [loaded,setLoaded]=useState(false)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [browsing,setBrowsing]=useState(false)
  const [attempt,setAttempt]=useState(0)
  const submitting=useRef(false)
  const alive=useRef(true)
  const panel=useRef<HTMLFormElement>(null)
  const nameInput=useRef<HTMLInputElement>(null)
  const close=()=>{if(!submitting.current)onClose()}
  useModalFocus(panel,close,true,'project-create')
  useDismissLevel(()=>setBrowsing(false),browsing,'folder-picker')
  useEffect(()=>{const frame=requestAnimationFrame(()=>nameInput.current?.focus());return()=>cancelAnimationFrame(frame)},[])
  useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[])
  useEffect(()=>{
    const controller=new AbortController()
    setLoaded(false);setError('')
    void api<{project_init_scripts?:InitScript[];new_project_parent?:string}>('GET','/api/config',undefined,{signal:controller.signal,timeoutMs:10000}).then(config=>{
      if(controller.signal.aborted)return
      setScripts(config.project_init_scripts||[])
      setDraft(current=>({...current,parent:current.parent||config.new_project_parent||''}))
      setLoaded(true)
    }).catch(cause=>{if(!controller.signal.aborted)setError(t("ProjectCreateDialog.4a2977dde9", { message: (cause as Error).message }))})
    return()=>controller.abort()
  },[attempt])
  const selectedScripts=scripts.filter(script=>script.default_enabled)
  const submit=async()=>{
    if(submitting.current||!loaded||!projectCreateReady(draft))return
    submitting.current=true;setBusy(true);setError('')
    let project:Project
    try{
      project=await api<Project>('POST','/api/projects',projectCreateRequest(draft),{timeoutMs:15000})
    }catch(cause){
      const failure=cause as ApiError
      if(alive.current){setError(failure.fields?Object.values(failure.fields).join(' · '):failure.message);setBusy(false)}
      submitting.current=false;return
    }
    onCreated(project)
    // These are the operator's globally enabled commands, not repository-authored policy.
    // Registration remains durable if a command fails, and the workspace reports the failure.
    const ids=defaultInitScriptSelection(scripts)
    if(ids.length){
      try{
        const result=await api<{errors:SetupResult['errors']}>('POST',`/api/projects/${project.id}/init-scripts/run`,{script_ids:ids},{timeoutMs:30000})
        onSetupComplete(project,{errors:result.errors,started:result.errors.length<ids.length})
      }catch(cause){onSetupComplete(project,{errors:[{get script() { return t("ProjectCreateDialog.ea2ab00c4c") },error:(cause as Error).message}],started:false})}
    }
    submitting.current=false
    if(alive.current)setBusy(false)
  }
  return <div class="modal-layer project-registry-dialog-layer" role="dialog" aria-modal="true" aria-label={t("ProjectCreateDialog.fa67e4d0da")} onMouseDown={event=>{if(event.target===event.currentTarget)close()}}>
    <form ref={panel} data-tutorial="project-form" class="modal project-create-dialog" onSubmit={event=>{event.preventDefault();void submit()}}>
      <div class="modal-heading"><h2 data-heading-message="ProjectCreateDialog.fa67e4d0da">{t("ProjectCreateDialog.fa67e4d0da")}</h2><button type="button" aria-label={t("ProjectCreateDialog.c446f1a379")} disabled={busy} onClick={close}>×</button></div>
      <fieldset disabled={busy} class="project-create-fields">
        <div class="project-create-mode" role="tablist" aria-label={t("ProjectCreateDialog.d919bdeddd")}>
          <button type="button" role="tab" aria-selected={draft.mode==='existing'} class={draft.mode==='existing'?'active':''} onClick={()=>setDraft(current=>({...current,mode:'existing'}))}>{t("ProjectCreateDialog.f160333f79")}</button>
          <button type="button" role="tab" aria-selected={draft.mode==='new'} class={draft.mode==='new'?'active':''} onClick={()=>setDraft(current=>({...current,mode:'new'}))}>{t("ProjectCreateDialog.cf28f49ec5")}</button>
        </div>
        <label>{rich("ProjectCreateDialog.5b326d4444", { value1: <input ref={nameInput} value={draft.name} onInput={event=>setDraft(current=>({...current,name:event.currentTarget.value}))} autofocus/> })}</label>
        {draft.mode==='existing' ? <label>{rich("ProjectCreateDialog.275a68a3f6", { value1: <div class="project-folder-field"><input aria-label={t("ProjectCreateDialog.74ccd43303")} value={draft.root} onInput={event=>setDraft(current=>({...current,root:event.currentTarget.value}))} placeholder={t("ProjectCreateDialog.09cfabdf66")}/><button type="button" onClick={()=>setBrowsing(true)}>{t("ProjectCreateDialog.93c6b664dc")}</button></div> })}</label> : <>
          <label>{rich("ProjectCreateDialog.9d4d0696ec", { value1: <div class="project-folder-field"><input aria-label={t("ProjectCreateDialog.158f5a01ef")} value={draft.parent} onInput={event=>setDraft(current=>({...current,parent:event.currentTarget.value}))} placeholder={t("ProjectCreateDialog.5dbae92725")}/><button type="button" onClick={()=>setBrowsing(true)}>{t("ProjectCreateDialog.93c6b664dc")}</button></div> })}</label>
          <label>{rich("ProjectCreateDialog.77dbe72d19", { value1: <input value={projectCreateFolder(draft)} onInput={event=>setDraft(current=>({...current,folder:event.currentTarget.value,folderTouched:true}))} placeholder={suggestFolderName(draft.name) || t("ProjectCreateDialog.1c7cd944c2")}/> })}</label>
        </>}
        {groups.length>0 && <label>{rich("ProjectCreateDialog.bd64d0e1c9", { value1: <Dropdown ariaLabel={t("ProjectCreateDialog.0a330c84d8")} value={draft.group_id} onChange={group_id=>setDraft(current=>({...current,group_id}))} options={[{value:'',get label() { return t("ProjectCreateDialog.674b38cae7") }},...groups.map(group=>({value:group.id,label:group.name}))]}/> })}</label>}
        <div class="project-create-defaults"><strong>{t("ProjectCreateDialog.2804d37d58")}</strong><span>{t("ProjectCreateDialog.9dd4f0be5e")}</span></div>
        {selectedScripts.length>0 && <details class="project-create-commands"><summary>{rich("ProjectCreateDialog.ba1462f6ef", { length: selectedScripts.length })}</summary><p class="modal-note">{t("ProjectCreateDialog.02e548b1ec")}</p><ul>{selectedScripts.map(script=><li key={script.id}><strong>{script.label}</strong><code>{script.command}</code></li>)}</ul></details>}
        {draft.mode==='new' && <p class="modal-note">{t("ProjectCreateDialog.5c1bcc30d2")}</p>}
      </fieldset>
      {!loaded&&!error && <p role="status" class="modal-note">{t("ProjectCreateDialog.f34fea6351")}</p>}
      {error && <p role="alert" class="project-create-error">{error}{!loaded && <button type="button" onClick={()=>setAttempt(value=>value+1)}>{t("ProjectCreateDialog.942087cc2d")}</button>}</p>}
      <div class="modal-footer"><button type="button" disabled={busy} onClick={close}>{t("ProjectCreateDialog.19766ed6cc")}</button><button class="primary" type="submit" disabled={busy||!loaded||!projectCreateReady(draft)}>{busy ? t("ProjectCreateDialog.c79ed9492e") : t("ProjectCreateDialog.fa67e4d0da")}</button></div>
    </form>
    {browsing && <DirectoryPicker initialPath={draft.mode==='new'?draft.parent:draft.root} onCancel={()=>setBrowsing(false)} onSelect={root=>{setDraft(current=>current.mode==='new'?{...current,parent:root}:{...current,root,name:current.name||folderNameFromPath(root)});setBrowsing(false)}}/>}
  </div>
}
