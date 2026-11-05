import { numberFormatter } from './localization/index.ts'
import { rich, t } from './localization/index.ts'
import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { Dropdown } from './Dropdown'
import { byProjectName, projectDropdownOptions } from './projectOptions'
import { formatBytes } from './networkUsage'

type StorageBucket={name:string;bytes:number;files:number}
type StorageProject={project_id:string;label:string;root:string;present:boolean;bytes:number;files:number;buckets:StorageBucket[]}
export type StorageUsageSnapshot={
  generated_at:number;duration_ms:number;cached:boolean;age_seconds:number;data_dir:string
  global:{present:boolean;error?:string;total_bytes:number;total_files:number;buckets:StorageBucket[]}
  projects:{total_bytes:number;items:StorageProject[]}
}

const integer=numberFormatter()

/** Human labels for the data-dir buckets the daemon reports by short key. */
const BUCKET_LABELS:Record<string,string>={
  get database() { return t("StorageUsageModal.6c746c643b") },
  get webview() { return t("StorageUsageModal.d7042b3eff") },
  get logs() { return t("StorageUsageModal.ea2100dc89") },
  get worktrees() { return t("StorageUsageModal.aec2f93d67") },
  get voice() { return t("StorageUsageModal.6d5bf4219d") },
  get media() { return t("StorageUsageModal.e548fc66cb") },
  get sessions() { return t("StorageUsageModal.0d5cf10f83") },
  get recovery() { return t("StorageUsageModal.0f3d1cc070") },
  get trash() { return t("StorageUsageModal.c560122ac4") },
  get other() { return t("StorageUsageModal.724983c2cf") },
}
const bucketLabel=(name:string)=>BUCKET_LABELS[name]||name

