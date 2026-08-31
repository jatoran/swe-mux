import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import {
  alphabetizedPluginProjects, selectedPluginProject,
  type InstalledPlugin, type PluginCatalogue, type PluginProject,
} from './pluginCatalog.ts'

type MarketRepo={full_name:string;description:string;stars:number;language?:string;license?:string;url:string;unreviewed:boolean}

const EMPTY:PluginCatalogue={execution_enabled:true,host_capabilities:[],plugins:[]}

export function PluginsSettings({focusedProjectId=''}:{focusedProjectId?:string}){
  const [catalogue,setCatalogue]=useState<PluginCatalogue>(EMPTY)
  const [projects,setProjects]=useState<PluginProject[]>([])
  const [projectId,setProjectId]=useState('')
  const [expandedId,setExpandedId]=useState<string|null>(null)
  const [source,setSource]=useState('')
  const [mode,setMode]=useState<'link'|'install'>('link')
  const [busy,setBusy]=useState('')
  const [message,setMessage]=useState('')
  const [logs,setLogs]=useState<Record<string,Array<Record<string,unknown>>>>({})
  const [market,setMarket]=useState<MarketRepo[]|null>(null)
  const [confirming,setConfirming]=useState<{id:string;purge:boolean}|null>(null)

  const load=async()=>{
    const [plugins,knownProjects]=await Promise.all([
      api<Partial<PluginCatalogue>>('GET','/api/plugins'),
      api<PluginProject[]>('GET','/api/projects'),
    ])
    // Field-by-field rather than adopting the payload wholesale: every field is
    // mapped or joined unconditionally below, so a payload missing one (an older
    // daemon, a harness answering `{}`) crashed the whole tab instead of drawing
    // its empty state.
    setCatalogue({
      execution_enabled:plugins?.execution_enabled!==false,
      host_capabilities:plugins?.host_capabilities||[],
      plugins:plugins?.plugins||[],
    })
    const sorted=alphabetizedPluginProjects(Array.isArray(knownProjects)?knownProjects:[])
    setProjects(sorted)
    setProjectId(current=>selectedPluginProject(sorted,focusedProjectId,current))
  }
  useEffect(()=>{void load().catch(error=>setMessage(String(error)))},[])

  const run=async(label:string,operation:()=>Promise<unknown>)=>{
    setBusy(label);setMessage('')
    try{await operation();await load();setMessage(`${label} complete.`)}
    catch(error){setMessage(error instanceof Error?error.message:String(error))}
    finally{setBusy('')}
  }
  const mutate=(method:string,path:string,body?:unknown)=>api(method,path,body,{timeoutMs:240_000})
  const install=()=>run(mode==='link'?'Link':'Install',()=>mutate('POST',`/api/plugins/${mode}`,mode==='link'?{path:source}:{source}))
  const projectContext={context:'project',project_id:projectId}
  const uninstall=(plugin:InstalledPlugin,purge:boolean)=>run(`${purge?'Purge':'Uninstall'} ${plugin.name}`,async()=>{
    await mutate('DELETE',`/api/plugins/${plugin.id}${purge?'?purge=1':''}`)
    setConfirming(null);if(expandedId===plugin.id)setExpandedId(null)
  })
  const openPane=(plugin:InstalledPlugin,paneId:string,title:string)=>run(title,async()=>{
    const result=await api<{session:Record<string,unknown>&{id:string};placement:string}>('POST',`/api/plugins/${plugin.id}/panes/${paneId}`,projectContext)
    window.dispatchEvent(new CustomEvent('mux:plugin-pane-opened',{detail:{session:result.session,placement:result.placement}}))
  })

  return <div class="plugins-settings">
    <section class="plugin-host-row"><div><h3>Plugins</h3><p>External tools run as your user. Permissions limit swe-mux callbacks, not filesystem or network access.</p></div><label class="check"><span>Execution</span><input type="checkbox" checked={catalogue.execution_enabled} onChange={event=>void run('Execution policy',()=>mutate('POST','/api/plugins/execution',{enabled:event.currentTarget.checked}))}/></label></section>

    <section class="plugin-list-section"><header class="plugin-list-heading"><div><h3>Installed</h3><span>{catalogue.plugins.length} plugins</span></div>{projects.length>0&&<label>Test Project<select value={projectId} onChange={event=>setProjectId(event.currentTarget.value)}>{projects.map(project=><option value={project.id}>{project.name}</option>)}</select></label>}</header>
      {!catalogue.plugins.length&&<p>No plugins installed.</p>}
      <div class="plugin-card-list">{catalogue.plugins.map(plugin=>{
        const expanded=expandedId===plugin.id
        const confirmingThis=confirming?.id===plugin.id&&!confirming.purge
        return <article class={`plugin-card ${plugin.enabled?'enabled':''} ${expanded?'expanded':''}`} key={plugin.id}>
          <header>
            <button class="plugin-card-summary" aria-expanded={expanded} onClick={()=>setExpandedId(expanded?null:plugin.id)}>
              <span class={`plugin-state-dot ${plugin.enabled?'enabled':'disabled'}`} aria-hidden="true"/>
              <span><strong>{plugin.name}</strong><small>{plugin.manifest?.description||plugin.id}</small></span>
              <em>{plugin.version} · {plugin.enabled?'on':plugin.lifecycle}</em>
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
            <dl><dt>ID</dt><dd><code>{plugin.id}</code></dd><dt>Source</dt><dd>{plugin.source_kind}: {plugin.source_ref}</dd><dt>Permissions</dt><dd>{plugin.manifest?.permissions?.join(', ')||'none'}</dd><dt>Config</dt><dd><code>{plugin.config_dir}</code></dd><dt>State</dt><dd><code>{plugin.state_dir}</code></dd></dl>
            {!!plugin.manifest&&((plugin.manifest.actions?.length||0)>0||(plugin.manifest.panes?.length||0)>0)&&<div class="plugin-contributions"><h4>Tools</h4><p>Project tools also appear under that Project's Run menu and in the command palette.</p>
              {plugin.enabled&&plugin.manifest.panes?.map(pane=><button disabled={!!busy||!projectId} title={pane.description||pane.command.command.join(' ')} onClick={()=>void openPane(plugin,pane.id,pane.title)}>{pane.title}</button>)}
              {plugin.enabled&&plugin.manifest.actions?.map(action=><button disabled={!!busy||(!projectId&&action.contexts.includes('project'))} title={action.description||action.command.command.join(' ')} onClick={()=>void run(action.title,()=>mutate('POST',`/api/plugins/${plugin.id}/actions/${action.id}`,action.contexts.includes('project')?projectContext:{context:'global'}))}>{action.title}</button>)}
            </div>}
            <details onToggle={event=>{if(event.currentTarget.open&&!logs[plugin.id])void api<Array<Record<string,unknown>>>('GET',`/api/plugins/logs?plugin_id=${plugin.id}`).then(items=>setLogs(current=>({...current,[plugin.id]:items})))}}><summary>Recent command log</summary><pre>{JSON.stringify(logs[plugin.id]||[],null,2)}</pre></details>
            <div class="plugin-advanced-actions">
              {plugin.source_kind==='managed'&&<button disabled={!!busy} onClick={()=>void run(`Update ${plugin.name}`,()=>mutate('POST',`/api/plugins/${plugin.id}/update`,{}))}>Stage update</button>}
              <button disabled={!!busy||!plugin.resolved_ref} onClick={()=>void run(`Rollback ${plugin.name}`,()=>mutate('POST',`/api/plugins/${plugin.id}/rollback`,{}))}>Rollback</button>
              {confirming?.id===plugin.id&&confirming.purge?<><button class="danger" disabled={!!busy} onClick={()=>void uninstall(plugin,true)}>Confirm purge data</button><button disabled={!!busy} onClick={()=>setConfirming(null)}>Cancel</button></>:<button class="danger" disabled={!!busy} onClick={()=>setConfirming({id:plugin.id,purge:true})}>Purge data</button>}
            </div>
          </div>}
        </article>
      })}</div>
    </section>

    <details class="plugin-add"><summary>Add a plugin</summary><section><label>Source mode<select value={mode} onChange={event=>setMode(event.currentTarget.value as 'link'|'install')}><option value="link">Link local directory</option><option value="install">Managed copy or GitHub owner/repo</option></select></label><label>{mode==='link'?'Directory':'Source'}<input value={source} onInput={event=>setSource(event.currentTarget.value)} placeholder={mode==='link'?'D:\\plugins\\my-plugin':'owner/repository'} /></label><div class="theme-actions"><button class="primary" disabled={!source.trim()||!!busy} onClick={install}>{mode==='link'?'Inspect and link':'Inspect and install'}</button><button disabled={!!busy} onClick={()=>void run('Refresh',load)}>Refresh</button></div></section></details>
    <details class="plugin-market"><summary>Community marketplace</summary><section><p>Unreviewed GitHub repositories. A listing is not an endorsement.</p><button disabled={!!busy} onClick={()=>void run('Marketplace',async()=>setMarket((await api<{repositories:MarketRepo[]}>('GET','/api/plugins/marketplace',undefined,{timeoutMs:20_000})).repositories))}>Browse marketplace</button>{market?.map(repo=><article class="plugin-market-row"><div><a href={repo.url} target="_blank" rel="noreferrer">{repo.full_name}</a><p>{repo.description}</p></div><span>{repo.stars}★ · {repo.language||'unknown'} · {repo.license||'license unknown'}</span><button onClick={()=>{setMode('install');setSource(repo.full_name)}}>Select</button></article>)}</section></details>
    {message&&<p class={message.toLowerCase().includes('failed')?'settings-inline-error':'profile-hint'} role="status">{message}</p>}
  </div>
}
