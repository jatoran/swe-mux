import { useEffect, useState } from 'preact/hooks'
import { api } from './api'

type CommandSpec={command:string[];cwd:string;timeout_seconds:number}
type Action={id:string;title:string;description:string;contexts:string[];command:CommandSpec}
type Pane={id:string;title:string;description:string;placement:string;contexts:string[];command:CommandSpec}
type Manifest={id:string;name:string;version:string;description:string;author:string;license:string;homepage:string;permissions:string[];requires:string[];runtime_requirements:string[];actions:Action[];panes:Pane[];events:Array<{id:string;on:string}>;startup:Array<{id:string}>;link_handlers:Array<{id:string;title:string;pattern:string}>}
type Plugin={id:string;name:string;version:string;enabled:boolean;lifecycle:string;source_kind:string;source_ref:string;resolved_ref:string;diagnostic:string;approval_current:boolean;config_dir:string;state_dir:string;manifest:Manifest|null}
type Catalogue={execution_enabled:boolean;host_capabilities:string[];plugins:Plugin[]}
type Project={id:string;name:string}
type MarketRepo={full_name:string;description:string;stars:number;language?:string;license?:string;url:string;unreviewed:boolean}

const EMPTY:Catalogue={execution_enabled:true,host_capabilities:[],plugins:[]}

