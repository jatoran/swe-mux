import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { notifyPromptLibraryChanged } from './promptLibraryEvents'
import { renderPromptTemplate } from './promptTemplates'
import type { Project, ProjectBackend } from './types'
import { allBackendNames } from './harnessRegistry'

export type PromptTemplate={
  id:string;key:string;scope:'global'|'project';title:string;body:string;tags:string[];variables:string[]
  backends:ProjectBackend[];created_at:number;updated_at:number;revision:string
  favorite:boolean;last_used_at?:number|null;use_count:number;conflict:boolean
}
type Library={configured_scope:string;items:PromptTemplate[];diagnostics:Array<{path:string;error:string}>}
type Draft={scope:'global'|'project';title:string;body:string;tags:string;backends:ProjectBackend[]}
const emptyDraft=(project?:Project):Draft=>({scope:project?'project':'global',title:'',body:'',tags:'',backends:allBackendNames()})

export function PromptLibrary({project,backend,onInsert,onClose}:{project?:Project;backend?:ProjectBackend;onInsert:(text:string)=>void;onClose:()=>void}){
  const [library,setLibrary]=useState<Library|null>(null)
  const [query,setQuery]=useState('')
  const [selected,setSelected]=useState<PromptTemplate|null>(null)
  const [variables,setVariables]=useState<Record<string,string>>({})
  const [draft,setDraft]=useState<Draft|null>(null)
  const [savedDraft,setSavedDraft]=useState<Draft|null>(null)
  const [editing,setEditing]=useState<PromptTemplate|null>(null)
  const [message,setMessage]=useState('')
  const [confirmClose,setConfirmClose]=useState(false)
  const [confirmDelete,setConfirmDelete]=useState(false)
  const search=useRef<HTMLInputElement>(null)
  const projectArg=project?`?project_id=${encodeURIComponent(project.id)}`:''
  const load=()=>api<Library>('GET',`/api/prompts${projectArg}`).then(next=>{setLibrary(next);setSelected(current=>next.items.find(item=>item.key===current?.key)||next.items[0]||null)}).catch(error=>setMessage(error.message))
  useEffect(()=>{void load();search.current?.focus()},[project?.id])
  useEffect(()=>setVariables(current=>Object.fromEntries((selected?.variables||[]).map(name=>[name,current[name]||'']))),[selected?.key])
  const dirty=Boolean(draft&&savedDraft&&JSON.stringify(draft)!==JSON.stringify(savedDraft))
  const close=()=>{if(dirty){setConfirmClose(true);return}onClose()}
  const filtered=useMemo(()=>{
    const needle=query.trim().toLocaleLowerCase()
    return (library?.items||[]).filter(item=>(!backend||item.backends.includes(backend))&&(!needle||`${item.title} ${item.tags.join(' ')} ${item.body}`.toLocaleLowerCase().includes(needle)))
  },[library,query,backend])
  const preview=selected?renderPromptTemplate(selected.body,variables):''
  const missing=selected?.variables.filter(name=>!variables[name]?.trim())||[]
  const beginEdit=(item?:PromptTemplate)=>{
    const next=item?{scope:item.scope,title:item.title,body:item.body,tags:item.tags.join(', '),backends:[...item.backends]}:emptyDraft(project)
    setEditing(item||null);setDraft(next);setSavedDraft(next);setMessage('')
  }
  const save=async():Promise<boolean>=>{
    if(!draft)return false
    const body={project_id:project?.id,scope:draft.scope,title:draft.title,body:draft.body,tags:draft.tags.split(',').map(item=>item.trim()).filter(Boolean),backends:draft.backends,revision:editing?.revision}
    try{
      if(editing)await api('PUT',`/api/prompts/${editing.scope}/${editing.id}`,body)
      else await api('POST','/api/prompts',body)
      notifyPromptLibraryChanged()
      setDraft(null);setSavedDraft(null);setEditing(null);setMessage('Saved.');await load()
      return true
    }catch(error){setMessage(error instanceof Error?error.message:String(error));return false}
  }
  const insert=async()=>{
    if(!selected||missing.length)return
    onInsert(preview)
    await api('POST',`/api/prompts/${selected.scope}/${selected.id}/use`,{project_id:project?.id}).then(()=>notifyPromptLibraryChanged()).catch(()=>{})
    onClose()
  }
  const favorite=async(item:PromptTemplate)=>{
    await api('PATCH',`/api/prompts/${item.scope}/${item.id}/favorite`,{project_id:project?.id,favorite:!item.favorite})
    notifyPromptLibraryChanged()
    await load()
  }
  const remove=async()=>{
    if(!editing)return
    try{await api('DELETE',`/api/prompts/${editing.scope}/${editing.id}`,{project_id:project?.id,revision:editing.revision});notifyPromptLibraryChanged();setDraft(null);setEditing(null);await load()}
    catch(error){setMessage(error instanceof Error?error.message:String(error))}
  }
  return <div class="modal-layer prompt-library-layer" onMouseDown={event=>event.target===event.currentTarget&&close()}>
    <section class="prompt-library" role="dialog" aria-modal="true" aria-label="Prompt library">
      <header><div><strong>PROMPT LIBRARY</strong><small>{project?project.name:'global templates'} · text is inserted, never sent</small></div><button aria-label="Close prompt library" onClick={close}>×</button></header>
      {draft?<div class="prompt-editor">
        <label>Scope<select value={draft.scope} disabled={Boolean(editing)} onChange={event=>setDraft({...draft,scope:event.currentTarget.value as Draft['scope']})}><option value="global">Global</option>{project&&<option value="project">Project</option>}</select></label>
        <label>Title<input value={draft.title} onInput={event=>setDraft({...draft,title:event.currentTarget.value})}/></label>
        <label>Tags<input value={draft.tags} placeholder="review, git, planning" onInput={event=>setDraft({...draft,tags:event.currentTarget.value})}/></label>
        <fieldset><legend>Compatible backends</legend>{allBackendNames().map(value=><label class="check"><span>{value}</span><input type="checkbox" checked={draft.backends.includes(value)} onChange={event=>setDraft({...draft,backends:event.currentTarget.checked?[...draft.backends,value]:draft.backends.filter(item=>item!==value)})}/></label>)}</fieldset>
        <label>Template body<textarea rows={14} value={draft.body} placeholder="Use {{variable_name}} placeholders." onInput={event=>setDraft({...draft,body:event.currentTarget.value})}/></label>
        <div class="prompt-actions"><button disabled={!dirty} class="primary" onClick={()=>void save()}>Save</button><button onClick={()=>{setDraft(null);setSavedDraft(null);setEditing(null)}}>Discard</button>{editing&&!confirmDelete&&<button class="danger" onClick={()=>setConfirmDelete(true)}>Delete</button>}{editing&&confirmDelete&&<><button class="danger" onClick={()=>void remove()}>Confirm delete</button><button onClick={()=>setConfirmDelete(false)}>Cancel</button></>}</div>
      </div>:<div class="prompt-library-body">
        <aside><div class="prompt-search"><input ref={search} value={query} onInput={event=>setQuery(event.currentTarget.value)} placeholder="Search prompts…"/><button onClick={()=>beginEdit()}>+</button></div>
          {filtered.map(item=><button key={item.key} class={selected?.key===item.key?'active':''} onClick={()=>setSelected(item)}><span>{item.favorite?'★ ':'☆ '}{item.title}</span><small>{item.scope}{item.conflict?' · ID CONFLICT':''}{item.tags.length?` · ${item.tags.join(', ')}`:''}</small></button>)}
          {!filtered.length&&<p>No compatible prompts.</p>}
        </aside>
        <main>{selected?<><header><div><strong>{selected.title}</strong><small>{selected.scope} · {selected.backends.join(' · ')}</small></div><div><button aria-label="Toggle favorite" onClick={()=>void favorite(selected)}>{selected.favorite?'★':'☆'}</button><button onClick={()=>beginEdit(selected)}>Edit</button></div></header>
          {selected.conflict&&<p class="settings-inline-error">A global and Project template share this stable ID. Choose by scope or edit one ID on disk.</p>}
          {selected.variables.map(name=><label key={name}>{name}<input value={variables[name]||''} onInput={event=>setVariables({...variables,[name]:event.currentTarget.value})}/></label>)}
          <label>Preview<textarea readOnly rows={12} value={preview}/></label>
          <div class="prompt-actions"><button class="primary" disabled={missing.length>0} title={missing.length?`Fill: ${missing.join(', ')}`:'Insert without submitting'} onClick={()=>void insert()}>Insert into terminal</button><button onClick={()=>void navigator.clipboard.writeText(preview)}>Copy</button></div>
        </>:<p>Create a global or Project prompt template.</p>}</main>
      </div>}
      {library?.diagnostics.map(item=><p class="settings-inline-error">{item.path}: {item.error}</p>)}
      {message&&<footer aria-live="polite">{message}</footer>}
    </section>
    {confirmClose&&<section class="modal prompt-close-confirm" role="alertdialog" aria-modal="true" onMouseDown={event=>event.stopPropagation()}><h3>Unsaved prompt</h3><p>Save or discard the pending template changes.</p><button class="primary" onClick={()=>void save().then(saved=>saved&&onClose())}>Save</button><button onClick={()=>{setConfirmClose(false);onClose()}}>Discard</button><button onClick={()=>setConfirmClose(false)}>Keep editing</button></section>}
  </div>
}
