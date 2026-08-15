import { Fragment } from 'preact'
import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { displayChord, type Command } from './commands'
import { isFocusTraversalKey, keyChord } from './keys'
import { dismissStack } from './dismissStack.ts'
import { useDismissLevel } from './modalFocus'
import { AccountSettings } from './ProviderAccounts'
import { NotificationAlertSettings } from './NotificationPushSettings'
import { SessionRowSettings } from './SessionRowSettings'
import { normalizeIgnorePatterns, parseIgnorePatternDraft, sameDraftValue } from './settingsDraft'
import { listShortcutBindings, type ShortcutPolicy } from '@continuity-editor/editor'
import { applyNoteEditorConfig, DEFAULT_NOTE_SHORTCUT_OVERRIDES, resetNoteRailArrangement } from './noteEditorSettings'
import { applyTheme, configureCustomTheme, type CustomTheme, type ThemeName } from './theme'
import { ThemePicker } from './ThemePicker'
import { uiScaleKeyboardIntent, uiScaleLabel, UI_SCALE_STEPS, type UiScale } from './uiScale'
import { CLAUDE_MAX_COLUMN_STEPS, claudeMaxColumnsLabel, type ClaudeMaxColumns } from './terminalViewport'
import { currentProfile } from './deviceSettings'
import { enableMobileVoice } from './mobileVoice'
import { VoiceLatencyReport } from './VoiceLatencyReport'
import { WakeWordTester } from './WakeWordTester'
import {
  completeVoiceReference, VOICE_ACTION_META, VOICE_ACTION_ORDER,
} from './voiceCommandReference'
import type { LatencyReportPayload } from './voiceLatency'
import { GESTURE_SLOTS, GESTURE_LABELS, defaultMobileGestureSettings } from './mobileGestures'
import { RailEditor } from './RailEditor'
import { allBackendNames, harnessDescriptor, harnessDisplayName, harnesses } from './harnessRegistry'
import { domVNode, harvestSettings, kindSelector, matchIndex, searchSettings, tabEntry, type SettingsSearchEntry } from './settingsSearch'
import type { InitScript } from './projectCreate'
import type { PromptTemplate } from './PromptLibrary'
import type { LaunchProfile, Project, ProjectBackend } from './types'
import { formatCommandLine, launchPreview, parseCommandLine } from './commandLine'
import { ModelPicker } from './ModelPicker'
import { includeSelectedModel } from './modelFilter'