export function PluginsSettings(){
  const [catalogue,setCatalogue]=useState<Catalogue>(EMPTY)
  const [projects,setProjects]=useState<Project[]>([])
  const [projectId,setProjectId]=useState('')
  const [source,setSource]=useState('')
  const [mode,setMode]=useState<'link'|'install'>('link')
  const [busy,setBusy]=useState('')
  const [message,setMessage]=useState('')
  const [logs,setLogs]=useState<Record<string,Array<Record<string,unknown>>>>({})
  const [market,setMarket]=useState<MarketRepo[]|null>(null)
  const [confirming,setConfirming]=useState<{id:string;purge:boolean}|null>(null)

  const load=async()=>{
    const [plugins,knownProjects]=await Promise.all([
      api<Partial<Catalogue>>('GET','/api/plugins'),
      api<Project[]>('GET','/api/projects'),
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
    setProjects(Array.isArray(knownProjects)?knownProjects:[])
    setProjectId(current=>current||(Array.isArray(knownProjects)?knownProjects[0]?.id:'')||'')
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

  return <div class="plugins-settings">
    <section><h3>Plugin host</h3>
      <p>Plugins are full-trust external programs. API permissions limit cooperative callbacks; they do not sandbox filesystem or network access.</p>
      <label class="check"><span>Allow plugin execution</span><input type="checkbox" checked={catalogue.execution_enabled} onChange={event=>void run('Execution policy',()=>mutate('POST','/api/plugins/execution',{enabled:event.currentTarget.checked}))}/></label>
      <p class="profile-hint">Host capabilities: {catalogue.host_capabilities.join(', ')||'unavailable'}</p>
    </section>
    <section><h3>Add a plugin</h3>
      <label>Source mode<select value={mode} onChange={event=>setMode(event.currentTarget.value as 'link'|'install')}><option value="link">Link local directory</option><option value="install">Managed copy or GitHub owner/repo</option></select></label>
      <label>{mode==='link'?'Directory':'Source'}<input value={source} onInput={event=>setSource(event.currentTarget.value)} placeholder={mode==='link'?'D:\\plugins\\my-plugin':'owner/repository'} /></label>
      <div class="theme-actions"><button class="primary" disabled={!source.trim()||!!busy} onClick={install}>{mode==='link'?'Inspect and link':'Inspect and install'}</button><button disabled={!!busy} onClick={()=>void run('Refresh',load)}>Refresh</button></div>
      <p>New content stays inert until you inspect and approve it below. Installation never runs build or package-manager commands.</p>
    </section>
    <section><h3>Installed plugins</h3>
      {!catalogue.plugins.length&&<p>No plugins installed.</p>}
      {catalogue.plugins.map(plugin=><article class={`plugin-card ${plugin.enabled?'enabled':''}`} key={plugin.id}>
        <header><div><strong>{plugin.name}</strong><code>{plugin.id}</code></div><span>{plugin.version} · {plugin.lifecycle}</span></header>
        {plugin.manifest?.description&&<p>{plugin.manifest.description}</p>}
        {plugin.diagnostic&&<p class="settings-inline-error">{plugin.diagnostic}</p>}
        <dl><dt>Source</dt><dd>{plugin.source_kind}: {plugin.source_ref}</dd><dt>Permissions</dt><dd>{plugin.manifest?.permissions?.join(', ')||'none'}</dd><dt>Config</dt><dd><code>{plugin.config_dir}</code></dd><dt>State</dt><dd><code>{plugin.state_dir}</code></dd></dl>
        <div class="theme-actions">
          {!plugin.approval_current&&<button class="primary" disabled={!!busy} onClick={()=>void run(`Approve ${plugin.name}`,()=>mutate('POST',`/api/plugins/${plugin.id}/approve`,{enable:true}))}>Approve and enable</button>}
          {plugin.approval_current&&<button disabled={!!busy} onClick={()=>void run(`${plugin.enabled?'Disable':'Enable'} ${plugin.name}`,()=>mutate('POST',`/api/plugins/${plugin.id}/enable`,{enabled:!plugin.enabled}))}>{plugin.enabled?'Disable':'Enable'}</button>}
          {plugin.source_kind==='managed'&&<button disabled={!!busy} onClick={()=>void run(`Update ${plugin.name}`,()=>mutate('POST',`/api/plugins/${plugin.id}/update`,{}))}>Check and stage update</button>}
          <button disabled={!!busy||!plugin.resolved_ref} onClick={()=>void run(`Rollback ${plugin.name}`,()=>mutate('POST',`/api/plugins/${plugin.id}/rollback`,{}))}>Rollback</button>
          {confirming?.id===plugin.id
            ?<><button class="danger" disabled={!!busy} onClick={()=>void run(`${confirming.purge?'Purge':'Uninstall'} ${plugin.name}`,async()=>{await mutate('DELETE',`/api/plugins/${plugin.id}${confirming.purge?'?purge=1':''}`);setConfirming(null)})}>Confirm {confirming.purge?'purge':'uninstall'}</button><button disabled={!!busy} onClick={()=>setConfirming(null)}>Cancel</button></>
            :<><button disabled={!!busy} onClick={()=>setConfirming({id:plugin.id,purge:false})}>Uninstall</button><button class="danger" disabled={!!busy} onClick={()=>setConfirming({id:plugin.id,purge:true})}>Purge</button></>}
        </div>
        {plugin.enabled&&!!plugin.manifest?.actions?.length&&<div class="plugin-contributions"><h4>Actions</h4>{plugin.manifest.actions.map(action=><button disabled={!!busy||(!projectId&&action.contexts.includes('project'))} title={action.description||action.command.command.join(' ')} onClick={()=>void run(action.title,()=>mutate('POST',`/api/plugins/${plugin.id}/actions/${action.id}`,action.contexts.includes('project')?projectContext:{context:'global'}))}>{action.title}</button>)}</div>}
        {plugin.enabled&&!!plugin.manifest?.panes?.length&&<div class="plugin-contributions"><h4>Panes</h4>{plugin.manifest.panes.map(pane=><button disabled={!!busy||!projectId} title={pane.description||pane.command.command.join(' ')} onClick={()=>void run(pane.title,async()=>{const result=await api<{session:Record<string,unknown>&{id:string};placement:string}>('POST',`/api/plugins/${plugin.id}/panes/${pane.id}`,projectContext);window.dispatchEvent(new CustomEvent('mux:plugin-pane-opened',{detail:{session:result.session,placement:result.placement}}))})}>{pane.title}</button>)}</div>}
        {!!(plugin.manifest?.actions?.length||plugin.manifest?.panes?.length)&&<label>Target Project<select value={projectId} onChange={event=>setProjectId(event.currentTarget.value)}>{projects.map(project=><option value={project.id}>{project.name}</option>)}</select></label>}
        <details onToggle={event=>{if(event.currentTarget.open&&!logs[plugin.id])void api<Array<Record<string,unknown>>>('GET',`/api/plugins/logs?plugin_id=${plugin.id}`).then(items=>setLogs(current=>({...current,[plugin.id]:items})))}}><summary>Command log</summary><pre>{JSON.stringify(logs[plugin.id]||[],null,2)}</pre></details>
      </article>)}
    </section>
    <section><h3>Community marketplace</h3>
      <p>The marketplace is an unreviewed GitHub-topic index. A listing is not a security review or endorsement.</p>
      <button disabled={!!busy} onClick={()=>void run('Marketplace',async()=>setMarket((await api<{repositories:MarketRepo[]}>('GET','/api/plugins/marketplace',undefined,{timeoutMs:20_000})).repositories))}>Browse marketplace</button>
      {market?.map(repo=><article class="plugin-market-row"><div><a href={repo.url} target="_blank" rel="noreferrer">{repo.full_name}</a><p>{repo.description}</p></div><span>{repo.stars}★ · {repo.language||'unknown'} · {repo.license||'license unknown'}</span><button onClick={()=>{setMode('install');setSource(repo.full_name)}}>Select</button></article>)}
    </section>
    {message&&<p class={message.toLowerCase().includes('failed')?'settings-inline-error':'profile-hint'} role="status">{message}</p>}
  </div>
}
