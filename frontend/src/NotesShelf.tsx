import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'

export type NoteShelfItem={
  id:string;category:'space'|'project'|'agent-run'|'recovered';kind:'spaces'|'projects'|'sessions'|null
  identity:string;owner_label:string;content_title?:string|null;project_scope_id?:string|null
  project_label?:string|null;project_root?:string|null;space_id?:string|null
  backend?:string|null;run_state?:string|null;storage:'app-data'|'project';path:string
  modified_at:number;excerpt:string;linked:boolean;recovered:boolean;active:boolean;openable:boolean
}

type Category='recent'|'space'|'project'|'agent-run'|'recovered'
type Props={onOpen:(item:NoteShelfItem)=>void;onClose:()=>void;hidden?:boolean}

const tabs:Array<{id:Category;label:string}>=[
  {id:'recent',label:'Recent'},
  {id:'space',label:'Spaces'},
  {id:'project',label:'Projects'},
  {id:'agent-run',label:'Agent runs'},
  {id:'recovered',label:'Recovered'},
]

const typeLabel=(item:NoteShelfItem)=>item.category==='agent-run'?'agent run':item.category

export function NotesShelf({onOpen,onClose,hidden=false}:Props){
  const [items,setItems]=useState<NoteShelfItem[]>([])
  const [category,setCategory]=useState<Category>('recent')
  const [query,setQuery]=useState('')
  const [loading,setLoading]=useState(true)
  const [error,setError]=useState('')
  const search=useRef<HTMLInputElement>(null)
  const load=async()=>{setLoading(true);try{const result=await api<{items:NoteShelfItem[]}>('GET','/api/note-shelf');setItems(result.items);setError('')}catch(cause){setError(cause instanceof Error?cause.message:String(cause))}finally{setLoading(false)}}
  useEffect(()=>{void load();const frame=requestAnimationFrame(()=>search.current?.focus());return()=>cancelAnimationFrame(frame)},[])
  useEffect(()=>{if(!hidden){const frame=requestAnimationFrame(()=>search.current?.focus());return()=>cancelAnimationFrame(frame)}},[hidden])
  useEffect(()=>{const dismiss=(event:KeyboardEvent)=>{if(event.key==='Escape')onClose()};window.addEventListener('keydown',dismiss,true);return()=>window.removeEventListener('keydown',dismiss,true)},[onClose])
  const visible=useMemo(()=>{
    const needle=query.trim().toLocaleLowerCase()
    return items.filter(item=>(category==='recent'||(category==='recovered'?item.recovered:item.category===category))&&(!needle||[item.owner_label,item.content_title,item.project_label,item.backend,item.run_state,item.excerpt].some(value=>String(value||'').toLocaleLowerCase().includes(needle))))
  },[items,category,query])
  const count=(target:Category)=>target==='recent'?items.length:items.filter(item=>target==='recovered'?item.recovered:item.category===target).length
  return <div class={`notes-shelf-layer ${hidden?'behind-note':''}`} role="dialog" aria-modal="true" aria-hidden={hidden} aria-label="Notes shelf" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="notes-shelf-panel">
      <header><div><span>NOTE INDEX</span><h2>Notes</h2></div><button aria-label="Refresh notes" title="Refresh notes" onClick={()=>void load()}>↻</button><button aria-label="Close notes" onClick={onClose}>×</button></header>
      <div class="notes-shelf-tools"><input ref={search} value={query} onInput={event=>setQuery(event.currentTarget.value)} placeholder="find by title, owner, project, or content…" aria-label="Search notes"/><span>{loading?'indexing…':`${visible.length} shown`}</span></div>
      <nav aria-label="Note categories">{tabs.map(tab=><button class={category===tab.id?'active':''} onClick={()=>setCategory(tab.id)}>{tab.label}<small>{count(tab.id)}</small></button>)}</nav>
      <main>
        {error&&<div class="notes-shelf-state error" role="alert">{error}</div>}
        {!error&&!loading&&!visible.length&&<div class="notes-shelf-state"><strong>No {category==='recent'?'saved':category} notes found.</strong><span>Notes appear here after their first save.</span></div>}
        {visible.map(item=><article class={!item.openable?'recovered':''}>
          <button disabled={!item.openable} onClick={()=>onOpen(item)}>
            <div class="note-shelf-title"><span class={`state-dot ${item.active?'working':item.recovered?'crashed':'idle'}`}/><strong>{item.content_title||item.owner_label}</strong><em>{typeLabel(item)}</em></div>
            {item.content_title&&<div class="note-shelf-owner">{item.owner_label}</div>}
            <div class="note-shelf-meta">{item.project_label&&<span>project::{item.project_label}</span>}{item.backend&&<span>{item.backend}</span>}{item.run_state&&<span>{item.run_state}</span>}<time>{new Date(item.modified_at*1000).toLocaleString()}</time></div>
            {item.excerpt&&<p>{item.excerpt}</p>}
            {!item.openable&&<small>unlinked recovery file · inspect it from Projects · {item.path}</small>}
          </button>
        </article>)}
      </main>
    </section>
  </div>
}
