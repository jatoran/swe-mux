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
    }).catch(cause=>{if(!controller.signal.aborted)setError(`Could not load global defaults. ${(cause as Error).message}`)})
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
      }catch(cause){onSetupComplete(project,{errors:[{script:'Setup commands',error:(cause as Error).message}],started:false})}
    }
    submitting.current=false
    if(alive.current)setBusy(false)
  }
  return <div class="modal-layer project-registry-dialog-layer" role="dialog" aria-modal="true" aria-label="Create project" onMouseDown={event=>{if(event.target===event.currentTarget)close()}}>
    <form ref={panel} data-tutorial="project-form" class="modal project-create-dialog" onSubmit={event=>{event.preventDefault();void submit()}}>
      <div class="modal-heading"><h2>Create project</h2><button type="button" aria-label="Close create project" disabled={busy} onClick={close}>×</button></div>
      <fieldset disabled={busy} class="project-create-fields">
        <div class="project-create-mode" role="tablist" aria-label="How to add this project">
          <button type="button" role="tab" aria-selected={draft.mode==='existing'} class={draft.mode==='existing'?'active':''} onClick={()=>setDraft(current=>({...current,mode:'existing'}))}>Existing folder</button>
          <button type="button" role="tab" aria-selected={draft.mode==='new'} class={draft.mode==='new'?'active':''} onClick={()=>setDraft(current=>({...current,mode:'new'}))}>New folder</button>
        </div>
        <label>Name<input ref={nameInput} value={draft.name} onInput={event=>setDraft(current=>({...current,name:event.currentTarget.value}))} autofocus/></label>
        {draft.mode==='existing'?<label>Folder<div class="project-folder-field"><input aria-label="Folder" value={draft.root} onInput={event=>setDraft(current=>({...current,root:event.currentTarget.value}))} placeholder="Path to an existing folder"/><button type="button" onClick={()=>setBrowsing(true)}>Browse…</button></div></label>:<>
          <label>Parent folder<div class="project-folder-field"><input aria-label="Parent folder" value={draft.parent} onInput={event=>setDraft(current=>({...current,parent:event.currentTarget.value}))} placeholder="Choose a parent folder"/><button type="button" onClick={()=>setBrowsing(true)}>Browse…</button></div></label>
          <label>New folder name<input value={projectCreateFolder(draft)} onInput={event=>setDraft(current=>({...current,folder:event.currentTarget.value,folderTouched:true}))} placeholder={suggestFolderName(draft.name)||'my-project'}/></label>
        </>}
        {groups.length>0&&<label>Group<Dropdown ariaLabel="Project group" value={draft.group_id} onChange={group_id=>setDraft(current=>({...current,group_id}))} options={[{value:'',label:'Ungrouped'},...groups.map(group=>({value:group.id,label:group.name}))]}/></label>}
        <div class="project-create-defaults"><strong>Global defaults selected</strong><span>Customize this Project after creation.</span></div>
        {selectedScripts.length>0&&<details class="project-create-commands"><summary>Global setup commands ({selectedScripts.length})</summary><p class="modal-note">These commands will run in the new Project.</p><ul>{selectedScripts.map(script=><li key={script.id}><strong>{script.label}</strong><code>{script.command}</code></li>)}</ul></details>}
        {draft.mode==='new'&&<p class="modal-note">Creates one folder inside the selected parent.</p>}
      </fieldset>
      {!loaded&&!error&&<p role="status" class="modal-note">Loading global defaults…</p>}
      {error&&<p role="alert" class="project-create-error">{error}{!loaded&&<button type="button" onClick={()=>setAttempt(value=>value+1)}>Retry</button>}</p>}
      <div class="modal-footer"><button type="button" disabled={busy} onClick={close}>Cancel</button><button class="primary" type="submit" disabled={busy||!loaded||!projectCreateReady(draft)}>{busy?'Creating…':'Create project'}</button></div>
    </form>
    {browsing&&<DirectoryPicker initialPath={draft.mode==='new'?draft.parent:draft.root} onCancel={()=>setBrowsing(false)} onSelect={root=>{setDraft(current=>current.mode==='new'?{...current,parent:root}:{...current,root,name:current.name||folderNameFromPath(root)});setBrowsing(false)}}/>}
  </div>
}
