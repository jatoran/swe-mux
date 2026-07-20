import { Fragment } from 'preact'
import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { displayChord } from './commands'
import { keyChord } from './keys'
import { AccountSettings } from './ProviderAccounts'
import { NotificationSoundSettings } from './NotificationSoundSettings'
import { comparableProjectDefaults, normalizeIgnorePatterns, parseIgnorePatternDraft, sameDraftValue } from './settingsDraft'
import { applyTheme, configureCustomTheme, type CustomTheme, type ThemeName } from './theme'
import type { ShellProfile, Project } from './types'
import { sttCapability } from './voice'

type Config = {
  revision:number; host:string; port:number; data_dir:string; requires_auth:boolean; access_mode:string; tailnet_enabled:boolean
  startup_cwd:string; default_backend:string; shell_exe:string; claude_exe:string
  codex_exe:string; scrollback_bytes:number; history_limit:number
  claude_args:string[]; codex_args:string[]
  git_poll_seconds:number; reconcile_external_history:boolean; theme:ThemeName
  process_poll_seconds:number;process_orphan_grace_seconds:number;process_evidence_retention_days:number
  operational_telemetry_retention_days:number;provider_quota_poll_minutes:number
  provider_quota_turn_refresh_enabled:boolean;provider_quota_turn_refresh_min_minutes:number
  middle_click_paste:boolean; broadcast_default:boolean
  mobile_vertical_drag:'smart'|'terminal'|'application'|'disabled'
  mobile_scroll_direction:'natural'|'wheel';mobile_scroll_sensitivity:number
  mobile_long_press:'context_menu'|'disabled'
  ccusage_enabled:boolean; ccusage_refresh_minutes:number
  ccusage_claude_command:string[]; ccusage_codex_command:string[]
  custom_theme:CustomTheme
  default_shell_profile:string; shell_profiles:ShellProfile[]
  project_ignore_patterns:string[]
  automation_enabled:boolean;automation_retention_days:number;automation_concurrency:number
  automation_queue_size:number;automation_max_input_tokens:number;automation_max_output_tokens:number
  automation_daily_token_budget:number;automation_daily_budget_usd:number;automation_rule_daily_token_budget:number
  automation_rule_daily_budget_usd:number;automation_hourly_call_cap:number
  automation_rule_hourly_call_cap:number;openrouter_cheap_model:string
  openrouter_standard_model:string;openrouter_request_timeout_seconds:number
  observer_titler_enabled:boolean;observer_summarizer_enabled:boolean
  phase7_observers_enabled:boolean
  tts_enabled:boolean;tts_default_mode:'off'|'on_demand'|'auto';tts_content:'summary'|'verbatim'
  tts_engine:'edge'|'sapi';tts_edge_voice:string;tts_edge_rate:string;tts_edge_pitch:string
  tts_soften_stops:boolean;tts_sapi_voice:string;tts_sapi_rate:number
  tts_summary_model:string;tts_summary_max_tokens:number;tts_verbatim_max_chars:number
  tts_daily_budget_usd:number;tts_cache_mb:number;stt_enabled:boolean
}
type VoiceStatusInfo = {
  enabled:boolean;engine:string;engine_available:boolean;diagnostic?:string|null;voice:string
  summary_model:string;spend_today:{tokens:number;cost_usd:number};daily_budget_usd:number
  cache_bytes:number;cache_limit_bytes:number;clip_count:number;stt_enabled:boolean
}
const EDGE_VOICE_SUGGESTIONS = [
  'en-AU-NatashaNeural','en-AU-WilliamNeural','en-US-AndrewNeural','en-US-AriaNeural',
  'en-US-AvaNeural','en-US-GuyNeural','en-US-JennyNeural','en-GB-SoniaNeural','en-GB-RyanNeural',
]

type AutomationStatus={enabled:boolean;diagnostic?:string;rules:Array<{id:string;name:string;enabled:boolean;shadow:boolean;revision:string}>;queue:{size:number;capacity:number;dropped:number};legacy:{active:boolean;diagnostic?:string;migration:string};repository_rules:Array<{project_scope_id:string;path:string;valid:boolean;diagnostic?:string;execution:string}>}
type ProviderStatus={secret:{configured:boolean;source:string;persistent:boolean};models:{models:Array<{id:string;name:string}>;fetched_at?:number;error?:string;stale:boolean};origin:string;cheap_model:string;standard_model:string}

type UsageStatus = {
  enabled:boolean; refreshing:boolean; package:string; install_command:string
  states:Record<string,{status:string;error?:string;refreshed_at?:number}>
  cache?:{updated_at?:number;providers?:Record<string,{totals?:Record<string,number>}>}
}

