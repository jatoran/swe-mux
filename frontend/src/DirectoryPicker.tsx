import { rich, t } from './localization/index.ts'
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
    <section data-tutorial="folder-picker" class="folder-picker" role="dialog" aria-modal="true" aria-label={t("DirectoryPicker.bfe3ec2aae")}>
      <header><div><span>{t("DirectoryPicker.7eb7a54197")}</span><h2 data-heading-message="DirectoryPicker.74bed7b49a">{t("DirectoryPicker.74bed7b49a")}</h2></div><button type="button" aria-label={t("DirectoryPicker.2db6d3ccf0")} onClick={onCancel}>×</button></header>
      <form class="folder-picker-path" onSubmit={event=>{event.preventDefault();void openDirectory(path)}}>
        <button type="button" onClick={showRoots}>{t("DirectoryPicker.e4c31dfdbb")}</button>
        <input aria-label={t("DirectoryPicker.98bca2fab9")} value={path} onInput={event=>setPath(event.currentTarget.value)} placeholder={t("DirectoryPicker.3eed47dd23")} autofocus />
        <button type="submit" disabled={!path.trim()||loading}>{t("DirectoryPicker.6cc8519b91")}</button>
      </form>
      {remote && <p class="folder-picker-hint">{t("DirectoryPicker.8b7e370b19")}</p>}
      {(listing||roots.length>0) && <div class="folder-picker-filter"><input value={filter} onInput={event=>setFilter(event.currentTarget.value)} placeholder={t("DirectoryPicker.1c5e6fc226")} aria-label={t("DirectoryPicker.e7059f77ef")} spellcheck={false} autoCapitalize="off" autoCorrect="off"/>{filter && <button type="button" aria-label={t("DirectoryPicker.ba59e2d4a5")} onClick={()=>setFilter('')}>×</button>}</div>}
      {error && <p class="folder-picker-error" role="alert">{error}</p>}
      <div class="folder-picker-list" aria-busy={loading}>
        {loading ? <p>{t("DirectoryPicker.d0aa0da7a5")}</p> : listing ? <>
          {listing.parent&&!needle && <button type="button" onClick={()=>void openDirectory(listing.parent!)}><span>↑</span><strong>{t("DirectoryPicker.158f5a01ef")}</strong><small>{listing.parent}</small></button>}
          {shownDirectories.map(directory=><button type="button" onClick={()=>void openDirectory(directory.path)}><span>▸</span><strong>{directory.name}</strong><small>{directory.path}</small></button>)}
          {!shownDirectories.length && (needle ? <p>{rich("DirectoryPicker.87df590306", { value1: filter.trim() })}</p> : !listing.parent ? <p>{t("DirectoryPicker.3e429c0a77")}</p> : null)}
        </> : roots.length ? (shownRoots.length ? shownRoots.map(root=><button type="button" onClick={()=>void openDirectory(root)}><span>▸</span><strong>{root}</strong><small>{t("DirectoryPicker.8cc852403b")}</small></button>) : <p>{rich("DirectoryPicker.fe40571615", { value1: filter.trim() })}</p>) : <p>{t("DirectoryPicker.0d3bcd76bc")}</p>}
      </div>
      <footer><span>{listing ? listing.path : t("DirectoryPicker.b4bb65903d")}</span><button type="button" onClick={onCancel}>{t("DirectoryPicker.19766ed6cc")}</button><button type="button" class="primary" disabled={!listing||loading} onClick={()=>listing&&onSelect(listing.path)}>{t("DirectoryPicker.92eff9d0bf")}</button></footer>
    </section>
  </div>
}
