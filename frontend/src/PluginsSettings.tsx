import { rich, t } from './localization/index.ts'
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
  const [messageFailed,setMessageFailed]=useState(false)
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
    const refreshOnFocus=()=>void load().catch(error=>{setMessageFailed(true);setMessage(String(error))})
    refreshOnFocus()
    window.addEventListener('focus',refreshOnFocus)
    return()=>window.removeEventListener('focus',refreshOnFocus)
  },[])

  const run=async(label:string,operation:()=>Promise<unknown>)=>{
    setBusy(label);setMessage('');setMessageFailed(false)
    try{await operation();await load();setMessage(t("PluginsSettings.f8b2596042", { label: label }))}
    catch(error){setMessageFailed(true);setMessage(error instanceof Error?error.message:String(error))}
    finally{setBusy('')}
  }
  const mutate=(method:string,path:string,body?:unknown)=>api(method,path,body,{timeoutMs:240_000})
  const install=()=>run(mode==='link'?t('plugins.link'):t('plugins.install'),()=>mutate(
    'POST',`/api/plugins/${mode}`,mode==='link'?{path:source}:{source,ref:ref.trim()},
  ))
  const uninstall=(plugin:InstalledPlugin,purge:boolean)=>run(t('plugins.remove', {purge:purge?'yes':'no', name:plugin.name}),async()=>{
    await mutate('DELETE',`/api/plugins/${plugin.id}${purge?'?purge=1':''}`)
    setConfirming(null);if(expandedId===plugin.id)setExpandedId(null)
  })
  const refresh=()=>run(t("PluginsSettings.0986f19f7a"),()=>mutate('POST','/api/plugins/refresh',{}))
  const checkUpdates=()=>run(t("PluginsSettings.f26f3275e7"),()=>mutate('POST','/api/plugins/updates/check',{}))
  const saveDevelopmentRoot=(create:boolean)=>run(create?t("PluginsSettings.b5559efb7f"):t("PluginsSettings.4d7dccee0c"),async()=>{
    const scan=await api<PluginDevelopmentScan>('PUT','/api/plugins/development',{path:developmentRoot,create})
    setDevelopment(scan)
  })
  const restartPanes=(plugin:InstalledPlugin)=>run(t("PluginsSettings.a903b2b4e8", { value1: plugin.name }),async()=>{
    const result=await api<{restarted:Array<{old_session_id:string;session:Record<string,unknown>&{id:string};placement:string}>}>('POST',`/api/plugins/${plugin.id}/panes/restart`,undefined,{timeoutMs:240_000})
    for(const item of result.restarted)window.dispatchEvent(new CustomEvent('mux:plugin-pane-restarted',{detail:item}))
  })
  const copyDevelopmentCommands=(plugin:InstalledPlugin)=>{
    const root=JSON.stringify(plugin.source_ref)
    const text=['swemux plugin validate '+root,'swemux plugin refresh','swemux plugin approve '+plugin.id,'swemux plugin restart-panes '+plugin.id].join('\n')
    void navigator.clipboard.writeText(text).then(()=>{setMessageFailed(false);setMessage(t("PluginsSettings.39b959186d"))}).catch(()=>{setMessageFailed(true);setMessage(t("PluginsSettings.2472971a19"))})
  }

  return <div class="plugins-settings">
    <section class="plugin-host-row"><div><h3 data-heading-message="PluginsSettings.9514b7ff48">{t("PluginsSettings.9514b7ff48")}</h3><p>{t("PluginsSettings.960b71f47e")}</p></div><div class="plugin-host-actions"><a href="https://swemux.dev/docs/plugins/" target="_blank" rel="noreferrer">{t("PluginsSettings.9a6a02b319")}</a><button disabled={!!busy} onClick={()=>void refresh()}>{t("PluginsSettings.0e91610117")}</button><button disabled={!!busy} onClick={()=>void checkUpdates()}>{t("PluginsSettings.f26f3275e7")}</button><label class="check"><span>{t("PluginsSettings.a45cd4bd09")}</span><input type="checkbox" checked={catalogue.execution_enabled} onChange={event=>void run(t("PluginsSettings.b9215cec74"),()=>mutate('POST','/api/plugins/execution',{enabled:event.currentTarget.checked}))}/></label></div></section>

    <section class="plugin-list-section"><header class="plugin-list-heading"><div><h3 data-heading-message="PluginsSettings.f8b32f4e92">{t("PluginsSettings.f8b32f4e92")}</h3><span>{rich("PluginsSettings.140d9fa057", { length: catalogue.plugins.length })}</span></div><span>{t("PluginsSettings.73bdf06a1e")}</span></header>
      {!catalogue.plugins.length && <p>{t("PluginsSettings.c4dcbf25d7")}</p>}
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
              <span><strong>{plugin.name}</strong><small>{plugin.manifest?.description || plugin.id}</small></span>
              <em class="plugin-card-badges"><span>{plugin.version}</span><span>{plugin.source_kind}</span>{running>0 && <span>{rich("PluginsSettings.d515eb00ec", { running: running })}</span>}<span class={staged||update?.status==='available'?'attention':''}>{staged ? t("PluginsSettings.d3b240802e") : update?.status==='available' ? t("PluginsSettings.a193f8190f") : plugin.enabled ? t("PluginsSettings.b8d31e8527") : plugin.lifecycle}</span></em>
            </button>
            <div class="plugin-card-actions">
              {!plugin.approval_current ? <button class="primary" disabled={!!busy} onClick={()=>void run(`Approve ${plugin.name}`,()=>mutate('POST',`/api/plugins/${plugin.id}/approve`,{enable:true}))}>{t("PluginsSettings.6007acbe30")}</button> : <button disabled={!!busy} onClick={()=>void run(`${plugin.enabled?'Disable':'Enable'} ${plugin.name}`,()=>mutate('POST',`/api/plugins/${plugin.id}/enable`,{enabled:!plugin.enabled}))}>{plugin.enabled ? t("PluginsSettings.b7e3e4aa42") : t("PluginsSettings.5342e09f27")}</button>}
              {confirmingThis ? <><button class="danger" disabled={!!busy} onClick={()=>void uninstall(plugin,false)}>{t("PluginsSettings.d6c33a825b")}</button><button disabled={!!busy} onClick={()=>setConfirming(null)}>{t("PluginsSettings.19766ed6cc")}</button></> : <button disabled={!!busy} onClick={()=>setConfirming({id:plugin.id,purge:false})}>{t("PluginsSettings.fe19952885")}</button>}
              <button class="plugin-expand" aria-label={t("PluginsSettings.9869239c0b", { value1: (expanded) ? 'yes' : 'no', name: plugin.name })} aria-expanded={expanded} onClick={()=>setExpandedId(expanded?null:plugin.id)}>{expanded ? '▴' : '▾'}</button>
            </div>
          </header>
          {expanded && <div class="plugin-card-details">
            {plugin.diagnostic && <p class="settings-inline-error">{plugin.diagnostic}</p>}
            <dl><dt>{t("PluginsSettings.3843971dcf")}</dt><dd><code>{plugin.id}</code></dd><dt>{t("PluginsSettings.0e570ca6fa")}</dt><dd>{plugin.source_kind}: {plugin.source_ref}{plugin.requested_ref && <> · {plugin.requested_ref}{plugin.selected_ref&&plugin.selected_ref!==plugin.requested_ref ? t("PluginsSettings.682123c07a", { selected_ref: plugin.selected_ref }) : ''}</>}</dd><dt>{t("PluginsSettings.2e516d68f4")}</dt><dd>{plugin.resolved_ref ? <code>{plugin.resolved_ref}</code> : t("PluginsSettings.b79fa7c2d1")}</dd><dt>{t("PluginsSettings.abccc78cc9")}</dt><dd>{plugin.manifest?.permissions?.join(', ') || t("PluginsSettings.140bedbf9c")}</dd><dt>{t("PluginsSettings.bdf3746460")}</dt><dd>{running || t("PluginsSettings.140bedbf9c")}{running ? t("PluginsSettings.da5cc0c3b0", { runningProjects: runningProjects, value2: (runningProjects===1) ? 'yes' : 'no' }) : ''}</dd><dt>{t("PluginsSettings.87e89abb4c")}</dt><dd><code>{plugin.config_dir}</code></dd><dt>{t("PluginsSettings.a3b50c4767")}</dt><dd><code>{plugin.state_dir}</code></dd></dl>
            <p class="plugin-launch-note">{t("PluginsSettings.ebef2f0dd5")}</p>
            {update && <div class={`plugin-update-status ${update.status}`}><strong>{update.status==='available' ? t("PluginsSettings.ff8b555d81") : update.status==='pinned' ? t("PluginsSettings.c741101f45") : update.status==='unavailable' ? t("PluginsSettings.45fd5081ff") : update.status==='staged' ? t("PluginsSettings.c22f0d8ff1") : t("PluginsSettings.ce29b7f85b")}</strong><span>{update.available_version && t("PluginsSettings.ba7674e502", { available_version: update.available_version })}{update.channel || ''}{update.available_ref && <> · <code>{update.available_ref}</code></>}{update.diagnostic && t("PluginsSettings.2a54e33e60", { diagnostic: update.diagnostic })}</span></div>}
            {staged && <section class="plugin-update-review"><header><div><strong>{t("PluginsSettings.dea441e594")}</strong><span>{staged.current_version} → {staged.version}</span></div><em>{staged.authority_changed ? t("PluginsSettings.ea986003f8") : t("PluginsSettings.d9f034cf52")}</em></header>{staged.diagnostic && <p class="settings-inline-error">{staged.diagnostic}</p>}<dl><dt>{t("PluginsSettings.9f67c5d3e9")}</dt><dd>{staged.permissions_added.join(', ') || t("PluginsSettings.140bedbf9c")}</dd><dt>{t("PluginsSettings.e2aa0f3dcf")}</dt><dd>{staged.permissions_removed.join(', ') || t("PluginsSettings.140bedbf9c")}</dd><dt>{t("PluginsSettings.90fc4d9511")}</dt><dd>{staged.capabilities_added.join(', ') || t("PluginsSettings.140bedbf9c")}</dd><dt>{t("PluginsSettings.cc66820d14")}</dt><dd>{staged.capabilities_removed.join(', ') || t("PluginsSettings.140bedbf9c")}</dd><dt>{t("PluginsSettings.2e516d68f4")}</dt><dd><code>{staged.resolved_ref||staged.selected_ref || t("PluginsSettings.a2553c361d")}</code></dd></dl><div class="theme-actions">{confirmUpdateId===plugin.id ? <><button class="primary" disabled={!!busy||!!staged.diagnostic} onClick={()=>void run(t("PluginsSettings.331fdfb957", { value1: plugin.name }),async()=>{await mutate('POST',`/api/plugins/${plugin.id}/update/approve`,{});setConfirmUpdateId(null)})}>{t("PluginsSettings.2a69fbf1bb")}</button><button disabled={!!busy} onClick={()=>setConfirmUpdateId(null)}>{t("PluginsSettings.19766ed6cc")}</button></> : <button class="primary" disabled={!!busy||!!staged.diagnostic} onClick={()=>setConfirmUpdateId(plugin.id)}>{t("PluginsSettings.3757c83565")}</button>}<button disabled={!!busy} onClick={()=>void run(t("PluginsSettings.d9329684d4", { value1: plugin.name }),()=>mutate('DELETE',`/api/plugins/${plugin.id}/update`))}>{t("PluginsSettings.eb1a70e392")}</button></div></section>}
            <details onToggle={event=>{if(event.currentTarget.open&&!logs[plugin.id])void api<Array<Record<string,unknown>>>('GET',`/api/plugins/logs?plugin_id=${plugin.id}`).then(items=>setLogs(current=>({...current,[plugin.id]:items})))}}><summary>{t("PluginsSettings.6aea739321")}</summary><pre>{JSON.stringify(logs[plugin.id]||[],null,2)}</pre></details>
            <div class="plugin-advanced-actions">
              {plugin.source_kind==='managed'&&!staged && <button disabled={!!busy||update?.status!=='available'} title={update?.status==='available' ? t("PluginsSettings.899b5e44b0") : t("PluginsSettings.8d2f30c4e4")} onClick={()=>void run(t("PluginsSettings.95ca09d913", { value1: plugin.name }),()=>mutate('POST',`/api/plugins/${plugin.id}/update`,{}))}>{t("PluginsSettings.d64da3131f")}</button>}
              {plugin.source_kind==='link' && <button disabled={!!busy} onClick={()=>void run(`Validate ${plugin.name}`,()=>mutate('POST','/api/plugins/inspect',{path:plugin.source_ref}))}>{t("PluginsSettings.900c8e32d5")}</button>}
              {confirmRestartId===plugin.id ? <><button class="primary" disabled={!!busy} onClick={()=>void restartPanes(plugin).then(()=>setConfirmRestartId(null))}>{rich("PluginsSettings.2badb25270", { running: running, value2: (running===1) ? 'yes' : 'no' })}</button><button disabled={!!busy} onClick={()=>setConfirmRestartId(null)}>{t("PluginsSettings.19766ed6cc")}</button></> : <button disabled={!!busy||!running||!plugin.approval_current||!plugin.enabled} onClick={()=>setConfirmRestartId(plugin.id)}>{t("PluginsSettings.50853c317f")}</button>}
              {plugin.source_kind==='link' && <button disabled={!!busy} onClick={()=>void run(t("PluginsSettings.25ece198b9", { value1: plugin.name }),()=>mutate('POST','/api/reveal',{path:plugin.source_ref}))}>{t("PluginsSettings.c01d26eb7a")}</button>}
              {plugin.source_kind==='link' && <button disabled={!!busy} onClick={()=>copyDevelopmentCommands(plugin)}>{t("PluginsSettings.8869acb168")}</button>}
              <button disabled={!!busy} onClick={()=>void run(t("PluginsSettings.42ecbed5c8", { value1: plugin.name }),()=>mutate('POST','/api/reveal',{path:plugin.config_dir}))}>{t("PluginsSettings.d6568d3a71")}</button>
              <button disabled={!!busy} onClick={()=>void run(t("PluginsSettings.c8fb178a46", { value1: plugin.name }),()=>mutate('POST','/api/reveal',{path:plugin.state_dir}))}>{t("PluginsSettings.1700031d63")}</button>
              <button disabled={!!busy||!plugin.resolved_ref} onClick={()=>void run(`Rollback ${plugin.name}`,()=>mutate('POST',`/api/plugins/${plugin.id}/rollback`,{}))}>{t("PluginsSettings.c591f55749")}</button>
              {confirming?.id===plugin.id&&confirming.purge ? <><button class="danger" disabled={!!busy} onClick={()=>void uninstall(plugin,true)}>{t("PluginsSettings.6710d0a48f")}</button><button disabled={!!busy} onClick={()=>setConfirming(null)}>{t("PluginsSettings.19766ed6cc")}</button></> : <button class="danger" disabled={!!busy} onClick={()=>setConfirming({id:plugin.id,purge:true})}>{t("PluginsSettings.64ae36e907")}</button>}
            </div>
          </div>}
        </article>
      })}</div>
    </section>

    <details class="plugin-development" open><summary>{t("PluginsSettings.c7fab03ab4")}</summary><section><p>{rich("PluginsSettings.cd4f824e9d", { value1: <code>{t("PluginsSettings.4fbf33c1e0")}</code> })}</p><label>{rich("PluginsSettings.8b85d671cd", { value1: <input value={developmentRoot} onInput={event=>setDevelopmentRoot(event.currentTarget.value)}/> })}</label><div class="theme-actions"><button class="primary" disabled={!developmentRoot.trim()||!!busy} onClick={()=>void saveDevelopmentRoot(false)}>{t("PluginsSettings.2ac36d2262")}</button><button disabled={!developmentRoot.trim()||!!busy} onClick={()=>void saveDevelopmentRoot(true)}>{development.exists ? t("PluginsSettings.ec4b3ecb7a") : t("PluginsSettings.82b9e1ef0b")}</button>{development.exists && <button disabled={!!busy} onClick={()=>void run(t("PluginsSettings.08333cd3ee"),()=>mutate('POST','/api/reveal',{path:development.root}))}>{t("PluginsSettings.6a9084025c")}</button>}</div>{!development.exists && <p class="profile-hint">{t("PluginsSettings.24814cecf9")}</p>}{development.diagnostic && <p class="settings-inline-error">{development.diagnostic}</p>}<div class="plugin-development-list">{development.candidates.map(candidate=><article class={`plugin-development-row ${candidate.diagnostic?'invalid':''}`} key={candidate.path}><div><strong>{candidate.name}</strong><code>{candidate.id || candidate.path}</code><small>{candidate.version && t("PluginsSettings.37be21bffc", { version: candidate.version })}{candidate.description||candidate.diagnostic || t("PluginsSettings.e3ab1d008e")}</small></div>{candidate.linked ? <span>{t("PluginsSettings.2272bea616")}</span> : candidate.conflict ? <span>{t("PluginsSettings.890bf3a970")}</span> : candidate.diagnostic ? <span>{t("PluginsSettings.f1234d7517")}</span> : <button disabled={!!busy} onClick={()=>void run(`Link ${candidate.name}`,()=>mutate('POST','/api/plugins/link',{path:candidate.path}))}>{t("PluginsSettings.a6a32dbc56")}</button>}</article>)}</div>{development.truncated && <p class="profile-hint">{t("PluginsSettings.9a401ca4f5")}</p>}</section></details>
    <details class="plugin-add"><summary>{t("PluginsSettings.9bec613ba4")}</summary><section><label>{rich("PluginsSettings.264a3e29f9", { value1: <select value={mode} onChange={event=>{const next=event.currentTarget.value as 'link'|'install';setMode(next);if(next==='link')setRef('')}}><option value="link">{t("PluginsSettings.52d38c5ec1")}</option><option value="install">{t("PluginsSettings.c08aab1fe6")}</option></select> })}</label><label>{mode==='link' ? t("PluginsSettings.c8f84c3c53") : t("PluginsSettings.0e570ca6fa")}<input value={source} onInput={event=>setSource(event.currentTarget.value)} placeholder={mode==='link' ? t("PluginsSettings.ac252edc2e") : t("PluginsSettings.21da1b566c")} /></label>{mode==='install' && <label>{rich("PluginsSettings.6bdb593a9d", { value1: <input value={ref} onInput={event=>setRef(event.currentTarget.value)} placeholder={t("PluginsSettings.20f6cb2c79")}/>, value2: <small>{rich("PluginsSettings.8bf34d2bfe", { value1: <code>{t("PluginsSettings.5e1e2bcac3")}</code> })}</small> })}</label>}<div class="theme-actions"><button class="primary" disabled={!source.trim()||!!busy} onClick={install}>{mode==='link' ? t("PluginsSettings.f2729cafee") : t("PluginsSettings.3e60cb8ad6")}</button></div></section></details>
    <details class="plugin-market"><summary>{t("PluginsSettings.46734f370d")}</summary><section><p>{t("PluginsSettings.dd607b955c")}</p><button disabled={!!busy} onClick={()=>void run('Marketplace',async()=>setMarket((await api<{repositories:MarketRepo[]}>('GET','/api/plugins/marketplace',undefined,{timeoutMs:20_000})).repositories))}>{t("PluginsSettings.2ad20c9748")}</button>{market?.map(repo=><article class="plugin-market-row"><div><a href={repo.url} target="_blank" rel="noreferrer">{repo.plugin_name || repo.full_name}</a><p>{repo.description}</p><small>{repo.official ? t("PluginsSettings.95e18c52f7") : ''}{repo.plugin_version && t("PluginsSettings.9992c0d08b", { plugin_version: repo.plugin_version })}{repo.platforms?.join(', ')||repo.language || t("PluginsSettings.02379767f1")}{repo.permissions?.length ? t("PluginsSettings.30bce595bf", { length: repo.permissions.length }) : ''}</small></div><span>{repo.stars}★ · {repo.license || t("PluginsSettings.0a07cb0a9c")}{repo.install_ref && t("PluginsSettings.01658bd842", { install_ref: repo.install_ref })}</span><button onClick={()=>{setMode('install');setSource(repo.full_name);setRef(repo.install_ref||'')}}>{t("PluginsSettings.2a78025de6")}</button></article>)}</section></details>
    {message && <p class={messageFailed?'settings-inline-error':'profile-hint'} role="status">{message}</p>}
  </div>
}