// The Storage segment of the Resources modal. See `NetworkUsageView` for why the dialog
// shell moved out of here.
export function StorageUsageView() {
  const [snapshot,setSnapshot]=useState<StorageUsageSnapshot|null>(null)
  const [refreshing,setRefreshing]=useState(true)
  const [error,setError]=useState('')
  // '' means "all projects" (the overview table); any other value pins the view
  // to that single project's .swe-mux breakdown.
  const [projectFilter,setProjectFilter]=useState('')
  const sequence=useRef(0)

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
  // The filter only offers projects that actually exist in the latest snapshot,
  // so a stale selection (a project that has since vanished) falls back to "all".
  const selected=projectFilter?projects.find(item=>item.project_id===projectFilter)||null:null
  const scoped=selected!==null

  return <>
      <div class="network-usage-actions">
        <div><strong>{snapshot ? t("StorageUsageModal.47b564c1e1", { value1: formatBytes(footprint) }) : t("StorageUsageModal.428edc1cf3")}</strong><span>{snapshot ? t("StorageUsageModal.ef289bde81", { data_dir: snapshot.data_dir, value2: snapshot.cached?` · cached ${Math.round(snapshot.age_seconds)}s ago`:t("StorageUsageModal.a4c5f188d4", { value1: Math.round(snapshot.duration_ms) }) }) : t("StorageUsageModal.139e22ccb4")}</span></div>
        <div class="resource-view-actions">
          <label class="storage-usage-filter">{rich("StorageUsageModal.727795fb5c", { value1: <Dropdown value={projectFilter} disabled={!snapshot} onChange={setProjectFilter} filter filterPlaceholder={t("StorageUsageModal.2e0060c45e")} options={[{value:'',get label() { return t("StorageUsageModal.4b87271b6b") }},...projectDropdownOptions(projects,project=>({value:project.project_id,label:project.label,detail:project.root}))]}/> })}</label>
          <button disabled={refreshing} onClick={()=>void load(true)}>{refreshing ? t("StorageUsageModal.821d52cb24") : t("StorageUsageModal.d6cc0a088c")}</button>
        </div>
      </div>
      {error && <div class="usage-error" role="alert">{error}</div>}
      <main>
        {!snapshot ? <div class="usage-empty"><strong>{error ? t("StorageUsageModal.c06ffacd11") : t("StorageUsageModal.55a06bcd40")}</strong><p>{error || t("StorageUsageModal.1f591e7a61")}</p></div> : scoped&&selected ? <>
          <section class="network-usage-summary" aria-label={t("StorageUsageModal.04c8266662")}>
            <article><span>{t("StorageUsageModal.e66ab87aee")}</span><strong>{formatBytes(selected.bytes)}</strong><small>{rich("StorageUsageModal.402ede62c5", { value1: integer.format(selected.files) })}</small></article>
            <article><span>{t("StorageUsageModal.287d0c57fd")}</span><strong>{snapshot.projects.total_bytes>0 ? t("StorageUsageModal.6a53a571fa", { value1: (selected.bytes/snapshot.projects.total_bytes*100).toFixed(1) }) : '—'}</strong><small>{t("StorageUsageModal.db41d88299")}</small></article>
            <article><span>{t("StorageUsageModal.9f88d2d7bf")}</span><strong>{integer.format(selected.buckets.length)}</strong><small>{t("StorageUsageModal.b1ced0b033")}</small></article>
          </section>

          <section class="network-usage-table">
            <h3>{rich("StorageUsageModal.d41bf8d908", { label: selected.label })}</h3>
            {selected.buckets.length ? <div class="usage-table-scroll"><table><thead><tr><th>{t("StorageUsageModal.4a91ee5f01")}</th><th>{t("StorageUsageModal.ccdcbe846f")}</th><th>{t("StorageUsageModal.3d7db37d08")}</th></tr></thead><tbody>{selected.buckets.map(bucket=><tr key={bucket.name}><td>{bucket.name}</td><td>{formatBytes(bucket.bytes)}</td><td>{integer.format(bucket.files)}</td></tr>)}</tbody></table></div> : <p>{t("StorageUsageModal.a96a93ab52")}</p>}
            <p class="storage-usage-path">{rich("StorageUsageModal.bdd96517a0", { root: selected.root })}</p>
          </section>
        </> : <>
          <section class="network-usage-summary" aria-label={t("StorageUsageModal.cba4311c0b")}>
            <article><span>{t("StorageUsageModal.50fd632c85")}</span><strong>{formatBytes(footprint)}</strong><small>{t("StorageUsageModal.a05165997f")}</small></article>
            <article><span>{t("StorageUsageModal.e071d7e548")}</span><strong>{formatBytes(snapshot.global.total_bytes)}</strong><small>{rich("StorageUsageModal.402ede62c5", { value1: integer.format(snapshot.global.total_files) })}</small></article>
            <article><span>{t("StorageUsageModal.e66ab87aee")}</span><strong>{formatBytes(snapshot.projects.total_bytes)}</strong><small>{rich("StorageUsageModal.dd4046657e", { value1: integer.format(projects.length) })}</small></article>
          </section>

          <section class="network-usage-table">
            <h3 data-heading-message="StorageUsageModal.4e677148d9">{t("StorageUsageModal.4e677148d9")}</h3>
            {snapshot.global.present ? (snapshot.global.buckets.length ? <div class="usage-table-scroll"><table><thead><tr><th>{t("StorageUsageModal.4a91ee5f01")}</th><th>{t("StorageUsageModal.ccdcbe846f")}</th><th>{t("StorageUsageModal.3d7db37d08")}</th></tr></thead><tbody>{snapshot.global.buckets.map(bucket=><tr key={bucket.name}><td>{bucketLabel(bucket.name)}</td><td>{formatBytes(bucket.bytes)}</td><td>{integer.format(bucket.files)}</td></tr>)}</tbody></table></div> : <p>{t("StorageUsageModal.70164bc8d3")}</p>) : <p class="usage-error">{rich("StorageUsageModal.1e268cf9b4", { value1: snapshot.global.error ? t("StorageUsageModal.8c11f157bd", { error: snapshot.global.error }) : '.' })}</p>}
          </section>

          <section class="network-usage-table">
            <h3 data-heading-message="StorageUsageModal.40bfd14c3b">{t("StorageUsageModal.40bfd14c3b")}</h3>
            {projects.length ? <div class="usage-table-scroll"><table><thead><tr><th>{t("StorageUsageModal.244210e484")}</th><th>{t("StorageUsageModal.ccdcbe846f")}</th><th>{t("StorageUsageModal.3d7db37d08")}</th></tr></thead><tbody>{byProjectName(projects,project=>project.label).map(project=><tr key={project.project_id}><td title={project.root}><button class="storage-usage-drill" onClick={()=>setProjectFilter(project.project_id)}>{project.label}</button></td><td>{formatBytes(project.bytes)}</td><td>{integer.format(project.files)}</td></tr>)}</tbody></table></div> : <p>{t("StorageUsageModal.8b110332fb")}</p>}
          </section>
        </>}
      </main>
      <footer><span>{rich("StorageUsageModal.395e955b6a", { value1: snapshot ? snapshot.data_dir : t("StorageUsageModal.a23247364e") })}</span></footer>
  </>
}
