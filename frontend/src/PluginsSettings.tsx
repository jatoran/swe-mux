import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import {
  type InstalledPlugin, type PluginCatalogue, type PluginDevelopmentScan,
} from './pluginCatalog.ts'

type MarketRepo={
  full_name:string;description:string;stars:number;language?:string;license?:string;url:string
  unreviewed:boolean;official?:boolean;plugin_name?:string;plugin_version?:string
  permissions?:string[];platforms?:string[];runtime_requirements?:string[];install_ref?:string
}

const EMPTY:PluginCatalogue={execution_enabled:true,host_capabilities:[],development_root:'',plugins:[]}
const EMPTY_DEVELOPMENT:PluginDevelopmentScan={root:'',exists:false,candidates:[],truncated:false}

export function PluginsSettings(){
  const [catalogue,setCatalogue]=useState<PluginCatalogue>(EMPTY)
  const [development,setDevelopment]=useState<PluginDevelopmentScan>(EMPTY_DEVELOPMENT)
  const [developmentRoot,setDevelopmentRoot]=useState('')
  const [expandedId,setExpandedId]=useState<string|null>(null)
  const [source,setSource]=useState('')
  const [ref,setRef]=useState('')
  const [mode,setMode]=useState<'link'|'install'>('link')
  const [busy,setBusy]=useState('')
  const [message,setMessage]=useState('')
  const [logs,setLogs]=useState<Record<string,Array<Record<string,unknown>>>>({})
  const [market,setMarket]=useState<MarketRepo[]|null>(null)
  const [confirming,setConfirming]=useState<{id:string;purge:boolean}|null>(null)
  const [confirmUpdateId,setConfirmUpdateId]=useState<string|null>(null)
  const [confirmRestartId,setConfirmRestartId]=useState<string|null>(null)

  const load=async()=>{
    const [plugins,developmentScan]=await Promise.all([
      api<Partial<PluginCatalogue>>('GET','/api/plugins'),
      api<PluginDevelopmentScan>('GET','/api/plugins/development'),
    ])
    // Field-by-field rather than adopting the payload wholesale: every field is
    // mapped or joined unconditionally below, so a payload missing one (an older
    // daemon, a harness answering `{}`) crashed the whole tab instead of drawing
    // its empty state.
    setCatalogue({
      execution_enabled:plugins?.execution_enabled!==false,
      host_capabilities:plugins?.host_capabilities||[],
      development_root:plugins?.development_root||developmentScan.root||'',
      plugins:plugins?.plugins||[],
    })
    const normalizedDevelopment={
      root:developmentScan?.root||plugins?.development_root||'',
      exists:developmentScan?.exists===true,
      candidates:Array.isArray(developmentScan?.candidates)?developmentScan.candidates:[],
      truncated:developmentScan?.truncated===true,
      diagnostic:developmentScan?.diagnostic||'',
    }
    setDevelopment(normalizedDevelopment)
    setDevelopmentRoot(current=>current||normalizedDevelopment.root)
  }
  useEffect(()=>{
    const refreshOnFocus=()=>void load().catch(error=>setMessage(String(error)))
    refreshOnFocus()
    window.addEventListener('focus',refreshOnFocus)
    return()=>window.removeEventListener('focus',refreshOnFocus)
  },[])

  const run=async(label:string,operation:()=>Promise<unknown>)=>{
    setBusy(label);setMessage('')
    try{await operation();await load();setMessage(`${label} complete.`)}
    catch(error){setMessage(error instanceof Error?error.message:String(error))}
    finally{setBusy('')}
  }
  const mutate=(method:string,path:string,body?:unknown)=>api(method,path,body,{timeoutMs:240_000})
  const install=()=>run(mode==='link'?'Link':'Install',()=>mutate(
    'POST',`/api/plugins/${mode}`,mode==='link'?{path:source}:{source,ref:ref.trim()},
  ))
  const uninstall=(plugin:InstalledPlugin,purge:boolean)=>run(`${purge?'Purge':'Uninstall'} ${plugin.name}`,async()=>{
    await mutate('DELETE',`/api/plugins/${plugin.id}${purge?'?purge=1':''}`)
    setConfirming(null);if(expandedId===plugin.id)setExpandedId(null)
  })
  const refresh=()=>run('Refresh plugins',()=>mutate('POST','/api/plugins/refresh',{}))
  const checkUpdates=()=>run('Check for updates',()=>mutate('POST','/api/plugins/updates/check',{}))
  const saveDevelopmentRoot=(create:boolean)=>run(create?'Create development folder':'Save development folder',async()=>{
    const scan=await api<PluginDevelopmentScan>('PUT','/api/plugins/development',{path:developmentRoot,create})
    setDevelopment(scan)
  })
  const restartPanes=(plugin:InstalledPlugin)=>run(`Restart ${plugin.name} panes`,async()=>{
    const result=await api<{restarted:Array<{old_session_id:string;session:Record<string,unknown>&{id:string};placement:string}>}>('POST',`/api/plugins/${plugin.id}/panes/restart`,undefined,{timeoutMs:240_000})
    for(const item of result.restarted)window.dispatchEvent(new CustomEvent('mux:plugin-pane-restarted',{detail:item}))
  })
  const copyDevelopmentCommands=(plugin:InstalledPlugin)=>{
    const root=JSON.stringify(plugin.source_ref)
    const text=[`swemux plugin validate ${root}`,`swemux plugin refresh`,`swemux plugin approve ${plugin.id}`,`swemux plugin restart-panes ${plugin.id}`].join('\n')
    void navigator.clipboard.writeText(text).then(()=>setMessage('Development commands copied.')).catch(()=>setMessage('Clipboard access was blocked.'))
  }

  return <div class="plugins-settings">
    <section class="plugin-host-row"><div><h3>Plugins</h3><p>Installed globally. Context is chosen where a tool launches. Plugins run as your user; permissions limit swe-mux callbacks, not files, processes, credentials, or network access.</p></div><div class="plugin-host-actions"><a href="https://swemux.dev/docs/plugins/" target="_blank" rel="noreferrer">Author docs</a><button disabled={!!busy} onClick={()=>void refresh()}>Refresh</button><button disabled={!!busy} onClick={()=>void checkUpdates()}>Check for updates</button><label class="check"><span>Execution</span><input type="checkbox" checked={catalogue.execution_enabled} onChange={event=>void run('Execution policy',()=>mutate('POST','/api/plugins/execution',{enabled:event.currentTarget.checked}))}/></label></div></section>

    <section class="plugin-list-section"><header class="plugin-list-heading"><div><h3>Installed</h3><span>{catalogue.plugins.length} plugins</span></div><span>global lifecycle</span></header>
      {!catalogue.plugins.length&&<p>No plugins installed.</p>}
      <div class="plugin-card-list">{catalogue.plugins.map(plugin=>{
        const expanded=expandedId===plugin.id
        const confirmingThis=confirming?.id===plugin.id&&!confirming.purge
        const running=plugin.running_panes?.length||0
        const runningProjects=new Set((plugin.running_panes||[]).map(item=>item.project_id)).size
        const update=plugin.update_check
        const staged=plugin.staged_update
        return <article class={`plugin-card ${plugin.enabled?'enabled':''} ${expanded?'expanded':''}`} key={plugin.id}>
          <header>
            <button class="plugin-card-summary" aria-expanded={expanded} onClick={()=>setExpandedId(expanded?null:plugin.id)}>
              <span class={`plugin-state-dot ${plugin.enabled?'enabled':'disabled'}`} aria-hidden="true"/>
              <span><strong>{plugin.name}</strong><small>{plugin.manifest?.description||plugin.id}</small></span>
              <em class="plugin-card-badges"><span>{plugin.version}</span><span>{plugin.source_kind}</span>{running>0&&<span>{running} live</span>}<span class={staged||update?.status==='available'?'attention':''}>{staged?'review update':update?.status==='available'?'update available':plugin.enabled?'on':plugin.lifecycle}</span></em>
            </button>
            <div class="plugin-card-actions">
              {!plugin.approval_current
                ?<button class="primary" disabled={!!busy} onClick={()=>void run(`Approve ${plugin.name}`,()=>mutate('POST',`/api/plugins/${plugin.id}/approve`,{enable:true}))}>Approve</button>
                :<button disabled={!!busy} onClick={()=>void run(`${plugin.enabled?'Disable':'Enable'} ${plugin.name}`,()=>mutate('POST',`/api/plugins/${plugin.id}/enable`,{enabled:!plugin.enabled}))}>{plugin.enabled?'Disable':'Enable'}</button>}
              {confirmingThis?<><button class="danger" disabled={!!busy} onClick={()=>void uninstall(plugin,false)}>Confirm uninstall</button><button disabled={!!busy} onClick={()=>setConfirming(null)}>Cancel</button></>:<button disabled={!!busy} onClick={()=>setConfirming({id:plugin.id,purge:false})}>Uninstall</button>}
              <button class="plugin-expand" aria-label={`${expanded?'Collapse':'Expand'} ${plugin.name}`} aria-expanded={expanded} onClick={()=>setExpandedId(expanded?null:plugin.id)}>{expanded?'▴':'▾'}</button>
            </div>
          </header>
          {expanded&&<div class="plugin-card-details">
            {plugin.diagnostic&&<p class="settings-inline-error">{plugin.diagnostic}</p>}
            <dl><dt>ID</dt><dd><code>{plugin.id}</code></dd><dt>Source</dt><dd>{plugin.source_kind}: {plugin.source_ref}{plugin.requested_ref&&<> · {plugin.requested_ref}{plugin.selected_ref&&plugin.selected_ref!==plugin.requested_ref?` → ${plugin.selected_ref}`:''}</>}</dd><dt>Revision</dt><dd>{plugin.resolved_ref?<code>{plugin.resolved_ref}</code>:'local source'}</dd><dt>Permissions</dt><dd>{plugin.manifest?.permissions?.join(', ')||'none'}</dd><dt>Live panes</dt><dd>{running||'none'}{running?` across ${runningProjects} Project${runningProjects===1?'':'s'}`:''}</dd><dt>Config</dt><dd><code>{plugin.config_dir}</code></dd><dt>State</dt><dd><code>{plugin.state_dir}</code></dd></dl>
            <p class="plugin-launch-note">Launch Project tools from that Project's Run menu and session tools from the relevant session. Settings controls global lifecycle only.</p>
            {update&&<div class={`plugin-update-status ${update.status}`}><strong>{update.status==='available'?'Update available':update.status==='pinned'?'Pinned release':update.status==='unavailable'?'Update check failed':update.status==='staged'?'Update ready for review':'Up to date'}</strong><span>{update.available_version&&`v${update.available_version} · `}{update.channel||''}{update.available_ref&&<> · <code>{update.available_ref}</code></>}{update.diagnostic&&` · ${update.diagnostic}`}</span></div>}
            {staged&&<section class="plugin-update-review"><header><div><strong>Review update</strong><span>{staged.current_version} → {staged.version}</span></div><em>{staged.authority_changed?'authority changed':'same declared authority'}</em></header>{staged.diagnostic&&<p class="settings-inline-error">{staged.diagnostic}</p>}<dl><dt>Permissions added</dt><dd>{staged.permissions_added.join(', ')||'none'}</dd><dt>Permissions removed</dt><dd>{staged.permissions_removed.join(', ')||'none'}</dd><dt>Capabilities added</dt><dd>{staged.capabilities_added.join(', ')||'none'}</dd><dt>Capabilities removed</dt><dd>{staged.capabilities_removed.join(', ')||'none'}</dd><dt>Revision</dt><dd><code>{staged.resolved_ref||staged.selected_ref||'local content'}</code></dd></dl><div class="theme-actions">{confirmUpdateId===plugin.id?<><button class="primary" disabled={!!busy||!!staged.diagnostic} onClick={()=>void run(`Approve update for ${plugin.name}`,async()=>{await mutate('POST',`/api/plugins/${plugin.id}/update/approve`,{});setConfirmUpdateId(null)})}>Confirm approval and activate</button><button disabled={!!busy} onClick={()=>setConfirmUpdateId(null)}>Cancel</button></>:<button class="primary" disabled={!!busy||!!staged.diagnostic} onClick={()=>setConfirmUpdateId(plugin.id)}>Approve update</button>}<button disabled={!!busy} onClick={()=>void run(`Discard update for ${plugin.name}`,()=>mutate('DELETE',`/api/plugins/${plugin.id}/update`))}>Discard</button></div></section>}
            <details onToggle={event=>{if(event.currentTarget.open&&!logs[plugin.id])void api<Array<Record<string,unknown>>>('GET',`/api/plugins/logs?plugin_id=${plugin.id}`).then(items=>setLogs(current=>({...current,[plugin.id]:items})))}}><summary>Recent command log</summary><pre>{JSON.stringify(logs[plugin.id]||[],null,2)}</pre></details>
            <div class="plugin-advanced-actions">
              {plugin.source_kind==='managed'&&!staged&&<button disabled={!!busy||update?.status!=='available'} title={update?.status==='available'?'Download and validate new content without replacing the active version.':'Check for updates first.'} onClick={()=>void run(`Download ${plugin.name} update`,()=>mutate('POST',`/api/plugins/${plugin.id}/update`,{}))}>Download for review</button>}
              {plugin.source_kind==='link'&&<button disabled={!!busy} onClick={()=>void run(`Validate ${plugin.name}`,()=>mutate('POST','/api/plugins/inspect',{path:plugin.source_ref}))}>Validate now</button>}
              {confirmRestartId===plugin.id?<><button class="primary" disabled={!!busy} onClick={()=>void restartPanes(plugin).then(()=>setConfirmRestartId(null))}>Confirm restart {running} pane{running===1?'':'s'}</button><button disabled={!!busy} onClick={()=>setConfirmRestartId(null)}>Cancel</button></>:<button disabled={!!busy||!running||!plugin.approval_current||!plugin.enabled} onClick={()=>setConfirmRestartId(plugin.id)}>Restart panes</button>}
              {plugin.source_kind==='link'&&<button disabled={!!busy} onClick={()=>void run(`Open ${plugin.name} source`,()=>mutate('POST','/api/reveal',{path:plugin.source_ref}))}>Open source folder</button>}
              {plugin.source_kind==='link'&&<button disabled={!!busy} onClick={()=>copyDevelopmentCommands(plugin)}>Copy dev commands</button>}
              <button disabled={!!busy} onClick={()=>void run(`Open ${plugin.name} config`,()=>mutate('POST','/api/reveal',{path:plugin.config_dir}))}>Open config</button>
              <button disabled={!!busy} onClick={()=>void run(`Open ${plugin.name} state`,()=>mutate('POST','/api/reveal',{path:plugin.state_dir}))}>Open state</button>
              <button disabled={!!busy||!plugin.resolved_ref} onClick={()=>void run(`Rollback ${plugin.name}`,()=>mutate('POST',`/api/plugins/${plugin.id}/rollback`,{}))}>Rollback</button>
              {confirming?.id===plugin.id&&confirming.purge?<><button class="danger" disabled={!!busy} onClick={()=>void uninstall(plugin,true)}>Confirm purge data</button><button disabled={!!busy} onClick={()=>setConfirming(null)}>Cancel</button></>:<button class="danger" disabled={!!busy} onClick={()=>setConfirming({id:plugin.id,purge:true})}>Purge data</button>}
            </div>
          </div>}
        </article>
      })}</div>
    </section>

    <details class="plugin-development" open><summary>Development folder</summary><section><p>Each direct child with <code>swe-mux-plugin.toml</code> is discovered inertly. Discovery never links, approves, or executes it.</p><label>Development root<input value={developmentRoot} onInput={event=>setDevelopmentRoot(event.currentTarget.value)}/></label><div class="theme-actions"><button class="primary" disabled={!developmentRoot.trim()||!!busy} onClick={()=>void saveDevelopmentRoot(false)}>Save and scan</button><button disabled={!developmentRoot.trim()||!!busy} onClick={()=>void saveDevelopmentRoot(true)}>{development.exists?'Rescan':'Create folder'}</button>{development.exists&&<button disabled={!!busy} onClick={()=>void run('Open development folder',()=>mutate('POST','/api/reveal',{path:development.root}))}>Open folder</button>}</div>{!development.exists&&<p class="profile-hint">The folder does not exist. Create it explicitly, or choose another absolute path.</p>}{development.diagnostic&&<p class="settings-inline-error">{development.diagnostic}</p>}<div class="plugin-development-list">{development.candidates.map(candidate=><article class={`plugin-development-row ${candidate.diagnostic?'invalid':''}`} key={candidate.path}><div><strong>{candidate.name}</strong><code>{candidate.id||candidate.path}</code><small>{candidate.version&&`v${candidate.version} · `}{candidate.description||candidate.diagnostic||'Ready to link'}</small></div>{candidate.linked?<span>linked</span>:candidate.conflict?<span>identity conflict</span>:candidate.diagnostic?<span>invalid</span>:<button disabled={!!busy} onClick={()=>void run(`Link ${candidate.name}`,()=>mutate('POST','/api/plugins/link',{path:candidate.path}))}>Link</button>}</article>)}</div>{development.truncated&&<p class="profile-hint">Only the first 256 direct children were inspected.</p>}</section></details>
    <details class="plugin-add"><summary>Add from another location</summary><section><label>Source mode<select value={mode} onChange={event=>{const next=event.currentTarget.value as 'link'|'install';setMode(next);if(next==='link')setRef('')}}><option value="link">Link arbitrary local directory</option><option value="install">Managed copy or GitHub owner/repo</option></select></label><label>{mode==='link'?'Directory':'Source'}<input value={source} onInput={event=>setSource(event.currentTarget.value)} placeholder={mode==='link'?'D:\\plugins\\my-plugin':'owner/repository'} /></label>{mode==='install'&&<label>Release channel or ref<input value={ref} onInput={event=>setRef(event.currentTarget.value)} placeholder="latest, a tag, or a branch"/><small><code>latest</code> follows the repository's newest GitHub release. A tag pins one release.</small></label>}<div class="theme-actions"><button class="primary" disabled={!source.trim()||!!busy} onClick={install}>{mode==='link'?'Inspect and link':'Inspect and install'}</button></div></section></details>
    <details class="plugin-market"><summary>Plugin marketplace</summary><section><p>The catalog validates manifests and exact indexed revisions. Community listings remain unreviewed software.</p><button disabled={!!busy} onClick={()=>void run('Marketplace',async()=>setMarket((await api<{repositories:MarketRepo[]}>('GET','/api/plugins/marketplace',undefined,{timeoutMs:20_000})).repositories))}>Browse marketplace</button>{market?.map(repo=><article class="plugin-market-row"><div><a href={repo.url} target="_blank" rel="noreferrer">{repo.plugin_name||repo.full_name}</a><p>{repo.description}</p><small>{repo.official?'Official · ':''}{repo.plugin_version&&`v${repo.plugin_version} · `}{repo.platforms?.join(', ')||repo.language||'runtime unspecified'}{repo.permissions?.length?` · ${repo.permissions.length} permissions`:''}</small></div><span>{repo.stars}★ · {repo.license||'license unknown'}{repo.install_ref&&` · ${repo.install_ref}`}</span><button onClick={()=>{setMode('install');setSource(repo.full_name);setRef(repo.install_ref||'')}}>Select</button></article>)}</section></details>
    {message&&<p class={message.toLowerCase().includes('failed')?'settings-inline-error':'profile-hint'} role="status">{message}</p>}
  </div>
}
