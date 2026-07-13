import { Fragment } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { applyTheme, configureCustomTheme, type CustomTheme, type ThemeName } from './theme'
import type { ShellProfile, Space } from './types'

type Config = {
  revision:number; host:string; port:number; data_dir:string; requires_auth:boolean; access_mode:string; tailnet_enabled:boolean
  startup_cwd:string; default_backend:string; shell_exe:string; claude_exe:string
  codex_exe:string; scrollback_bytes:number; history_limit:number
  claude_args:string[]; codex_args:string[]
  git_poll_seconds:number; reconcile_external_history:boolean; theme:ThemeName
  middle_click_paste:boolean; broadcast_default:boolean
  ccusage_enabled:boolean; ccusage_refresh_minutes:number
  ccusage_claude_command:string[]; ccusage_codex_command:string[]
  custom_theme:CustomTheme
  default_shell_profile:string; shell_profiles:ShellProfile[]
}

type UsageStatus = {
  enabled:boolean; refreshing:boolean; package:string; install_command:string
  states:Record<string,{status:string;error?:string;refreshed_at?:number}>
  cache?:{updated_at?:number;providers?:Record<string,{totals?:Record<string,number>}>}
}

type ProjectConfig = {
  project:{id:string;label:string;root:string};path:string;status:string;revision:string;error?:string
  values:{project_label?:string;default_cwd?:string;default_shell_profile?:string;notes_enabled?:boolean}
}
type RemoteStatus = {
  mode:string;listen_url:string;available:boolean;serve_configured:boolean
  serve_url?:string|null;funnel_detected:boolean;setup_command:string;diagnostic:string
  tailnet_enabled:boolean;tailnet_ip?:string|null;tailnet_urls:string[];direct_available:boolean
}