type Config = {
  revision:number; host:string; port:number; data_dir:string; requires_auth:boolean; access_mode:string; tailnet_enabled:boolean
  startup_cwd:string; default_backend:string; shell_exe:string
  harness_exe:Record<string,string>; scrollback_bytes:number; history_limit:number
  terminal_renderer:'auto'|'dom'|'webgl'
  harness_args:Record<string,string[]>
  git_poll_seconds:number;worktree_root:string;reconcile_external_history:boolean;theme:ThemeName
  drawer_tab_display:'icon'|'title'
  utility_rail_display:'icon'|'title'
  process_poll_seconds:number;process_orphan_grace_seconds:number;process_evidence_retention_days:number
  operational_telemetry_retention_days:number;provider_quota_poll_minutes:number
  provider_quota_turn_refresh_enabled:boolean;provider_quota_turn_refresh_min_minutes:number
  middle_click_paste:boolean; broadcast_default:boolean
  mobile_vertical_drag:'smart'|'terminal'|'application'|'disabled'
  mobile_scroll_direction:'natural'|'wheel';mobile_scroll_sensitivity:number
  mobile_long_press:'context_menu'|'disabled'
  mobile_gestures:Record<string,string>
  mobile_gesture_swipe_away_close:boolean
  mobile_gesture_overlay_back:boolean
  terminal_auto_copy_selection:boolean
  clipboard_history_enabled:boolean;clipboard_history_persist:boolean
  clipboard_history_limit:number;clipboard_history_entry_max_chars:number
  clipboard_history_retention_hours:number;clipboard_history_redact_secrets:boolean
  note_spellcheck:boolean;note_syntax:'markdown'|'plain'
  note_tab_behavior:'indent'|'focus';note_shortcut_policy:ShortcutPolicy
  note_font_family:string;note_font_size_px:number;note_line_height:number
  note_command_rail:'auto'|'on'|'off';note_rail_button_size_px:number
  note_indent_guides:boolean
  ui_scale_desktop:UiScale;ui_scale_mobile:UiScale
  claude_max_columns:ClaudeMaxColumns
  note_shortcut_overrides:Record<string,string>
  ccusage_enabled:boolean; ccusage_refresh_minutes:number
  usage_commands:Record<string,string[]>
  custom_theme:CustomTheme
  default_shell_profile:string; shell_profiles:LaunchProfile[]
  project_ignore_patterns:string[]
  project_init_scripts:InitScript[]
  auto_delivery_enabled:boolean;auto_delivery_stable_seconds:number
  auto_delivery_max_consecutive:number;auto_delivery_session_ttl_minutes:number
  agent_messaging_enabled:boolean;agent_message_max_chain_depth:number
  agent_message_max_thread_turns:number;agent_message_hourly_budget:number
  agent_message_pending_per_target:number
  auto_delivery_refusal_backoff_seconds:number
  auto_delivery_quiet_start:string;auto_delivery_quiet_end:string
  automation_enabled:boolean;automation_retention_days:number;automation_concurrency:number
  automation_queue_size:number;automation_max_input_tokens:number;automation_max_output_tokens:number
  automation_daily_token_budget:number;automation_daily_budget_usd:number;automation_rule_daily_token_budget:number
  automation_rule_daily_budget_usd:number;automation_hourly_call_cap:number
  automation_rule_hourly_call_cap:number;openrouter_cheap_model:string
  scan_timeline_enabled:boolean;scan_timeline_model:string;scan_timeline_run_token_budget:number
  attention_daily_interrupt_budget:number;attention_hourly_interrupt_cap:number
  attention_incident_window_seconds:number;attention_breakpoint_markers:boolean
  attention_narration_enabled:boolean;attention_narration_model:string
  attention_narration_daily_budget_usd:number
  openrouter_standard_model:string;openrouter_request_timeout_seconds:number
  observer_titler_enabled:boolean;observer_summarizer_enabled:boolean
  phase7_observers_enabled:boolean
  tts_enabled:boolean;tts_default_mode:'off'|'on_demand'|'auto';tts_content:'summary'|'verbatim'
  tts_engine:'edge'|'sapi';tts_edge_voice:string;tts_edge_rate:string;tts_edge_pitch:string
  tts_soften_stops:boolean;tts_sapi_voice:string;tts_sapi_rate:number
  tts_summary_model:string;tts_summary_max_tokens:number;tts_verbatim_max_chars:number
  tts_daily_budget_usd:number;tts_cache_mb:number;stt_enabled:boolean
  stt_engine:'sapi'|'whisper';stt_language:string;stt_whisper_model:string;stt_routing_model:string
  voice_wake_words:string[];voice_commands:{action:string;phrases:string[]}[]
}
type VoiceStatusInfo = {
  enabled:boolean;engine:string;engine_available:boolean;diagnostic?:string|null;voice:string
  summary_model:string;spend_today:{tokens:number;cost_usd:number};daily_budget_usd:number
  cache_bytes:number;cache_limit_bytes:number;clip_count:number;stt_enabled:boolean
  stt_engine:'sapi'|'whisper';stt_available:boolean;stt_diagnostic?:string|null
  stt_language:string;stt_whisper_model:string;stt_routing_model?:string
  wake_words?:string[];commands?:{action:string;phrases:string[]}[]
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

type RemoteStatus = {
  mode:string;listen_url:string;available:boolean;serve_configured:boolean
  serve_url?:string|null;funnel_detected:boolean;setup_command:string;diagnostic:string
  tailnet_enabled:boolean;tailnet_ip?:string|null;tailnet_urls:string[];direct_available:boolean
  mobile_voice_configured:boolean;mobile_voice_url?:string|null;mobile_voice_https_port:number
}
type KeybindingCommand = {id:string;label:string;category:string}
type KeybindingPolicy = {browser_reserved:string[];desktop_only:string[];application_reserved:string[];terminal_reserved:string[];rules:string[]}
type KeybindingsResponse = {
  bindings:Record<string,string>;defaults:Record<string,string>;commands:KeybindingCommand[]
  policy:KeybindingPolicy;rejected:Record<string,string>
}
type CloseIntent = 'close'|'usage'|'automation'|'tutorial'

// One round trip for everything the panel needs on open. `config` is required;
// every other part arrives null when its section failed server-side, with the
// reason under `errors`.
type SettingsBundle = {
  config:Config
  automation_rules:{text:string}|null
  keybindings:KeybindingsResponse|null
  profiles:{profiles:LaunchProfile[];detected:LaunchProfile[]}|null
  projects:Project[]|null
  automation:AutomationStatus|null
  provider:ProviderStatus|null
  usage:UsageStatus|null
  errors:Record<string,string>
}

const settingsTabs = [
  {id:'general',label:'General'},
  {id:'terminals',label:'Terminals'},
  {id:'workspace',label:'Git & processes'},
  {id:'notes',label:'Notes'},
  {id:'agents',label:'Agents'},
  {id:'accounts',label:'Accounts'},
  {id:'input',label:'Input'},
  {id:'commandrail',label:'Command rail'},
  {id:'usage',label:'Usage'},
  {id:'automation',label:'Automation'},
  {id:'notifications',label:'Alerts'},
  {id:'voice',label:'Voice'},
  {id:'remote',label:'Remote'},
  {id:'appearance',label:'Appearance'},
] as const
type SettingsTab = typeof settingsTabs[number]['id']
// Search entries harvested from a tab's real DOM while it was on screen. Module
// scope, not component state: a tab visited in one Settings session stays fully
// searchable in the next one, for as long as the page lives.
const liveTabEntries = new Map<SettingsTab,SettingsSearchEntry[]>()
const tabForSection = (section:string):SettingsTab => ({
  Terminals:'terminals',
  Agents:'agents',Accounts:'accounts',Input:'input','Command rail':'commandrail','Git and history':'workspace','Usage analytics':'usage',
  Notes:'notes',
  Automation:'automation','Hooks and notifications':'notifications',Notifications:'notifications',Alerts:'notifications',Voice:'voice','Remote and security':'remote',Appearance:'appearance',
}[section] as SettingsTab|undefined)||'general'

// Which tab Settings opens on when nothing asked for a specific one. Persisted per
// device rather than held in App state so it survives a reload - Settings is opened,
// scanned, and closed dozens of times a session, and landing on General every time
// re-costs the navigation that brought you to the tab you actually live in. An
// explicit `initialSection` (Voice from the TTS chip, Accounts from the switcher,
// …) always wins: that caller knows where the user needs to be.
const SETTINGS_TAB_KEY='mux.settings.tab.v1'
const rememberedTab = ():SettingsTab => {
  let stored:string|null=null
  try { stored=localStorage.getItem(SETTINGS_TAB_KEY) } catch { return 'general' }
  // Validated against the live tab list, so a tab that is renamed or removed
  // degrades to General instead of rendering an empty panel.
  return settingsTabs.some(tab=>tab.id===stored)?stored as SettingsTab:'general'
}
const rememberTab = (tab:SettingsTab) => {
  try { localStorage.setItem(SETTINGS_TAB_KEY,tab) } catch { /* private mode */ }
}

// The note editor's own binding table, enumerated from the editor package so the
// list can never drift from what it actually binds. `isBrowserSafe` is false for
// the chords Chromium claims first (Ctrl+R, Ctrl+K, …): the default browser-safe
// policy releases those unless an explicit override reclaims one.
const NOTE_CHORDS = listShortcutBindings()
const CHORD_PARTS:Record<string,string> = {mod:'Mod',ctrl:'Ctrl',meta:'Meta',alt:'Alt',shift:'Shift'}
const noteChordLabel = (chord:string) => chord.split('+').map(part=>
  CHORD_PARTS[part] ?? (part.length===1?part.toUpperCase():part.replace(/^arrow/,'').replace(/^./,head=>head.toUpperCase()))
).join('+')
// A chord is on the editor's policy default, bound to its command explicitly, or
// released to the browser. '' is how a release survives TOML (see config.py).
type NoteChordState = 'default'|'bind'|'release'
const noteChordState = (overrides:Record<string,string>,chord:string):NoteChordState =>
  !(chord in overrides) ? 'default' : overrides[chord]===''?'release':'bind'

export function Settings({ activeUiScale, onUiScalePreview, onClose, onOpenUsage:openUsage, onOpenAutomation:openAutomation, onStartTutorial, initialSection, voiceCommands=[] }: { activeUiScale:UiScale;onUiScalePreview:(config:Record<string,unknown>)=>UiScale;onClose: () => void; onOpenUsage?:() => void;onOpenAutomation?:()=>void;onStartTutorial?:()=>void; initialSection?:string;voiceCommands?:Command[] }) {
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
  const [bindingPolicy, setBindingPolicy] = useState<KeybindingPolicy>({browser_reserved:[],desktop_only:[],application_reserved:[],terminal_reserved:[],rules:[]})
  const [capturingCommand, setCapturingCommand] = useState<string|null>(null)
  const [bindingError, setBindingError] = useState('')
  const [harnessArgs, setHarnessArgs] = useState<Record<string,string>>({})
  const [detectedProfiles, setDetectedProfiles] = useState<LaunchProfile[]>([])
  //: Profiles exactly as the daemon last published them, which is the only place
  //: derived capabilities come from. Never edited; compared against the draft.
  const [savedProfiles, setSavedProfiles] = useState<LaunchProfile[]>([])
  //: The arguments field holds typed text, not the stored argv, because a
  //: controlled input rebuilt from argv on every keystroke eats a trailing space
  //: the moment you type one. Parsed into argv on each change; re-seeded only when
  //: the selection changes.
  const [argsText, setArgsText] = useState('')
  const [usage, setUsage] = useState<UsageStatus | null>(null)
  const [voiceInfo, setVoiceInfo] = useState<VoiceStatusInfo | null>(null)
  const [latencyReport, setLatencyReport] = useState<LatencyReportPayload | null>(null)
  const completeVoiceCatalog=useMemo(()=>completeVoiceReference(voiceCommands,draft?.voice_commands||[]),[voiceCommands,draft?.voice_commands])
  const [usageRefreshMessage, setUsageRefreshMessage] = useState('')
  const [remote, setRemote] = useState<RemoteStatus | null>(null)
  const [mobileVoiceBusy,setMobileVoiceBusy]=useState(false)
  const [mobileVoiceMessage,setMobileVoiceMessage]=useState('')
  const [savedRules, setSavedRules] = useState('version = 1\n')
  const [savedBindings, setSavedBindings] = useState<Record<string,string>>({})
  const [status, setStatus] = useState('loading…')
  const [errors, setErrors] = useState<Record<string,string>>({})
  const [activeTab,setActiveTab] = useState<SettingsTab>(()=>initialSection?tabForSection(initialSection):rememberedTab())
  const [selectedProfileId,setSelectedProfileId] = useState<string|null>(null)
  const [noteChordQuery,setNoteChordQuery] = useState('')
  const [closeIntent,setCloseIntent] = useState<CloseIntent|null>(null)
  const [query,setQuery] = useState('')
  const [highlight,setHighlight] = useState(0)
  const [jump,setJump] = useState<{entry:SettingsSearchEntry}|null>(null)
  const [themePickerOpen,setThemePickerOpen] = useState(false)
  const panel = useRef<HTMLElement>(null)
  const searchInput = useRef<HTMLInputElement>(null)
  const searchIndex = useRef<{source:Config|null;entries:SettingsSearchEntry[]}|null>(null)
  const wasSearching = useRef(false)
  const confirmPanel = useRef<HTMLElement>(null)
  const themeFile = useRef<HTMLInputElement>(null)
  const restoreFocus = useRef<HTMLElement | null>(document.activeElement as HTMLElement | null)

  useEffect(() => {
    api<RemoteStatus>('GET','/api/remote/status').then(setRemote).catch(()=>setRemote(null))
    api<VoiceStatusInfo>('GET','/api/voice').then(setVoiceInfo).catch(()=>setVoiceInfo(null))
    api<LatencyReportPayload>('GET','/api/voice/stt-latency').then(setLatencyReport).catch(()=>setLatencyReport(null))
    // One bundled request instead of nine — on a high-RTT client (phone over
    // Tailscale) per-request connection setup dominated the panel's open delay.
    api<SettingsBundle>('GET','/api/settings/bundle').then(bundle => {
      const { config: next, automation_rules: rulesData, keybindings: keyData } = bundle
      // Saving unconditionally PUTs rules + keybindings back; rendering without
      // them would let a Save overwrite their files with empty defaults.
      if (!rulesData || !keyData) {
        setStatus(['automation_rules','keybindings'].filter(key=>bundle.errors[key]).map(key=>`${key}: ${bundle.errors[key]}`).join(' · ')||'settings payload incomplete')
        return
      }
      setConfig(next); setDraft(next); setRules(rulesData.text);setSavedRules(rulesData.text)
      setHarnessArgs(Object.fromEntries(Object.entries(next.harness_args).map(([name,args])=>[name,formatCommandLine(args)])))
      setBindings(keyData.bindings);setSavedBindings(keyData.bindings);setBindingDefaults(keyData.defaults||{})
      setBindingCommands(keyData.commands||[]);setBindingPolicy({
        browser_reserved:keyData.policy?.browser_reserved||[],desktop_only:keyData.policy?.desktop_only||[],application_reserved:keyData.policy?.application_reserved||[],
        terminal_reserved:keyData.policy?.terminal_reserved||[],rules:keyData.policy?.rules||[],
      })
      if(Object.keys(keyData.rejected||{}).length)setBindingError(`Ignored saved shortcuts · ${Object.entries(keyData.rejected).map(([chord,message])=>`${displayChord(chord)}: ${message}`).join(' · ')}`)
      setStatus('ready')
      // Both halves of the profile payload are kept. `detected` seeds "restore
      // detected"; `profiles` carries the daemon's *derived* capabilities, which
      // /api/config does not (it returns the stored list, which is now vestigial).
      if(bundle.profiles){setDetectedProfiles(bundle.profiles.detected);setSavedProfiles(bundle.profiles.profiles)}
      setAutomation(bundle.automation);setProvider(bundle.provider); setUsage(bundle.usage)
      configureCustomTheme(next.custom_theme); applyTheme(next.theme)
    }).catch(error => setStatus(error.message))
  }, [])

  // A caller that names a section while the panel is already open still redirects it.
  // Without a section there is nothing to redirect *to* - the remembered tab was already
  // chosen at mount, and re-applying it here would yank the user off a tab they just picked.
  useEffect(() => {
    if (initialSection) setActiveTab(tabForSection(initialSection))
  },[initialSection])

  useEffect(()=>rememberTab(activeTab),[activeTab])

  // Ctrl+wheel/+/- is owned by App so it can intercept before xterm and browser zoom.
  // While Settings is open, reflect that active-profile preview into this panel's draft
  // so its existing Save/Discard transaction remains the only persistence authority.
  useEffect(()=>{
    const key=currentProfile()==='mobile'?'ui_scale_mobile':'ui_scale_desktop'
    setDraft(current=>current&&current[key]!==activeUiScale?{...current,[key]:activeUiScale}:current)
  },[activeUiScale,config?.revision])

  useEffect(()=>setThemePickerOpen(false),[activeTab])

  const dirty = useMemo(() => Boolean(config&&draft&&(
    !sameDraftValue(config,draft)
    ||Object.entries(config.harness_args).some(([name,args])=>harnessArgs[name]!==formatCommandLine(args))
    ||rules!==savedRules
    ||!sameDraftValue(bindings,savedBindings)
  )),[config,draft,harnessArgs,rules,savedRules,bindings,savedBindings])

  const leaveSettings = useCallback((intent:CloseIntent) => {
    setCloseIntent(null)
    if(intent==='usage'&&openUsage){openUsage();return}
    if(intent==='automation'&&openAutomation){openAutomation();return}
    if(intent==='tutorial'&&onStartTutorial){onClose();onStartTutorial();return}
    onClose()
  },[onClose,openUsage,openAutomation,onStartTutorial])

  const requestClose = useCallback((intent:CloseIntent='close') => {
    if(dirty){setCloseIntent(intent);return}
    leaveSettings(intent)
  },[dirty,leaveSettings])
  const onOpenUsage=useCallback(()=>requestClose('usage'),[requestClose])
  const onOpenAutomation=useCallback(()=>requestClose('automation'),[requestClose])

  // Settings unwinds one layer at a time, and the layers are independent levels rather
  // than a fixed ladder: each opens when its own state does, so back undoes them in the
  // order they were actually opened instead of a hardcoded precedence that could not know
  // whether the theme picker or the search came first. `requestClose` is deliberately the
  // panel's dismiss even though it may not close: with unsaved edits it raises the
  // Save/Discard decision, which registers as a level above it, and back then reaches
  // that decision rather than closing the panel out from under it.
  // Every level stands down while a shortcut is being recorded, because Escape then means
  // "cancel the recording" and belongs to the capture handler.
  useDismissLevel(()=>requestClose(),!capturingCommand,'settings')
  useDismissLevel(()=>setQuery(''),!!query&&!capturingCommand,'settings-search')
  useDismissLevel(()=>setThemePickerOpen(false),themePickerOpen&&!capturingCommand,'settings-theme-picker')
  useDismissLevel(()=>setCloseIntent(null),!!closeIntent&&!capturingCommand,'settings-close-confirm')

  const loadLatency=()=>{void api<LatencyReportPayload>('GET','/api/voice/stt-latency').then(setLatencyReport).catch(()=>{})}
  const resetLatency=()=>{void api<LatencyReportPayload>('DELETE','/api/voice/stt-latency').then(setLatencyReport).catch(()=>{})}

  const setupMobileVoice=async()=>{
    if(mobileVoiceBusy)return
    setMobileVoiceBusy(true);setMobileVoiceMessage('Creating and verifying the private HTTPS address…')
    try{
      const result=await enableMobileVoice()
      if(!result.url){setMobileVoiceMessage('Complete the one-time Tailscale approval, then tap Enable secure mobile voice again.');return}
      setMobileVoiceMessage(result.diagnostic)
      setRemote(current=>current&&{...current,mobile_voice_configured:true,mobile_voice_url:result.url,mobile_voice_https_port:result.https_port||443,serve_configured:false,serve_url:null,diagnostic:result.diagnostic})
    }catch(cause){setMobileVoiceMessage(cause instanceof Error?cause.message:String(cause))}
    finally{setMobileVoiceBusy(false)}
  }

  useEffect(() => {
    if(!closeIntent)return
    confirmPanel.current?.querySelector<HTMLElement>('button')?.focus()
  },[closeIntent])

  useEffect(() => () => restoreFocus.current?.focus(),[])

  // Index what the tab on screen actually rendered, shortly after it settles —
  // a child component's own fetches land after the switch. Doing this on every
  // tab visit rather than only when a search starts is what lets a search from
  // one tab find a control that lives inside another tab's child component.
  useEffect(() => {
    if(!draft)return
    const timer=window.setTimeout(()=>{
      const mounted=panel.current?.querySelectorAll('.settings-content > *:not(.settings-errors)')
      if(!mounted?.length)return
      const index=settingsTabs.findIndex(tab=>tab.id===activeTab)
      const label=settingsTabs[index]?.label||activeTab
      liveTabEntries.set(activeTab,[...mounted].flatMap(node=>harvestSettings(domVNode(node),activeTab,label,index)))
    },450)
    return()=>window.clearTimeout(timer)
  },[activeTab,draft!==null])

  // Reveal a picked search result once its tab has rendered. Landing on the right
  // tab is not enough on the long ones — the control is often several screens
  // down, so it is scrolled to and flashed. `jump` is a fresh object per pick so
  // choosing the same result twice still re-runs this.
  useEffect(() => {
    if(!jump)return
    const root=panel.current?.querySelector('.settings-content')
    if(!root)return
    const candidates=[...root.querySelectorAll<HTMLElement>(kindSelector[jump.entry.kind])]
    const index=matchIndex(candidates.map(item=>item.textContent||''),jump.entry.key,jump.entry.occurrence)
    const target=index>=0?candidates[index]:null
    if(!target)return
    for(let node=target.parentElement;node;node=node.parentElement)if(node instanceof HTMLDetailsElement)node.open=true
    target.scrollIntoView({block:'center'})
    target.classList.add('settings-search-hit')
    const timer=window.setTimeout(()=>target.classList.remove('settings-search-hit'),1800)
    return()=>{window.clearTimeout(timer);target.classList.remove('settings-search-hit')}
  },[jump])

  useEffect(() => {
    if (!capturingCommand) return
    const capture = (event:KeyboardEvent) => {
      // Fixed scale keys are application-reserved, including while the shortcut
      // recorder is open. Leave them untouched for App's scale capture listener.
      if(uiScaleKeyboardIntent(event))return
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
        dismissStack.pop()
      }
      // Find-in-settings takes over the browser's find while the panel owns the
      // screen; its own find cannot see the tabs that are not mounted anyway.
      if ((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='f'&&!closeIntent&&!capturingCommand) {
        event.preventDefault()
        event.stopImmediatePropagation()
        searchInput.current?.focus()
        searchInput.current?.select()
      }
      const focusRoot=closeIntent?confirmPanel.current:panel.current
      if (isFocusTraversalKey(event) && focusRoot) {
        const focusable = [...focusRoot.querySelectorAll<HTMLElement>('button,input,select,textarea,[tabindex]:not([tabindex="-1"])')].filter(item => !item.hasAttribute('disabled'))
        if (!focusable.length) return
        const first = focusable[0], last = focusable[focusable.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    window.addEventListener('keydown', close, true)
    return () => window.removeEventListener('keydown', close, true)
  }, [closeIntent,capturingCommand])

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
  // Previewed live, like the theme, because the only way to judge a chrome scale
  // is to see the chrome at it. Editing the *other* device class is a no-op on
  // screen, which is correct: `applyUiScale` resolves this device's key either
  // way, so setting the phone's scale from a desktop shows nothing here.
  const changeUiScale = (key:'ui_scale_desktop'|'ui_scale_mobile', raw:string) => {
    const scale = Number(raw) as UiScale
    change(key,scale)
    onUiScalePreview({...draft!,[key]:scale})
  }
  // Only the overlay is stored, never the whole table: a chord left on the
  // editor's default keeps following the editor, so a Continuity upgrade that
  // rebinds something is not frozen out by a copy saved here.
  const setNoteChord=(overrides:Record<string,string>,chord:string,command:string,state:NoteChordState)=>{
    const next={...overrides}
    if(state==='default')delete next[chord]
    else next[chord]=state==='release'?'':command
    change('note_shortcut_overrides',next)
  }
  const save = async ():Promise<boolean> => {
    if (!draft || !config) return false
    // Both argument fields in this app now take a command line and store argv.
    // This one used to want a JSON array while the profile editor wanted one token
    // per line: two syntaxes for one concept, neither of them labelled.
    const savingDraft: Config = {
      ...draft,
      harness_args:Object.fromEntries(
        Object.entries(harnessArgs).map(([name,text])=>[name,parseCommandLine(text)]),
      ),
      project_ignore_patterns:normalizeIgnorePatterns(draft.project_ignore_patterns),
    }
    setStatus('saving…')
    const body: Record<string,unknown> = {_revision:config.revision}
    for (const key of Object.keys(savingDraft) as (keyof Config)[]) {
      if (!['revision','host','port','data_dir','requires_auth'].includes(key) && savingDraft[key] !== config[key]) body[key] = savingDraft[key]
    }
    try {
      await Promise.all([
        api('PUT','/api/automation/rules?validate=1',{text:rules}),
        api('PUT','/api/keybindings?validate=1',{bindings}),
      ])
      const [next] = await Promise.all([
        api<Config & {hot_applied:string[];restart_required:string[]}>('PATCH','/api/config',body),
        api('PUT','/api/automation/rules',{text:rules}),
        api('PUT','/api/keybindings',{bindings}),
      ])
      setConfig(next); setDraft(next);setHarnessArgs(Object.fromEntries(Object.entries(next.harness_args).map(([name,args])=>[name,formatCommandLine(args)])));setErrors({})
      setSavedRules(rules);setSavedBindings(bindings)
      setStatus(next.restart_required.length ? `saved · restart required: ${next.restart_required.join(', ')}` : 'saved · hot applied')
      configureCustomTheme(next.custom_theme); applyTheme(next.theme); applyNoteEditorConfig(next); onUiScalePreview(next)
      return true
    } catch (error) {
      const typed = error as Error & {fields?:Record<string,string>}
      setErrors(typed.fields || {settings:typed.message}); setStatus('invalid · nothing was changed')
      return false
    }
  }
  const reset = async () => {
    const next = await api<Config>('POST','/api/config/reset',{})
    setConfig(next); setDraft(next);setHarnessArgs(Object.fromEntries(Object.entries(next.harness_args).map(([name,args])=>[name,formatCommandLine(args)]))); configureCustomTheme(next.custom_theme); applyTheme(next.theme); applyNoteEditorConfig(next); onUiScalePreview(next); setStatus('defaults restored')
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
  const updateProfile = (index:number, changes:Partial<LaunchProfile>) => {
    const previous=draft!.shell_profiles[index]
    // Switching to an agent backend hides the shell-only controls, so their values
    // have to be normalized here. Leaving them would save a profile the daemon
    // refuses on a field with no control left on screen to fix it.
    const normalized:Partial<LaunchProfile>=changes.backend&&changes.backend!=='shell'
      ?{...changes,cwd_strategy:'native',cwd_integration:false}
      :changes
    change('shell_profiles',draft!.shell_profiles.map((profile,itemIndex)=>itemIndex===index?{...profile,...normalized}:profile))
    if(changes.id&&selectedProfileId===previous.id)setSelectedProfileId(changes.id)
  }
  const addProfile = (source?:LaunchProfile, backend:ProjectBackend='shell') => {
    const base:LaunchProfile = source || (backend==='shell'
      ? {id:'shell',label:'New shell',executable:'',args:[],env:{},platforms:['windows'],cwd_strategy:'native',marker:'sh',capabilities:['interactive'],cwd_integration:false,enabled:true,backend:'shell'}
      // An agent profile leaves `executable` empty so it inherits `harness_exe`, and
      // carries no shell capabilities: it contributes arguments, not a command line.
      // `platforms` is empty rather than ['windows'] because nothing about an
      // argument list is platform-specific, and a stray default would make the
      // profile unavailable on a host it would have worked on.
      : {id:backend,label:`New ${harnessDescriptor(backend)?.display_name||backend} profile`,executable:'',args:[],env:{},platforms:[],cwd_strategy:'native',marker:'ag',capabilities:[],cwd_integration:false,enabled:true,backend})
    let id=base.id, suffix=2
    while(draft!.shell_profiles.some(profile=>profile.id===id)) id=`${base.id}-${suffix++}`
    change('shell_profiles',[...draft!.shell_profiles,{...base,id,args:[...base.args],env:{...base.env},capabilities:[...base.capabilities]}])
    setSelectedProfileId(id)
  }
  /** Reserved argv for the selected profile's backend, as the daemon declares it. */
  const reservedArgs = (backend:string) => harnessDescriptor(backend)?.reserved_launch_args||[]
  /** The first reserved token these arguments set, matching the daemon's own rule. */
  const reservedConflict = (backend:string, args:string[]) => {
    for(const argument of args){
      for(const token of reservedArgs(backend)){
        if(token.endsWith('=')||token.endsWith('.')){ if(argument.startsWith(token)) return token }
        else if(argument===token||argument.startsWith(`${token}=`)) return token
      }
    }
    return ''
  }
  // Init scripts are ordinary config rows; the id is generated once and then left
  // alone, because the Add-project dialog selects by id and a label edit must not
  // silently orphan a selection.
  const initScripts = ():InitScript[] => draft!.project_init_scripts||[]
  const updateInitScript = (index:number,changes:Partial<InitScript>) =>
    change('project_init_scripts',initScripts().map((script,itemIndex)=>itemIndex===index?{...script,...changes}:script))
  const addInitScript = () => {
    let id='setup', suffix=2
    while(initScripts().some(script=>script.id===id)) id=`setup-${suffix++}`
    change('project_init_scripts',[...initScripts(),{id,label:'',command:'',default_enabled:false}])
  }
  const removeInitScript = (index:number) =>
    change('project_init_scripts',initScripts().filter((_,itemIndex)=>itemIndex!==index))
  const moveInitScript = (index:number,offset:number) => {
    const items=[...initScripts()],target=index+offset
    if(target<0||target>=items.length)return
    ;[items[index],items[target]]=[items[target],items[index]]
    change('project_init_scripts',items)
  }
  const moveProfile = (index:number,offset:number) => { const items=[...draft!.shell_profiles],target=index+offset;if(target<0||target>=items.length)return;[items[index],items[target]]=[items[target],items[index]];change('shell_profiles',items) }
  const restoreDetected = () => { const existing=new Set(draft!.shell_profiles.map(profile=>profile.id));change('shell_profiles',[...draft!.shell_profiles,...detectedProfiles.filter(profile=>!existing.has(profile.id)).map(profile=>({...profile,configured:undefined}))]) }
  const refreshUsage = async () => {
    setUsageRefreshMessage('Refreshing agent usage… this may take up to a minute.')
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
    // Theme and chrome scale are previewed live as you pick them, so discarding
    // has to put both back — not just the draft that was never saved.
    if(config){configureCustomTheme(config.custom_theme);applyTheme(config.theme);onUiScalePreview(config)}
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
  // Re-seed the arguments text only when the selection moves. Keyed on the id and
  // not on the profile object, which is rebuilt on every keystroke.
  useEffect(()=>{
    const profile=draft?.shell_profiles.find(item=>item.id===selectedProfileId)
    setArgsText(formatCommandLine(profile?.args||[]))
  },[selectedProfileId])
  /** A short scannable tag, derived rather than typed. */
  const profileTag=(profile:LaunchProfile)=>{
    if(profile.backend!=='shell')return (harnessDescriptor(profile.backend)?.cli_name||profile.backend).slice(0,3)
    const name=(profile.executable.split(/[\\/]/).pop()||'').replace(/\.exe$/i,'').toLowerCase()
    if(name==='pwsh')return 'ps7'
    if(name==='powershell')return 'ps'
    return name.slice(0,3)||'sh'
  }
  /** What the daemon last derived for this profile, and whether it is now stale. */
  const savedCapabilities=(profile:LaunchProfile)=>savedProfiles.find(item=>item.id===profile.id)?.capabilities
  const capabilitiesStale=(profile:LaunchProfile)=>{
    const saved=savedProfiles.find(item=>item.id===profile.id)
    if(!saved)return true
    return saved.backend!==profile.backend||saved.executable!==profile.executable
      ||saved.cwd_strategy!==profile.cwd_strategy||saved.cwd_integration!==profile.cwd_integration
      ||formatCommandLine(saved.args)!==formatCommandLine(profile.args)
  }
  // Before the bundle lands the panel renders its full chrome — header, tab
  // rail, footer — with a placeholder in the content area, so opening Settings
  // paints immediately and the chosen tab can be selected while data loads.
  if (!draft) return <div class="settings-layer" onMouseDown={event=>event.target===event.currentTarget&&requestClose()}><section class="settings-panel" ref={panel} role="dialog" aria-modal="true" aria-label="Settings">
    <header><div><span>CONFIG::V6</span><h2>Settings</h2></div>
      {/* Disabled twin of the real search box: the index needs the config, and a
          control that appears late would shift the header out from under a tap. */}
      <div class="settings-search"><input type="search" disabled placeholder="Search settings…" aria-label="Search settings (loading)" /></div>
      <button aria-label="Close Settings" onClick={()=>requestClose()}>×</button></header>
    <main class="settings-body">
      <nav class="settings-tabs" role="tablist" aria-label="Settings sections">
        {settingsTabs.map(tab=><button role="tab" aria-selected={activeTab===tab.id} class={activeTab===tab.id?'active':''} onClick={()=>setActiveTab(tab.id)}>{tab.label}</button>)}
      </nav>
      <div class="settings-content"><section class="settings-loading" role="status" aria-live="polite">{status}</section></div>
    </main>
    <footer><span aria-live="polite">{status}</span><button onClick={()=>requestClose()}>Cancel</button></footer>
  </section></div>
  const modelOptions=(selected:string)=>includeSelectedModel(provider?.models.models||[],selected)
  // One function renders every tab, and it takes the tab id as an argument
  // instead of reading `activeTab` from state, so the search index can build the
  // vnode tree of a tab that is not mounted. Building vnodes only allocates plain
  // objects — no DOM, no effects, no child-component bodies run — which is what
  // lets the index be derived from the same JSX that renders the form rather than
  // from a hand-maintained duplicate list of every setting.
  const tabContent = (activeTab: SettingsTab) => <Fragment>
        {activeTab==='general'&&<section><h3>General</h3>
          <label>Startup directory<input value={draft.startup_cwd} onInput={e=>change('startup_cwd',e.currentTarget.value)} /></label>
          <label>Default backend<select value={draft.default_backend} onChange={e=>change('default_backend',e.currentTarget.value)}>{allBackendNames().map(name=><option value={name}>{name==='shell'?'Shell':harnessDisplayName(name)}</option>)}</select></label>
          <label>Scrollback bytes<input type="number" value={draft.scrollback_bytes} onInput={e=>change('scrollback_bytes',Number(e.currentTarget.value))} /></label>
          <label>History limit<input type="number" value={draft.history_limit} onInput={e=>change('history_limit',Number(e.currentTarget.value))} /></label>
          {/* Setup commands offered when a Project is registered. They are yours, typed
              here, stored on this machine — nothing is ever read out of a repository, so
              there is no trust prompt and no fingerprint to approve. */}
          <div class="settings-init-scripts">
            <div class="settings-init-scripts-head"><div><strong>Project setup commands</strong><p>Offered as unchecked options in Add project. Each selected command opens its own one-shot terminal at the new Project root, started in this order.</p></div><button onClick={addInitScript}>+ Add command</button></div>
            {initScripts().map((script,index)=><div class="settings-init-script" key={script.id}>
              <label>Name<input value={script.label} placeholder="Initialize git" onInput={e=>updateInitScript(index,{label:e.currentTarget.value})} /></label>
              <label>Command<textarea value={script.command} placeholder="git init && git commit --allow-empty -m init" onInput={e=>updateInitScript(index,{command:e.currentTarget.value})} /></label>
              <div class="settings-init-script-actions">
                <label class="check"><span>Checked by default</span><input type="checkbox" checked={!!script.default_enabled} onChange={e=>updateInitScript(index,{default_enabled:e.currentTarget.checked})} /></label>
                <button disabled={index===0} onClick={()=>moveInitScript(index,-1)}>↑</button>
                <button disabled={index===initScripts().length-1} onClick={()=>moveInitScript(index,1)}>↓</button>
                <button class="danger" onClick={()=>removeInitScript(index)}>Remove</button>
              </div>
            </div>)}
            {!initScripts().length&&<p>No setup commands yet. Add one to have it offered whenever you register a Project.</p>}
          </div>
          <div class="settings-tutorial-reset"><div><strong>Getting started tutorial</strong><p>Replay the guided tour of Projects, provider accounts, tabs, pane splits, resources, and the main navigation.</p></div><button onClick={()=>requestClose('tutorial')}>Reset &amp; run tutorial</button></div>
          {/* Config-file actions live here rather than in the footer: they act on the
              whole configuration, not the visible tab, so repeating them under every
              tab implied a per-tab scope they never had — and on a phone they pushed
              Cancel/Save into a horizontally scrolling footer. */}
          <div class="settings-config-actions"><div><strong>Configuration file</strong><p>Stored in <code>{draft.data_dir}</code>. The export is sanitized of credentials. Restoring defaults rewrites the saved configuration at once — it is not staged behind <em>Save changes</em>, and it discards unsaved edits.</p></div>
            <div><button onClick={()=>void api('POST','/api/reveal',{path:draft.data_dir})}>Reveal config directory</button><button onClick={exportConfig}>Export sanitized</button><button class="danger" onClick={()=>void reset()}>Restore defaults</button></div>
          </div>
        </section>}

        {activeTab==='terminals'&&<section class="profile-settings"><h3>Terminals</h3>
          <label>Renderer<select value={draft.terminal_renderer} onChange={e=>change('terminal_renderer',e.currentTarget.value as Config['terminal_renderer'])}><option value="auto">Auto (WebGL with DOM fallback)</option><option value="webgl">Prefer WebGL</option><option value="dom">DOM compatibility mode</option></select></label>
        <p>Mobile viewports and Claude sessions always use the built-in DOM renderer. Auto also uses DOM for terminals that repaint scrollback.</p>
          <label>Claude width limit<select value={String(draft.claude_max_columns)} onChange={e=>change('claude_max_columns',Number(e.currentTarget.value) as ClaudeMaxColumns)}>{CLAUDE_MAX_COLUMN_STEPS.map(step=><option value={String(step)}>{claudeMaxColumnsLabel(step)}</option>)}</select></label>
          <p>Claude Code's renderer can leave stale and duplicated cells when its width changes by a lot, so a Claude pane dragged past this many columns adds margin instead of resizing the terminal again. That is why a wide Claude pane stops growing its text while Codex and shell panes keep filling the space. Raise it for wide diffs and long log lines; choose <strong>No limit</strong> to let Claude fill its pane like every other session, and watch for leftover text on the right after a resize. Phone and other compact panes are never limited - they are narrower than the smallest setting here.</p>
          <label>Global default terminal profile<select value={draft.default_shell_profile} onChange={e=>change('default_shell_profile',e.currentTarget.value)}>{draft.shell_profiles.filter(profile=>profile.enabled&&profile.backend==='shell').map(profile=><option value={profile.id}>{profile.label}</option>)}</select></label>
          <p>A launch profile names an executable, arguments, and environment for one backend. A <strong>shell</strong> profile is a terminal. An <strong>agent</strong> profile starts a harness with extra arguments, so one Project can offer Claude and Claude (plan) side by side; pick which one a Project starts by default in Projects → Options.</p>
          <div class="profile-browser">
            <div class="profile-index" aria-label="Configured launch profiles">
              {draft.shell_profiles.map(profile=><button class={selectedProfileId===profile.id?'active':''} onClick={()=>setSelectedProfileId(selectedProfileId===profile.id?null:profile.id)}><span>{profileTag(profile)}</span><strong>{profile.label}</strong><small>{profile.id} · {profile.backend==='shell'?'shell':harnessDisplayName(profile.backend)} · {profile.enabled?'on':'off'}</small></button>)}
              <div class="profile-index-actions"><button onClick={()=>addProfile()}>+ add shell</button>{harnesses().map(harness=><button key={harness.name} onClick={()=>addProfile(undefined,harness.name)}>+ add {harness.display_name}</button>)}<button onClick={restoreDetected}>restore detected</button></div>
            </div>
            {selectedProfile&&<article class="profile-editor">
              <header><strong>PROFILE::{selectedProfile.label}</strong><button aria-label="Collapse launch profile" onClick={()=>setSelectedProfileId(null)}>×</button></header>
              <label>Profile ID<input value={selectedProfile.id} onInput={e=>updateProfile(selectedProfileIndex,{id:e.currentTarget.value})}/></label>
              <label>Label<input value={selectedProfile.label} onInput={e=>updateProfile(selectedProfileIndex,{label:e.currentTarget.value})}/></label>
              <label>Backend<select value={selectedProfile.backend} onChange={e=>updateProfile(selectedProfileIndex,{backend:e.currentTarget.value as ProjectBackend})}><option value="shell">Shell</option>{harnesses().map(harness=><option value={harness.name}>{harness.display_name}</option>)}</select></label>
              <label>Executable{selectedProfile.backend!=='shell'&&<em> optional</em>}<input value={selectedProfile.executable} placeholder={selectedProfile.backend==='shell'?'':draft.harness_exe[selectedProfile.backend]||''} onInput={e=>updateProfile(selectedProfileIndex,{executable:e.currentTarget.value})}/></label>
              <label>Arguments<input value={argsText} spellcheck={false} placeholder={selectedProfile.backend==='shell'?'-NoLogo':'--model claude-opus-4-8'} onInput={e=>{const text=e.currentTarget.value;setArgsText(text);updateProfile(selectedProfileIndex,{args:parseCommandLine(text)})}}/></label>
              <p class="profile-hint">Type it as you would in a terminal. Quote anything containing a space: <code>--append-system-prompt "be terse"</code>. A backslash is literal, so Windows paths need no escaping.</p>
              {selectedProfile.backend!=='shell'&&reservedConflict(selectedProfile.backend,selectedProfile.args)&&<p class="error" role="alert">{reservedConflict(selectedProfile.backend,selectedProfile.args)} is built by swe-mux for {harnessDisplayName(selectedProfile.backend)} and cannot be set here.</p>}
              <label>Environment<textarea value={Object.entries(selectedProfile.env).map(([key,value])=>`${key}=${value}`).join('\n')} onInput={e=>updateProfile(selectedProfileIndex,{env:Object.fromEntries(e.currentTarget.value.split('\n').filter(line=>line.includes('=')).map(line=>{const at=line.indexOf('=');return [line.slice(0,at),line.slice(at+1)]}))})}/></label>
              {selectedProfile.backend==='shell'&&<><label>Cwd strategy<select value={selectedProfile.cwd_strategy} onChange={e=>updateProfile(selectedProfileIndex,{cwd_strategy:e.currentTarget.value as LaunchProfile['cwd_strategy']})}><option value="native">native</option><option value="home">home</option><option value="wsl">wsl</option></select></label>
              <label class="check"><span>Live cwd telemetry</span><input type="checkbox" checked={selectedProfile.cwd_integration} onChange={e=>updateProfile(selectedProfileIndex,{cwd_integration:e.currentTarget.checked})}/></label></>}
              <div class="profile-preview"><span>LAUNCHES</span><code>{launchPreview(selectedProfile.executable||(selectedProfile.backend!=='shell'?draft.harness_exe[selectedProfile.backend]||selectedProfile.backend:''),selectedProfile.backend==='shell'?[]:parseCommandLine(harnessArgs[selectedProfile.backend]||''),selectedProfile.args)}</code>
                <small>{selectedProfile.backend==='shell'?'swe-mux appends its own bootstrap to an interactive PowerShell profile.':'swe-mux adds the conversation id, the per-session settings file, and the MCP registration around these.'}</small>
              </div>
              <div class="profile-capabilities"><span>SUPPORTS</span>
                {(savedCapabilities(selectedProfile)||[]).map(item=><em key={item}>{item}</em>)}
                {!(savedCapabilities(selectedProfile)||[]).length&&<em class="muted">nothing yet</em>}
                <small>{capabilitiesStale(selectedProfile)?'Derived by the daemon; recomputed when you save.':'Derived by the daemon from this profile.'}</small>
              </div>
              <div class="profile-editor-actions"><button onClick={()=>moveProfile(selectedProfileIndex,-1)}>move up</button><button onClick={()=>moveProfile(selectedProfileIndex,1)}>move down</button><button onClick={()=>addProfile(selectedProfile)}>duplicate</button><button onClick={()=>updateProfile(selectedProfileIndex,{enabled:!selectedProfile.enabled})}>{selectedProfile.enabled?'disable':'enable'}</button>{/* The last *shell* profile is what cannot go: `default_shell_profile` must name
    one, and with none left the select above has no options, so the save would fail
    on a field the user could no longer set. Agent profiles are freely removable. */}
<button class="danger" disabled={selectedProfile.backend==='shell'&&draft.shell_profiles.filter(profile=>profile.backend==='shell').length===1} onClick={()=>{change('shell_profiles',draft.shell_profiles.filter((_,index)=>index!==selectedProfileIndex));setSelectedProfileId(null)}}>remove</button></div>
              <small>{selectedProfile.backend==='shell'?'swe-mux wraps this shell process only and never edits your shell profile files.':`These arguments follow the ${harnessDisplayName(selectedProfile.backend)} default args and precede anything the launch itself asks for. Reserved: ${reservedArgs(selectedProfile.backend).join(' ')||'none'}.`}</small>
            </article>}
            {!selectedProfile&&<div class="profile-placeholder"><span>TERMINAL::PROFILES</span><strong>Select a profile to inspect or edit it.</strong><p>Nothing is expanded until you choose one.</p></div>}
          </div>
        </section>}

        {/* Per-Project options are NOT here: the Projects registry is the single
            per-Project editor (menu → Manage projects, or a Project's context menu →
            Project settings…). This tab is the global half only. */}
        {activeTab==='workspace'&&<Fragment>
          <section><h3>Git and worktrees</h3><label>Worktree root<input value={draft.worktree_root} onInput={e=>change('worktree_root',e.currentTarget.value)}/></label><p>New Run worktrees are grouped below this absolute directory by Project and branch. Clear the field to restore <code>{draft.data_dir}{draft.data_dir.includes('\\')?'\\':'/'}worktrees</code>. Existing worktrees and manually edited Run paths are not moved.</p><label>Git poll seconds<input type="number" step=".25" value={draft.git_poll_seconds} onInput={e=>change('git_poll_seconds',Number(e.currentTarget.value))} /></label></section>
          <section><h3>Project and process evidence</h3><label>Process inspector poll seconds<input type="number" min=".5" max="60" step=".5" value={draft.process_poll_seconds} onInput={e=>change('process_poll_seconds',Number(e.currentTarget.value))}/></label><label>Suspected-orphan grace seconds<input type="number" min="1" max="3600" value={draft.process_orphan_grace_seconds} onInput={e=>change('process_orphan_grace_seconds',Number(e.currentTarget.value))}/></label><label>Process evidence retention days<input type="number" min="1" max="3650" value={draft.process_evidence_retention_days} onInput={e=>change('process_evidence_retention_days',Number(e.currentTarget.value))}/></label><p>Process evidence uses PID plus creation time and command fingerprint. Surviving descendants are flagged after the grace period and are never killed automatically.</p><label>Global project ignores<textarea value={draft.project_ignore_patterns.join('\n')} onInput={e=>change('project_ignore_patterns',parseIgnorePatternDraft(e.currentTarget.value))}/></label><p>One glob per line. A name such as <code>node_modules</code> matches that folder at any depth. These rules affect the file tree and resource watchers, not Git.</p></section>
        </Fragment>}

        {/* One editor renders every Markdown surface (notes,
            Markdown files from Files), so everything here applies to all of them.
            Colours are absent on purpose: the theme already drives the editor's
            colour variables, and a second source for them would fight it. */}
        {activeTab==='notes'&&<Fragment>
          <section><h3>Note editor</h3>
            <label class="check"><span>Spellcheck</span><input type="checkbox" checked={draft.note_spellcheck} onChange={e=>change('note_spellcheck',e.currentTarget.checked)}/></label>
            <p>The browser's own spellchecker, on the editor's text input. Notes are prose, so it is worth more here than in a terminal — but it also underlines code, paths, and identifiers.</p>
            <label class="check"><span>Indent guides</span><input type="checkbox" checked={draft.note_indent_guides} onChange={e=>change('note_indent_guides',e.currentTarget.checked)}/></label>
            <p>Vertical rules marking each enclosing indent level, the way the standalone Continuity app draws them. A guide marks where a parent list item's content starts, so the outermost level shows none, and the level the caret sits in is drawn brighter.</p>
            <label>Markdown<select value={draft.note_syntax} onChange={e=>change('note_syntax',e.currentTarget.value as Config['note_syntax'])}><option value="markdown">Render Markdown</option><option value="plain">Show raw text</option></select></label>
            <p>Raw text keeps every editing feature — undo, multi-cursor, list continuation, autosave — and only stops the Markdown projection, so headings, emphasis, links, and task markers stay as the characters you typed.</p>
            <label>Tab key<select value={draft.note_tab_behavior} onChange={e=>change('note_tab_behavior',e.currentTarget.value as Config['note_tab_behavior'])}><option value="indent">Indent and outdent lines</option><option value="focus">Move focus out of the editor</option></select></label>
            <p>Indenting is the default; Escape then Tab still leaves the editor either way, so the keyboard is never trapped.</p>
            <label>Font family<input value={draft.note_font_family} placeholder="editor default: ui-monospace, Cascadia Mono, Consolas" onInput={e=>change('note_font_family',e.currentTarget.value)}/></label>
            <label>Font size<input type="number" min="0" max="48" value={draft.note_font_size_px} onInput={e=>change('note_font_size_px',Number(e.currentTarget.value))}/></label>
            <label>Line height<input type="number" min="0" max="3" step="0.05" value={draft.note_line_height} onInput={e=>change('note_line_height',Number(e.currentTarget.value))}/></label>
            <p>Pixels and a multiplier. Zero keeps the editor's own default (16px, 1.55) so a future editor update can still move it. Saving re-measures open notes in place; nothing is remounted, so no undo history is lost.</p>
          </section>

          <section><h3>Touch command rail</h3>
            <p>The editor's bottom quick-action strip — undo, indent, bullet, task, bold, heading, and this app's <code>→ agent</code> action — which runs each one without dismissing the on-screen keyboard.</p>
            <label>Show the rail<select value={draft.note_command_rail} onChange={e=>change('note_command_rail',e.currentTarget.value as Config['note_command_rail'])}><option value="auto">Automatic: touch devices only</option><option value="on">Always</option><option value="off">Never</option></select></label>
            <label>Button size<input type="number" min="0" max="96" value={draft.note_rail_button_size_px} onInput={e=>change('note_rail_button_size_px',Number(e.currentTarget.value))}/></label>
            <p>Pixels, zero for the default 48px button in a 56px rail; the rail's height and the content inset above it follow the button size together. It is deliberately not sized in <code>rem</code>, so a dense page font cannot shrink a touch target.</p>
            <div class="keybinding-heading"><div><strong>RAIL::ARRANGEMENT</strong><p>Which buttons appear, and in what order, is chosen from the gear button on the rail itself and saved by the editor per device — not in this config, so it is not part of this draft and Cancel does not undo a reset.</p></div><button type="button" onClick={resetNoteRailArrangement}>Reset rail arrangement</button></div>
          </section>

          <section><h3>Editor shortcuts</h3>
            <label>Policy<select value={draft.note_shortcut_policy} onChange={e=>change('note_shortcut_policy',e.currentTarget.value as ShortcutPolicy)}><option value="browser-safe">Browser-safe: leave browser chords alone</option><option value="editor-first">Editor first: claim every chord</option><option value="none">None: no editor shortcuts</option></select></label>
            <p>Browser-safe releases the chords Chromium claims for itself (reload, search, DevTools) so they keep doing what they do everywhere else. Editor-first suits the desktop app, where those chords are not wanted. Either way the browser may still swallow an accelerator before the page sees it.</p>
            <div class="keybinding-heading"><div><strong>EDITOR::CHORDS</strong><p>Per-chord overrides on top of that policy. <em>Run the command</em> reclaims a chord the policy would release; <em>leave to the browser</em> gives one back. These apply inside a note only, and are separate from this app's own shortcuts on the Input tab.</p></div><button type="button" onClick={()=>change('note_shortcut_overrides',{...DEFAULT_NOTE_SHORTCUT_OVERRIDES})}>Restore editor shortcut defaults</button></div>
            <input class="note-chord-filter" type="search" value={noteChordQuery} placeholder="Filter chords and commands…" aria-label="Filter editor shortcuts" spellcheck={false} onInput={e=>setNoteChordQuery(e.currentTarget.value)}/>
            <div class="note-chord-list">
              {NOTE_CHORDS.filter(binding=>{
                const needle=noteChordQuery.trim().toLowerCase()
                return !needle||binding.chord.includes(needle)||binding.command.includes(needle)
              }).map(binding=><article key={binding.chord}>
                <span class="note-chord"><kbd>{noteChordLabel(binding.chord)}</kbd>{!binding.isBrowserSafe&&<em title="A chord the browser claims first; the browser-safe policy releases it unless it is reclaimed here">browser chord</em>}</span>
                <small title={binding.command}>{binding.command}</small>
                <select aria-label={`Behaviour of ${noteChordLabel(binding.chord)} in the note editor`} value={noteChordState(draft.note_shortcut_overrides,binding.chord)} onChange={e=>setNoteChord(draft.note_shortcut_overrides,binding.chord,binding.command,e.currentTarget.value as NoteChordState)}>
                  <option value="default">Policy default</option>
                  <option value="bind">Run the command</option>
                  <option value="release">Leave to the browser</option>
                </select>
              </article>)}
            </div>
          </section>

          <section><h3>Project resources</h3><p>Project notes, Files, file editors, terminals, and previews all open as tabs in the focused pane. Drag any tab between panes or onto a pane edge to create a split.</p></section>
        </Fragment>}

        {activeTab==='agents'&&<section><h3>Agents</h3>{harnesses().map(harness=><Fragment key={harness.name}><label>{harness.display_name} executable<input value={draft.harness_exe[harness.name]||''} onInput={e=>change('harness_exe',{...draft.harness_exe,[harness.name]:e.currentTarget.value})} /></label><label>{harness.display_name} default args<input value={harnessArgs[harness.name]||''} spellcheck={false} placeholder="--model claude-opus-4-8" onInput={e=>setHarnessArgs(current=>({...current,[harness.name]:e.currentTarget.value}))} /></label><p class="profile-hint">Applies to every {harness.display_name} session. For one named alternative instead, add a launch profile under Terminal. Reserved: {(harness.reserved_launch_args||[]).join(' ')||'none'}.</p></Fragment>)}<label class="check"><span>Reconcile native history</span><input type="checkbox" checked={draft.reconcile_external_history} onChange={e=>change('reconcile_external_history',e.currentTarget.checked)} /></label>
          <div class="keybinding-heading"><div><strong>PROMPT QUEUE::AUTO-DELIVERY</strong><p>When this install-wide switch is on, every new observed agent conversation starts with bounded auto-delivery enabled. Armed messages still wait until the agent has held a safe-to-interrupt state for the whole stability window. A conversation can be turned off from its queue pane, and its grant lapses on its own once nobody has used that conversation for a while.</p></div></div>
          <label class="check"><span>Allow auto-delivery for agent conversations</span><input type="checkbox" checked={draft.auto_delivery_enabled} onChange={e=>change('auto_delivery_enabled',e.currentTarget.checked)} /></label>
          <label>Stability window seconds<input type="number" min="2" max="600" step="0.5" value={draft.auto_delivery_stable_seconds} onInput={e=>change('auto_delivery_stable_seconds',Number(e.currentTarget.value))} /></label>
          <label>Consecutive automatic sends before the grant disables itself<input type="number" min="1" max="50" value={draft.auto_delivery_max_consecutive} onInput={e=>change('auto_delivery_max_consecutive',Number(e.currentTarget.value))} /></label>
          <label>Grant lapses after this many idle minutes<input type="number" min="1" max="1440" value={draft.auto_delivery_session_ttl_minutes} onInput={e=>change('auto_delivery_session_ttl_minutes',Number(e.currentTarget.value))} /></label>
          <label>Back-off seconds after a refused attempt<input type="number" min="0" max="3600" step="0.5" value={draft.auto_delivery_refusal_backoff_seconds} onInput={e=>change('auto_delivery_refusal_backoff_seconds',Number(e.currentTarget.value))} /></label>
          <div class="quiet-hours"><label>Quiet from<input type="time" value={draft.auto_delivery_quiet_start} onInput={e=>change('auto_delivery_quiet_start',e.currentTarget.value)} /></label><label>Until<input type="time" value={draft.auto_delivery_quiet_end} onInput={e=>change('auto_delivery_quiet_end',e.currentTarget.value)} /></label></div>
          <p>These are the bounds every conversation grant runs under, not a schedule. A manual send resets the consecutive count, because it is evidence you are watching; quiet hours (local time, both empty for none) pause automatic sends only and never your own Send now. The emergency pause in the queue pane is separate and takes effect instantly.</p>
          <p>The grant is measured against idleness, not against the conversation's age: a session you are still using keeps it, and one nobody has touched for the window above loses it and gets it back when the conversation is in use again. An opt-out, an exhausted send budget, and a failed delivery are decisions rather than lapses, so those stay off until you clear them.</p>
          <div class="keybinding-heading"><div><strong>AGENT MESSAGING</strong><p>Bounds on messages agents address to each other. A message still enters the target's queue under every rule above; these limit how far one thread may travel.</p></div></div>
          <label class="check"><span>Allow agent-to-agent messages</span><input type="checkbox" checked={draft.agent_messaging_enabled} onChange={e=>change('agent_messaging_enabled',e.currentTarget.checked)} /></label>
          <label>Relay hops before a thread must be restarted by a human<input type="number" min="1" max="10" value={draft.agent_message_max_chain_depth} onInput={e=>change('agent_message_max_chain_depth',Number(e.currentTarget.value))} /></label>
          <label>Messages in one thread<input type="number" min="1" max="100" value={draft.agent_message_max_thread_turns} onInput={e=>change('agent_message_max_thread_turns',Number(e.currentTarget.value))} /></label>
          <label>Messages one session may originate per hour<input type="number" min="1" max="1000" value={draft.agent_message_hourly_budget} onInput={e=>change('agent_message_hourly_budget',Number(e.currentTarget.value))} /></label>
          <label>Pending messages allowed per target<input type="number" min="1" max="100" value={draft.agent_message_pending_per_target} onInput={e=>change('agent_message_pending_per_target',Number(e.currentTarget.value))} /></label>
          <p>Hops bound how far a hand-off propagates: each new session a thread reaches counts one, and reaching back to a session already upstream is refused outright as a ring. A relay that needs to go further is a fresh thread a human starts, not a limit to raise until it disappears.</p>
        </section>}

        {activeTab==='accounts'&&<AccountSettings/>}

        {activeTab==='input'&&<section class="input-settings"><h3>Input</h3>
          <label class="check"><span>Middle-click paste</span><input type="checkbox" checked={draft.middle_click_paste} onChange={e=>change('middle_click_paste',e.currentTarget.checked)} /></label>
          <label class="check"><span>Broadcast by default</span><input type="checkbox" checked={draft.broadcast_default} onChange={e=>change('broadcast_default',e.currentTarget.checked)} /></label>
          <div class="keybinding-heading"><div><strong>MOBILE::TERMINAL</strong><p>Touch settings apply on coarse-pointer devices. Text input goes directly to the focused terminal.</p></div></div>
          <label>Vertical drag<select value={draft.mobile_vertical_drag} onChange={e=>change('mobile_vertical_drag',e.currentTarget.value as Config['mobile_vertical_drag'])}><option value="smart">Smart: app wheel or scrollback</option><option value="terminal">Terminal scrollback only</option><option value="application">Application wheel</option><option value="disabled">Disabled</option></select></label>
          <label>Scroll direction<select value={draft.mobile_scroll_direction} onChange={e=>change('mobile_scroll_direction',e.currentTarget.value as Config['mobile_scroll_direction'])}><option value="natural">Natural touch</option><option value="wheel">Mouse wheel</option></select></label>
          <label>Scroll sensitivity<input type="number" min="0.25" max="4" step="0.25" value={draft.mobile_scroll_sensitivity} onInput={e=>change('mobile_scroll_sensitivity',Number(e.currentTarget.value))} /></label>
          <label>Long press<select value={draft.mobile_long_press} onChange={e=>change('mobile_long_press',e.currentTarget.value as Config['mobile_long_press'])}><option value="context_menu">Select terminal text</option><option value="disabled">Disabled</option></select></label>
          <label class="check"><span>Copy terminal selection automatically</span><input type="checkbox" checked={draft.terminal_auto_copy_selection} onChange={e=>change('terminal_auto_copy_selection',e.currentTarget.checked)}/></label>
          <div class="keybinding-heading"><div><strong>CLIPBOARD::HISTORY</strong><p>Every copy made <em>inside</em> swe-mux is kept in a shared ring you can insert from on any device (panel: <code>clipboard.open</code>, by default a two-finger swipe left on touch). The OS clipboard is never read or polled, so copies from other applications do not appear. The ring lives in memory only unless you save it to disk — a durable list of copied text accumulates credentials, and it is readable by anyone who can reach this daemon.</p></div></div>
          <label class="check"><span>Keep clipboard history</span><input type="checkbox" checked={draft.clipboard_history_enabled} onChange={e=>change('clipboard_history_enabled',e.currentTarget.checked)}/></label>
          <label class="check"><span>Save history to disk (survives daemon restarts)</span><input type="checkbox" checked={draft.clipboard_history_persist} onChange={e=>change('clipboard_history_persist',e.currentTarget.checked)}/></label>
          <label class="check"><span>Skip secret-shaped copies (API keys, tokens, JWTs, private keys)</span><input type="checkbox" checked={draft.clipboard_history_redact_secrets} onChange={e=>change('clipboard_history_redact_secrets',e.currentTarget.checked)}/></label>
          <label>Entries kept<input type="number" min="1" max="2000" value={draft.clipboard_history_limit} onInput={e=>change('clipboard_history_limit',Number(e.currentTarget.value))}/></label>
          <label>Retention hours (0 keeps until evicted)<input type="number" min="0" max="8760" value={draft.clipboard_history_retention_hours} onInput={e=>change('clipboard_history_retention_hours',Number(e.currentTarget.value))}/></label>
          <label>Maximum characters per entry<input type="number" min="256" max="1000000" value={draft.clipboard_history_entry_max_chars} onInput={e=>change('clipboard_history_entry_max_chars',Number(e.currentTarget.value))}/></label>
          <p>Longer copies are skipped rather than stored truncated, so a history entry never pastes a silently partial payload. Turning history off clears the ring; turning saving off deletes what was already written. Pinned entries survive eviction, retention, and Clear.</p>
          <div class="keybinding-heading"><div><strong>TOUCH::GESTURES</strong><p>Map mobile swipe and multi-finger gestures to commands. Vertical <em>single</em>-finger drags stay reserved for terminal scrolling, but two-finger vertical swipes are mappable; edge swipes are left to the OS (back / home).</p></div><button onClick={()=>change('mobile_gestures',{...defaultMobileGestureSettings})}>Restore gesture defaults</button></div>
          {GESTURE_SLOTS.map(slot=><label>{GESTURE_LABELS[slot]}<select value={draft.mobile_gestures?.[slot]??''} onChange={e=>change('mobile_gestures',{...draft.mobile_gestures,[slot]:e.currentTarget.value})}><option value="">Disabled</option>{bindingCommands.map(command=><option value={command.id}>{command.label}</option>)}</select></label>)}
          <label class="check"><span>Swipe-away closes an open panel: either horizontal direction closes the left sidebar; swiping right closes the right side panel instead of running that swipe's binding</span><input type="checkbox" checked={draft.mobile_gesture_swipe_away_close!==false} onChange={e=>change('mobile_gesture_swipe_away_close',e.currentTarget.checked)}/></label>
          <label class="check"><span>Swipe back closes an open overlay: while a dialog is open, swiping right closes one level (the transcript inside session history returns to the results). Off restores the older behaviour where a dialog ignored every swipe; the Android back gesture keeps working either way</span><input type="checkbox" checked={draft.mobile_gesture_overlay_back!==false} onChange={e=>change('mobile_gesture_overlay_back',e.currentTarget.checked)}/></label>
          <div class="keybinding-heading"><div><strong>KEYBOARD::SHORTCUTS</strong><p>Click a command, then press the new shortcut. Changes apply when Settings is saved.</p></div><button onClick={()=>{setBindings({...bindingDefaults});setCapturingCommand(null);setBindingError('')}}>Restore shortcut defaults</button></div>
          {capturingCommand&&<div class="keybinding-capture" role="status"><span>PRESS KEYS FOR</span><strong>{bindingCommands.find(command=>command.id===capturingCommand)?.label||capturingCommand}</strong><button onClick={()=>{setCapturingCommand(null);setBindingError('')}}>Cancel</button></div>}
          {bindingError&&<p class="keybinding-error" role="alert">{bindingError}</p>}
          <div class="keybinding-list">
            {[...new Set(bindingCommands.map(command=>command.category))].map(category=><section class="keybinding-group" aria-label={`${category} shortcuts`}><h4>{category}</h4>{bindingCommands.filter(command=>command.category===category).map(command=>{const chord=bindingForCommand(command.id);return <article class={capturingCommand===command.id?'capturing':''}><button class="keybinding-command" onClick={()=>{setCapturingCommand(command.id);setBindingError('')}} title={command.id}><span>{command.label}</span><small>{command.id}</small></button><button class="keybinding-chord" onClick={()=>{setCapturingCommand(command.id);setBindingError('')}} aria-label={`Set shortcut for ${command.label}`}><kbd>{chord?displayChord(chord):'not set'}</kbd></button><button class="keybinding-clear" disabled={!chord} onClick={()=>clearBinding(command.id)} aria-label={`Clear shortcut for ${command.label}`}>×</button></article>})}</section>)}
          </div>
          <details class="keybinding-policy"><summary>Reserved shortcut policy</summary><ul>{bindingPolicy.rules.map(rule=><li>{rule}</li>)}</ul><div><strong>BROWSER</strong>{bindingPolicy.browser_reserved.map(chord=><kbd>{displayChord(chord)}</kbd>)}</div><div><strong>DESKTOP APP</strong>{bindingPolicy.desktop_only.map(chord=><kbd>{displayChord(chord)}</kbd>)}</div><div><strong>APPLICATION</strong>{bindingPolicy.application_reserved.map(chord=><kbd>{displayChord(chord)}</kbd>)}</div><div><strong>TERMINAL</strong>{bindingPolicy.terminal_reserved.map(chord=><kbd>{displayChord(chord)}</kbd>)}</div></details>
        </section>}

        {activeTab==='commandrail'&&<RailEditor/>}

        {activeTab==='usage'&&<section><h3>Usage and operational telemetry</h3><p>The dashboard combines optional ccusage history with durable provider quota samples, reset evidence, probabilistic mux correlation, tools, explicit skills, and compactions.</p><div class="theme-actions"><button class="primary" onClick={onOpenUsage}>Open telemetry dashboard</button><button disabled={!config?.ccusage_enabled || usage?.refreshing || usageRefreshMessage.startsWith('Refreshing')} onClick={()=>void refreshUsage()}>{usageRefreshMessage.startsWith('Refreshing')?'Refreshing…':'Refresh historical usage'}</button><button onClick={()=>void clearUsage()}>Clear ccusage cache</button></div><label>Operational telemetry retention days<input type="number" min="1" max="3650" value={draft.operational_telemetry_retention_days} onInput={e=>change('operational_telemetry_retention_days',Number(e.currentTarget.value))}/></label><label>Provider quota poll minutes<input type="number" min="5" max="1440" value={draft.provider_quota_poll_minutes} onInput={e=>change('provider_quota_poll_minutes',Number(e.currentTarget.value))}/></label><label class="check"><span>Refresh active quota after eligible root turns</span><input type="checkbox" checked={draft.provider_quota_turn_refresh_enabled} onChange={e=>change('provider_quota_turn_refresh_enabled',e.currentTarget.checked)}/></label><label>Minimum minutes between turn-triggered refreshes<input type="number" min="1" max="1440" value={draft.provider_quota_turn_refresh_min_minutes} onInput={e=>change('provider_quota_turn_refresh_min_minutes',Number(e.currentTarget.value))}/></label><p>Turn-triggered refresh is globally rate limited, selected-account only, and never assumes provider data updates immediately. Unexpected-reset sounds are optional per device in the account switcher.</p><h3>Historical ccusage</h3><p class={usageRefreshMessage.startsWith('Refresh failed')?'settings-inline-error':''} aria-live="polite">{usageRefreshMessage || (usage ? Object.entries(usage.states).map(([provider,state])=>`${provider}: ${state.status}${state.error?` (${state.error})`:''}`).join(' · ') : 'usage status unavailable')}</p>{draft.ccusage_enabled&&!config?.ccusage_enabled&&<p>Save these settings before refreshing.</p>}<label class="check"><span>Enable ccusage refresh</span><input type="checkbox" checked={draft.ccusage_enabled} onChange={e=>change('ccusage_enabled',e.currentTarget.checked)} /></label><label>Background refresh minutes<input type="number" min="0" max="1440" value={draft.ccusage_refresh_minutes} onInput={e=>change('ccusage_refresh_minutes',Number(e.currentTarget.value))} /></label><label>Install/update command<input readonly value={usage?.install_command||'npm install -g ccusage@latest'} onFocus={event=>event.currentTarget.select()} /></label><button onClick={()=>void navigator.clipboard.writeText(usage?.install_command||'npm install -g ccusage@latest')}>Copy install command</button><p>The `latest` tag is resolved when you install or update. Refreshes use the installed unified executable and never download code in the background.</p><details class="settings-advanced"><summary>Advanced command overrides</summary>{Object.entries(draft.usage_commands).map(([name,command])=><label>{harnessDisplayName(name)} command<textarea value={command.join('\n')} onInput={e=>change('usage_commands',{...draft.usage_commands,[name]:e.currentTarget.value.split('\n').filter(Boolean)})} /></label>)}</details></section>}

        {activeTab==='automation'&&<section><h3>Automation</h3>
          <p>Enable and disable automation in the Automation dashboard. Settings holds provider, model, budget, execution, and advanced rule configuration.</p>
          <div class="theme-actions"><button class="primary" onClick={onOpenAutomation}>Open Automation dashboard</button></div>
          <p class="settings-warning">Privacy boundary: each enabled observer sends only its selected bounded transcript slice to OpenRouter and the routed model provider. swe-mux does not crawl project files.</p>
          <h3>OpenRouter</h3>
          <p><span class={`state-dot ${provider?.secret.configured?'idle':'running'}`}/> key::{provider?.secret.configured?'configured':'not configured'} · source::{provider?.secret.source||'none'} · endpoint::{provider?.origin||'fixed OpenRouter API'}</p>
          <label>API key<input type="password" autocomplete="off" value={openRouterKey} placeholder={provider?.secret.configured?'write only · enter to replace':'sk-or-…'} onInput={event=>setOpenRouterKey(event.currentTarget.value)} /></label>
          <div class="theme-actions"><button disabled={!openRouterKey} onClick={()=>void providerKeyAction('test')}>Test entered key</button><button class="primary" disabled={!openRouterKey} onClick={()=>void providerKeyAction('set')}>Test + set/replace</button><button disabled={!provider?.secret.configured} onClick={()=>void providerKeyAction('clear')}>Clear stored key</button></div>
          <p aria-live="polite">{providerMessage||'The key is write-only and never appears in config, exports, logs, or browser reads.'}</p>
          <div class="theme-actions"><button disabled={!provider?.secret.configured} onClick={()=>void refreshModels()}>Refresh models</button><span>{provider?.models.models.length||0} models{provider?.models.stale?' · stale':''}{provider?.models.error?` · ${provider.models.error}`:''}</span></div>
          <label for="cheap-model-picker">Cheap model<ModelPicker id="cheap-model-picker" value={draft.openrouter_cheap_model} options={modelOptions(draft.openrouter_cheap_model)} emptyLabel="Select exact model…" onChange={value=>change('openrouter_cheap_model',value)}/></label>
          <label for="standard-model-picker">Standard model<ModelPicker id="standard-model-picker" value={draft.openrouter_standard_model} options={modelOptions(draft.openrouter_standard_model)} emptyLabel="Select exact model…" onChange={value=>change('openrouter_standard_model',value)}/></label>
          <label>Scan timeline model<input value={draft.scan_timeline_model} readOnly /><small>Fixed default: OpenRouter DeepSeek V4 Flash latest alias.</small></label>
          <h3>Budgets + execution</h3>
          <label>Daily token budget<input type="number" value={draft.automation_daily_token_budget} onInput={event=>change('automation_daily_token_budget',Number(event.currentTarget.value))}/></label>
          <label>Daily dollar budget<input type="number" step="0.01" value={draft.automation_daily_budget_usd} onInput={event=>change('automation_daily_budget_usd',Number(event.currentTarget.value))}/></label>
          <label>Per-rule daily tokens<input type="number" value={draft.automation_rule_daily_token_budget} onInput={event=>change('automation_rule_daily_token_budget',Number(event.currentTarget.value))}/></label>
          <label>Per-rule daily dollars<input type="number" step="0.01" value={draft.automation_rule_daily_budget_usd} onInput={event=>change('automation_rule_daily_budget_usd',Number(event.currentTarget.value))}/></label>
          <label>Scan tokens per run<input type="number" min="512" max="1000000" value={draft.scan_timeline_run_token_budget} onInput={event=>change('scan_timeline_run_token_budget',Number(event.currentTarget.value))}/></label>
          <label>Hourly call cap<input type="number" value={draft.automation_hourly_call_cap} onInput={event=>change('automation_hourly_call_cap',Number(event.currentTarget.value))}/></label>
          <label>Per-rule hourly calls<input type="number" value={draft.automation_rule_hourly_call_cap} onInput={event=>change('automation_rule_hourly_call_cap',Number(event.currentTarget.value))}/></label>
          <label>Concurrent observers<input type="number" min="1" max="16" value={draft.automation_concurrency} onInput={event=>change('automation_concurrency',Number(event.currentTarget.value))}/></label>
          <label>Maximum input tokens<input type="number" value={draft.automation_max_input_tokens} onInput={event=>change('automation_max_input_tokens',Number(event.currentTarget.value))}/></label>
          <label>Maximum output tokens<input type="number" value={draft.automation_max_output_tokens} onInput={event=>change('automation_max_output_tokens',Number(event.currentTarget.value))}/></label>
          <label>Retention days<input type="number" value={draft.automation_retention_days} onInput={event=>change('automation_retention_days',Number(event.currentTarget.value))}/></label>
          <h3>Attention</h3>
          <p>Ranking decides which findings are worth interrupting you for. The daily budget is a hard bound counted per incident, so several detectors describing one event spend one slot. Cheap-to-resolve work never spends any. Ranked items appear in Alerts and are never pushed to a device.</p>
          <label>Daily interrupts<input type="number" min="0" max="100" value={draft.attention_daily_interrupt_budget} onInput={event=>change('attention_daily_interrupt_budget',Number(event.currentTarget.value))}/></label>
          <label>Hourly burst cap<input type="number" min="0" max="100" value={draft.attention_hourly_interrupt_cap} onInput={event=>change('attention_hourly_interrupt_cap',Number(event.currentTarget.value))}/></label>
          <label>Incident window seconds<input type="number" min="60" max="86400" value={draft.attention_incident_window_seconds} onInput={event=>change('attention_incident_window_seconds',Number(event.currentTarget.value))}/></label>
          <label class="check"><span>Report shell breakpoints (OSC 133)</span><input type="checkbox" checked={draft.attention_breakpoint_markers} onChange={event=>change('attention_breakpoint_markers',event.currentTarget.checked)}/></label>
          <label class="check"><span>Model narration on ranked items</span><input type="checkbox" checked={draft.attention_narration_enabled} onChange={event=>change('attention_narration_enabled',event.currentTarget.checked)}/></label>
          <label for="narration-model-picker">Narration model<ModelPicker id="narration-model-picker" value={draft.attention_narration_model} options={modelOptions(draft.attention_narration_model)} emptyLabel="Use the cheap model…" onChange={value=>change('attention_narration_model',value)}/></label>
          <label>Narration daily dollars<input type="number" step="0.01" min="0" max="100" value={draft.attention_narration_daily_budget_usd} onInput={event=>change('attention_narration_daily_budget_usd',Number(event.currentTarget.value))}/></label>
          <details class="settings-advanced"><summary>Advanced rules.toml editor</summary><p>Canonical machine-owned rules only. Repository .swe-mux/rules.toml files remain diagnostic and inert.</p><label>rules.toml<textarea value={rules} onInput={event=>setRules(event.currentTarget.value)} /></label></details>
          <p aria-live="polite">engine::{automation?.diagnostic?'error':'ready'} · rules::{automation?.rules.length||0} · queue::{automation?.queue.size||0}/{automation?.queue.capacity||0} · dropped::{automation?.queue.dropped||0}{automation?.legacy.active?' · legacy hooks compatibility active':''}</p>
        </section>}

        {activeTab==='notifications'&&<NotificationAlertSettings/>}

        {activeTab==='voice'&&<section><h3>Read aloud (TTS)</h3>
          <p>Mark an observed agent session with its pane <code>tts:</code> chip or context menu. On demand adds a speak button; auto generates audio when each reply completes. Playback and per-device autoplay live in the pane's player strip.</p>
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
          <label class="check"><span>Enable microphone input and hands-free Conversation mode</span><input type="checkbox" checked={draft.stt_enabled} onChange={e=>change('stt_enabled',e.currentTarget.checked)} /></label>
          <label>Daemon transcription engine<select value={draft.stt_engine} onChange={e=>change('stt_engine',e.currentTarget.value as Config['stt_engine'])}><option value="whisper">Whisper Turbo (local, recommended)</option><option value="sapi">Windows Speech Recognition (legacy)</option></select></label>
          <label>Recognition language<input value={draft.stt_language} placeholder="en-US" onInput={e=>change('stt_language',e.currentTarget.value)} /></label>
          {draft.stt_engine==='whisper'&&<label>Dictation model<input value={draft.stt_whisper_model} placeholder="turbo" onInput={e=>change('stt_whisper_model',e.currentTarget.value)} /></label>}
          {draft.stt_engine==='whisper'&&<label title="Used for the speculative pass that only has to recognize a wake word and a command phrase. Blank decodes commands on the dictation model: correct, but slower.">Routing model (spoken commands)<input value={draft.stt_routing_model} placeholder="small.en" onInput={e=>change('stt_routing_model',e.currentTarget.value)} /></label>}
          <p>STT::{voiceInfo?.stt_available?'available':'unavailable'} · engine::{voiceInfo?.stt_engine||draft.stt_engine}{voiceInfo?.stt_diagnostic?` · ${voiceInfo.stt_diagnostic}`:''}</p>
          <p>Conversation mode captures microphone audio in swe-mux, sends bounded speech-only WAV utterances to muxd, and keeps listening across pauses. It acts only when an utterance ends with a wake word followed by a command phrase; everything before that is buffered as your message. Raw audio is deleted after transcription.</p>
          <h3>Spoken command latency</h3>
          <p>End of speech to executed action, broken into the four stages it passes through. Samples are recorded by the browser after each utterance and also written to <code>daemon.log</code>. The target is under 500 ms for a short command.</p>
          <VoiceLatencyReport report={latencyReport} onRefresh={loadLatency} onReset={resetLatency} />
          <h3>Wake words and commands</h3>
          <p>Add every spelling your recognizer actually produces (comma separated) — the matcher does not invent variants. <code>Standby</code> keeps the mic on but ignores speech until you say a <code>resume</code> command; <code>stop</code> turns Conversation mode off and releases the mic. Leave a command blank to disable its voice trigger.</p>
          <p>Fleet and reading queries are built in and deterministic: list active or pending sessions overall or in a Project, list Projects, ask for session or Project status, open numbered results, and read a named or numbered session’s last reply in the current, summary, or verbatim mode. Say <code>list voice commands</code> to hear the full groups and examples.</p>
          <label>Wake words<input value={(draft.voice_wake_words||[]).join(', ')} placeholder="mux, mucks, max" onInput={e=>change('voice_wake_words',e.currentTarget.value.split(',').map(item=>item.trim()).filter(Boolean))} /></label>
          <div class="voice-commands">
            {VOICE_ACTION_ORDER.map(action=>{
              const meta=VOICE_ACTION_META[action]
              const phrases=(draft.voice_commands||[]).find(command=>command.action===action)?.phrases||[]
              return <label key={action} title={meta.hint}><span>{meta.label} <em>· {meta.hint}</em></span><input value={phrases.join(', ')} placeholder="(no voice trigger)" onInput={e=>{
                const updated=e.currentTarget.value.split(',').map(item=>item.trim()).filter(Boolean)
                const byAction=new Map(VOICE_ACTION_ORDER.map(name=>[name as string,(draft.voice_commands||[]).find(command=>command.action===name)?.phrases||[]]))
                byAction.set(action,updated)
                change('voice_commands',VOICE_ACTION_ORDER.map(name=>({action:name,phrases:byAction.get(name)||[]})))
              }} /></label>
            })}
          </div>
          <h3>Complete voice command reference</h3>
          <p>Say a configured wake word before every command. <code>Project N</code> uses the visible sidebar order. <code>Session N</code> uses the selected Project. Braced values such as <code>{'{text}'}</code> are spoken content, not literal words.</p>
          <div class="voice-command-reference">
            {completeVoiceCatalog.map((section,index)=><details open={index===0} key={section.id}>
              <summary>{section.title} <span>{section.phrases.length+section.commands.length}</span></summary>
              {!!section.phrases.length&&<div class="voice-reference-phrases">{section.phrases.map(phrase=><code key={phrase}>{phrase}</code>)}</div>}
              {!!section.commands.length&&<div class="voice-reference-commands">{section.commands.map(command=><article key={command.id}>
                <strong>{command.label}</strong>
                {!command.available&&<small>{command.disabledReason||'Unavailable in the current workspace state'}</small>}
                <div class="voice-reference-phrases">{command.phrases.map(phrase=><code key={phrase}>{phrase}</code>)}</div>
              </article>)}</div>}
            </details>)}
          </div>
          <h4>Test what the recognizer actually hears</h4>
          <p>Speak the wake word and a command a few times. Each utterance goes through the same transcription and the same matcher the command path uses, so this reports what would really have happened. Choose a trigger word from this, not from how it looks: a good one is two or three syllables, distinctive, rare in ordinary speech, and not the start of a common word. Save any pending changes above first — the test scores against the saved configuration.</p>
          <WakeWordTester
            wakeWords={voiceInfo?.wake_words||draft.voice_wake_words||[]}
            commands={voiceInfo?.commands||draft.voice_commands||[]}
            available={!!voiceInfo?.stt_available}
            diagnostic={voiceInfo?.stt_diagnostic||''}
          />
          <h3>Mobile voice</h3>
          <p>Regular mobile access works over the direct 100.x Tailscale address. Browser microphone capture additionally requires HTTPS. swe-mux configures a private Tailscale Serve address (<code>https://&lt;device&gt;.ts.net/</code>) automatically at startup; use the button below if it needs a one-time Tailscale approval or repair.</p>
          <div class="theme-actions"><button class="primary" disabled={mobileVoiceBusy||!draft.tailnet_enabled} onClick={()=>void setupMobileVoice()}>{mobileVoiceBusy?'Setting up…':remote?.mobile_voice_configured?'Repair secure mobile voice':'Enable secure mobile voice'}</button>{remote?.mobile_voice_url&&<a href={remote.mobile_voice_url} target="_blank" rel="noreferrer">Open secure mobile voice</a>}</div>
          {mobileVoiceMessage&&<p class={mobileVoiceMessage.toLowerCase().includes('failed')?'settings-inline-error':''} aria-live="polite">{mobileVoiceMessage}</p>}
        </section>}

        {activeTab==='remote'&&<section>
          <h3>Remote and security</h3>
          <label class="check"><span>Listen on Tailscale IPv4</span><input type="checkbox" checked={draft.tailnet_enabled} onChange={event=>change('tailnet_enabled',event.currentTarget.checked)} /></label>
          <p>Changing the listener requires a daemon restart. swe-mux binds localhost plus the specific Tailscale address—never every LAN interface.</p>
          <dl><dt>Local URL</dt><dd>{remote?.listen_url||`http://${draft.host}:${draft.port}`}</dd><dt>Direct tailnet</dt><dd>{remote?.direct_available?'active':draft.tailnet_enabled?'Tailscale address unavailable':'disabled'}</dd>{remote?.tailnet_urls.map(url=><Fragment key={url}><dt>Tailnet URL</dt><dd><a href={url} target="_blank" rel="noreferrer">{url}</a></dd></Fragment>)}</dl>
          <p>Direct tailnet HTTP is encrypted in transit by Tailscale. Mobile microphone access additionally requires the private HTTPS address below.</p>
          <strong>Optional HTTPS with Tailscale Serve</strong>
          <p>{remote?.diagnostic||'Checking the private HTTPS address…'}</p>
          {remote?.funnel_detected&&<p class="settings-inline-error">Tailscale Funnel appears enabled. Public ingress is unsupported; swe-mux only configures private tailnet access.</p>}
          <div class="theme-actions"><button class="primary" disabled={mobileVoiceBusy||!draft.tailnet_enabled} onClick={()=>void setupMobileVoice()}>{mobileVoiceBusy?'Setting up…':remote?.mobile_voice_configured?'Repair secure mobile access':'Enable secure mobile access'}</button>{remote?.mobile_voice_url&&<a href={remote.mobile_voice_url} target="_blank" rel="noreferrer">Open secure address</a>}<button onClick={()=>void api<RemoteStatus>('GET','/api/remote/status').then(setRemote)}>Recheck</button></div>
          {mobileVoiceMessage&&<p class={mobileVoiceMessage.toLowerCase().includes('failed')?'settings-inline-error':''} aria-live="polite">{mobileVoiceMessage}</p>}
          <p>No public Funnel access is enabled. Regular mobile access remains available at the direct 100.x tailnet URL; Tailscale access policy controls which devices can connect.</p>
        </section>}

        {activeTab==='appearance'&&<section><h3>Appearance</h3>
          <div class="theme-field">
            <span>Theme</span>
            <ThemePicker value={draft.theme} customTheme={draft.custom_theme} open={themePickerOpen} onOpenChange={setThemePickerOpen} onChange={value=>{change('theme',value);applyTheme(value)}} />
          </div>
          {draft.theme==='custom' && <div class="theme-tokens">{Object.entries(draft.custom_theme).map(([key,value])=><label>{key}<input value={value} onInput={e=>{const custom={...draft.custom_theme,[key]:e.currentTarget.value};change('custom_theme',custom);configureCustomTheme(custom);applyTheme('custom')}} /></label>)}</div>}
          <input class="file-input" ref={themeFile} type="file" accept="application/json" onChange={e=>void importTheme(e.currentTarget.files?.[0])} />
          <div class="theme-actions"><button onClick={()=>themeFile.current?.click()}>Import theme</button><button onClick={exportTheme}>Export theme</button></div>
          <p>Settings, menus, controls, and terminal chrome use the same monospace font token.</p>
          <SessionRowSettings />
          <h3>Side panel tabs</h3>
          <label>Drawer tabs<select value={draft.drawer_tab_display} onChange={e=>change('drawer_tab_display',e.currentTarget.value as Config['drawer_tab_display'])}><option value="icon">Icons</option><option value="title">Titles</option></select></label>
          <label>Right rail<select value={draft.utility_rail_display} onChange={e=>change('utility_rail_display',e.currentTarget.value as Config['utility_rail_display'])}><option value="icon">Icons</option><option value="title">Titles</option></select></label>
          <p>The drawer's tab strips and the always-visible desktop rail keep independent icon or title modes.</p>
          <h3>Interface scale</h3>
          <label>Desktop interface scale<select value={String(draft.ui_scale_desktop)} onChange={e=>changeUiScale('ui_scale_desktop',e.currentTarget.value)}>{UI_SCALE_STEPS.map(step=><option value={String(step)}>{uiScaleLabel(step)}</option>)}</select></label>
          <label>Mobile interface scale<select value={String(draft.ui_scale_mobile)} onChange={e=>changeUiScale('ui_scale_mobile',e.currentTarget.value)}>{UI_SCALE_STEPS.map(step=><option value={String(step)}>{uiScaleLabel(step)}</option>)}</select></label>
          <p class="settings-scale-active">This window is using the <strong>{currentProfile()==='mobile'?'mobile':'desktop'}</strong> value — the other one will not change anything you can see from here.</p>
          <p>The desktop browser and the phone keep separate scales, because they rarely want the same density. Both are editable from either device, so you can size the phone from here rather than on the phone. A window picks its value by width, at the same point the mobile layout takes over, so a desktop window dragged narrow adopts the mobile scale.</p>
          <p><kbd>Ctrl</kbd>+mouse wheel, <kbd>Ctrl</kbd>+<kbd>+</kbd>, and <kbd>Ctrl</kbd>+<kbd>-</kbd> move the active value one step; <kbd>Ctrl</kbd>+<kbd>0</kbd> resets it to 100%. Scale moves the text of every menu, tab, sidebar row, panel, and terminal together with the row and bar heights that hold it, so nothing clips at a larger size. Padding, icons, and touch targets deliberately stay put. The note editor keeps its own typography under <strong>Notes</strong>.</p></section>}
  </Fragment>

  // Rebuilt on the first keystroke of a search and then reused until the search
  // ends or the config changes under it: typing costs a scan of a few hundred
  // pre-normalized strings, not fourteen vnode trees per keystroke, while state
  // that arrived after the last search (loaded prompts, edited keybindings) is
  // still picked up the next time someone looks for it.
  const searching=query.trim().length>0
  if(searching&&(searchIndex.current?.source!==draft||!wasSearching.current)){
    // The tab on screen is indexed from its live DOM instead of its vnodes, which
    // is the only way what a child component rendered (`<AccountSettings/>`, the
    // notification panels) becomes searchable — a component vnode is a function
    // reference, not markup. Those harvests are kept for the page session, so a
    // tab reached once stays fully searchable from every other tab afterwards.
    const mounted=panel.current?.querySelectorAll('.settings-content > *:not(.settings-errors)')
    if(mounted?.length){
      const index=settingsTabs.findIndex(tab=>tab.id===activeTab)
      const label=settingsTabs[index]?.label||activeTab
      liveTabEntries.set(activeTab,[...mounted].flatMap(node=>harvestSettings(domVNode(node),activeTab,label,index)))
    }
    // Building a tab's vnodes evaluates the expressions inside its JSX, and those
    // now run for tabs nobody opened. A latent throw in one tab must cost that
    // tab's entries, not the whole panel — this runs during render.
    searchIndex.current={source:draft,entries:settingsTabs.flatMap((tab,index)=>{
      const own=liveTabEntries.get(tab.id)
      if(own)return [tabEntry(tab.id,tab.label,index),...own]
      try{return [tabEntry(tab.id,tab.label,index),...harvestSettings(tabContent(tab.id),tab.id,tab.label,index)]}
      catch{return [tabEntry(tab.id,tab.label,index)]}
    })}
  }
  wasSearching.current=searching
  const searchResults=searching?searchSettings(searchIndex.current?.entries||[],query):[]
  const activeResult=Math.min(highlight,Math.max(0,searchResults.length-1))
  const openResult=(entry:SettingsSearchEntry)=>{
    setActiveTab(entry.tab as SettingsTab)
    setJump({entry})
    setQuery('')
    setHighlight(0)
  }
  const onSearchKey=(event:{key:string;preventDefault:()=>void})=>{
    if(!searchResults.length)return
    if(event.key==='ArrowDown'){event.preventDefault();setHighlight((activeResult+1)%searchResults.length)}
    else if(event.key==='ArrowUp'){event.preventDefault();setHighlight((activeResult-1+searchResults.length)%searchResults.length)}
    else if(event.key==='Enter'){event.preventDefault();openResult(searchResults[activeResult])}
  }
  return <div class="settings-layer" onMouseDown={event=>event.target===event.currentTarget&&requestClose()}><section class="settings-panel" ref={panel} role="dialog" aria-modal={!closeIntent} aria-hidden={Boolean(closeIntent)} aria-label="Settings">
    <header><div><span>CONFIG::V6</span><h2>Settings</h2></div>
      <div class="settings-search">
        <input ref={searchInput} type="search" value={query} placeholder="Search settings…" aria-label="Search settings" role="combobox" aria-expanded={searchResults.length>0} aria-controls="settings-search-results" autocomplete="off" spellcheck={false} onInput={event=>{setQuery(event.currentTarget.value);setHighlight(0)}} onKeyDown={onSearchKey} />
        {!!query.trim()&&<div id="settings-search-results" class="settings-search-results" role="listbox" aria-label="Search results">
          {searchResults.length?searchResults.map((entry,index)=><button type="button" role="option" aria-selected={index===activeResult} class={index===activeResult?'active':''} key={`${entry.tab}:${entry.kind}:${entry.key}:${entry.occurrence}`} onPointerDown={event=>event.preventDefault()} onClick={()=>openResult(entry)}><strong>{entry.label}</strong><small>{entry.tabLabel}{entry.section?` · ${entry.section}`:''}</small></button>):<p>No setting matches “{query.trim()}”.</p>}
        </div>}
      </div>
      <button aria-label="Close Settings" onClick={()=>requestClose()}>×</button></header>
    {!!query.trim()&&<div class="settings-search-scrim" onPointerDown={()=>setQuery('')} />}
    <main class="settings-body">
      <nav class="settings-tabs" role="tablist" aria-label="Settings sections">
        {settingsTabs.map(tab=><button role="tab" aria-selected={activeTab===tab.id} class={activeTab===tab.id?'active':''} onClick={()=>setActiveTab(tab.id)}>{tab.label}</button>)}
      </nav>
      <div class="settings-content">
        {Object.keys(errors).length > 0 && <section class="settings-errors" aria-live="assertive"><h3>Validation errors</h3>{Object.entries(errors).map(([field,message])=><p><strong>{field}</strong> — {message}</p>)}</section>}

        {tabContent(activeTab)}
      </div>
    </main>
    <footer><span aria-live="polite">{status==='saving…'?status:dirty?'unsaved changes':status}</span><button onClick={()=>requestClose()}>Cancel</button><button class={`primary${dirty?' unsaved':''}`} disabled={!dirty||status==='saving…'} onClick={()=>void save()}>{status==='saving…'?'Saving…':dirty?'Save changes':'Saved'}</button></footer>
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
