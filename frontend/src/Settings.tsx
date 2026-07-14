import { Fragment } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { displayChord } from './commands'
import { keyChord } from './keys'
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
type KeybindingCommand = {id:string;label:string;category:string}
type KeybindingPolicy = {browser_reserved:string[];terminal_reserved:string[];rules:string[]}
type KeybindingsResponse = {
  bindings:Record<string,string>;defaults:Record<string,string>;commands:KeybindingCommand[]
  policy:KeybindingPolicy;rejected:Record<string,string>
}

const settingsTabs = [
  {id:'general',label:'General'},
  {id:'terminals',label:'Terminals'},
  {id:'workspace',label:'Spaces + project'},
  {id:'agents',label:'Agents'},
  {id:'input',label:'Input'},
  {id:'usage',label:'Usage'},
  {id:'hooks',label:'Hooks'},
  {id:'remote',label:'Remote'},
  {id:'appearance',label:'Appearance'},
] as const
type SettingsTab = typeof settingsTabs[number]['id']
const tabForSection = (section:string):SettingsTab => ({
  Terminals:'terminals','Space defaults':'workspace','Current project':'workspace',
  Agents:'agents',Input:'input','Git and history':'workspace','Usage analytics':'usage',
  'Hooks and notifications':'hooks','Remote and security':'remote',Appearance:'appearance',
}[section] as SettingsTab|undefined)||'general'