type ProjectConfig = {
  project:{id:string;label:string;root:string};path:string;status:string;revision:string;error?:string
  values:{default_shell_profile?:string;preferred_backend?:'shell'|'claude'|'codex';prompt_library_scope?:'off'|'global'|'project'|'both';notification_sounds_enabled?:boolean;ignore_patterns?:string[]}
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
type CloseIntent = 'close'|'usage'|'automation'

const settingsTabs = [
  {id:'general',label:'General'},
  {id:'terminals',label:'Terminals'},
  {id:'workspace',label:'Projects'},
  {id:'notes',label:'Notes'},
  {id:'agents',label:'Agents'},
  {id:'accounts',label:'Accounts'},
  {id:'input',label:'Input'},
  {id:'usage',label:'Usage'},
  {id:'automation',label:'Automation'},
  {id:'notifications',label:'Notifications'},
  {id:'voice',label:'Voice'},
  {id:'remote',label:'Remote'},
  {id:'appearance',label:'Appearance'},
] as const
type SettingsTab = typeof settingsTabs[number]['id']
const tabForSection = (section:string):SettingsTab => ({
  Terminals:'terminals','Project defaults':'workspace','Current project':'workspace',
  Agents:'agents',Accounts:'accounts',Input:'input','Git and history':'workspace','Usage analytics':'usage',
  Notes:'notes',
  Automation:'automation','Hooks and notifications':'notifications',Notifications:'notifications',Voice:'voice','Remote and security':'remote',Appearance:'appearance',
}[section] as SettingsTab|undefined)||'general'

export function Settings({ onClose, onOpenUsage:openUsage, onOpenAutomation:openAutomation, cwd, initialSection='General' }: { onClose: () => void; onOpenUsage?:() => void;onOpenAutomation?:()=>void; cwd?:string; initialSection?:string }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [draft, setDraft] = useState<Config | null>(null)
  const [rules, setRules] = useState('version = 1\n')
  const [automation,setAutomation]=useState<AutomationStatus|null>(null)
  const [provider,setProvider]=useState<ProviderStatus|null>(null)
  const [openRouterKey,setOpenRouterKey]=useState('')
  const [providerMessage,setProviderMessage]=useState('')
  const [bindings, setBindings] = useState<Record<string,string>>({})
  const [bindingDefaults, setBindingDefaults] = useState<Record<string,string>>({})
  const [bindingCommands, setBindingCommands] = useState<KeybindingCommand[]>([])
  const [bindingPolicy, setBindingPolicy] = useState<KeybindingPolicy>({browser_reserved:[],terminal_reserved:[],rules:[]})
  const [capturingCommand, setCapturingCommand] = useState<string|null>(null)
  const [bindingError, setBindingError] = useState('')
  const [claudeArgs, setClaudeArgs] = useState('[]')
  const [codexArgs, setCodexArgs] = useState('[]')
  const [detectedProfiles, setDetectedProfiles] = useState<ShellProfile[]>([])
  const [projectDefaults, setProjectDefaults] = useState<Project[]>([])
  const [savedProjectDefaults, setSavedProjectDefaults] = useState<Project[]>([])
  const [usage, setUsage] = useState<UsageStatus | null>(null)
  const [voiceInfo, setVoiceInfo] = useState<VoiceStatusInfo | null>(null)
  const [usageRefreshMessage, setUsageRefreshMessage] = useState('')
  const [remote, setRemote] = useState<RemoteStatus | null>(null)
  const [projectConfig, setProjectConfig] = useState<ProjectConfig | null>(null)
  const [projectValues, setProjectValues] = useState<ProjectConfig['values']>({})
  const [savedProjectValues, setSavedProjectValues] = useState<ProjectConfig['values']>({})
  const [savedRules, setSavedRules] = useState('version = 1\n')
  const [savedBindings, setSavedBindings] = useState<Record<string,string>>({})
  const [status, setStatus] = useState('loading…')
  const [errors, setErrors] = useState<Record<string,string>>({})
  const [activeTab,setActiveTab] = useState<SettingsTab>(()=>tabForSection(initialSection))
  const [selectedProfileId,setSelectedProfileId] = useState<string|null>(null)
  const [closeIntent,setCloseIntent] = useState<CloseIntent|null>(null)
  const panel = useRef<HTMLElement>(null)
  const confirmPanel = useRef<HTMLElement>(null)
  const themeFile = useRef<HTMLInputElement>(null)
  const restoreFocus = useRef<HTMLElement | null>(document.activeElement as HTMLElement | null)

  useEffect(() => {
    api<RemoteStatus>('GET','/api/remote/status').then(setRemote).catch(()=>setRemote(null))
    api<VoiceStatusInfo>('GET','/api/voice').then(setVoiceInfo).catch(()=>setVoiceInfo(null))
    Promise.all([
      api<Config>('GET','/api/config'),
      api<{text:string}>('GET','/api/automation/rules'),
      api<KeybindingsResponse>('GET','/api/keybindings'),
      api<{detected:ShellProfile[]}>('GET','/api/profiles'),
      api<Project[]>('GET','/api/projects'),
      api<AutomationStatus>('GET','/api/automation'),
      api<ProviderStatus>('GET','/api/automation/provider'),
      api<UsageStatus>('GET','/api/usage'),
      cwd ? api<ProjectConfig>('GET',`/api/project/config?cwd=${encodeURIComponent(cwd)}`) : Promise.resolve(null),
    ]).then(([next, rulesData, keyData, profileData, projects, automationStatus,providerStatus, usageStatus, project]) => {
      setConfig(next); setDraft(next); setRules(rulesData.text);setSavedRules(rulesData.text)
      setClaudeArgs(JSON.stringify(next.claude_args)); setCodexArgs(JSON.stringify(next.codex_args))
      setBindings(keyData.bindings);setSavedBindings(keyData.bindings);setBindingDefaults(keyData.defaults||{})
      setBindingCommands(keyData.commands||[]);setBindingPolicy(keyData.policy||{browser_reserved:[],terminal_reserved:[],rules:[]})
      if(Object.keys(keyData.rejected||{}).length)setBindingError(`Ignored saved shortcuts · ${Object.entries(keyData.rejected).map(([chord,message])=>`${displayChord(chord)}: ${message}`).join(' · ')}`)
      setStatus('ready')
      setDetectedProfiles(profileData.detected); setProjectDefaults(projects);setSavedProjectDefaults(projects)
      setAutomation(automationStatus);setProvider(providerStatus); setUsage(usageStatus)
      setProjectConfig(project);setProjectValues(project?.values||{});setSavedProjectValues(project?.values||{})
      configureCustomTheme(next.custom_theme); applyTheme(next.theme)
    }).catch(error => setStatus(error.message))
  }, [cwd])

  useEffect(() => {
    setActiveTab(tabForSection(initialSection))
  },[initialSection])

  const dirty = useMemo(() => Boolean(config&&draft&&(
    !sameDraftValue(config,draft)
    ||claudeArgs!==JSON.stringify(config.claude_args)
    ||codexArgs!==JSON.stringify(config.codex_args)
    ||rules!==savedRules
    ||!sameDraftValue(bindings,savedBindings)
    ||!sameDraftValue(comparableProjectDefaults(projectDefaults),comparableProjectDefaults(savedProjectDefaults))
    ||!sameDraftValue(projectValues,savedProjectValues)
  )),[
    config,draft,claudeArgs,codexArgs,rules,savedRules,bindings,savedBindings,
    projectDefaults,savedProjectDefaults,projectValues,savedProjectValues,
  ])

  const leaveSettings = useCallback((intent:CloseIntent) => {
    setCloseIntent(null)
    if(intent==='usage'&&openUsage){openUsage();return}
    if(intent==='automation'&&openAutomation){openAutomation();return}
    onClose()
  },[onClose,openUsage,openAutomation])

  const requestClose = useCallback((intent:CloseIntent='close') => {
    if(dirty){setCloseIntent(intent);return}
    leaveSettings(intent)
  },[dirty,leaveSettings])
  const onOpenUsage=useCallback(()=>requestClose('usage'),[requestClose])
  const onOpenAutomation=useCallback(()=>requestClose('automation'),[requestClose])

  useEffect(() => {
    if(!closeIntent)return
    confirmPanel.current?.querySelector<HTMLElement>('button')?.focus()
  },[closeIntent])

  useEffect(() => () => restoreFocus.current?.focus(),[])

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
      if (event.key === 'Escape'&&!capturingCommand) {
        event.preventDefault()
        event.stopImmediatePropagation()
        if(closeIntent){setCloseIntent(null);return}
        requestClose()
      }
      const focusRoot=closeIntent?confirmPanel.current:panel.current
      if (event.key === 'Tab' && focusRoot) {
        const focusable = [...focusRoot.querySelectorAll<HTMLElement>('button,input,select,textarea')].filter(item => !item.hasAttribute('disabled'))
        if (!focusable.length) return
        const first = focusable[0], last = focusable[focusable.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    window.addEventListener('keydown', close, true)
    return () => window.removeEventListener('keydown', close, true)
  }, [requestClose,closeIntent,capturingCommand])

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
  const save = async ():Promise<boolean> => {
    if (!draft || !config) return false
    let savingDraft: Config
    try {
      savingDraft = {
        ...draft,
        claude_args:JSON.parse(claudeArgs),
        codex_args:JSON.parse(codexArgs),
        project_ignore_patterns:normalizeIgnorePatterns(draft.project_ignore_patterns),
      }
    } catch { setErrors({agent_args:'agent args must be JSON arrays of strings'}); return false }
    setStatus('saving…')
    const body: Record<string,unknown> = {_revision:config.revision}
    for (const key of Object.keys(savingDraft) as (keyof Config)[]) {
      if (!['revision','host','port','data_dir','requires_auth'].includes(key) && savingDraft[key] !== config[key]) body[key] = savingDraft[key]
    }
    try {
      const projectPatch=(project:Project):Record<string,unknown>=>({
        default_backend:project.default_backend||null,
        default_profile_id:project.default_profile_id||null,
      })
      const savingProjectValues:ProjectConfig['values']={
        ...projectValues,
        ...(projectValues.ignore_patterns
          ? {ignore_patterns:normalizeIgnorePatterns(projectValues.ignore_patterns)}
          : {}),
      }
      await Promise.all([
        api('PUT','/api/automation/rules?validate=1',{text:rules}),
        api('PUT','/api/keybindings?validate=1',{bindings}),
      ])
      const [next] = await Promise.all([
        api<Config & {hot_applied:string[];restart_required:string[]}>('PATCH','/api/config',body),
        api('PUT','/api/automation/rules',{text:rules}),
        api('PUT','/api/keybindings',{bindings}),
        ...projectDefaults.map(project=>api('PATCH',`/api/projects/${project.id}`,projectPatch(project))),
        ...(projectConfig&&cwd?[api<ProjectConfig>('PUT','/api/project/config',{cwd,values:savingProjectValues,revision:projectConfig.revision}).then(result=>{setProjectConfig(result);setProjectValues(result.values)})]:[]),
      ])
      setConfig(next); setDraft(next);setClaudeArgs(JSON.stringify(next.claude_args));setCodexArgs(JSON.stringify(next.codex_args));setErrors({})
      setSavedRules(rules);setSavedBindings(bindings);setSavedProjectValues(savingProjectValues)
      const refreshedProjects=await api<Project[]>('GET','/api/projects')
      setProjectDefaults(refreshedProjects);setSavedProjectDefaults(refreshedProjects)
      setStatus(next.restart_required.length ? `saved · restart required: ${next.restart_required.join(', ')}` : 'saved · hot applied')
      configureCustomTheme(next.custom_theme); applyTheme(next.theme)
      return true
    } catch (error) {
      const typed = error as Error & {fields?:Record<string,string>}
      setErrors(typed.fields || {settings:typed.message}); setStatus('invalid · nothing was changed')
      return false
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
  const providerKeyAction=async(operation:'test'|'set'|'clear')=>{
    setProviderMessage(operation==='clear'?'Clearing key…':operation==='set'?'Testing and storing key…':'Testing key…')
    try{
      const result=await api<{ok?:boolean;status:ProviderStatus['secret']}>('POST','/api/automation/provider/key',{operation,key:openRouterKey||undefined,test:true})
      setProvider(current=>current?{...current,secret:result.status}:current);if(operation==='set'||operation==='clear')setOpenRouterKey('')
      setProviderMessage(operation==='clear'?'Stored key cleared.':operation==='set'?'Key tested and stored with Windows DPAPI.':'OpenRouter connection succeeded.')
    }catch(error){setProviderMessage(`OpenRouter error · ${(error as Error).message}`)}
  }
  const refreshModels=async()=>{
    setProviderMessage('Refreshing OpenRouter model catalog…')
    try{const models=await api<ProviderStatus['models']>('POST','/api/automation/provider/models/refresh',{});setProvider(current=>current?{...current,models}:current);setProviderMessage(`Model catalog ready · ${models.models.length} structured-output text models.`)}catch(error){setProviderMessage(`Model refresh failed · ${(error as Error).message}`)}
  }
  const discardAndLeave=()=>{
    if(!closeIntent)return
    if(config){configureCustomTheme(config.custom_theme);applyTheme(config.theme)}
    leaveSettings(closeIntent)
  }
  const saveAndLeave=async()=>{
    if(!closeIntent)return
    const intent=closeIntent
    if(await save())leaveSettings(intent)
    else setCloseIntent(null)
  }
  const selectedProfileIndex=draft?.shell_profiles.findIndex(profile=>profile.id===selectedProfileId)??-1
  const selectedProfile=selectedProfileIndex>=0?draft?.shell_profiles[selectedProfileIndex]:undefined
  if (!draft) return <div class="settings-layer" onMouseDown={event=>event.target===event.currentTarget&&requestClose()}><section class="settings-panel">{status}</section></div>
  const modelOptions=(selected:string)=>{const items=[...(provider?.models.models||[])];if(selected&&!items.some(item=>item.id===selected))items.unshift({id:selected,name:'Configured model (catalog unavailable)'});return items}
  return <div class="settings-layer" onMouseDown={event=>event.target===event.currentTarget&&requestClose()}><section class="settings-panel" ref={panel} role="dialog" aria-modal={!closeIntent} aria-hidden={Boolean(closeIntent)} aria-label="Settings">
    <header><div><span>CONFIG::V6</span><h2>Settings</h2></div><button aria-label="Close Settings" onClick={()=>requestClose()}>×</button></header>
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
          <section><h3>Project defaults</h3><p>Database overrides take precedence over portable Project options, then global defaults. Choosing inherit removes the override.</p>{projectDefaults.map((project,index)=><article class="project-default"><strong>{project.name}</strong><small>{project.root}</small><label>Backend<select value={project.default_backend||''} onChange={e=>setProjectDefaults(items=>items.map((item,itemIndex)=>itemIndex===index?{...item,default_backend:(e.currentTarget.value||undefined) as Project['default_backend']}:item))}><option value="">Inherit ({project.effective_options?.backend||draft.default_backend})</option><option value="shell">Shell</option><option value="claude">Claude</option><option value="codex">Codex</option></select></label><label>Profile<select value={project.default_profile_id||''} onChange={e=>setProjectDefaults(items=>items.map((item,itemIndex)=>itemIndex===index?{...item,default_profile_id:e.currentTarget.value||undefined}:item))}><option value="">Inherit ({project.effective_options?.profile_id||draft.default_shell_profile})</option>{draft.shell_profiles.filter(profile=>profile.enabled).map(profile=><option value={profile.id}>{profile.label}</option>)}</select></label></article>)}</section>
          <section><h3>Git, history, and process evidence</h3><label>Git poll seconds<input type="number" step=".25" value={draft.git_poll_seconds} onInput={e=>change('git_poll_seconds',Number(e.currentTarget.value))} /></label><label>Process inspector poll seconds<input type="number" min=".5" max="60" step=".5" value={draft.process_poll_seconds} onInput={e=>change('process_poll_seconds',Number(e.currentTarget.value))}/></label><label>Suspected-orphan grace seconds<input type="number" min="1" max="3600" value={draft.process_orphan_grace_seconds} onInput={e=>change('process_orphan_grace_seconds',Number(e.currentTarget.value))}/></label><label>Process evidence retention days<input type="number" min="1" max="3650" value={draft.process_evidence_retention_days} onInput={e=>change('process_evidence_retention_days',Number(e.currentTarget.value))}/></label><p>Process evidence uses PID plus creation time and command fingerprint. Surviving descendants are flagged after the grace period and are never killed automatically.</p><label>Global project ignores<textarea value={draft.project_ignore_patterns.join('\n')} onInput={e=>change('project_ignore_patterns',parseIgnorePatternDraft(e.currentTarget.value))}/></label><p>One glob per line. A name such as <code>node_modules</code> matches that folder at any depth. These rules affect the file tree and resource watchers, not Git.</p></section>
          {projectConfig&&<section><h3>Portable current-Project options</h3><p>{projectConfig.project.root}</p><p aria-live="polite">.swe-mux/config.toml: {projectConfig.status}{projectConfig.error?` · ${projectConfig.error}`:''}</p><p>Only typed presentation preferences live here—never commands, hooks, credentials, network authority, or automatic actions. Blank values inherit database/global defaults.</p><label>Preferred backend<select value={projectValues.preferred_backend||''} onChange={e=>setProjectValues(values=>({...values,preferred_backend:(e.currentTarget.value||undefined) as ProjectConfig['values']['preferred_backend']}))}><option value="">Inherit</option><option value="shell">Shell</option><option value="claude">Claude</option><option value="codex">Codex</option></select></label><label>Shell profile<select value={projectValues.default_shell_profile||''} onChange={e=>setProjectValues(values=>({...values,default_shell_profile:e.currentTarget.value||undefined}))}><option value="">Inherit</option>{draft.shell_profiles.filter(profile=>profile.enabled).map(profile=><option value={profile.id}>{profile.label}</option>)}</select></label><label>Prompt library scope<select value={projectValues.prompt_library_scope||''} onChange={e=>setProjectValues(values=>({...values,prompt_library_scope:(e.currentTarget.value||undefined) as ProjectConfig['values']['prompt_library_scope']}))}><option value="">Inherit (global + Project)</option><option value="off">Off</option><option value="global">Global only</option><option value="project">Project only</option><option value="both">Global + Project</option></select></label><label class="check"><span>Allow device notification sounds for this Project</span><input type="checkbox" checked={projectValues.notification_sounds_enabled!==false} onChange={e=>setProjectValues(values=>({...values,notification_sounds_enabled:e.currentTarget.checked}))}/></label><button onClick={()=>setProjectValues({})}>Reset all portable options to inherited</button><label>Additional project ignores<textarea value={(projectValues.ignore_patterns||[]).join('\n')} onInput={e=>setProjectValues(values=>({...values,ignore_patterns:parseIgnorePatternDraft(e.currentTarget.value)}))}/></label><p>Project rules extend the global list. The folder is initialized when the project is created.</p></section>}
        </Fragment>}

        {activeTab==='notes'&&<section><h3>Project resources</h3><p>Project notes, Files, file editors, terminals, and previews all open as tabs in the focused pane. Drag any tab between panes or onto a pane edge to create a split.</p></section>}

        {activeTab==='agents'&&<section><h3>Agents</h3><label>Claude executable<input value={draft.claude_exe} onInput={e=>change('claude_exe',e.currentTarget.value)} /></label><label>Claude default args<input value={claudeArgs} onInput={e=>setClaudeArgs(e.currentTarget.value)} /></label><label>Codex executable<input value={draft.codex_exe} onInput={e=>change('codex_exe',e.currentTarget.value)} /></label><label>Codex default args<input value={codexArgs} onInput={e=>setCodexArgs(e.currentTarget.value)} /></label><label class="check"><span>Reconcile native history</span><input type="checkbox" checked={draft.reconcile_external_history} onChange={e=>change('reconcile_external_history',e.currentTarget.checked)} /></label></section>}

        {activeTab==='accounts'&&<AccountSettings/>}

        {activeTab==='input'&&<section class="input-settings"><h3>Input</h3>
          <label class="check"><span>Middle-click paste</span><input type="checkbox" checked={draft.middle_click_paste} onChange={e=>change('middle_click_paste',e.currentTarget.checked)} /></label>
          <label class="check"><span>Broadcast by default</span><input type="checkbox" checked={draft.broadcast_default} onChange={e=>change('broadcast_default',e.currentTarget.checked)} /></label>
          <div class="keybinding-heading"><div><strong>MOBILE::TERMINAL</strong><p>Touch settings apply on coarse-pointer devices. Text input goes directly to the focused terminal.</p></div></div>
          <label>Vertical drag<select value={draft.mobile_vertical_drag} onChange={e=>change('mobile_vertical_drag',e.currentTarget.value as Config['mobile_vertical_drag'])}><option value="smart">Smart: app wheel or scrollback</option><option value="terminal">Terminal scrollback only</option><option value="application">Application wheel</option><option value="disabled">Disabled</option></select></label>
          <label>Scroll direction<select value={draft.mobile_scroll_direction} onChange={e=>change('mobile_scroll_direction',e.currentTarget.value as Config['mobile_scroll_direction'])}><option value="natural">Natural touch</option><option value="wheel">Mouse wheel</option></select></label>
          <label>Scroll sensitivity<input type="number" min="0.25" max="4" step="0.25" value={draft.mobile_scroll_sensitivity} onInput={e=>change('mobile_scroll_sensitivity',Number(e.currentTarget.value))} /></label>
          <label>Long press<select value={draft.mobile_long_press} onChange={e=>change('mobile_long_press',e.currentTarget.value as Config['mobile_long_press'])}><option value="context_menu">Terminal context menu</option><option value="disabled">Disabled</option></select></label>
          <div class="keybinding-heading"><div><strong>KEYBOARD::SHORTCUTS</strong><p>Click a command, then press the new shortcut. Changes apply when Settings is saved.</p></div><button onClick={()=>{setBindings({...bindingDefaults});setCapturingCommand(null);setBindingError('')}}>Restore shortcut defaults</button></div>
          {capturingCommand&&<div class="keybinding-capture" role="status"><span>PRESS KEYS FOR</span><strong>{bindingCommands.find(command=>command.id===capturingCommand)?.label||capturingCommand}</strong><button onClick={()=>{setCapturingCommand(null);setBindingError('')}}>Cancel</button></div>}
          {bindingError&&<p class="keybinding-error" role="alert">{bindingError}</p>}
          <div class="keybinding-list">
            {[...new Set(bindingCommands.map(command=>command.category))].map(category=><section class="keybinding-group" aria-label={`${category} shortcuts`}><h4>{category}</h4>{bindingCommands.filter(command=>command.category===category).map(command=>{const chord=bindingForCommand(command.id);return <article class={capturingCommand===command.id?'capturing':''}><button class="keybinding-command" onClick={()=>{setCapturingCommand(command.id);setBindingError('')}} title={command.id}><span>{command.label}</span><small>{command.id}</small></button><button class="keybinding-chord" onClick={()=>{setCapturingCommand(command.id);setBindingError('')}} aria-label={`Set shortcut for ${command.label}`}><kbd>{chord?displayChord(chord):'not set'}</kbd></button><button class="keybinding-clear" disabled={!chord} onClick={()=>clearBinding(command.id)} aria-label={`Clear shortcut for ${command.label}`}>×</button></article>})}</section>)}
          </div>
          <details class="keybinding-policy"><summary>Reserved shortcut policy</summary><ul>{bindingPolicy.rules.map(rule=><li>{rule}</li>)}</ul><div><strong>BROWSER</strong>{bindingPolicy.browser_reserved.map(chord=><kbd>{displayChord(chord)}</kbd>)}</div><div><strong>TERMINAL</strong>{bindingPolicy.terminal_reserved.map(chord=><kbd>{displayChord(chord)}</kbd>)}</div></details>
        </section>}

        {activeTab==='usage'&&<section><h3>Usage and operational telemetry</h3><p>The dashboard combines optional ccusage history with durable provider quota samples, reset evidence, probabilistic mux correlation, tools, explicit skills, and compactions.</p><div class="theme-actions"><button class="primary" onClick={onOpenUsage}>Open telemetry dashboard</button><button disabled={!config?.ccusage_enabled || usage?.refreshing || usageRefreshMessage.startsWith('Refreshing')} onClick={()=>void refreshUsage()}>{usageRefreshMessage.startsWith('Refreshing')?'Refreshing…':'Refresh historical usage'}</button><button onClick={()=>void clearUsage()}>Clear ccusage cache</button></div><label>Operational telemetry retention days<input type="number" min="1" max="3650" value={draft.operational_telemetry_retention_days} onInput={e=>change('operational_telemetry_retention_days',Number(e.currentTarget.value))}/></label><label>Provider quota poll minutes<input type="number" min="5" max="1440" value={draft.provider_quota_poll_minutes} onInput={e=>change('provider_quota_poll_minutes',Number(e.currentTarget.value))}/></label><label class="check"><span>Refresh active quota after eligible root turns</span><input type="checkbox" checked={draft.provider_quota_turn_refresh_enabled} onChange={e=>change('provider_quota_turn_refresh_enabled',e.currentTarget.checked)}/></label><label>Minimum minutes between turn-triggered refreshes<input type="number" min="1" max="1440" value={draft.provider_quota_turn_refresh_min_minutes} onInput={e=>change('provider_quota_turn_refresh_min_minutes',Number(e.currentTarget.value))}/></label><p>Turn-triggered refresh is globally rate limited, selected-account only, and never assumes provider data updates immediately. Unexpected-reset sounds are optional per device in the account switcher.</p><h3>Historical ccusage</h3><p class={usageRefreshMessage.startsWith('Refresh failed')?'settings-inline-error':''} aria-live="polite">{usageRefreshMessage || (usage ? Object.entries(usage.states).map(([provider,state])=>`${provider}: ${state.status}${state.error?` (${state.error})`:''}`).join(' · ') : 'usage status unavailable')}</p>{draft.ccusage_enabled&&!config?.ccusage_enabled&&<p>Save these settings before refreshing.</p>}<label class="check"><span>Enable ccusage refresh</span><input type="checkbox" checked={draft.ccusage_enabled} onChange={e=>change('ccusage_enabled',e.currentTarget.checked)} /></label><label>Background refresh minutes<input type="number" min="0" max="1440" value={draft.ccusage_refresh_minutes} onInput={e=>change('ccusage_refresh_minutes',Number(e.currentTarget.value))} /></label><label>Install/update command<input readonly value={usage?.install_command||'npm install -g ccusage@latest'} onFocus={event=>event.currentTarget.select()} /></label><button onClick={()=>void navigator.clipboard.writeText(usage?.install_command||'npm install -g ccusage@latest')}>Copy install command</button><p>The `latest` tag is resolved when you install or update. Refreshes use the installed unified executable and never download code in the background.</p><details class="settings-advanced"><summary>Advanced command overrides</summary><label>Claude command<textarea value={draft.ccusage_claude_command.join('\n')} onInput={e=>change('ccusage_claude_command',e.currentTarget.value.split('\n').filter(Boolean))} /></label><label>Codex command<textarea value={draft.ccusage_codex_command.join('\n')} onInput={e=>change('ccusage_codex_command',e.currentTarget.value.split('\n').filter(Boolean))} /></label></details></section>}

        {activeTab==='automation'&&<section><h3>Automation</h3>
          <p>Observers watch Claude and Codex out of band. The Automation dashboard shows every system observer and custom rule together; these settings are global shortcuts for the same system observers.</p>
          <div class="theme-actions"><button class="primary" onClick={onOpenAutomation}>Open Automation dashboard</button></div>
          <label class="check"><span>Automation enabled</span><input type="checkbox" checked={draft.automation_enabled} onChange={e=>change('automation_enabled',e.currentTarget.checked)} /></label>
          <label class="check"><span>Session titler</span><input type="checkbox" checked={draft.observer_titler_enabled} onChange={e=>change('observer_titler_enabled',e.currentTarget.checked)} /></label>
          <label class="check"><span>Turn summarizer</span><input type="checkbox" checked={draft.observer_summarizer_enabled} onChange={e=>change('observer_summarizer_enabled',e.currentTarget.checked)} /></label>
          <label class="check"><span>Attention observers (stalls, approvals, context)</span><input type="checkbox" checked={draft.phase7_observers_enabled} onChange={e=>change('phase7_observers_enabled',e.currentTarget.checked)} /></label>
          <p class="settings-warning">Privacy boundary: each enabled observer sends only its selected bounded transcript slice to OpenRouter and the routed model provider. swe-mux does not crawl project files.</p>
          <h3>OpenRouter</h3>
          <p><span class={`state-dot ${provider?.secret.configured?'idle':'running'}`}/> key::{provider?.secret.configured?'configured':'not configured'} · source::{provider?.secret.source||'none'} · endpoint::{provider?.origin||'fixed OpenRouter API'}</p>
          <label>API key<input type="password" autocomplete="off" value={openRouterKey} placeholder={provider?.secret.configured?'write only · enter to replace':'sk-or-…'} onInput={event=>setOpenRouterKey(event.currentTarget.value)} /></label>
          <div class="theme-actions"><button disabled={!openRouterKey} onClick={()=>void providerKeyAction('test')}>Test entered key</button><button class="primary" disabled={!openRouterKey} onClick={()=>void providerKeyAction('set')}>Test + set/replace</button><button disabled={!provider?.secret.configured} onClick={()=>void providerKeyAction('clear')}>Clear stored key</button></div>
          <p aria-live="polite">{providerMessage||'The key is write-only and never appears in config, exports, logs, or browser reads.'}</p>
          <div class="theme-actions"><button disabled={!provider?.secret.configured} onClick={()=>void refreshModels()}>Refresh models</button><span>{provider?.models.models.length||0} models{provider?.models.stale?' · stale':''}{provider?.models.error?` · ${provider.models.error}`:''}</span></div>
          <label>Cheap model<select value={draft.openrouter_cheap_model} onChange={event=>change('openrouter_cheap_model',event.currentTarget.value)}><option value="">Select exact model…</option>{modelOptions(draft.openrouter_cheap_model).map(model=><option value={model.id}>{model.name} · {model.id}</option>)}</select></label>
          <label>Standard model<select value={draft.openrouter_standard_model} onChange={event=>change('openrouter_standard_model',event.currentTarget.value)}><option value="">Select exact model…</option>{modelOptions(draft.openrouter_standard_model).map(model=><option value={model.id}>{model.name} · {model.id}</option>)}</select></label>
          <h3>Budgets + execution</h3>
          <label>Daily token budget<input type="number" value={draft.automation_daily_token_budget} onInput={event=>change('automation_daily_token_budget',Number(event.currentTarget.value))}/></label>
          <label>Daily dollar budget<input type="number" step="0.01" value={draft.automation_daily_budget_usd} onInput={event=>change('automation_daily_budget_usd',Number(event.currentTarget.value))}/></label>
          <label>Per-rule daily tokens<input type="number" value={draft.automation_rule_daily_token_budget} onInput={event=>change('automation_rule_daily_token_budget',Number(event.currentTarget.value))}/></label>
          <label>Per-rule daily dollars<input type="number" step="0.01" value={draft.automation_rule_daily_budget_usd} onInput={event=>change('automation_rule_daily_budget_usd',Number(event.currentTarget.value))}/></label>
          <label>Hourly call cap<input type="number" value={draft.automation_hourly_call_cap} onInput={event=>change('automation_hourly_call_cap',Number(event.currentTarget.value))}/></label>
          <label>Per-rule hourly calls<input type="number" value={draft.automation_rule_hourly_call_cap} onInput={event=>change('automation_rule_hourly_call_cap',Number(event.currentTarget.value))}/></label>
          <label>Concurrent observers<input type="number" min="1" max="16" value={draft.automation_concurrency} onInput={event=>change('automation_concurrency',Number(event.currentTarget.value))}/></label>
          <label>Maximum input tokens<input type="number" value={draft.automation_max_input_tokens} onInput={event=>change('automation_max_input_tokens',Number(event.currentTarget.value))}/></label>
          <label>Maximum output tokens<input type="number" value={draft.automation_max_output_tokens} onInput={event=>change('automation_max_output_tokens',Number(event.currentTarget.value))}/></label>
          <label>Retention days<input type="number" value={draft.automation_retention_days} onInput={event=>change('automation_retention_days',Number(event.currentTarget.value))}/></label>
          <details class="settings-advanced"><summary>Advanced rules.toml editor</summary><p>Canonical machine-owned rules only. Repository .swe-mux/rules.toml files remain diagnostic and inert.</p><label>rules.toml<textarea value={rules} onInput={event=>setRules(event.currentTarget.value)} /></label></details>
          <p aria-live="polite">engine::{automation?.diagnostic?'error':'ready'} · rules::{automation?.rules.length||0} · queue::{automation?.queue.size||0}/{automation?.queue.capacity||0} · dropped::{automation?.queue.dropped||0}{automation?.legacy.active?' · legacy hooks compatibility active':''}</p>
        </section>}

        {activeTab==='notifications'&&<NotificationSoundSettings/>}

        {activeTab==='voice'&&<section><h3>Read aloud (TTS)</h3>
          <p>Mark a Claude or Codex session with its pane <code>tts:</code> chip or context menu. On demand adds a speak button; auto generates audio when each reply completes. Playback and per-device autoplay live in the pane's player strip.</p>
          <p aria-live="polite"><span class={`state-dot ${voiceInfo?.engine_available?'idle':'running'}`}/> engine::{voiceInfo?.engine||draft.tts_engine} {voiceInfo?.engine_available?'available':'unavailable'}{voiceInfo?.diagnostic?` · ${voiceInfo.diagnostic}`:''} · clips::{voiceInfo?.clip_count??0} · cache::{Math.round((voiceInfo?.cache_bytes||0)/1048576)}/{Math.round((voiceInfo?.cache_limit_bytes||0)/1048576)} MB · summary spend today::${(voiceInfo?.spend_today.cost_usd||0).toFixed(3)}</p>
          <label class="check"><span>Enable read aloud</span><input type="checkbox" checked={draft.tts_enabled} onChange={e=>change('tts_enabled',e.currentTarget.checked)} /></label>
          <label>Default mode for agent sessions<select value={draft.tts_default_mode} onChange={e=>change('tts_default_mode',e.currentTarget.value as Config['tts_default_mode'])}><option value="off">Off until marked</option><option value="on_demand">On demand (speak button)</option><option value="auto">Auto on every reply</option></select></label>
          <label>Content<select value={draft.tts_content} onChange={e=>change('tts_content',e.currentTarget.value as Config['tts_content'])}><option value="summary">Spoken summary (LLM, like /say)</option><option value="verbatim">Verbatim reply (markdown stripped)</option></select></label>
          <label>Engine<select value={draft.tts_engine} onChange={e=>change('tts_engine',e.currentTarget.value as Config['tts_engine'])}><option value="edge">Edge neural voices (online, free)</option><option value="sapi">Windows SAPI (offline)</option></select></label>
          {draft.tts_engine==='edge'&&<>
            <label>Edge voice<input list="edge-voice-suggestions" value={draft.tts_edge_voice} onInput={e=>change('tts_edge_voice',e.currentTarget.value)} /></label>
            <datalist id="edge-voice-suggestions">{EDGE_VOICE_SUGGESTIONS.map(voice=><option value={voice}/>)}</datalist>
            <label>Speaking rate<input value={draft.tts_edge_rate} placeholder="+10%" onInput={e=>change('tts_edge_rate',e.currentTarget.value)} /></label>
            <label>Pitch<input value={draft.tts_edge_pitch} placeholder="+0Hz" onInput={e=>change('tts_edge_pitch',e.currentTarget.value)} /></label>
            <label class="check"><span>Soften sentence stops (shorter pauses at periods)</span><input type="checkbox" checked={draft.tts_soften_stops} onChange={e=>change('tts_soften_stops',e.currentTarget.checked)} /></label>
          </>}
          {draft.tts_engine==='sapi'&&<>
            <label>SAPI voice (blank = system default)<input value={draft.tts_sapi_voice} onInput={e=>change('tts_sapi_voice',e.currentTarget.value)} /></label>
            <label>SAPI rate (-10 slow … 10 fast)<input type="number" min="-10" max="10" value={draft.tts_sapi_rate} onInput={e=>change('tts_sapi_rate',Number(e.currentTarget.value))} /></label>
          </>}
          <h3>Spoken summary</h3>
          <p>Summaries call OpenRouter with the last turn only, record spend beside observer calls, and stop at the daily budget. Configure the key under Automation.</p>
          <label>Summary model<select value={draft.tts_summary_model} onChange={e=>change('tts_summary_model',e.currentTarget.value)}><option value="">Use automation cheap model</option>{modelOptions(draft.tts_summary_model).map(model=><option value={model.id}>{model.name} · {model.id}</option>)}</select></label>
          <label>Summary max tokens<input type="number" min="64" max="2000" value={draft.tts_summary_max_tokens} onInput={e=>change('tts_summary_max_tokens',Number(e.currentTarget.value))} /></label>
          <label>Daily summary budget (USD)<input type="number" step="0.01" min="0" max="100" value={draft.tts_daily_budget_usd} onInput={e=>change('tts_daily_budget_usd',Number(e.currentTarget.value))} /></label>
          <label>Verbatim character cap<input type="number" min="200" max="40000" value={draft.tts_verbatim_max_chars} onInput={e=>change('tts_verbatim_max_chars',Number(e.currentTarget.value))} /></label>
          <h3>Storage and dictation</h3>
          <label>Audio cache limit (MB)<input type="number" min="10" max="5000" value={draft.tts_cache_mb} onInput={e=>change('tts_cache_mb',Number(e.currentTarget.value))} /></label>
          <label class="check"><span>Microphone dictation button on agent panes</span><input type="checkbox" checked={draft.stt_enabled} onChange={e=>change('stt_enabled',e.currentTarget.checked)} /></label>
          <p>Capability::{sttCapability().available?'available':'unavailable'} · backend::browser Web Speech · {sttCapability().reason}</p><p>Support and recognition providers vary by browser. swe-mux records capability/availability only; it does not retain audio or transcript content for analytics. Phone keyboard dictation remains the mobile fallback. Transcribed text is inserted without submitting. Local Whisper or Windows-native recognition remains a separate opt-in decision if browser reliability proves limiting.</p>
        </section>}

        {activeTab==='remote'&&<section><h3>Remote and security</h3><label class="check"><span>Listen on Tailscale IPv4</span><input type="checkbox" checked={draft.tailnet_enabled} onChange={event=>change('tailnet_enabled',event.currentTarget.checked)} /></label><p>Changing the listener requires a daemon restart. swe-mux binds localhost plus the specific Tailscale address—never every LAN interface.</p><dl><dt>Local URL</dt><dd>{remote?.listen_url||`http://${draft.host}:${draft.port}`}</dd><dt>Direct tailnet</dt><dd>{remote?.direct_available?'active':draft.tailnet_enabled?'Tailscale address unavailable':'disabled'}</dd>{remote?.tailnet_urls.map(url=><Fragment key={url}><dt>Tailnet URL</dt><dd><a href={url} target="_blank" rel="noreferrer">{url}</a></dd></Fragment>)}</dl><p>Direct tailnet HTTP is encrypted in transit by Tailscale, but browsers may restrict secure-context clipboard APIs. Normal terminal input remains available.</p><strong>Optional HTTPS with Tailscale Serve</strong><p>{remote?.diagnostic||'Checking Tailscale Serve…'}</p>{remote?.serve_url&&<p><a href={remote.serve_url} target="_blank" rel="noreferrer">{remote.serve_url}</a></p>}{remote?.funnel_detected&&<p class="settings-inline-error">Tailscale Funnel appears enabled. Public ingress is unsupported; use direct tailnet access or tailnet-only Serve.</p>}<label>Optional Serve command<input readonly value={remote?.setup_command||`tailscale serve --bg http://127.0.0.1:${draft.port}`} onFocus={event=>event.currentTarget.select()} /></label><div class="theme-actions"><button onClick={()=>void navigator.clipboard.writeText(remote?.setup_command||`tailscale serve --bg http://127.0.0.1:${draft.port}`)}>Copy Serve command</button><button onClick={()=>void api<RemoteStatus>('GET','/api/remote/status').then(setRemote)}>Recheck</button></div><p>No swe-mux login is used. Tailscale access policy controls which tailnet devices can connect.</p></section>}

        {activeTab==='appearance'&&<section><h3>Appearance</h3><label>Theme<select value={draft.theme} onChange={e=>{const value=e.currentTarget.value as ThemeName;change('theme',value);applyTheme(value)}}><option value="dark">Dark</option><option value="light">Light</option><option value="system">System</option><option value="solarized-dark">Solarized Dark</option><option value="tokyo-night">Tokyo Night</option><option value="custom">Custom</option></select></label>{draft.theme==='custom' && <div class="theme-tokens">{Object.entries(draft.custom_theme).map(([key,value])=><label>{key}<input value={value} onInput={e=>{const custom={...draft.custom_theme,[key]:e.currentTarget.value};change('custom_theme',custom);configureCustomTheme(custom);applyTheme('custom')}} /></label>)}</div>}<input class="file-input" ref={themeFile} type="file" accept="application/json" onChange={e=>void importTheme(e.currentTarget.files?.[0])} /><div class="theme-actions"><button onClick={()=>themeFile.current?.click()}>Import theme</button><button onClick={exportTheme}>Export theme</button></div><p>Settings, menus, controls, and terminal chrome use the same monospace font token.</p></section>}
      </div>
    </main>
    <footer><span aria-live="polite">{status==='saving…'?status:dirty?'unsaved changes':status}</span><button onClick={()=>void api('POST','/api/reveal',{path:draft.data_dir})}>Reveal config directory</button><button onClick={exportConfig}>Export sanitized</button><button onClick={()=>void reset()}>Restore defaults</button><button onClick={()=>requestClose()}>Cancel</button><button class={`primary${dirty?' unsaved':''}`} disabled={!dirty||status==='saving…'} onClick={()=>void save()}>{status==='saving…'?'Saving…':dirty?'Save changes':'Saved'}</button></footer>
  </section>
  {closeIntent&&<div class="modal-layer settings-confirm-layer" onMouseDown={event=>event.target===event.currentTarget&&setCloseIntent(null)}>
    <section class="modal settings-confirm" ref={confirmPanel} role="alertdialog" aria-modal="true" aria-label="Unsaved settings" onMouseDown={event=>event.stopPropagation()}>
      <div class="modal-heading"><div><span>SETTINGS::UNSAVED</span><h2>Save your changes?</h2></div><button aria-label="Keep editing" onClick={()=>setCloseIntent(null)}>×</button></div>
      <div class="settings-confirm-body"><p>You have changes that have not been saved. Save them before leaving Settings, or discard them and restore the last saved configuration.</p></div>
      <div class="modal-footer"><span>Settings stay open if saving fails.</span><button onClick={()=>setCloseIntent(null)}>Keep editing</button><button class="danger" onClick={discardAndLeave}>Discard</button><button class="primary" disabled={status==='saving…'} onClick={()=>void saveAndLeave()}>Save changes</button></div>
    </section>
  </div>}
  </div>
}