export function Settings({ onClose, onOpenUsage, cwd, initialSection='General' }: { onClose: () => void; onOpenUsage?:() => void; cwd?:string; initialSection?:string }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [draft, setDraft] = useState<Config | null>(null)
  const [hooks, setHooks] = useState('')
  const [bindings, setBindings] = useState('')
  const [claudeArgs, setClaudeArgs] = useState('[]')
  const [codexArgs, setCodexArgs] = useState('[]')
  const [detectedProfiles, setDetectedProfiles] = useState<ShellProfile[]>([])
  const [spaceDefaults, setSpaceDefaults] = useState<Space[]>([])
  const [hookDiagnostic, setHookDiagnostic] = useState<{status:string;error?:string;rules?:number}>({status:'loading'})
  const [usage, setUsage] = useState<UsageStatus | null>(null)
  const [usageRefreshMessage, setUsageRefreshMessage] = useState('')
  const [remote, setRemote] = useState<RemoteStatus | null>(null)
  const [projectConfig, setProjectConfig] = useState<ProjectConfig | null>(null)
  const [projectValues, setProjectValues] = useState<ProjectConfig['values']>({})
  const [status, setStatus] = useState('loading…')
  const [errors, setErrors] = useState<Record<string,string>>({})
  const [mobileSection,setMobileSection] = useState(initialSection)
  const panel = useRef<HTMLElement>(null)
  const themeFile = useRef<HTMLInputElement>(null)
  const restoreFocus = useRef<HTMLElement | null>(document.activeElement as HTMLElement | null)

  useEffect(() => {
    api<RemoteStatus>('GET','/api/remote/status').then(setRemote).catch(()=>setRemote(null))
    Promise.all([
      api<Config>('GET','/api/config'),
      api<{text:string}>('GET','/api/hooks'),
      api<{bindings:Record<string,string>}>('GET','/api/keybindings'),
      api<{detected:ShellProfile[]}>('GET','/api/profiles'),
      api<Space[]>('GET','/api/spaces'),
      api<{diagnostic:{status:string;error?:string;rules?:number}}>('GET','/api/hooks/status'),
      api<UsageStatus>('GET','/api/usage'),
      cwd ? api<ProjectConfig>('GET',`/api/project/config?cwd=${encodeURIComponent(cwd)}`) : Promise.resolve(null),
    ]).then(([next, hookData, keyData, profileData, spaces, hookStatus, usageStatus, project]) => {
      setConfig(next); setDraft(next); setHooks(hookData.text)
      setClaudeArgs(JSON.stringify(next.claude_args)); setCodexArgs(JSON.stringify(next.codex_args))
      setBindings(JSON.stringify(keyData.bindings, null, 2)); setStatus('ready')
      setDetectedProfiles(profileData.detected); setSpaceDefaults(spaces)
      setHookDiagnostic(hookStatus.diagnostic); setUsage(usageStatus)
      setProjectConfig(project);setProjectValues(project?.values||{})
      configureCustomTheme(next.custom_theme); applyTheme(next.theme)
    }).catch(error => setStatus(error.message))
  }, [cwd])

  useEffect(() => {
    if (!draft || !panel.current) return
    setMobileSection(initialSection)
    const frame=requestAnimationFrame(()=>{
      const target=[...panel.current!.querySelectorAll<HTMLElement>('main>section')].find(section=>section.querySelector('h3')?.textContent===initialSection)
      target?.scrollIntoView({block:'start'})
    })
    return()=>cancelAnimationFrame(frame)
  },[draft,initialSection])

  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { if (config) applyTheme(config.theme); onClose() }
      if (event.key === 'Tab' && panel.current) {
        const focusable = [...panel.current.querySelectorAll<HTMLElement>('button,input,select,textarea')].filter(item => !item.hasAttribute('disabled'))
        if (!focusable.length) return
        const first = focusable[0], last = focusable[focusable.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    window.addEventListener('keydown', close, true)
    return () => { window.removeEventListener('keydown', close, true); restoreFocus.current?.focus() }
  }, [config, onClose])

  const change = <K extends keyof Config>(key: K, value: Config[K]) => setDraft(current => current ? {...current,[key]:value} : current)
  const save = async () => {
    if (!draft || !config) return
    let savingDraft: Config
    try {
      savingDraft = {...draft,claude_args:JSON.parse(claudeArgs),codex_args:JSON.parse(codexArgs)}
    } catch { setErrors({agent_args:'agent args must be JSON arrays of strings'}); return }
    const body: Record<string,unknown> = {_revision:config.revision}
    for (const key of Object.keys(savingDraft) as (keyof Config)[]) {
      if (!['revision','host','port','data_dir','requires_auth'].includes(key) && savingDraft[key] !== config[key]) body[key] = savingDraft[key]
    }
    try {
      const parsedBindings = JSON.parse(bindings)
      await Promise.all([
        api('PUT','/api/hooks?validate=1',{text:hooks}),
        api('PUT','/api/keybindings?validate=1',{bindings:parsedBindings}),
      ])
      const [next] = await Promise.all([
        api<Config & {hot_applied:string[];restart_required:string[]}>('PATCH','/api/config',body),
        api('PUT','/api/hooks',{text:hooks}),
        api('PUT','/api/keybindings',{bindings:parsedBindings}),
        ...spaceDefaults.map(space=>api('PATCH',`/api/spaces/${space.id}`,{default_profile_id:space.default_profile_id||null,default_cwd:space.default_cwd||null})),
        ...(projectConfig&&cwd?[api<ProjectConfig>('PUT','/api/project/config',{cwd,values:projectValues,revision:projectConfig.revision}).then(result=>{setProjectConfig(result);setProjectValues(result.values)})]:[]),
      ])
      setConfig(next); setDraft(next); setErrors({})
      setStatus(next.restart_required.length ? `saved · restart required: ${next.restart_required.join(', ')}` : 'saved · hot applied')
      configureCustomTheme(next.custom_theme); applyTheme(next.theme)
    } catch (error) {
      const typed = error as Error & {fields?:Record<string,string>}
      setErrors(typed.fields || {settings:typed.message}); setStatus('invalid · nothing was changed')
    }
  }
  const reset = async () => {
    const next = await api<Config>('POST','/api/config/reset',{})
    setConfig(next); setDraft(next); configureCustomTheme(next.custom_theme); applyTheme(next.theme); setStatus('defaults restored')
  }
  const exportConfig = () => {
    if (!draft) return
    const blob = new Blob([JSON.stringify(draft,null,2)],{type:'application/json'})
    const link = document.createElement('a'); link.href=URL.createObjectURL(blob); link.download='swe-mux-settings.json'; link.click(); URL.revokeObjectURL(link.href)
  }
  const exportTheme = () => {
    if (!draft) return
    const blob = new Blob([JSON.stringify(draft.custom_theme,null,2)],{type:'application/json'})
    const link = document.createElement('a'); link.href=URL.createObjectURL(blob); link.download='swe-mux-theme.json'; link.click(); URL.revokeObjectURL(link.href)
  }
  const importTheme = async (file?:File) => {
    if (!file) return
    try {
      const custom = JSON.parse(await file.text()) as CustomTheme
      change('custom_theme',custom); change('theme','custom'); configureCustomTheme(custom); applyTheme('custom'); setErrors({})
    } catch { setErrors({custom_theme:'theme file must contain valid JSON semantic tokens'}) }
  }
  const updateProfile = (index:number, changes:Partial<ShellProfile>) => change('shell_profiles',draft!.shell_profiles.map((profile,itemIndex)=>itemIndex===index?{...profile,...changes}:profile))
  const addProfile = (source?:ShellProfile) => {
    const base = source || {id:'shell',label:'New shell',executable:'',args:[],env:{},platforms:['windows'],cwd_strategy:'native',marker:'sh',capabilities:['interactive'],enabled:true}
    let id=base.id, suffix=2
    while(draft!.shell_profiles.some(profile=>profile.id===id)) id=`${base.id}-${suffix++}`
    change('shell_profiles',[...draft!.shell_profiles,{...base,id,args:[...base.args],env:{...base.env},capabilities:[...base.capabilities]}])
  }
  const moveProfile = (index:number,offset:number) => { const items=[...draft!.shell_profiles],target=index+offset;if(target<0||target>=items.length)return;[items[index],items[target]]=[items[target],items[index]];change('shell_profiles',items) }
  const restoreDetected = () => { const existing=new Set(draft!.shell_profiles.map(profile=>profile.id));change('shell_profiles',[...draft!.shell_profiles,...detectedProfiles.filter(profile=>!existing.has(profile.id)).map(profile=>({...profile,configured:undefined}))]) }
  const refreshUsage = async () => {
    setUsageRefreshMessage('Refreshing Claude + Codex usage… this may take up to a minute.')
    setErrors(current=>{const next={...current};delete next.ccusage;return next})
    try {
      const next=await api<UsageStatus>('POST','/api/usage/refresh',{})
      setUsage(next)
      setUsageRefreshMessage(`Refresh finished · ${Object.entries(next.states).map(([provider,state])=>`${provider} ${state.status}`).join(' · ')}`)
    } catch (error) {
      const message=(error as Error).message
      setUsageRefreshMessage(`Refresh failed · ${message}`)
      setErrors(current=>({...current,ccusage:message}))
    }
  }
  const clearUsage = async () => setUsage(await api<UsageStatus>('DELETE','/api/usage/cache'))
  if (!draft) return <div class="settings-layer"><section class="settings-panel">{status}</section></div>
  return <div class="settings-layer"><section class="settings-panel" ref={panel} role="dialog" aria-modal="true" aria-label="Settings">
    <header><div><span>CONFIG::V4</span><h2>Settings</h2></div><button onClick={() => { if(config) applyTheme(config.theme); onClose() }}>×</button></header>
    <main>
      <nav class="settings-mobile-nav" aria-label="Settings sections"><select value={mobileSection} onChange={event=>{const label=event.currentTarget.value;setMobileSection(label);const target=[...panel.current!.querySelectorAll<HTMLElement>('main>section')].find(section=>section.querySelector('h3')?.textContent===label);target?.scrollIntoView({block:'start'})}}>{['General','Terminals','Space defaults','Agents','Input','Git and history','Current project','Usage analytics','Hooks and notifications','Remote and security','Appearance'].filter(label=>label!=='Current project'||projectConfig).map(label=><option value={label}>{label}</option>)}</select></nav>
      {Object.keys(errors).length > 0 && <section class="settings-errors" aria-live="assertive"><h3>Validation errors</h3>{Object.entries(errors).map(([field,message])=><p><strong>{field}</strong> — {message}</p>)}</section>}
      <section><h3>General</h3><label>Startup directory<input value={draft.startup_cwd} onInput={e=>change('startup_cwd',e.currentTarget.value)} /></label><label>Default backend<select value={draft.default_backend} onChange={e=>change('default_backend',e.currentTarget.value)}><option value="shell">Shell</option><option value="claude">Claude</option><option value="codex">Codex</option></select></label><label>Scrollback bytes<input type="number" value={draft.scrollback_bytes} onInput={e=>change('scrollback_bytes',Number(e.currentTarget.value))} /></label><label>History limit<input type="number" value={draft.history_limit} onInput={e=>change('history_limit',Number(e.currentTarget.value))} /></label></section>
      <section class="profile-settings"><h3>Terminals</h3><label>Global default profile<select value={draft.default_shell_profile} onChange={e=>change('default_shell_profile',e.currentTarget.value)}>{draft.shell_profiles.filter(profile=>profile.enabled).map(profile=><option value={profile.id}>{profile.label}</option>)}</select></label><div class="profile-list">{draft.shell_profiles.map((profile,index)=><article><div class="profile-row"><input value={profile.id} aria-label="Profile ID" onInput={e=>updateProfile(index,{id:e.currentTarget.value})}/><input value={profile.label} aria-label="Profile label" onInput={e=>updateProfile(index,{label:e.currentTarget.value})}/><button onClick={()=>moveProfile(index,-1)}>↑</button><button onClick={()=>moveProfile(index,1)}>↓</button><button onClick={()=>addProfile(profile)}>dup</button><button onClick={()=>updateProfile(index,{enabled:!profile.enabled})}>{profile.enabled?'on':'off'}</button><button disabled={draft.shell_profiles.length===1} onClick={()=>change('shell_profiles',draft.shell_profiles.filter((_,itemIndex)=>itemIndex!==index))}>×</button></div><label>Executable<input value={profile.executable} onInput={e=>updateProfile(index,{executable:e.currentTarget.value})}/></label><label>Arguments (one per line)<textarea value={profile.args.join('\n')} onInput={e=>updateProfile(index,{args:e.currentTarget.value.split('\n').filter(Boolean)})}/></label><label>Environment (KEY=value per line)<textarea value={Object.entries(profile.env).map(([key,value])=>`${key}=${value}`).join('\n')} onInput={e=>updateProfile(index,{env:Object.fromEntries(e.currentTarget.value.split('\n').filter(line=>line.includes('=')).map(line=>{const at=line.indexOf('=');return [line.slice(0,at),line.slice(at+1)]}))})}/></label><div class="profile-row"><label>Marker<input value={profile.marker} onInput={e=>updateProfile(index,{marker:e.currentTarget.value})}/></label><label>Cwd strategy<select value={profile.cwd_strategy} onChange={e=>updateProfile(index,{cwd_strategy:e.currentTarget.value as ShellProfile['cwd_strategy']})}><option value="native">native</option><option value="home">home</option><option value="wsl">wsl</option></select></label></div><label>Capabilities (comma separated)<input value={profile.capabilities.join(', ')} onInput={e=>updateProfile(index,{capabilities:e.currentTarget.value.split(',').map(item=>item.trim()).filter(Boolean)})}/></label><small>{profile.capabilities.join(' · ')}</small></article>)}</div><div class="theme-actions"><button onClick={()=>addProfile()}>Add profile</button><button onClick={restoreDetected}>Restore detected presets</button></div></section>
      <section><h3>Space defaults</h3>{spaceDefaults.map((space,index)=><article class="space-default"><strong>{space.name}</strong><label>Profile<select value={space.default_profile_id||''} onChange={e=>setSpaceDefaults(items=>items.map((item,itemIndex)=>itemIndex===index?{...item,default_profile_id:e.currentTarget.value||undefined}:item))}><option value="">Use global default</option>{draft.shell_profiles.filter(profile=>profile.enabled).map(profile=><option value={profile.id}>{profile.label}</option>)}</select></label><label>Working directory<input value={space.default_cwd||''} onInput={e=>setSpaceDefaults(items=>items.map((item,itemIndex)=>itemIndex===index?{...item,default_cwd:e.currentTarget.value||undefined}:item))}/></label></article>)}</section>
      <section><h3>Agents</h3><label>Claude executable<input value={draft.claude_exe} onInput={e=>change('claude_exe',e.currentTarget.value)} /></label><label>Claude default args (JSON)<input value={claudeArgs} onInput={e=>setClaudeArgs(e.currentTarget.value)} /></label><label>Codex executable<input value={draft.codex_exe} onInput={e=>change('codex_exe',e.currentTarget.value)} /></label><label>Codex default args (JSON)<input value={codexArgs} onInput={e=>setCodexArgs(e.currentTarget.value)} /></label><label class="check"><input type="checkbox" checked={draft.reconcile_external_history} onChange={e=>change('reconcile_external_history',e.currentTarget.checked)} /> Reconcile native history</label></section>
      <section><h3>Input</h3><label class="check"><input type="checkbox" checked={draft.middle_click_paste} onChange={e=>change('middle_click_paste',e.currentTarget.checked)} /> Middle-click paste</label><label class="check"><input type="checkbox" checked={draft.broadcast_default} onChange={e=>change('broadcast_default',e.currentTarget.checked)} /> Broadcast enabled by default</label><label>Keybindings JSON<textarea value={bindings} onInput={e=>setBindings(e.currentTarget.value)} /></label></section>
      <section><h3>Git and history</h3><label>Git poll seconds<input type="number" step=".25" value={draft.git_poll_seconds} onInput={e=>change('git_poll_seconds',Number(e.currentTarget.value))} /></label></section>
      {projectConfig&&<section><h3>Current project</h3><p>{projectConfig.project.root}</p><p aria-live="polite">.swe-mux/config.toml: {projectConfig.status}{projectConfig.error?` · ${projectConfig.error}`:''}</p><label>Friendly project label<input value={projectValues.project_label||''} onInput={e=>setProjectValues(values=>({...values,project_label:e.currentTarget.value||undefined}))} /></label><label>Project default directory<input value={projectValues.default_cwd||''} placeholder="relative to project root" onInput={e=>setProjectValues(values=>({...values,default_cwd:e.currentTarget.value||undefined}))} /></label><label>Project default shell profile<select value={projectValues.default_shell_profile||''} onChange={e=>setProjectValues(values=>({...values,default_shell_profile:e.currentTarget.value||undefined}))}><option value="">Use space/global default</option>{draft.shell_profiles.filter(profile=>profile.enabled).map(profile=><option value={profile.id}>{profile.label}</option>)}</select></label><label class="check"><input type="checkbox" checked={projectValues.notes_enabled!==false} onChange={e=>setProjectValues(values=>({...values,notes_enabled:e.currentTarget.checked}))} /> Enable project notes</label><p>Opening a project never creates this folder. The first explicit Save writes it atomically.</p></section>}
      <section><h3>Usage analytics</h3><p>Settings control collection. The dashboard is where you inspect totals, days, models, provider state, and refresh results.</p><div class="theme-actions"><button class="primary" onClick={onOpenUsage}>Open usage dashboard</button><button disabled={!config?.ccusage_enabled || usage?.refreshing || usageRefreshMessage.startsWith('Refreshing')} onClick={()=>void refreshUsage()}>{usageRefreshMessage.startsWith('Refreshing')?'Refreshing…':'Refresh now'}</button><button onClick={()=>void clearUsage()}>Clear cache</button></div><p class={usageRefreshMessage.startsWith('Refresh failed')?'settings-inline-error':''} aria-live="polite">{usageRefreshMessage || (usage ? Object.entries(usage.states).map(([provider,state])=>`${provider}: ${state.status}${state.error?` (${state.error})`:''}`).join(' · ') : 'usage status unavailable')}</p>{draft.ccusage_enabled&&!config?.ccusage_enabled&&<p>Save these settings before refreshing.</p>}<label class="check"><input type="checkbox" checked={draft.ccusage_enabled} onChange={e=>change('ccusage_enabled',e.currentTarget.checked)} /> Enable optional ccusage refresh</label><label>Background refresh minutes (0 = manual only)<input type="number" min="0" max="1440" value={draft.ccusage_refresh_minutes} onInput={e=>change('ccusage_refresh_minutes',Number(e.currentTarget.value))} /></label><label>Claude command (one argument per line)<textarea value={draft.ccusage_claude_command.join('\n')} onInput={e=>change('ccusage_claude_command',e.currentTarget.value.split('\n').filter(Boolean))} /></label><label>Codex command (one argument per line)<textarea value={draft.ccusage_codex_command.join('\n')} onInput={e=>change('ccusage_codex_command',e.currentTarget.value.split('\n').filter(Boolean))} /></label><p>Both providers use one unified ccusage installation. swe-mux never downloads or updates it implicitly.</p><label>Required install command<input readonly value={usage?.install_command||'npm install -g ccusage@20.0.17'} onFocus={event=>event.currentTarget.select()} /></label><button onClick={()=>void navigator.clipboard.writeText(usage?.install_command||'npm install -g ccusage@20.0.17')}>Copy install command</button></section>
      <section><h3>Hooks and notifications</h3><label>hooks.toml<textarea value={hooks} onInput={e=>setHooks(e.currentTarget.value)} /></label><p aria-live="polite">Hook engine: {hookDiagnostic.status}{hookDiagnostic.rules!==undefined?` · ${hookDiagnostic.rules} rules`:''}{hookDiagnostic.error?` · ${hookDiagnostic.error}`:''}</p></section>
      <section><h3>Remote and security</h3><label class="check"><input type="checkbox" checked={draft.tailnet_enabled} onChange={event=>change('tailnet_enabled',event.currentTarget.checked)} /> Listen directly on the detected Tailscale IPv4 address</label><p>Changing the listener requires a daemon restart. swe-mux binds localhost plus the specific Tailscale address—never every LAN interface.</p><dl><dt>Local URL</dt><dd>{remote?.listen_url||`http://${draft.host}:${draft.port}`}</dd><dt>Direct tailnet</dt><dd>{remote?.direct_available?'active':draft.tailnet_enabled?'Tailscale address unavailable':'disabled'}</dd>{remote?.tailnet_urls.map(url=><Fragment key={url}><dt>Tailnet URL</dt><dd><a href={url} target="_blank" rel="noreferrer">{url}</a></dd></Fragment>)}</dl><p>Direct tailnet HTTP is encrypted in transit by Tailscale, but browsers may restrict secure-context clipboard APIs. Normal terminal input remains available.</p><strong>Optional HTTPS with Tailscale Serve</strong><p>{remote?.diagnostic||'Checking Tailscale Serve…'}</p>{remote?.serve_url&&<p><a href={remote.serve_url} target="_blank" rel="noreferrer">{remote.serve_url}</a></p>}{remote?.funnel_detected&&<p class="settings-inline-error">Tailscale Funnel appears enabled. Public ingress is unsupported; use direct tailnet access or tailnet-only Serve.</p>}<label>Optional Serve command<input readonly value={remote?.setup_command||`tailscale serve --bg http://127.0.0.1:${draft.port}`} onFocus={event=>event.currentTarget.select()} /></label><div class="theme-actions"><button onClick={()=>void navigator.clipboard.writeText(remote?.setup_command||`tailscale serve --bg http://127.0.0.1:${draft.port}`)}>Copy Serve command</button><button onClick={()=>void api<RemoteStatus>('GET','/api/remote/status').then(setRemote)}>Recheck</button></div><p>No swe-mux login is used. Tailscale access policy controls which tailnet devices can connect.</p></section>
      <section><h3>Appearance</h3><label>Theme<select value={draft.theme} onChange={e=>{const value=e.currentTarget.value as ThemeName;change('theme',value);applyTheme(value)}}><option value="dark">Dark</option><option value="light">Light</option><option value="system">System</option><option value="solarized-dark">Solarized Dark</option><option value="tokyo-night">Tokyo Night</option><option value="custom">Custom</option></select></label>{draft.theme==='custom' && <div class="theme-tokens">{Object.entries(draft.custom_theme).map(([key,value])=><label>{key}<input value={value} onInput={e=>{const custom={...draft.custom_theme,[key]:e.currentTarget.value};change('custom_theme',custom);configureCustomTheme(custom);applyTheme('custom')}} /></label>)}</div>}<input class="file-input" ref={themeFile} type="file" accept="application/json" onChange={e=>void importTheme(e.currentTarget.files?.[0])} /><div class="theme-actions"><button onClick={()=>themeFile.current?.click()}>Import theme</button><button onClick={exportTheme}>Export theme</button></div><p>One monospace font token is shared by all interface and terminal chrome.</p></section>
    </main>
    <footer><span aria-live="polite">{status}</span><button onClick={()=>void api('POST','/api/reveal',{path:draft.data_dir})}>Reveal config directory</button><button onClick={exportConfig}>Export sanitized</button><button onClick={()=>void reset()}>Restore defaults</button><button onClick={()=>{if(config)applyTheme(config.theme);onClose()}}>Cancel</button><button class="primary" onClick={()=>void save()}>Save</button></footer>
  </section></div>
}