export function Settings({ onClose, onOpenUsage, cwd, initialSection='General' }: { onClose: () => void; onOpenUsage?:() => void; cwd?:string; initialSection?:string }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [draft, setDraft] = useState<Config | null>(null)
  const [hooks, setHooks] = useState('')
  const [bindings, setBindings] = useState<Record<string,string>>({})
  const [bindingDefaults, setBindingDefaults] = useState<Record<string,string>>({})
  const [bindingCommands, setBindingCommands] = useState<KeybindingCommand[]>([])
  const [bindingPolicy, setBindingPolicy] = useState<KeybindingPolicy>({browser_reserved:[],terminal_reserved:[],rules:[]})
  const [capturingCommand, setCapturingCommand] = useState<string|null>(null)
  const [bindingError, setBindingError] = useState('')
  const [claudeArgs, setClaudeArgs] = useState('[]')
  const [codexArgs, setCodexArgs] = useState('[]')
  const [detectedProfiles, setDetectedProfiles] = useState<ShellProfile[]>([])
  const [spaceDefaults, setSpaceDefaults] = useState<Space[]>([])
  const [spaceBaseline,setSpaceBaseline]=useState<Space[]>([])
  const [hookDiagnostic, setHookDiagnostic] = useState<{status:string;error?:string;rules?:number}>({status:'loading'})
  const [usage, setUsage] = useState<UsageStatus | null>(null)
  const [usageRefreshMessage, setUsageRefreshMessage] = useState('')
  const [remote, setRemote] = useState<RemoteStatus | null>(null)
  const [projectConfig, setProjectConfig] = useState<ProjectConfig | null>(null)
  const [projectValues, setProjectValues] = useState<ProjectConfig['values']>({})
  const [status, setStatus] = useState('loading…')
  const [errors, setErrors] = useState<Record<string,string>>({})
  const [activeTab,setActiveTab] = useState<SettingsTab>(()=>tabForSection(initialSection))
  const [selectedProfileId,setSelectedProfileId] = useState<string|null>(null)
  const panel = useRef<HTMLElement>(null)
  const themeFile = useRef<HTMLInputElement>(null)
  const restoreFocus = useRef<HTMLElement | null>(document.activeElement as HTMLElement | null)

  useEffect(() => {
    api<RemoteStatus>('GET','/api/remote/status').then(setRemote).catch(()=>setRemote(null))
    Promise.all([
      api<Config>('GET','/api/config'),
      api<{text:string}>('GET','/api/hooks'),
      api<KeybindingsResponse>('GET','/api/keybindings'),
      api<{detected:ShellProfile[]}>('GET','/api/profiles'),
      api<Space[]>('GET','/api/spaces'),
      api<{diagnostic:{status:string;error?:string;rules?:number}}>('GET','/api/hooks/status'),
      api<UsageStatus>('GET','/api/usage'),
      cwd ? api<ProjectConfig>('GET',`/api/project/config?cwd=${encodeURIComponent(cwd)}`) : Promise.resolve(null),
    ]).then(([next, hookData, keyData, profileData, spaces, hookStatus, usageStatus, project]) => {
      setConfig(next); setDraft(next); setHooks(hookData.text)
      setClaudeArgs(JSON.stringify(next.claude_args)); setCodexArgs(JSON.stringify(next.codex_args))
      setBindings(keyData.bindings);setBindingDefaults(keyData.defaults||{})
      setBindingCommands(keyData.commands||[]);setBindingPolicy(keyData.policy||{browser_reserved:[],terminal_reserved:[],rules:[]})
      if(Object.keys(keyData.rejected||{}).length)setBindingError(`Ignored saved shortcuts · ${Object.entries(keyData.rejected).map(([chord,message])=>`${displayChord(chord)}: ${message}`).join(' · ')}`)
      setStatus('ready')
      setDetectedProfiles(profileData.detected); setSpaceDefaults(spaces);setSpaceBaseline(spaces)
      setHookDiagnostic(hookStatus.diagnostic); setUsage(usageStatus)
      setProjectConfig(project);setProjectValues(project?.values||{})
      configureCustomTheme(next.custom_theme); applyTheme(next.theme)
    }).catch(error => setStatus(error.message))
  }, [cwd])

  useEffect(() => {
    if (!draft) return
    setActiveTab(tabForSection(initialSection))
  },[draft,initialSection])

  useEffect(() => {
    if (!capturingCommand) return
    const capture = (event:KeyboardEvent) => {
      event.preventDefault();event.stopImmediatePropagation()
      if(event.key==='Escape'){setCapturingCommand(null);setBindingError('');return}
      if(event.repeat||['Control','Shift','Alt','Meta'].includes(event.key))return
      void captureBinding(event,capturingCommand)
    }
    window.addEventListener('keydown',capture,true)
    return()=>window.removeEventListener('keydown',capture,true)
  },[capturingCommand,bindings,bindingCommands])

  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape'&&!capturingCommand) { if (config) applyTheme(config.theme); onClose() }
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
  }, [config, onClose, capturingCommand])

  async function captureBinding(event:KeyboardEvent,commandId:string) {
    const chord=keyChord(event)
    const conflict=bindings[chord]
    if(conflict&&conflict!==commandId){
      const label=bindingCommands.find(command=>command.id===conflict)?.label||conflict
      setBindingError(`${displayChord(chord)} is already assigned to ${label}. Clear or change that shortcut first.`)
      return
    }
    const next=Object.fromEntries(Object.entries(bindings).filter(([,assigned])=>assigned!==commandId))
    next[chord]=commandId
    try {
      await api('PUT','/api/keybindings?validate=1',{bindings:next})
      setBindings(next);setCapturingCommand(null);setBindingError('')
    } catch(error) {
      const typed=error as Error&{fields?:Record<string,string>}
      setBindingError(typed.fields?.[chord]||typed.message)
    }
  }

  const bindingForCommand=(commandId:string)=>Object.entries(bindings).find(([,assigned])=>assigned===commandId)?.[0]
  const clearBinding=(commandId:string)=>{
    setBindings(current=>Object.fromEntries(Object.entries(current).filter(([,assigned])=>assigned!==commandId)))
    if(capturingCommand===commandId)setCapturingCommand(null)
    setBindingError('')
  }

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
      const spacePatch=(space:Space)=>{
        const original=spaceBaseline.find(item=>item.id===space.id)
        const values:Record<string,unknown>={default_profile_id:space.default_profile_id||null,default_cwd:space.default_cwd||null}
        return values
      }
      await Promise.all([
        api('PUT','/api/hooks?validate=1',{text:hooks}),
        api('PUT','/api/keybindings?validate=1',{bindings}),
      ])
      const [next] = await Promise.all([
        api<Config & {hot_applied:string[];restart_required:string[]}>('PATCH','/api/config',body),
        api('PUT','/api/hooks',{text:hooks}),
        api('PUT','/api/keybindings',{bindings}),
        ...spaceDefaults.map(space=>api('PATCH',`/api/spaces/${space.id}`,spacePatch(space))),
        ...(projectConfig&&cwd?[api<ProjectConfig>('PUT','/api/project/config',{cwd,values:projectValues,revision:projectConfig.revision}).then(result=>{setProjectConfig(result);setProjectValues(result.values)})]:[]),
      ])
      setConfig(next); setDraft(next); setErrors({})
      const refreshedSpaces=await api<Space[]>('GET','/api/spaces')
      setSpaceDefaults(refreshedSpaces);setSpaceBaseline(refreshedSpaces)
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
  const updateProfile = (index:number, changes:Partial<ShellProfile>) => {
    const previous=draft!.shell_profiles[index]
    change('shell_profiles',draft!.shell_profiles.map((profile,itemIndex)=>itemIndex===index?{...profile,...changes}:profile))
    if(changes.id&&selectedProfileId===previous.id)setSelectedProfileId(changes.id)
  }
  const addProfile = (source?:ShellProfile) => {
    const base = source || {id:'shell',label:'New shell',executable:'',args:[],env:{},platforms:['windows'],cwd_strategy:'native',marker:'sh',capabilities:['interactive'],cwd_integration:false,enabled:true}
    let id=base.id, suffix=2
    while(draft!.shell_profiles.some(profile=>profile.id===id)) id=`${base.id}-${suffix++}`
    change('shell_profiles',[...draft!.shell_profiles,{...base,id,args:[...base.args],env:{...base.env},capabilities:[...base.capabilities]}])
    setSelectedProfileId(id)
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
  const selectedProfileIndex=draft?.shell_profiles.findIndex(profile=>profile.id===selectedProfileId)??-1
  const selectedProfile=selectedProfileIndex>=0?draft?.shell_profiles[selectedProfileIndex]:undefined
  if (!draft) return <div class="settings-layer"><section class="settings-panel">{status}</section></div>
  return <div class="settings-layer"><section class="settings-panel" ref={panel} role="dialog" aria-modal="true" aria-label="Settings">
    <header><div><span>CONFIG::V5</span><h2>Settings</h2></div><button onClick={() => { if(config) applyTheme(config.theme); onClose() }}>×</button></header>
    <main class="settings-body">
      <nav class="settings-tabs" role="tablist" aria-label="Settings sections">
        {settingsTabs.map(tab=><button role="tab" aria-selected={activeTab===tab.id} class={activeTab===tab.id?'active':''} onClick={()=>setActiveTab(tab.id)}>{tab.label}</button>)}
      </nav>
      <div class="settings-content">
        {Object.keys(errors).length > 0 && <section class="settings-errors" aria-live="assertive"><h3>Validation errors</h3>{Object.entries(errors).map(([field,message])=><p><strong>{field}</strong> — {message}</p>)}</section>}

        {activeTab==='general'&&<section><h3>General</h3>
          <label>Startup directory<input value={draft.startup_cwd} onInput={e=>change('startup_cwd',e.currentTarget.value)} /></label>
          <label>Default backend<select value={draft.default_backend} onChange={e=>change('default_backend',e.currentTarget.value)}><option value="shell">Shell</option><option value="claude">Claude</option><option value="codex">Codex</option></select></label>
          <label>Scrollback bytes<input type="number" value={draft.scrollback_bytes} onInput={e=>change('scrollback_bytes',Number(e.currentTarget.value))} /></label>
          <label>History limit<input type="number" value={draft.history_limit} onInput={e=>change('history_limit',Number(e.currentTarget.value))} /></label>
        </section>}

        {activeTab==='terminals'&&<section class="profile-settings"><h3>Terminals</h3>
          <label>Global default profile<select value={draft.default_shell_profile} onChange={e=>change('default_shell_profile',e.currentTarget.value)}>{draft.shell_profiles.filter(profile=>profile.enabled).map(profile=><option value={profile.id}>{profile.label}</option>)}</select></label>
          <div class="profile-browser">
            <div class="profile-index" aria-label="Configured terminal profiles">
              {draft.shell_profiles.map(profile=><button class={selectedProfileId===profile.id?'active':''} onClick={()=>setSelectedProfileId(selectedProfileId===profile.id?null:profile.id)}><span>{profile.marker}</span><strong>{profile.label}</strong><small>{profile.id} · {profile.enabled?'on':'off'}</small></button>)}
              <div class="profile-index-actions"><button onClick={()=>addProfile()}>+ add profile</button><button onClick={restoreDetected}>restore detected</button></div>
            </div>
            {selectedProfile&&<article class="profile-editor">
              <header><strong>PROFILE::{selectedProfile.label}</strong><button aria-label="Collapse terminal profile" onClick={()=>setSelectedProfileId(null)}>×</button></header>
              <label>Profile ID<input value={selectedProfile.id} onInput={e=>updateProfile(selectedProfileIndex,{id:e.currentTarget.value})}/></label>
              <label>Label<input value={selectedProfile.label} onInput={e=>updateProfile(selectedProfileIndex,{label:e.currentTarget.value})}/></label>
              <label>Executable<input value={selectedProfile.executable} onInput={e=>updateProfile(selectedProfileIndex,{executable:e.currentTarget.value})}/></label>
              <label>Arguments<textarea value={selectedProfile.args.join('\n')} onInput={e=>updateProfile(selectedProfileIndex,{args:e.currentTarget.value.split('\n').filter(Boolean)})}/></label>
              <label>Environment<textarea value={Object.entries(selectedProfile.env).map(([key,value])=>`${key}=${value}`).join('\n')} onInput={e=>updateProfile(selectedProfileIndex,{env:Object.fromEntries(e.currentTarget.value.split('\n').filter(line=>line.includes('=')).map(line=>{const at=line.indexOf('=');return [line.slice(0,at),line.slice(at+1)]}))})}/></label>
              <label>Marker<input value={selectedProfile.marker} onInput={e=>updateProfile(selectedProfileIndex,{marker:e.currentTarget.value})}/></label>
              <label>Cwd strategy<select value={selectedProfile.cwd_strategy} onChange={e=>updateProfile(selectedProfileIndex,{cwd_strategy:e.currentTarget.value as ShellProfile['cwd_strategy']})}><option value="native">native</option><option value="home">home</option><option value="wsl">wsl</option></select></label>
              <label class="check"><span>Live cwd telemetry</span><input type="checkbox" checked={selectedProfile.cwd_integration} onChange={e=>updateProfile(selectedProfileIndex,{cwd_integration:e.currentTarget.checked})}/></label>
              <label>Capabilities<input value={selectedProfile.capabilities.join(', ')} onInput={e=>updateProfile(selectedProfileIndex,{capabilities:e.currentTarget.value.split(',').map(item=>item.trim()).filter(Boolean)})}/></label>
              <div class="profile-editor-actions"><button onClick={()=>moveProfile(selectedProfileIndex,-1)}>move up</button><button onClick={()=>moveProfile(selectedProfileIndex,1)}>move down</button><button onClick={()=>addProfile(selectedProfile)}>duplicate</button><button onClick={()=>updateProfile(selectedProfileIndex,{enabled:!selectedProfile.enabled})}>{selectedProfile.enabled?'disable':'enable'}</button><button class="danger" disabled={draft.shell_profiles.length===1} onClick={()=>{change('shell_profiles',draft.shell_profiles.filter((_,index)=>index!==selectedProfileIndex));setSelectedProfileId(null)}}>remove</button></div>
              <small>swe-mux wraps this shell process only and never edits your shell profile files.</small>
            </article>}
            {!selectedProfile&&<div class="profile-placeholder"><span>TERMINAL::PROFILES</span><strong>Select a profile to inspect or edit it.</strong><p>Nothing is expanded until you choose one.</p></div>}
          </div>
        </section>}

        {activeTab==='workspace'&&<Fragment>
          <section><h3>Space defaults</h3><p>A space is a workflow container, not a project. New terminals use its configured directory.</p>{spaceDefaults.map((space,index)=><article class="space-default"><strong>{space.name}</strong><label>Profile<select value={space.default_profile_id||''} onChange={e=>setSpaceDefaults(items=>items.map((item,itemIndex)=>itemIndex===index?{...item,default_profile_id:e.currentTarget.value||undefined}:item))}><option value="">Use global default</option>{draft.shell_profiles.filter(profile=>profile.enabled).map(profile=><option value={profile.id}>{profile.label}</option>)}</select></label><label>New-terminal directory<input value={space.default_cwd||''} placeholder="Use global startup directory" onInput={e=>setSpaceDefaults(items=>items.map((item,itemIndex)=>itemIndex===index?{...item,default_cwd:e.currentTarget.value||undefined}:item))}/></label></article>)}</section>
          <section><h3>Git and history</h3><label>Git poll seconds<input type="number" step=".25" value={draft.git_poll_seconds} onInput={e=>change('git_poll_seconds',Number(e.currentTarget.value))} /></label></section>
          {projectConfig&&<section><h3>Current project</h3><p>{projectConfig.project.root}</p><p aria-live="polite">.swe-mux/config.toml: {projectConfig.status}{projectConfig.error?` · ${projectConfig.error}`:''}</p><label>Friendly project label<input value={projectValues.project_label||''} onInput={e=>setProjectValues(values=>({...values,project_label:e.currentTarget.value||undefined}))} /></label><label>Project default directory<input value={projectValues.default_cwd||''} placeholder="relative to project root" onInput={e=>setProjectValues(values=>({...values,default_cwd:e.currentTarget.value||undefined}))} /></label><label>Project default shell profile<select value={projectValues.default_shell_profile||''} onChange={e=>setProjectValues(values=>({...values,default_shell_profile:e.currentTarget.value||undefined}))}><option value="">Use space/global default</option>{draft.shell_profiles.filter(profile=>profile.enabled).map(profile=><option value={profile.id}>{profile.label}</option>)}</select></label><label class="check"><span>Enable project notes</span><input type="checkbox" checked={projectValues.notes_enabled!==false} onChange={e=>setProjectValues(values=>({...values,notes_enabled:e.currentTarget.checked}))} /></label><p>Opening a project never creates this folder. The first explicit Save writes it atomically.</p></section>}
        </Fragment>}

        {activeTab==='agents'&&<section><h3>Agents</h3><label>Claude executable<input value={draft.claude_exe} onInput={e=>change('claude_exe',e.currentTarget.value)} /></label><label>Claude default args<input value={claudeArgs} onInput={e=>setClaudeArgs(e.currentTarget.value)} /></label><label>Codex executable<input value={draft.codex_exe} onInput={e=>change('codex_exe',e.currentTarget.value)} /></label><label>Codex default args<input value={codexArgs} onInput={e=>setCodexArgs(e.currentTarget.value)} /></label><label class="check"><span>Reconcile native history</span><input type="checkbox" checked={draft.reconcile_external_history} onChange={e=>change('reconcile_external_history',e.currentTarget.checked)} /></label></section>}

        {activeTab==='input'&&<section class="input-settings"><h3>Input</h3>
          <label class="check"><span>Middle-click paste</span><input type="checkbox" checked={draft.middle_click_paste} onChange={e=>change('middle_click_paste',e.currentTarget.checked)} /></label>
          <label class="check"><span>Broadcast by default</span><input type="checkbox" checked={draft.broadcast_default} onChange={e=>change('broadcast_default',e.currentTarget.checked)} /></label>
          <div class="keybinding-heading"><div><strong>KEYBOARD::SHORTCUTS</strong><p>Click a command, then press the new shortcut. Changes apply when Settings is saved.</p></div><button onClick={()=>{setBindings({...bindingDefaults});setCapturingCommand(null);setBindingError('')}}>Restore shortcut defaults</button></div>
          {capturingCommand&&<div class="keybinding-capture" role="status"><span>PRESS KEYS FOR</span><strong>{bindingCommands.find(command=>command.id===capturingCommand)?.label||capturingCommand}</strong><button onClick={()=>{setCapturingCommand(null);setBindingError('')}}>Cancel</button></div>}
          {bindingError&&<p class="keybinding-error" role="alert">{bindingError}</p>}
          <div class="keybinding-list">
            {[...new Set(bindingCommands.map(command=>command.category))].map(category=><section class="keybinding-group" aria-label={`${category} shortcuts`}><h4>{category}</h4>{bindingCommands.filter(command=>command.category===category).map(command=>{const chord=bindingForCommand(command.id);return <article class={capturingCommand===command.id?'capturing':''}><button class="keybinding-command" onClick={()=>{setCapturingCommand(command.id);setBindingError('')}} title={command.id}><span>{command.label}</span><small>{command.id}</small></button><button class="keybinding-chord" onClick={()=>{setCapturingCommand(command.id);setBindingError('')}} aria-label={`Set shortcut for ${command.label}`}><kbd>{chord?displayChord(chord):'not set'}</kbd></button><button class="keybinding-clear" disabled={!chord} onClick={()=>clearBinding(command.id)} aria-label={`Clear shortcut for ${command.label}`}>×</button></article>})}</section>)}
          </div>
          <details class="keybinding-policy"><summary>Reserved shortcut policy</summary><ul>{bindingPolicy.rules.map(rule=><li>{rule}</li>)}</ul><div><strong>BROWSER</strong>{bindingPolicy.browser_reserved.map(chord=><kbd>{displayChord(chord)}</kbd>)}</div><div><strong>TERMINAL</strong>{bindingPolicy.terminal_reserved.map(chord=><kbd>{displayChord(chord)}</kbd>)}</div></details>
        </section>}

        {activeTab==='usage'&&<section><h3>Usage analytics</h3><p>Daily Claude and Codex data is cached locally. The dashboard derives timelines and aggregate breakdowns from that cache.</p><div class="theme-actions"><button class="primary" onClick={onOpenUsage}>Open usage dashboard</button><button disabled={!config?.ccusage_enabled || usage?.refreshing || usageRefreshMessage.startsWith('Refreshing')} onClick={()=>void refreshUsage()}>{usageRefreshMessage.startsWith('Refreshing')?'Refreshing…':'Refresh now'}</button><button onClick={()=>void clearUsage()}>Clear cache</button></div><p class={usageRefreshMessage.startsWith('Refresh failed')?'settings-inline-error':''} aria-live="polite">{usageRefreshMessage || (usage ? Object.entries(usage.states).map(([provider,state])=>`${provider}: ${state.status}${state.error?` (${state.error})`:''}`).join(' · ') : 'usage status unavailable')}</p>{draft.ccusage_enabled&&!config?.ccusage_enabled&&<p>Save these settings before refreshing.</p>}<label class="check"><span>Enable ccusage refresh</span><input type="checkbox" checked={draft.ccusage_enabled} onChange={e=>change('ccusage_enabled',e.currentTarget.checked)} /></label><label>Background refresh minutes<input type="number" min="0" max="1440" value={draft.ccusage_refresh_minutes} onInput={e=>change('ccusage_refresh_minutes',Number(e.currentTarget.value))} /></label><label>Install/update command<input readonly value={usage?.install_command||'npm install -g ccusage@latest'} onFocus={event=>event.currentTarget.select()} /></label><button onClick={()=>void navigator.clipboard.writeText(usage?.install_command||'npm install -g ccusage@latest')}>Copy install command</button><p>The `latest` tag is resolved when you install or update. Refreshes use the installed unified executable and never download code in the background.</p><details class="settings-advanced"><summary>Advanced command overrides</summary><label>Claude command<textarea value={draft.ccusage_claude_command.join('\n')} onInput={e=>change('ccusage_claude_command',e.currentTarget.value.split('\n').filter(Boolean))} /></label><label>Codex command<textarea value={draft.ccusage_codex_command.join('\n')} onInput={e=>change('ccusage_codex_command',e.currentTarget.value.split('\n').filter(Boolean))} /></label></details></section>}

        {activeTab==='hooks'&&<section><h3>Hooks and notifications</h3><label>hooks.toml<textarea value={hooks} onInput={e=>setHooks(e.currentTarget.value)} /></label><p aria-live="polite">Hook engine: {hookDiagnostic.status}{hookDiagnostic.rules!==undefined?` · ${hookDiagnostic.rules} rules`:''}{hookDiagnostic.error?` · ${hookDiagnostic.error}`:''}</p></section>}

        {activeTab==='remote'&&<section><h3>Remote and security</h3><label class="check"><span>Listen on Tailscale IPv4</span><input type="checkbox" checked={draft.tailnet_enabled} onChange={event=>change('tailnet_enabled',event.currentTarget.checked)} /></label><p>Changing the listener requires a daemon restart. swe-mux binds localhost plus the specific Tailscale address—never every LAN interface.</p><dl><dt>Local URL</dt><dd>{remote?.listen_url||`http://${draft.host}:${draft.port}`}</dd><dt>Direct tailnet</dt><dd>{remote?.direct_available?'active':draft.tailnet_enabled?'Tailscale address unavailable':'disabled'}</dd>{remote?.tailnet_urls.map(url=><Fragment key={url}><dt>Tailnet URL</dt><dd><a href={url} target="_blank" rel="noreferrer">{url}</a></dd></Fragment>)}</dl><p>Direct tailnet HTTP is encrypted in transit by Tailscale, but browsers may restrict secure-context clipboard APIs. Normal terminal input remains available.</p><strong>Optional HTTPS with Tailscale Serve</strong><p>{remote?.diagnostic||'Checking Tailscale Serve…'}</p>{remote?.serve_url&&<p><a href={remote.serve_url} target="_blank" rel="noreferrer">{remote.serve_url}</a></p>}{remote?.funnel_detected&&<p class="settings-inline-error">Tailscale Funnel appears enabled. Public ingress is unsupported; use direct tailnet access or tailnet-only Serve.</p>}<label>Optional Serve command<input readonly value={remote?.setup_command||`tailscale serve --bg http://127.0.0.1:${draft.port}`} onFocus={event=>event.currentTarget.select()} /></label><div class="theme-actions"><button onClick={()=>void navigator.clipboard.writeText(remote?.setup_command||`tailscale serve --bg http://127.0.0.1:${draft.port}`)}>Copy Serve command</button><button onClick={()=>void api<RemoteStatus>('GET','/api/remote/status').then(setRemote)}>Recheck</button></div><p>No swe-mux login is used. Tailscale access policy controls which tailnet devices can connect.</p></section>}

        {activeTab==='appearance'&&<section><h3>Appearance</h3><label>Theme<select value={draft.theme} onChange={e=>{const value=e.currentTarget.value as ThemeName;change('theme',value);applyTheme(value)}}><option value="dark">Dark</option><option value="light">Light</option><option value="system">System</option><option value="solarized-dark">Solarized Dark</option><option value="tokyo-night">Tokyo Night</option><option value="custom">Custom</option></select></label>{draft.theme==='custom' && <div class="theme-tokens">{Object.entries(draft.custom_theme).map(([key,value])=><label>{key}<input value={value} onInput={e=>{const custom={...draft.custom_theme,[key]:e.currentTarget.value};change('custom_theme',custom);configureCustomTheme(custom);applyTheme('custom')}} /></label>)}</div>}<input class="file-input" ref={themeFile} type="file" accept="application/json" onChange={e=>void importTheme(e.currentTarget.files?.[0])} /><div class="theme-actions"><button onClick={()=>themeFile.current?.click()}>Import theme</button><button onClick={exportTheme}>Export theme</button></div><p>Settings, menus, controls, and terminal chrome use the same monospace font token.</p></section>}
      </div>
    </main>
    <footer><span aria-live="polite">{status}</span><button onClick={()=>void api('POST','/api/reveal',{path:draft.data_dir})}>Reveal config directory</button><button onClick={exportConfig}>Export sanitized</button><button onClick={()=>void reset()}>Restore defaults</button><button onClick={()=>{if(config)applyTheme(config.theme);onClose()}}>Cancel</button><button class="primary" onClick={()=>void save()}>Save</button></footer>
  </section></div>
}
