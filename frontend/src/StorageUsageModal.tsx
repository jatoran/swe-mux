import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { useModalFocus } from './modalFocus'
import { formatBytes } from './networkUsage'

type StorageBucket={name:string;bytes:number;files:number}
type StorageProject={project_id:string;label:string;root:string;present:boolean;bytes:number;files:number}
export type StorageUsageSnapshot={
  generated_at:number;duration_ms:number;cached:boolean;age_seconds:number;data_dir:string
  global:{present:boolean;error?:string;total_bytes:number;total_files:number;buckets:StorageBucket[]}
  projects:{total_bytes:number;items:StorageProject[]}
}

const integer=new Intl.NumberFormat()

/** Human labels for the data-dir buckets the daemon reports by short key. */
const BUCKET_LABELS:Record<string,string>={
  database:'Database (mux.db)',
  webview:'WebView cache',
  logs:'Logs',
  worktrees:'Worktrees',
  voice:'Voice clips',
  media:'Session media',
  sessions:'Adapter session state',
  trash:'Trash',
  other:'Other state',
}
const bucketLabel=(name:string)=>BUCKET_LABELS[name]||name

export function StorageUsageModal({onClose}:{onClose:()=>void}) {
  const [snapshot,setSnapshot]=useState<StorageUsageSnapshot|null>(null)
  const [refreshing,setRefreshing]=useState(true)
  const [error,setError]=useState('')
  const sequence=useRef(0)
  const panel=useRef<HTMLElement>(null)
  useModalFocus(panel,onClose)

  // `force` re-walks the tree on the daemon (`?refresh=1`); the passive first
  // load accepts the TTL cache so opening the panel is never blocked on a walk.
  const load=async(force=false)=>{
    const request=++sequence.current
    setRefreshing(true)
    try{
      const next=await api<StorageUsageSnapshot>('GET',`/api/diagnostics/storage${force?'?refresh=1':''}`,undefined,{timeoutMs:30_000})
      if(request!==sequence.current)return
      setSnapshot(next)
      setError('')
    }catch(cause){
      if(request===sequence.current)setError(cause instanceof Error?cause.message:String(cause))
    }finally{
      if(request===sequence.current)setRefreshing(false)
    }
  }

  useEffect(()=>{
    void load()
    return()=>{sequence.current+=1}
  },[])

  const footprint=snapshot?snapshot.global.total_bytes+snapshot.projects.total_bytes:0
  const projects=snapshot?[...snapshot.projects.items].filter(item=>item.present||item.bytes>0):[]

  return <div class="usage-layer storage-usage-layer" role="dialog" aria-modal="true" aria-label="Storage usage" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="usage-panel storage-usage-panel" ref={panel}>
      <header><div><span>STORAGE::USAGE</span><strong>Disk space swe-mux uses, by area and by project</strong></div><div class="usage-header-actions"><button disabled={refreshing} onClick={()=>void load(true)}>{refreshing?'measuring…':'refresh'}</button><button aria-label="Close storage usage" onClick={onClose}>×</button></div></header>
      <div class="network-usage-actions">
        <div><strong>{snapshot?`swe-mux footprint ${formatBytes(footprint)}`:'Measuring on-disk footprint…'}</strong><span>{snapshot?`${snapshot.data_dir}${snapshot.cached?` · cached ${Math.round(snapshot.age_seconds)}s ago`:` · measured in ${Math.round(snapshot.duration_ms)}ms`}`:'Walking the data directory and project files'}</span></div>
      </div>
      {error&&<div class="usage-error" role="alert">{error}</div>}
      <main>
        {!snapshot?<div class="usage-empty"><strong>{error?'Storage usage is unavailable.':'Loading storage usage…'}</strong><p>{error||'Reading the data directory and each project’s .swe-mux folder.'}</p></div>:<>
          <section class="network-usage-summary" aria-label="Footprint totals">
            <article><span>total footprint</span><strong>{formatBytes(footprint)}</strong><small>data dir + all projects</small></article>
            <article><span>data directory</span><strong>{formatBytes(snapshot.global.total_bytes)}</strong><small>{integer.format(snapshot.global.total_files)} files</small></article>
            <article><span>project files</span><strong>{formatBytes(snapshot.projects.total_bytes)}</strong><small>{integer.format(projects.length)} project(s) with .swe-mux</small></article>
          </section>

          <section class="network-usage-table">
            <h3>Data directory by area</h3>
            {snapshot.global.present?(snapshot.global.buckets.length?<div class="usage-table-scroll"><table><thead><tr><th>area</th><th>size</th><th>files</th></tr></thead><tbody>{snapshot.global.buckets.map(bucket=><tr key={bucket.name}><td>{bucketLabel(bucket.name)}</td><td>{formatBytes(bucket.bytes)}</td><td>{integer.format(bucket.files)}</td></tr>)}</tbody></table></div>:<p>The data directory is empty.</p>):<p class="usage-error">Could not read the data directory{snapshot.global.error?`: ${snapshot.global.error}`:'.'}</p>}
          </section>

          <section class="network-usage-table">
            <h3>Projects (.swe-mux)</h3>
            {projects.length?<div class="usage-table-scroll"><table><thead><tr><th>project</th><th>size</th><th>files</th></tr></thead><tbody>{projects.map(project=><tr key={project.project_id}><td title={project.root}>{project.label}</td><td>{formatBytes(project.bytes)}</td><td>{integer.format(project.files)}</td></tr>)}</tbody></table></div>:<p>No project has written a .swe-mux folder yet.</p>}
          </section>
        </>}
      </main>
      <footer><span>Measures the bytes swe-mux stores: the data directory ({snapshot?snapshot.data_dir:'~/.mux'}) grouped by area, plus each project’s .swe-mux folder. The host drive’s free space is deliberately excluded, so this reflects swe-mux’s footprint rather than this machine’s disk. Read-only; nothing is deleted or pruned.</span></footer>
    </section>
  </div>
}
