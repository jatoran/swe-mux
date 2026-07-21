import { useEffect, useState } from 'preact/hooks'
import { api } from './api'

type FilesystemRoots = { roots:string[];remote:boolean }
type DirectoryListing = {
  path:string
  parent:string|null
  directories:Array<{name:string;path:string}>
}

export function DirectoryPicker({initialPath,onCancel,onSelect}:{
  initialPath:string
  onCancel:()=>void
  onSelect:(path:string)=>void
}) {
  const [roots,setRoots]=useState<string[]>([])
  const [remote,setRemote]=useState(false)
  const [listing,setListing]=useState<DirectoryListing|null>(null)
  const [path,setPath]=useState(initialPath)
  const [loading,setLoading]=useState(true)
  const [error,setError]=useState('')
  const [filter,setFilter]=useState('')

  const openDirectory=async(target:string)=>{
    if(!target.trim())return
    setLoading(true);setError('');setFilter('')
    try{
      const next=await api<DirectoryListing>('GET',`/api/fs/list?path=${encodeURIComponent(target)}`)
      setListing(next);setPath(next.path)
    }catch(cause){
      setError(cause instanceof Error?cause.message:String(cause))
    }finally{setLoading(false)}
  }

  useEffect(()=>{
    let active=true
    void api<FilesystemRoots>('GET','/api/fs/roots').then(result=>{
      if(!active)return
      setRoots(result.roots);setRemote(result.remote)
      if(initialPath.trim())return openDirectory(initialPath)
      setLoading(false)
    }).catch(cause=>{
      if(active){setError(cause instanceof Error?cause.message:String(cause));setLoading(false)}
    })
    return()=>{active=false}
  },[])

  const showRoots=()=>{setListing(null);setPath('');setError('');setFilter('');setLoading(false)}

  const needle=filter.trim().toLowerCase()
  const shownDirectories=(listing?.directories||[]).filter(directory=>!needle||directory.name.toLowerCase().includes(needle))
  const shownRoots=roots.filter(root=>!needle||root.toLowerCase().includes(needle))

  return <div class="folder-picker-layer" onMouseDown={event=>event.target===event.currentTarget&&onCancel()}>
    <section data-tutorial="folder-picker" class="folder-picker" role="dialog" aria-modal="true" aria-label="Select project folder">
      <header><div><span>PROJECT::FOLDER</span><h2>Select a folder</h2></div><button type="button" aria-label="Close folder picker" onClick={onCancel}>×</button></header>
      <form class="folder-picker-path" onSubmit={event=>{event.preventDefault();void openDirectory(path)}}>
        <button type="button" onClick={showRoots}>Roots</button>
        <input aria-label="Folder path" value={path} onInput={event=>setPath(event.currentTarget.value)} placeholder="Enter an absolute folder path" autofocus />
        <button type="submit" disabled={!path.trim()||loading}>Go</button>
      </form>
      {remote&&<p class="folder-picker-hint">Browsing folders on the machine running swe-mux.</p>}
      {(listing||roots.length>0)&&<div class="folder-picker-filter"><input value={filter} onInput={event=>setFilter(event.currentTarget.value)} placeholder="Filter folders by name…" aria-label="Filter folders by name" spellcheck={false} autoCapitalize="off" autoCorrect="off"/>{filter&&<button type="button" aria-label="Clear filter" onClick={()=>setFilter('')}>×</button>}</div>}
      {error&&<p class="folder-picker-error" role="alert">{error}</p>}
      <div class="folder-picker-list" aria-busy={loading}>
        {loading?<p>Loading folders…</p>:listing?<>
          {listing.parent&&!needle&&<button type="button" onClick={()=>void openDirectory(listing.parent!)}><span>↑</span><strong>Parent folder</strong><small>{listing.parent}</small></button>}
          {shownDirectories.map(directory=><button type="button" onClick={()=>void openDirectory(directory.path)}><span>▸</span><strong>{directory.name}</strong><small>{directory.path}</small></button>)}
          {!shownDirectories.length&&(needle?<p>No folders match “{filter.trim()}”.</p>:!listing.parent?<p>No child folders.</p>:null)}
        </>:roots.length?(shownRoots.length?shownRoots.map(root=><button type="button" onClick={()=>void openDirectory(root)}><span>▸</span><strong>{root}</strong><small>Filesystem root</small></button>):<p>No roots match “{filter.trim()}”.</p>):<p>No filesystem roots are available.</p>}
      </div>
      <footer><span>{listing?listing.path:'Choose a filesystem root'}</span><button type="button" onClick={onCancel}>Cancel</button><button type="button" class="primary" disabled={!listing||loading} onClick={()=>listing&&onSelect(listing.path)}>Select this folder</button></footer>
    </section>
  </div>
}
