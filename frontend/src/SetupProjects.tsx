import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { emitTutorialAction } from './tutorial'
import { DirectoryPicker } from './DirectoryPicker'
import { useDismissLevel } from './modalFocus'

type Candidate={root:string;name:string;last_activity:number;sessions:number;harnesses:string[];available:boolean}
export function SetupProjects({harnesses,onContinue,autoDiscover=false}:{harnesses:string[];onContinue:()=>Promise<void>;autoDiscover?:boolean}) {
  const [items,setItems]=useState<Candidate[]>([])
  const [selected,setSelected]=useState<Set<string>>(new Set())
  const [registered,setRegistered]=useState<Set<string>>(new Set())
  const [root,setRoot]=useState('')
  const [name,setName]=useState('')
  const [busy,setBusy]=useState(false)
  const [scanning,setScanning]=useState(false)
  const [error,setError]=useState('')
  const [limited,setLimited]=useState(false)
  const [browsing,setBrowsing]=useState(false)
  useDismissLevel(()=>setBrowsing(false),browsing,'setup-folder-picker')
  useEffect(()=>{void api<{root:string}[]>('GET','/api/projects').then(projects=>setRegistered(new Set(projects.map(project=>project.root.toLocaleLowerCase())))).catch(cause=>setError(cause.message))},[])
  const discover=async()=>{
    setScanning(true);setError('')
    try{const result=await api<{items:Candidate[];limited:boolean}>('GET',`/api/onboarding/projects?harnesses=${encodeURIComponent(harnesses.join(','))}`,undefined,{timeoutMs:50000});setItems(result.items);setLimited(result.limited)}
    catch(cause){setError((cause as Error).message)}finally{setScanning(false)}
  }
  useEffect(()=>{if(autoDiscover&&harnesses.length)void discover()},[autoDiscover,harnesses.join(',')])
  const add=async(paths:{root:string;name:string}[])=>{
    setBusy(true);setError('')
    try{
      for(const project of paths){
        if(registered.has(project.root.toLocaleLowerCase()))continue
        const added=await api<{root:string}>('POST','/api/projects',project)
        setRegistered(current=>new Set([...current,added.root.toLocaleLowerCase()]))
        setSelected(current=>new Set([...current].filter(path=>path!==project.root)))
        emitTutorialAction({action:'project-created'})
      }
      setRoot('');setName('')
    }catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
  }
  return <section><h2>Add your first Project</h2><p>A Project is a folder for sessions, files, notes, and history. Add existing folders here; each inherits your global defaults and keeps any explicit project settings.</p>
    <div class="setup-project-manual"><label>Folder path<input value={root} placeholder="Full path to an existing folder" onInput={event=>setRoot(event.currentTarget.value)}/></label><label>Project name (optional)<input value={name} onInput={event=>setName(event.currentTarget.value)}/></label><button disabled={busy||!root.trim()} onClick={()=>void add([{root:root.trim(),name:name.trim()}])}>Add folder</button><button disabled={busy} onClick={()=>setBrowsing(true)}>Browse folders…</button></div>
    {browsing&&<DirectoryPicker initialPath={root} onCancel={()=>setBrowsing(false)} onSelect={path=>{setRoot(path);setBrowsing(false)}}/>}
    <h3>From your harness history</h3><p>Discover folders from the enabled harnesses. Nothing is added until you select it.</p><button disabled={scanning||!harnesses.length} onClick={()=>void discover()}>{scanning?'Reading recent history…':'Find recent project folders'}</button>
    {items.map(item=>{const exists=registered.has(item.root.toLocaleLowerCase());return <label class="harness-setup-row check" key={item.root}><span><strong>{item.name}{exists?' - already added':''}</strong><small>{item.root}</small><small>{item.harnesses.join(', ')} · {item.sessions} sessions · {new Date(item.last_activity*1000).toLocaleString()}{item.available?'':' · folder unavailable'}</small></span><input type="checkbox" disabled={busy||exists||!item.available} checked={selected.has(item.root)} onChange={event=>{const checked=event.currentTarget.checked;setSelected(current=>{const next=new Set(current);if(checked)next.add(item.root);else next.delete(item.root);return next})}}/></label>})}
    {limited&&<p>Showing a bounded selection of recent history. Add any other folder manually.</p>}
    {!!items.length&&<button disabled={busy||!selected.size} onClick={()=>void add(items.filter(item=>selected.has(item.root)))}>Add selected folders ({selected.size})</button>}
    {!!registered.size&&<p role="status">{registered.size} Project{registered.size===1?'':'s'} ready. Launch your first session from Run after setup.</p>}
    {error&&<p role="alert">{error}</p>}
    <footer><button class="primary" disabled={busy} onClick={()=>void onContinue().catch(cause=>setError(cause.message))}>{registered.size?'Continue':'Add a Project later'}</button></footer>
  </section>
}
