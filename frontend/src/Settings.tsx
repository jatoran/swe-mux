import { Fragment } from 'preact'
import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
import { saveFailureStatus, type SettingsApplyResponse } from './settingsSave'
import { displayChord, type Command } from './commands'
import { isFocusTraversalKey, keyChord } from './keys'
import { dismissStack } from './dismissStack.ts'
import { useDismissLevel } from './modalFocus'
import { AccountSettings } from './ProviderAccounts'
import { NotificationAlertSettings } from './NotificationPushSettings'
import { SessionRowSettings } from './SessionRowSettings'
import { normalizeIgnorePatterns, parseIgnorePatternDraft, sameDraftValue } from './settingsDraft'
import { commonestParent } from './projectCreate'
import { listShortcutBindings, type ShortcutPolicy } from '@continuity-editor/editor'
import { applyNoteEditorConfig, DEFAULT_NOTE_SHORTCUT_OVERRIDES, resetNoteRailArrangement } from './noteEditorSettings'
import { applyTheme, configureCustomTheme, type CustomTheme, type ThemeName } from './theme'
import { kokoroVoiceLabel, sortKokoroVoices } from './kokoroVoices'
import { ThemePicker } from './ThemePicker'
import { BudgetControl } from './BudgetControl'
import type { Budget } from './types'
import { uiScaleKeyboardIntent, uiScaleLabel, UI_SCALE_STEPS, type UiScale } from './uiScale'
import { applyRailDensity, railDensityLabel, RAIL_DENSITIES, type RailDensity } from './railDensity'
import { CLAUDE_MAX_COLUMN_STEPS, claudeMaxColumnsLabel, type ClaudeMaxColumns } from './terminalViewport'
import { currentProfile } from './deviceSettings'
import { DRAWER_TABS, type DrawerTabId } from './drawerTabs'
import { canHideDrawerTab } from './drawerVisibility'
import { enableMobileVoice } from './mobileVoice'
import { autoplayEnabled, setAutoplayEnabled } from './voice'
import { TailscaleConnection, PhoneDnsChecklist, FirewallPanel, type RemoteStatus, type FirewallStatus } from './remoteConnection'
import { WslBridgePanel } from './WslBridgePanel'
import { type WslBridgeStatus } from './wslBridge'
import { ConnectPhone } from './ConnectPhone'
import { VoiceLatencyReport } from './VoiceLatencyReport'
import { WakeWordTester } from './WakeWordTester'
import {
  completeVoiceReference, VOICE_ACTION_META, VOICE_ACTION_ORDER,
} from './voiceCommandReference'
import type { LatencyReportPayload } from './voiceLatency'
import { GESTURE_SLOTS, GESTURE_LABELS, defaultMobileGestureSettings } from './mobileGestures'
import { allBackendNames, allHarnessesIncludingDisabled, appliesWidthEnvelope, harnessDescriptor, harnessDisplayName, harnesses } from './harnessRegistry'
import { domVNode, harvestSettings, kindSelector, matchIndex, searchSettings, tabEntry, type SettingsSearchEntry } from './settingsSearch'
import { flashSetting, revealSetting, settingSelector } from './settingReveal'
import {
  railSectionIds, rememberedSections, rememberedTab, rememberSection, rememberTab,
  sameRailSections, SECTION_RAIL_MIN, settingsSubpageId, settingsSubpages, settingsTabGroups, settingsTabs, tabForSection,
  type SettingsRailSection, type SettingsTab,
} from './settingsTabs'
import {
  forgetLlmProvider, verifyLlmProvider,
  capabilitySummary, type LlmProviderEntry, type LlmReadiness, type VerifyResult,
} from './llmProvider'
import type { InitScript } from './projectCreate'
import type { PromptTemplate } from './promptTemplates'
import type { LaunchProfile, Project, ProjectBackend } from './types'
import { formatCommandLine, launchPreview, parseCommandLine } from './commandLine'
import { Dropdown } from './Dropdown'
import { includeSelectedModel, type ModelOption } from './modelFilter'
import { ModelRoutingSummary } from './ModelRoutingSummary'
import { EdgeTtsSettings, type EdgeProviderStatus } from './EdgeTtsSettings'
import {
  customProviderOverride, MODEL_ROUTES, resolveRoute, type ModelRoutingConfig,
} from './modelRouting'

type Config = {
  revision:number; host:string; port:number; data_dir:string; requires_auth:boolean; access_mode:string; tailnet_enabled:boolean
  startup_cwd:string; default_backend:string; default_harness:string; shell_exe:string
  harness_exe:Record<string,string>; scrollback_bytes:number; attach_replay_bytes:number; history_limit:number
  session_recovery_checkpoint_bytes:number;session_recovery_retention_days:number
  session_recovery_max_sessions:number
  log_level:'DEBUG'|'INFO'|'WARNING'|'ERROR'
  terminal_renderer:'auto'|'dom'|'webgl'
  harness_args:Record<string,string[]>
  harness_enabled:Record<string,boolean>
  harness_mcp_enabled:Record<string,boolean>
  harness_instrument_enabled:Record<string,boolean>
  git_poll_seconds:number;worktree_root:string;new_project_parent:string;reconcile_external_history:boolean;theme:ThemeName
  drawer_tab_display:'icon'|'title'
  utility_rail_display:'icon'|'title'
  process_poll_seconds:number;process_orphan_grace_seconds:number;process_evidence_retention_days:number
  ghost_window_sweep_enabled:boolean;ghost_window_poll_seconds:number
  status_timeline_retention_days:number
  operational_telemetry_retention_days:number;provider_quota_poll_minutes:number
  provider_quota_turn_refresh_enabled:boolean;provider_quota_turn_refresh_min_minutes:number
  middle_click_paste:boolean; broadcast_default:boolean
  mobile_vertical_drag:'smart'|'terminal'|'application'|'disabled'
  mobile_scroll_direction:'natural'|'wheel';mobile_scroll_sensitivity:number
  mobile_long_press:'context_menu'|'disabled'
  mobile_gestures:Record<string,string>
  mobile_gesture_swipe_away_close:boolean
  mobile_surface_gestures:boolean
  mobile_gesture_overlay_back:boolean
  mobile_back_view_history:boolean
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
  rail_density_desktop:RailDensity;rail_density_mobile:RailDensity
  claude_max_columns:ClaudeMaxColumns
  note_shortcut_overrides:Record<string,string>
  ccusage_enabled:boolean; ccusage_refresh_minutes:number
  usage_command:string[]
  usage_commands:Record<string,string[]>
  custom_theme:CustomTheme
  default_shell_profile:string; shell_profiles:LaunchProfile[]
  agent_shims_on_shell_path:boolean
  project_ignore_patterns:string[]
  project_init_scripts:InitScript[]
  auto_delivery_enabled:boolean;auto_delivery_stable_seconds:number
  auto_delivery_max_consecutive:number;auto_delivery_session_ttl_minutes:number
  auto_delivery_reply_window_minutes:number
  agent_messaging_enabled:boolean;agent_message_limits_enabled:boolean
  agent_message_max_chain_depth:number
  agent_message_max_thread_turns:number;agent_message_hourly_budget:number
  agent_message_pending_per_target:number;agent_message_max_chars:number
  agent_interject_enabled:boolean;agent_interject_hourly_budget:number
  agent_interject_min_interval_seconds:number
  request_spawn_enabled:boolean;agent_spawn_hourly_budget:number
  session_control_enabled:boolean;session_control_hourly_budget:number
  session_control_graceful_timeout_s:number
  session_watch_enabled:boolean;session_watch_max_per_session:number;session_watch_max_minutes:number
  prompt_queue_retention_days:number
  auto_delivery_refusal_backoff_seconds:number
  auto_delivery_quiet_start:string;auto_delivery_quiet_end:string
  approval_auto_enabled:boolean;approval_allow_all_permitted:boolean
  approval_grant_ttl_minutes:number;approval_max_auto_per_grant:number
  approval_hook_timeout_seconds:number
  approval_keystroke_delivery:boolean;approval_keystroke_window_seconds:number
  automation_enabled:boolean;automation_retention_days:number;automation_concurrency:number
  automation_queue_size:number;automation_max_input_tokens:number;automation_max_output_tokens:number
  attention_narration_max_output_tokens:number
  automation_daily_budget:Budget;automation_rule_daily_budget:Budget
  automation_hourly_call_cap:number
  automation_rule_hourly_call_cap:number;openrouter_cheap_model:string
  llm_provider:string;custom_llm_base_url:string;custom_llm_model:string;custom_llm_catalog_url:string
  // The Project context card is an automation like any other, so its model, its
  // budget, and its per-build token ceilings are all edited in Settings ->
  // Automation -> Budgets and execution. The model was config-file only until the
  // 2026-08-21 settings audit, which is why two places in this app used to tell
  // the reader to look for a control that did not exist.
  project_card_model:string;project_card_daily_budget:Budget
  project_card_max_input_tokens:number;project_card_max_output_tokens:number
  land_queue_enabled:boolean;land_hourly_budget:number
  land_hold_timeout_seconds:number;land_retry_verification:boolean;land_verify_memo_seconds:number
  scheduled_runs_enabled:boolean;scheduled_runs_max_concurrent:number
  scheduled_runs_poll_seconds:number;scheduled_run_retention_days:number
  scan_timeline_enabled:boolean;scan_timeline_model:string;scan_timeline_run_budget:Budget
  scan_timeline_daily_budget:Budget
  scan_timeline_hourly_call_cap:number;scan_timeline_max_output_tokens:number
  attention_daily_interrupt_budget:number;attention_hourly_interrupt_cap:number
  attention_incident_window_seconds:number;attention_breakpoint_markers:boolean
  attention_narration_enabled:boolean;attention_narration_model:string
  attention_narration_daily_budget:Budget
  openrouter_standard_model:string;openrouter_request_timeout_seconds:number
  observer_titler_enabled:boolean
  attention_observers_enabled:boolean
  tts_enabled:boolean;tts_default_mode:'off'|'on_demand'|'auto';tts_content:'summary'|'verbatim'
  tts_engine:'sapi'|'kokoro'|'edge';tts_kokoro_voice:string;tts_kokoro_speed:number
  tts_kokoro_lexicon:Record<string,string>
  tts_sapi_voice:string;tts_sapi_rate:number
  tts_edge_python:string;tts_edge_voice:string;tts_edge_rate_percent:number
  tts_edge_volume_percent:number;tts_edge_pitch_hz:number;tts_edge_risk_ack_version:number
  tts_summary_model:string;tts_summary_max_tokens:number;tts_verbatim_max_chars:number
  tts_daily_budget:Budget;tts_cache_mb:number;stt_enabled:boolean
  stt_engine:'sapi'|'whisper';stt_language:string;stt_whisper_model:string;stt_routing_model:string
  voice_wake_words:string[];voice_commands:{action:string;phrases:string[]}[]
  voice_chat_patience_ms:number
  assistant_enabled:boolean;assistant_model:string;assistant_daily_budget:Budget
  assistant_max_output_tokens:number;assistant_context_messages:number
  assistant_trust_reversible:'auto'|'cancel_window'|'confirm'
  assistant_stream_replies:boolean
}
type KokoroModelInfo = {
  status:'not_downloaded'|'downloading'|'ready'|'error'
  total_bytes:number;downloaded_bytes:number;current_file?:string|null
  error?:string|null;voices:string[]
}
type VoiceStatusInfo = {
  enabled:boolean;engine:string;engine_available:boolean;diagnostic?:string|null;voice:string
  summary_model:string;spend_today:{tokens:number;cost_usd:number;unpriced_calls?:number}
  daily_budget:Budget
  cache_bytes:number;cache_limit_bytes:number;clip_count:number;stt_enabled:boolean
  kokoro_model?:KokoroModelInfo;kokoro_voice?:string
  kokoro_spelled_words?:{word:string;count:number;first_seen:number;last_seen:number}[]
  providers?:{sapi?:{available:boolean;diagnostic?:string|null};kokoro?:{available:boolean;diagnostic?:string|null};edge?:EdgeProviderStatus}
  stt_engine:'sapi'|'whisper';stt_available:boolean;stt_diagnostic?:string|null
  stt_language:string;stt_whisper_model:string;stt_routing_model?:string
  wake_words?:string[];commands?:{action:string;phrases:string[]}[]
}

type AutomationStatus={enabled:boolean;diagnostic?:string;rules:Array<{id:string;name:string;enabled:boolean;shadow:boolean;revision:string}>;queue:{size:number;capacity:number;dropped:number};legacy:{active:boolean;diagnostic?:string;migration:string};repository_rules:Array<{project_scope_id:string;path:string;valid:boolean;diagnostic?:string;execution:string}>}
// `models.models` is the cached OpenRouter catalog verbatim, so it already carries
// per-token pricing and the context window alongside the id and name. Typing it as
// `ModelOption` is what lets the pickers show a model's cost without a second request.
type ProviderStatus={secret:{configured:boolean;source:string;persistent:boolean};models:{models:ModelOption[];fetched_at?:number;error?:string;stale:boolean};origin:string;cheap_model:string;standard_model:string
  // Phase 15 bring-your-own endpoint. `provider` is the active id; `providers` is the
  // per-endpoint view, each with its own key status and verification; `llm` is the
  // resolved verdict every gate in the app renders.
  provider:string;providers:LlmProviderEntry[];llm:LlmReadiness}

type UsageStatus = {
  enabled:boolean; refreshing:boolean; package:string; install_command:string
  collector:{id:string;status:string;error?:string;refreshed_at?:number}
  cache?:{updated_at?:number;sources?:Record<string,{totals?:Record<string,number>}>}
}

type Prerequisite = {id:string;label:string;purpose:string;present:boolean;path:string|null;download_url:string;install_command:string}
type KeybindingCommand = {id:string;label:string;category:string}
type KeybindingPolicy = {browser_reserved:string[];desktop_only:string[];application_reserved:string[];terminal_reserved:string[];rules:string[]}
type KeybindingsResponse = {
  bindings:Record<string,string>;defaults:Record<string,string>;commands:KeybindingCommand[]
  policy:KeybindingPolicy;rejected:Record<string,string>
}
type CloseIntent = 'close'|'usage'|'automation'|'tutorial'
/** The two in-flight statuses, named because the footer and the dialogs both test for them. */
const SAVING = 'saving…'
const RESTORING = 'restoring defaults…'
/** The one atomic-save answer: the new config, the keybindings as re-read, and what committed. */
type SettingsApplyResult = SettingsApplyResponse<Config>

// One round trip for everything the panel needs on open. `config` is required;
// every other part arrives null when its section failed server-side, with the
// reason under `errors`.
type SettingsBundle = {
  config:Config
  keybindings:KeybindingsResponse|null
  profiles:{profiles:LaunchProfile[];detected:LaunchProfile[]}|null
  projects:Project[]|null
  automation:AutomationStatus|null
  provider:ProviderStatus|null
  usage:UsageStatus|null
  errors:Record<string,string>
}

/** How long a rail click owns the active section before scroll-spy resumes. Long
 *  enough to cover a smooth scroll, short enough that a manual scroll during it is
 *  not noticeably ignored. */
const SCROLL_CLAIM_MS = 800

/** Below this the section list is a slide-in drawer rather than a docked column. The
 *  same breakpoint the workspace uses, because it is the width at which the panel stops
 *  being a dialog on a desktop and becomes the whole screen. */
const SETTINGS_NARROW_QUERY = '(max-width:760px)'

// Search entries harvested from a tab's real DOM while it was on screen. Module
// scope, not component state: a tab visited in one Settings session stays fully
// searchable in the next one, for as long as the page lives.
const liveTabEntries = new Map<SettingsTab,SettingsSearchEntry[]>()

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
type HistoryScanJob = {
  status:'idle'|'running'|'completed'|'cancelled'|'failed'
  phase:string;backends:string[];scanned:number;processed:number;imported:number
  started_at:number|null;completed_at:number|null;error:string|null;cancel_requested:boolean
}
const noteChordState = (overrides:Record<string,string>,chord:string):NoteChordState =>
  !(chord in overrides) ? 'default' : overrides[chord]===''?'release':'bind'

/**
 * The one sentence saying whether model-backed features can run, and why not.
 *
 * The daemon's own `reason` is rendered verbatim rather than paraphrased per surface:
 * "no key", "no endpoint", "never verified", and "verified then edited" need four
 * different next actions, and a surface that flattened them into "not configured" would
 * be sending the reader to the wrong control three times out of four.
 */
function ProviderReadiness({readiness}:{readiness?:LlmReadiness|null}){
  if(!readiness) return null
  return <p class={readiness.ready?'provider-readiness ready':'provider-readiness blocked'}>
    <span class={`state-dot ${readiness.ready?'idle':'running'}`}/> {readiness.reason}
  </p>
}

/** Verified, edited-since, or never proven — as three distinct words, never two. */
function VerificationBadge({entry}:{entry:LlmProviderEntry}){
  if(entry.verification.verified) return <em class="project-setting-chip">verified</em>
  if(entry.verification.stale) return <em class="project-setting-chip spends">endpoint changed</em>
  if(!entry.requires_verification) return <em class="project-setting-chip">no verification needed</em>
  return <em class="project-setting-chip spends">not verified</em>
}


export function Settings({ activeUiScale, onUiScalePreview, onClose, onOpenUsage:openUsage, onOpenAutomation:openAutomation, onStartTutorial, onLaunchConfigurator, initialSection, initialSetting, revealToken, voiceCommands=[], navOpen=false, onNavOpenChange, drawerHiddenTabs=[], onDrawerTabHidden, onShowAllDrawerTabs }: { activeUiScale:UiScale;onUiScalePreview:(config:Record<string,unknown>)=>UiScale;onClose: () => void; onOpenUsage?:() => void;onOpenAutomation?:()=>void;onStartTutorial?:()=>void;
  /** Start the configurator agent. Owned by the composition root, like the tutorial:
   *  a launch places a pane in the workspace, which this panel does not have. */
  onLaunchConfigurator?:(harness?:string)=>void; initialSection?:string;
  /** `data-setting` id of one control to scroll to and flash on arrival (`settingTargets.ts`). */
  initialSetting?:string;
  /** Changes per deep-link request, so the same link twice reveals twice. */
  revealToken?:number;
  voiceCommands?:Command[];
  /** The narrow layout's section drawer, owned by the composition root so the shell's
   *  gesture recognizer can work it the way it works the workspace sidebar. */
  navOpen?:boolean;onNavOpenChange?:(open:boolean)=>void;
  /** Side-panel tab visibility, owned by the composition root so this mirror and the
   *  drawer's own context menu edit one value rather than two copies of it. */
  drawerHiddenTabs?:readonly DrawerTabId[];onDrawerTabHidden?:(tab:DrawerTabId,hidden:boolean)=>void;onShowAllDrawerTabs?:()=>void }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [draft, setDraft] = useState<Config | null>(null)
  const [automation,setAutomation]=useState<AutomationStatus|null>(null)
  const [provider,setProvider]=useState<ProviderStatus|null>(null)
  const [openRouterKey,setOpenRouterKey]=useState('')
  const [customKey,setCustomKey]=useState('')
  const [verifying,setVerifying]=useState('')
  const [verifyResult,setVerifyResult]=useState<VerifyResult|null>(null)
  const customProvider=provider?.providers?.find(entry=>entry.id==='custom')
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
  // Layer 3 of the read-aloud policy lives in this browser's localStorage, not in the
  // config, so it is read once and mirrored in local state. Deliberately not a
  // `subscribePlayback` subscription: that listener fires on every `timeupdate` of a
  // playing clip, and re-rendering the whole Settings panel four times a second to
  // keep one checkbox live is a bad trade for a value only this panel changes.
  const [deviceAutoplay, setDeviceAutoplay] = useState(() => autoplayEnabled())
  const [latencyReport, setLatencyReport] = useState<LatencyReportPayload | null>(null)
  const completeVoiceCatalog=useMemo(()=>completeVoiceReference(voiceCommands,draft?.voice_commands||[]),[voiceCommands,draft?.voice_commands])
  const [usageRefreshMessage, setUsageRefreshMessage] = useState('')
  const [remote, setRemote] = useState<RemoteStatus | null>(null)
  const [mobileVoiceBusy,setMobileVoiceBusy]=useState(false)
  const [mobileVoiceMessage,setMobileVoiceMessage]=useState('')
  const [firewall,setFirewall]=useState<FirewallStatus | null>(null)
  const [firewallBusy,setFirewallBusy]=useState(false)
  const [firewallMessage,setFirewallMessage]=useState('')
  const [wsl,setWsl]=useState<WslBridgeStatus | null>(null)
  // Which WSL action is in flight: a distro name for an install, 'firewall' for
  // the rule. One value rather than a flag per button, so two cannot run at once.
  const [wslBusy,setWslBusy]=useState('')
  const [wslProbing,setWslProbing]=useState(false)
  const [wslMessage,setWslMessage]=useState('')
  const [diagnosticsBusy,setDiagnosticsBusy]=useState(false)
  const [diagnosticsMessage,setDiagnosticsMessage]=useState('')
  const [diagnosticsText,setDiagnosticsText]=useState('')
  const [connectPhoneOpen,setConnectPhoneOpen]=useState(false)
  const [prerequisites,setPrerequisites]=useState<Prerequisite[]|null>(null)
  // Placeholder for the assistant's new-project location: the parent directory most
  // of the registered projects already live in. A hint only - never written back.
  const [projectParentHint,setProjectParentHint]=useState('')
  useEffect(()=>{void api<{root?:string}[]>('GET','/api/projects')
    .then(rows=>setProjectParentHint(commonestParent(rows.map(row=>row.root||''))))
    .catch(()=>{})},[])
  const [savedBindings, setSavedBindings] = useState<Record<string,string>>({})
  const [status, setStatus] = useState('loading…')
  const [errors, setErrors] = useState<Record<string,string>>({})
  const [scanJob, setScanJob] = useState<HistoryScanJob|null>(null)
  const [activeTab,setActiveTab] = useState<SettingsTab>(()=>initialSection?tabForSection(initialSection):rememberedTab())
  // The same breakpoint the workspace switches on, watched live rather than sampled
  // once: the panel fills the viewport at this width, so a rotation or a desktop window
  // dragged narrow has to move the section list between column and drawer with it.
  const [narrow,setNarrow] = useState(()=>window.matchMedia(SETTINGS_NARROW_QUERY).matches)
  const [railSections,setRailSections] = useState<SettingsRailSection[]>([])
  const [activeSection,setActiveSection] = useState('')
  const [selectedSubpages,setSelectedSubpages] = useState<Record<string,string>>(()=>rememberedSections())
  const [expandedTabs,setExpandedTabs] = useState<Set<SettingsTab>>(()=>new Set([initialSection?tabForSection(initialSection):rememberedTab()]))
  const [selectedProfileId,setSelectedProfileId] = useState<string|null>(null)
  const [noteChordQuery,setNoteChordQuery] = useState('')
  const [closeIntent,setCloseIntent] = useState<CloseIntent|null>(null)
  // Restore defaults is the one control in this panel that writes the whole saved
  // configuration on a single click, is not staged behind Save, and cannot be undone.
  // It asks first.
  const [resetIntent,setResetIntent] = useState(false)
  const [query,setQuery] = useState('')
  const [highlight,setHighlight] = useState(0)
  const [jump,setJump] = useState<{entry:SettingsSearchEntry}|null>(null)
  const [themePickerOpen,setThemePickerOpen] = useState(false)
  const declaredSubpages=settingsSubpages[activeTab]||[]
  const pagedSubpages=declaredSubpages.length>1
  const selectedSubpage=selectedSubpages[activeTab]||declaredSubpages[0]?.id||''
  const panel = useRef<HTMLElement>(null)
  const tabNavRef = useRef<HTMLElement>(null)
  const searchInput = useRef<HTMLInputElement>(null)
  const searchIndex = useRef<{source:Config|null;entries:SettingsSearchEntry[]}|null>(null)
  const wasSearching = useRef(false)
  const confirmPanel = useRef<HTMLElement>(null)
  const resetPanel = useRef<HTMLElement>(null)
  const themeFile = useRef<HTMLInputElement>(null)
  const restoreFocus = useRef<HTMLElement | null>(document.activeElement as HTMLElement | null)

  const selectPageForSetting=(root:ParentNode,setting:string)=>{
    if(!pagedSubpages)return
    const page=root.querySelector<HTMLElement>(settingSelector(setting))?.closest<HTMLElement>('section[data-settings-subpage]')?.dataset.settingsSubpage
    if(page)setSelectedSubpages(current=>({...current,[activeTab]:page}))
  }

  // One place that takes a config as newly authoritative. Three paths do it — the panel
  // opening, a save returning, and Restore defaults — and each used to spell the chain
  // out again. They had already drifted apart: open applied the theme and nothing else,
  // so a note-editor, chrome-scale, or rail-density value written from another device
  // reached this panel's draft but never the document. The three device previews are
  // idempotent (`App.applyConfig` re-applies the same set on every daemon
  // `configuration_changed`), so running them on open costs nothing and closes the gap.
  const adoptConfig = useCallback((next: Config) => {
    setConfig(next)
    setDraft(next)
    setHarnessArgs(Object.fromEntries(Object.entries(next.harness_args).map(([name,args])=>[name,formatCommandLine(args)])))
    configureCustomTheme(next.custom_theme)
    applyTheme(next.theme)
    applyNoteEditorConfig(next)
    onUiScalePreview(next)
    applyRailDensity(next)
  },[onUiScalePreview])

  useEffect(() => {
    api<RemoteStatus>('GET','/api/remote/status').then(setRemote).catch(()=>setRemote(null))
    api<FirewallStatus>('GET','/api/remote/firewall').then(setFirewall).catch(()=>setFirewall(null))
    // Without `probe`, so opening Settings never starts a stopped distribution.
    api<WslBridgeStatus>('GET','/api/wsl/bridge').then(setWsl).catch(()=>setWsl(null))
    api<{prerequisites:Prerequisite[]}>('GET','/api/diagnostics/prerequisites').then(p=>setPrerequisites(p.prerequisites)).catch(()=>setPrerequisites(null))
    api<VoiceStatusInfo>('GET','/api/voice').then(setVoiceInfo).catch(()=>setVoiceInfo(null))
    // Re-read on open: the player strip and the command palette also flip it.
    setDeviceAutoplay(autoplayEnabled())
    api<LatencyReportPayload>('GET','/api/voice/stt-latency').then(setLatencyReport).catch(()=>setLatencyReport(null))
    // One bundled request instead of nine — on a high-RTT client (phone over
    // Tailscale) per-request connection setup dominated the panel's open delay.
    api<SettingsBundle>('GET','/api/settings/bundle').then(bundle => {
      const { config: next, keybindings: keyData } = bundle
      // Saving unconditionally PUTs keybindings back; rendering without them
      // would let a Save overwrite their file with empty defaults. (The rules
      // file left this transaction: the Automation dashboard owns its editor.)
      if (!keyData) {
        setStatus(bundle.errors.keybindings?`keybindings: ${bundle.errors.keybindings}`:'settings payload incomplete')
        return
      }
      adoptConfig(next)
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
    }).catch(error => setStatus(error.message))
  }, [])

  // A caller that names a section while the panel is already open still redirects it.
  // Without a section there is nothing to redirect *to* - the remembered tab was already
  // chosen at mount, and re-applying it here would yank the user off a tab they just picked.
  useEffect(() => {
    if (initialSection) setActiveTab(tabForSection(initialSection))
  },[initialSection])

  useEffect(()=>rememberTab(activeTab),[activeTab])

  // Arriving on a tab by any route — sidebar click, search result, deep link — reveals
  // its pages in the sidebar. Only the chevron collapses a tree, and never automatically:
  // an expansion the panel took back on its own read as the sidebar forgetting.
  useEffect(()=>{
    setExpandedTabs(current=>current.has(activeTab)?current:new Set(current).add(activeTab))
  },[activeTab])

  // ---- In-tab section rail -------------------------------------------------
  // The rail is scroll anchors, never sub-tabs. Every section of a tab stays
  // mounted, which is what keeps the search index able to see the whole tab, keeps
  // Ctrl+F working, and keeps the single Save transaction from ever hiding a dirty
  // field behind a tab you cannot see while its validation error names it.
  const contentEl = ():HTMLElement|null => panel.current?.querySelector<HTMLElement>('.settings-content')||null
  // How far below the scroller's top edge a section counts as arrived: a small margin
  // so a heading is not measured flush against the very top pixel.
  const SECTION_SCROLL_MARGIN = 10

  // A section chosen by clicking holds the rail until the scroll settles. Without
  // this the late sections of a short tab are unpickable: scrolling to one lands at
  // the bottom of the scroller, where the bottom-of-scroller rule immediately
  // re-selects the final section, so the chip you clicked lights up and jumps away.
  const scrollClaim = useRef(0)

  const scrollToSection = useCallback((id:string,behavior:ScrollBehavior='smooth'):void => {
    const content=contentEl()
    const heading=content?.querySelector<HTMLElement>(`h3[data-settings-section="${CSS.escape(id)}"]`)
    if(!content||!heading)return
    const offset=heading.getBoundingClientRect().top-content.getBoundingClientRect().top+content.scrollTop
    scrollClaim.current=performance.now()
    content.scrollTo({top:Math.max(0,offset-SECTION_SCROLL_MARGIN),behavior})
    setActiveSection(id)
  },[])

  // Derived from the headings the tab actually rendered, never from a declared
  // list: a new `<section><h3>` joins the rail the moment it renders, and a renamed
  // one renames itself. Child panels (Accounts, Alerts, the WSL bridge) fetch before
  // they paint their headings, so a MutationObserver keeps the rail correct where a
  // one-shot read would miss them. Attributes are deliberately not observed — the
  // read stamps `data-settings-section` on each heading, which would re-trigger it.
  useEffect(()=>{
    setRailSections([])
    setActiveSection('')
    if(!draft)return
    const content=contentEl()
    if(!content)return
    let frame=0
    const read=()=>{
      frame=0
      const headings=[...content.querySelectorAll<HTMLElement>('h3')]
      const sections=pagedSubpages?declaredSubpages:railSectionIds(headings.map(heading=>heading.textContent||''))
      let at=0
      for(const heading of headings){
        const label=(heading.textContent||'').trim()
        if(!label)continue
        const id=pagedSubpages?settingsSubpageId(activeTab,label):(sections[at]?.id||'')
        heading.dataset.settingsSection=id
        const owner=heading.closest<HTMLElement>('section')
        if(owner&&id)owner.dataset.settingsSubpage=id
        at+=1
      }
      setRailSections(current=>sameRailSections(current,sections)?current:sections)
    }
    read()
    const observer=new MutationObserver(()=>{ if(!frame)frame=requestAnimationFrame(read) })
    observer.observe(content,{childList:true,subtree:true})
    return()=>{observer.disconnect();if(frame)window.cancelAnimationFrame(frame)}
  },[activeTab,draft!==null,pagedSubpages,declaredSubpages])

  // A declared subpage is a page, not a scroll anchor. The Settings form remains one
  // atomic draft, but only the selected top-level section participates in layout. This
  // DOM annotation is derived from the same headings the search index and deep links use,
  // so adding or renaming a section cannot create a second navigation vocabulary.
  useEffect(()=>{
    const content=contentEl()
    if(!content)return
    const sections=[...content.querySelectorAll<HTMLElement>('section[data-settings-subpage]')]
    if(!pagedSubpages){for(const section of sections)section.hidden=false;return}
    const valid=railSections.some(section=>section.id===selectedSubpage)
    const next=valid?selectedSubpage:(rememberedSections()[activeTab]||railSections[0]?.id||declaredSubpages[0]?.id||'')
    if(next&&next!==selectedSubpage){setSelectedSubpages(current=>({...current,[activeTab]:next}));return}
    for(const section of sections)section.hidden=section.dataset.settingsSubpage!==next
    content.scrollTop=0
  },[activeTab,declaredSubpages,pagedSubpages,railSections,selectedSubpage])

  // Scroll-spy. Skipped entirely on a tab too short to render a rail, so no tab
  // pays for a listener that has nothing to highlight.
  useEffect(()=>{
    const content=contentEl()
    if(!content||pagedSubpages||railSections.length<SECTION_RAIL_MIN)return
    let frame=0
    const measure=()=>{
      frame=0
      if(performance.now()-scrollClaim.current<SCROLL_CLAIM_MS)return
      const headings=[...content.querySelectorAll<HTMLElement>('h3[data-settings-section]')]
      if(!headings.length)return
      const top=content.getBoundingClientRect().top
      const edge=SECTION_SCROLL_MARGIN+1
      let current=headings[0]
      for(const heading of headings)if(heading.getBoundingClientRect().top-top<=edge)current=heading
      // The final section is usually shorter than the viewport, so its heading never
      // crosses the anchor line; hitting the bottom selects it explicitly rather than
      // leaving the rail one section behind for the rest of the scroll.
      if(content.scrollTop+content.clientHeight>=content.scrollHeight-2)current=headings[headings.length-1]
      setActiveSection(current.dataset.settingsSection||'')
    }
    measure()
    const onScroll=()=>{ if(!frame)frame=requestAnimationFrame(measure) }
    content.addEventListener('scroll',onScroll,{passive:true})
    return()=>{content.removeEventListener('scroll',onScroll);if(frame)window.cancelAnimationFrame(frame)}
  },[activeTab,pagedSubpages,railSections])

  // Restore the remembered section once the rail for this tab exists. A pending
  // search jump always wins: that caller named an exact control, not a section.
  const restoredFor = useRef('')
  useEffect(()=>{
    if(!railSections.length||restoredFor.current===activeTab)return
    restoredFor.current=activeTab
    if(jump)return
    const remembered=rememberedSections()[activeTab]
    if(pagedSubpages){
      const next=remembered&&railSections.some(section=>section.id===remembered)?remembered:railSections[0]?.id
      if(next)setSelectedSubpages(current=>({...current,[activeTab]:next}))
    }else if(remembered&&remembered!==railSections[0].id&&railSections.some(section=>section.id===remembered)){
      scrollToSection(remembered,'auto')
    }
  },[activeTab,pagedSubpages,railSections,jump,scrollToSection])

  useEffect(()=>{ const section=pagedSubpages?selectedSubpage:activeSection;if(section)rememberSection(activeTab,section) },[activeTab,activeSection,pagedSubpages,selectedSubpage])

  // Native-history scan status: fetch once when the Harnesses tab opens, then poll
  // while a scan is running so its progress and completion land without a refresh.
  useEffect(()=>{
    if(activeTab!=='harnesses')return
    let live=true
    const poll=async()=>{
      try{
        const job=(await api<{job:HistoryScanJob}>('GET','/api/history/scan')).job
        if(live)setScanJob(job)
        return job.status
      }catch{ return undefined }
    }
    void poll()
    const timer=window.setInterval(async()=>{ if((await poll())!=='running')window.clearInterval(timer) },900)
    return ()=>{ live=false; window.clearInterval(timer) }
  },[activeTab,scanJob?.status==='running'])

  // Ctrl+wheel/+/- is owned by App so it can intercept before xterm and browser zoom.
  // While Settings is open, reflect that active-profile preview into this panel's draft
  // so its existing Save/Discard transaction remains the only persistence authority.
  useEffect(()=>{
    const key=currentProfile()==='mobile'?'ui_scale_mobile':'ui_scale_desktop'
    setDraft(current=>current&&current[key]!==activeUiScale?{...current,[key]:activeUiScale}:current)
  },[activeUiScale,config?.revision])

  useEffect(()=>setThemePickerOpen(false),[activeTab])

  useEffect(()=>{
    const query=window.matchMedia(SETTINGS_NARROW_QUERY)
    const changed=()=>setNarrow(query.matches)
    changed();query.addEventListener('change',changed)
    return()=>query.removeEventListener('change',changed)
  },[])

  const setNavOpen=useCallback((open:boolean)=>onNavOpenChange?.(open),[onNavOpenChange])
  // Widening past the breakpoint turns the drawer back into a docked column, and a
  // column that is permanently on screen must not leave a dismiss level standing —
  // back would then swallow a press doing nothing visible.
  useEffect(()=>{if(!narrow&&navOpen)setNavOpen(false)},[narrow,navOpen,setNavOpen])
  /** Every route to a different tab, so none of them leaves the drawer standing open
   *  over the tab it just switched to: the rail, a search result, a deep link. */
  const selectTab=useCallback((tab:SettingsTab)=>{setActiveTab(tab);setNavOpen(false)},[setNavOpen])
  const toggleTabExpanded=(tab:SettingsTab)=>setExpandedTabs(current=>{
    const next=new Set(current)
    if(next.has(tab))next.delete(tab);else next.add(tab)
    return next
  })
  const selectSubpage=(tab:SettingsTab,id:string)=>{
    setSelectedSubpages(current=>({...current,[tab]:id}))
    setExpandedTabs(current=>new Set(current).add(tab))
    selectTab(tab)
  }
  /**
   * Switch tab and arrive at one named control, for a link *inside* the panel.
   *
   * `settingTargets.ts` is the registry for links from outside Settings, which must
   * survive a rename without a compiler to catch it; a link between two of this
   * component's own sections needs no such indirection. Both end at the same
   * `revealSetting`, so arriving looks identical whichever way you came.
   */
  const [pendingReveal,setPendingReveal]=useState<{setting:string;token:number}|null>(null)
  const goToSetting=useCallback((tab:SettingsTab,setting:string)=>{
    selectTab(tab)
    // A fresh token per click, so asking twice for the control you are already on
    // flashes it again rather than doing nothing.
    setPendingReveal(current=>({setting,token:(current?.token||0)+1}))
  },[selectTab])
  // Opening the drawer moves focus onto the tab you are on, so the list is navigable by
  // keyboard from where it starts rather than from wherever the trigger left the cursor.
  useEffect(()=>{
    if(!narrow||!navOpen)return
    tabNavRef.current?.querySelector<HTMLElement>('button.active')?.focus()
  },[narrow,navOpen])

  const dirty = useMemo(() => Boolean(config&&draft&&(
    !sameDraftValue(config,draft)
    ||Object.entries(config.harness_args).some(([name,args])=>harnessArgs[name]!==formatCommandLine(args))
    ||!sameDraftValue(bindings,savedBindings)
  )),[config,draft,harnessArgs,bindings,savedBindings])

  // A write to the daemon is in flight, or the last one was refused. Both are things the
  // footer has to say over the dirty hint, and `errors` is exactly the failure's lifetime:
  // every path that writes clears it on success and fills it on refusal.
  const writeBusy = status===SAVING||status===RESTORING
  const writeFailed = Object.keys(errors).length>0

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
  // The section drawer is a level exactly as the workspace sidebar is one, and for the
  // same reason: back has to close the navigation before it closes what you navigated to.
  useDismissLevel(()=>setNavOpen(false),narrow&&navOpen&&!capturingCommand,'settings-nav')
  useDismissLevel(()=>setQuery(''),!!query&&!capturingCommand,'settings-search')
  useDismissLevel(()=>setThemePickerOpen(false),themePickerOpen&&!capturingCommand,'settings-theme-picker')
  useDismissLevel(()=>setCloseIntent(null),!!closeIntent&&!capturingCommand,'settings-close-confirm')
  useDismissLevel(()=>setResetIntent(false),resetIntent&&!capturingCommand,'settings-reset-confirm')

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

  const repairFirewall=async()=>{
    if(firewallBusy)return
    setFirewallBusy(true);setFirewallMessage('Requesting an elevated firewall repair; approve the Windows prompt.')
    try{
      // Raw fetch (not the api helper) so the explicit user-gesture header the
      // daemon requires before triggering a UAC prompt is sent.
      const response=await fetch('/api/remote/firewall/repair',{method:'POST',headers:{'Content-Type':'application/json','X-Mux-User-Gesture':'firewall-repair'}})
      const result=await response.json().catch(()=>({}))
      if(response.ok&&result.ok){
        setFirewallMessage('Added a scoped inbound Allow rule. Rechecking…')
        setFirewall(await api<FirewallStatus>('GET','/api/remote/firewall'))
        setFirewallMessage('Windows Defender Firewall now allows phone connections.')
      }else{
        setFirewallMessage(result.reason==='cancelled'?'The Windows elevation prompt was declined; no rule was changed.':result.reason==='unsupported'?'Firewall repair is only available in the packaged Windows app.':'The firewall repair did not complete.')
      }
    }catch(cause){setFirewallMessage(cause instanceof Error?cause.message:String(cause))}
    finally{setFirewallBusy(false)}
  }

  const refreshWsl=async(probe:boolean)=>{
    try{setWsl(await api<WslBridgeStatus>('GET',`/api/wsl/bridge${probe?'?probe=1':''}`))}
    catch(cause){setWslMessage(cause instanceof Error?cause.message:String(cause))}
  }

  const probeWsl=async()=>{
    if(wslProbing)return
    setWslProbing(true);setWslMessage('Checking distributions; a stopped one is being started.')
    try{await refreshWsl(true);setWslMessage('')}
    finally{setWslProbing(false)}
  }

  const toggleWsl=async(enabled:boolean)=>{
    // Written straight through rather than into the settings draft: this is a
    // one-switch decision with its own explanation, and burying it in a Save
    // button would leave the panel's other actions acting on a state the daemon
    // does not have yet.
    setWslMessage(enabled?'Enabling…':'Disabling…')
    try{
      // `PATCH /api/config` with a flat field map. It was `PUT` with a `{values:{…}}`
      // wrapper, which is neither the method the daemon routes nor the body it parses,
      // so this switch answered 405 every time it was pressed.
      await api('PATCH','/api/config',{wsl_bridge_enabled:enabled})
      await refreshWsl(false)
      setWslMessage(enabled?'Enabled. Restart the daemon so it binds the WSL adapter.':'Disabled.')
    }catch(cause){setWslMessage(cause instanceof Error?cause.message:String(cause))}
  }

  const installWslBridge=async(distro:string)=>{
    if(wslBusy)return
    setWslBusy(distro);setWslMessage(`Installing the bridge into ${distro}…`)
    try{
      // Raw fetch for the user-gesture header the daemon requires before it will
      // write into a distribution.
      const response=await fetch('/api/wsl/bridge/install',{method:'POST',headers:{'Content-Type':'application/json','X-Mux-User-Gesture':'wsl-bridge-install'},body:JSON.stringify({distro})})
      const result=await response.json().catch(()=>({}))
      if(response.ok&&result.ok){await refreshWsl(true);setWslMessage(`Installed the bridge into ${distro}.`)}
      else setWslMessage(result.reason||`The bridge could not be installed into ${distro}.`)
    }catch(cause){setWslMessage(cause instanceof Error?cause.message:String(cause))}
    finally{setWslBusy('')}
  }

  const repairWslFirewall=async()=>{
    if(wslBusy)return
    setWslBusy('firewall');setWslMessage('Requesting an elevated firewall rule; approve the Windows prompt.')
    try{
      const response=await fetch('/api/wsl/bridge/firewall/repair',{method:'POST',headers:{'Content-Type':'application/json','X-Mux-User-Gesture':'wsl-firewall-repair'}})
      const result=await response.json().catch(()=>({}))
      if(response.ok&&result.ok){await refreshWsl(true);setWslMessage('Added an inbound rule scoped to the WSL subnet.')}
      else setWslMessage(result.reason==='cancelled'?'The Windows elevation prompt was declined; no rule was changed.':result.reason==='no_wsl_adapter'?'No WSL virtual adapter was found, so there is no subnet to scope a rule to.':result.reason==='unsupported'?'Firewall changes are only available in the packaged Windows app.':'The firewall rule could not be added.')
    }catch(cause){setWslMessage(cause instanceof Error?cause.message:String(cause))}
    finally{setWslBusy('')}
  }

  const exportDiagnostics=async()=>{
    if(diagnosticsBusy)return
    setDiagnosticsBusy(true);setDiagnosticsMessage('Collecting diagnostics…')
    try{
      const bundle=await api<unknown>('GET','/api/diagnostics/export')
      const text=JSON.stringify(bundle,null,2)
      setDiagnosticsText(text)
      // Clipboard write needs a secure context; over plain-HTTP tailnet it can
      // reject, so the copy failure falls back to the textarea below.
      try{await navigator.clipboard.writeText(text);setDiagnosticsMessage('Diagnostics copied to the clipboard.')}
      catch{setDiagnosticsMessage('Could not copy automatically; select the text below and copy it.')}
    }catch(cause){setDiagnosticsMessage(cause instanceof Error?cause.message:String(cause))}
    finally{setDiagnosticsBusy(false)}
  }

  useEffect(() => {
    if(!closeIntent)return
    confirmPanel.current?.querySelector<HTMLElement>('button')?.focus()
  },[closeIntent])

  useEffect(() => {
    if(!resetIntent)return
    resetPanel.current?.querySelector<HTMLElement>('button')?.focus()
  },[resetIntent])

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
    const arrive=()=>{
      for(let node=target.parentElement;node;node=node.parentElement)if(node instanceof HTMLDetailsElement)node.open=true
      target.scrollIntoView({block:'center'})
      return flashSetting(target)
    }
    const page=target.closest<HTMLElement>('section[data-settings-subpage]')?.dataset.settingsSubpage
    if(pagedSubpages&&page&&page!==selectedSubpage){
      setSelectedSubpages(current=>({...current,[activeTab]:page}))
      const timer=window.setTimeout(arrive,0)
      return()=>window.clearTimeout(timer)
    }
    return arrive()
  },[jump,pagedSubpages,selectedSubpage,activeTab])

  // Reveal a control a gated surface deep-linked to (`settingTargets.ts`). Unlike the search
  // jump above, this can arrive before the panel has anything to show: the bundle is still in
  // flight on a cold open, and a tab's child panels fetch again after that, so the reveal
  // waits for its control rather than firing once and missing. `revealToken` changes per
  // request, so a second click on the same link flashes the control again.
  useEffect(() => {
    if(!initialSetting)return
    const root=panel.current
    if(!root)return
    selectPageForSetting(root,initialSetting)
    return revealSetting(root,initialSetting)
  },[initialSetting,revealToken,activeTab,draft!==null,pagedSubpages])

  // The same arrival for an in-panel link (`goToSetting`). Separate from the deep-link
  // effect rather than folded into it so an incoming `initialSetting` and a click inside
  // the panel cannot cancel each other's reveal by sharing one piece of state.
  useEffect(() => {
    if(!pendingReveal)return
    const root=panel.current
    if(!root)return
    selectPageForSetting(root,pendingReveal.setting)
    return revealSetting(root,pendingReveal.setting)
  },[pendingReveal,activeTab,draft!==null,pagedSubpages])

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
      const focusRoot=closeIntent?confirmPanel.current:resetIntent?resetPanel.current:panel.current
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
  }, [closeIntent,resetIntent,capturingCommand])

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
  // Previewing a highlighted theme touches the document's tokens and nothing else —
  // never the draft — so Cancel, Save, and the dirty flag all stay ignorant of it and
  // a walk through the catalogue costs no unsaved change. `null` reverts to whatever
  // the draft currently holds, which is the chosen theme whether or not it is saved.
  // A preview outlives the picker when one gesture closes both: a pointerdown on the
  // Settings backdrop dismisses the list and the panel together, and the panel is gone
  // before the list's own revert can run. So the revert is owned here, where it can
  // still happen after everything below has unmounted. It reverts to the
  // *authoritative* theme rather than the draft's, because discarding already put the
  // saved one back on its way out and re-applying the draft would restore the
  // discarded choice.
  const authoritativeTheme = useRef<ThemeName>('dark')
  authoritativeTheme.current = draft?.theme ?? config?.theme ?? 'dark'
  const previewing = useRef(false)
  const previewTheme = useCallback((name:ThemeName|null)=>{
    previewing.current=name!==null
    applyTheme(name??authoritativeTheme.current)
  },[])
  useEffect(()=>()=>{ if(previewing.current)applyTheme(authoritativeTheme.current) },[])
  // Previewed live, like the theme, because the only way to judge a chrome scale
  // is to see the chrome at it. Editing the *other* device class is a no-op on
  // screen, which is correct: `applyUiScale` resolves this device's key either
  // way, so setting the phone's scale from a desktop shows nothing here.
  const changeUiScale = (key:'ui_scale_desktop'|'ui_scale_mobile', raw:string) => {
    const scale = Number(raw) as UiScale
    change(key,scale)
    onUiScalePreview({...draft!,[key]:scale})
  }
  // Previewed live for the same reason as scale, and directly rather than through a prop:
  // density is one attribute on the root element and nothing outside CSS reads it, so
  // there is no second authority to keep in step the way xterm's font size is.
  const changeRailDensity = (key:'rail_density_desktop'|'rail_density_mobile', raw:string) => {
    const density = raw as RailDensity
    change(key,density)
    applyRailDensity({...draft!,[key]:density})
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
      // A row mid-edit can hold an empty respelling; the daemon rejects those,
      // so an unfinished row is dropped rather than blocking the whole save.
      tts_kokoro_lexicon:Object.fromEntries(Object.entries(draft.tts_kokoro_lexicon||{})
        .map(([word,spoken])=>[word.trim().toLowerCase(),spoken.trim()])
        .filter(([word,spoken])=>word&&spoken)),
    }
    setStatus(SAVING)
    const changes: Record<string,unknown> = {}
    for (const key of Object.keys(savingDraft) as (keyof Config)[]) {
      if (!['revision','host','port','data_dir','requires_auth'].includes(key) && savingDraft[key] !== config[key]) changes[key] = savingDraft[key]
    }
    // One request, because two could half-succeed. Save used to fire a `PATCH /api/config`
    // and a `PUT /api/keybindings` through `Promise.all` and report either failure as
    // "invalid · nothing was changed" — which a `_revision` conflict raised by another
    // device said *after* the keybindings file had already been rewritten. The daemon now
    // commits both or neither (see `routes/settings.apply_settings`), and the one case it
    // genuinely cannot make atomic reports which half landed instead of denying both.
    try {
      const result = await api<SettingsApplyResult>('POST','/api/settings/apply',{
        _revision: config.revision,
        config: changes,
        keybindings: {bindings},
      })
      const next = result.config
      adoptConfig(next); setErrors({})
      if (result.keybindings?.bindings) { setBindings(result.keybindings.bindings); setSavedBindings(result.keybindings.bindings) }
      else setSavedBindings(bindings)
      setStatus(next.restart_required.length ? `saved · restart required: ${next.restart_required.join(', ')}` : 'saved · hot applied')
      return true
    } catch (error) {
      const typed = error as ApiError
      setErrors(typed.fields || {settings:typed.message})
      setStatus(saveFailureStatus(typed))
      return false
    }
  }
  const reset = async () => {
    setResetIntent(false)
    setStatus(RESTORING)
    try {
      adoptConfig(await api<Config>('POST','/api/config/reset',{}))
      setErrors({})
      setStatus('defaults restored')
    } catch (error) {
      // A failed reset used to be an unhandled rejection: the button reported nothing,
      // and the panel went on showing a draft that no longer matched the daemon.
      const typed = error as ApiError
      setErrors(typed.fields || {settings:typed.message})
      setStatus(`restore failed · ${typed.message}`)
    }
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
  // Harness enablement is three-state: `harness_enabled` holds only explicit
  // choices, and an absent key follows detection. These read the draft so the
  // section reflects unsaved edits, and match the daemon's `enabled_backends` rule.
  const enablementChoice = (name:string):boolean|undefined => draft!.harness_enabled?.[name]
  const detectedInstalled = (name:string):boolean => allHarnessesIncludingDisabled().find(harness=>harness.name===name)?.installed ?? false
  const detectedPath = (name:string):string|null|undefined => allHarnessesIncludingDisabled().find(harness=>harness.name===name)?.resolved_path
  const harnessEnabledDraft = (name:string):boolean => {
    const choice = enablementChoice(name)
    return typeof choice==='boolean' ? choice : detectedInstalled(name)
  }
  const setHarnessEnabledChoice = (name:string, enabled:boolean):void =>
    change('harness_enabled',{...draft!.harness_enabled,[name]:enabled})
  const clearHarnessEnabledChoice = (name:string):void => {
    const next={...draft!.harness_enabled}; delete next[name]; change('harness_enabled',next)
  }
  // The MCP and instrumentation toggles are per-harness dicts where an absent key
  // means on. Writing false records an override; setting back to true clears it so
  // the stored map stays minimal, matching harness_enabled's three-state shape.
  const harnessDictOn = (fieldKey:'harness_mcp_enabled'|'harness_instrument_enabled', name:string):boolean =>
    draft![fieldKey]?.[name] ?? true
  const setHarnessDict = (fieldKey:'harness_mcp_enabled'|'harness_instrument_enabled', name:string, on:boolean):void => {
    const next={...(draft![fieldKey]||{})}
    if(on) delete next[name]; else next[name]=false
    change(fieldKey,next)
  }
  const startHistoryScan = async ():Promise<void> => {
    try { setScanJob((await api<{job:HistoryScanJob}>('POST','/api/history/scan')).job) }
    catch(cause){ setStatus(`Scan could not start: ${cause instanceof Error?cause.message:String(cause)}`) }
  }
  const cancelHistoryScan = async ():Promise<void> => {
    try { setScanJob((await api<{job:HistoryScanJob}>('DELETE','/api/history/scan')).job) }
    catch{ /* the poll below reconciles the real state */ }
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
      setUsageRefreshMessage(`Refresh finished · ${next.collector.id} ${next.collector.status}`)
    } catch (error) {
      const message=(error as Error).message
      setUsageRefreshMessage(`Refresh failed · ${message}`)
      setErrors(current=>({...current,ccusage:message}))
    }
  }
  const clearUsage = async () => setUsage(await api<UsageStatus>('DELETE','/api/usage/cache'))
  const providerKeyAction=async(operation:'test'|'set'|'clear',target:'openrouter'|'custom'='openrouter')=>{
    const key=target==='custom'?customKey:openRouterKey
    const label=target==='custom'?'Endpoint':'OpenRouter'
    setProviderMessage(operation==='clear'?'Clearing key…':operation==='set'?'Testing and storing key…':'Testing key…')
    try{
      const result=await api<{ok?:boolean;status:ProviderStatus['secret']}>('POST','/api/automation/provider/key',{operation,provider:target,key:key||undefined,test:true})
      // The whole payload is refetched rather than patched in: storing or clearing a key
      // drops the endpoint's verification (the key is part of its fingerprint), so the
      // verified badge and the readiness sentence both change with the key status and
      // patching only `secret` would leave a stale "verified" beside a new credential.
      forgetLlmProvider()
      await reloadProviderStatus()
      if(target==='custom')setCustomKey('');else if(operation==='set'||operation==='clear')setOpenRouterKey('')
      setProviderMessage(operation==='clear'?`${label} key cleared.`:operation==='set'?`${label} key tested and stored in the platform credential store.`:`${label} connection succeeded.`)
      void result
    }catch(error){setProviderMessage(`${label} error · ${(error as Error).message}`)}
  }
  const reloadProviderStatus=async()=>{
    try{setProvider(await api<ProviderStatus>('GET','/api/automation/provider'))}catch{/* the banner already reports it */}
  }
  const verifyProvider=async(target:string)=>{
    setVerifying(target);setVerifyResult(null)
    try{
      const result=await verifyLlmProvider(target)
      setVerifyResult(result)
      await reloadProviderStatus()
    }catch(error){
      // A refusal comes back as a resolved 422 carrying the endpoint's own words; only a
      // transport failure lands here, and it has no endpoint text to show.
      setVerifyResult({ok:false,provider:target,error:(error as Error).message,
        verification:{provider:target,verified:false,stale:false,verified_at:null,base_url:'',
          model:'',resolved_model:'',sample:'',latency_ms:0,
          // A transport failure measured nothing, so this is the unproven profile
          // rather than a claim that the endpoint has no catalog.
          capabilities:{catalog:'none',reports_cost:false,reports_cache:false}},
        llm:{ready:false,provider:target,code:'unverified',reason:(error as Error).message}})
    }finally{setVerifying('')}
  }
  const refreshModels=async()=>{
    setProviderMessage('Refreshing OpenRouter model catalog…')
    try{const models=await api<ProviderStatus['models']>('POST','/api/automation/provider/models/refresh',{});setProvider(current=>current?{...current,models}:current);setProviderMessage(`Model catalog ready · ${models.models.length} structured-output text models.`)}catch(error){setProviderMessage(`Model refresh failed · ${(error as Error).message}`)}
  }
  const discardAndLeave=()=>{
    if(!closeIntent)return
    // Theme, chrome scale, and rail density are previewed live as you pick them, so
    // discarding has to put all three back — not just the draft that was never saved.
    // Pointing the authoritative theme at the saved one keeps the unmount revert agreeing.
    if(config){configureCustomTheme(config.custom_theme);applyTheme(config.theme);authoritativeTheme.current=config.theme;onUiScalePreview(config);applyRailDensity(config)}
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
  // The panel's chrome, shared by the loading shell and the loaded one so the two
  // cannot drift apart — the placeholder is the same header, the same section list,
  // and the same footer, with only the content area standing in.
  const tabLabel=settingsTabs.find(tab=>tab.id===activeTab)?.label||'Settings'
  const pageLabel=declaredSubpages.find(page=>page.id===selectedSubpage)?.label
  const activeTabLabel = pagedSubpages&&pageLabel?`${tabLabel} · ${pageLabel}`:tabLabel
  const toggleNav = () => setNavOpen(!navOpen)
  // Narrow, the header names where you are rather than what the panel is: the panel
  // fills the screen, so "Settings" is the one thing already obvious, and the tab is
  // the thing the docked column would otherwise have been showing. Both it and the
  // hamburger open the section drawer, because the title is where the eye already is.
  const heading = <div class="settings-heading">
    <span>{narrow?'SETTINGS':'CONFIG::V6'}</span>
    {narrow
      ? <h2><button type="button" class="settings-heading-trigger" aria-expanded={navOpen} aria-controls="settings-tab-nav" onClick={toggleNav}>{activeTabLabel}<i aria-hidden="true">▾</i></button></h2>
      : <h2>Settings</h2>}
  </div>
  const navTrigger = narrow
    ? <button type="button" class="settings-nav-trigger" aria-label="Settings sections" aria-expanded={navOpen} aria-controls="settings-tab-nav" onClick={toggleNav}>
        <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" aria-hidden="true"><line x1="2.5" y1="4" x2="13.5" y2="4"/><line x1="2.5" y1="8" x2="13.5" y2="8"/><line x1="2.5" y1="12" x2="13.5" y2="12"/></svg>
      </button>
    : null
  // Grouped from one array in both layouts. The wrapper is `display:contents` so its
  // buttons stay direct flex children of the nav, and `role=presentation` because a
  // tablist admits only tabs — the heading is a visual affordance.
  // Narrow, the same list slides in over the content instead of docking beside it;
  // closed it is `visibility:hidden` rather than `aria-hidden`, which is what keeps its
  // buttons out of the focus order without hiding elements that are still focusable.
  const tabNav = <Fragment>
    <nav ref={tabNavRef} id="settings-tab-nav" class={`settings-tabs${narrow?' settings-tabs-drawer':''}${narrow&&navOpen?' open':''}`} role="tablist" aria-label="Settings sections">
      {settingsTabGroups.map(group=><div class="settings-tab-group" role="presentation" key={group.group}>
        <span aria-hidden="true">{group.group}</span>
        {group.tabs.map(tab=>{
          // A paged tab lists its declared pages. An unpaged tab lists the sections it
          // actually rendered, as scroll anchors — knowable only while it is the active
          // tab, which is also the only time scrolling it means anything.
          const pages=settingsSubpages[tab.id]||[]
          const sections=!pages.length&&tab.id===activeTab&&railSections.length>=SECTION_RAIL_MIN?railSections:[]
          const entries=pages.length?pages:sections
          const expanded=expandedTabs.has(tab.id)&&entries.length>0
          return <Fragment key={tab.id}>
            <div class="settings-tab-row" role="presentation">
              <button role="tab" aria-selected={activeTab===tab.id} class={activeTab===tab.id?'active':''} onClick={()=>selectTab(tab.id)}>{tab.label}</button>
              {!!entries.length&&<button type="button" class="settings-tab-expand" aria-label={`${expanded?'Collapse':'Expand'} ${tab.label} pages`} aria-expanded={expanded} onClick={()=>toggleTabExpanded(tab.id)}>{expanded?'▾':'▸'}</button>}
            </div>
            {expanded&&<div class="settings-subtabs" role="group" aria-label={`${tab.label} pages`}>
              {pages.length
                ?pages.map(page=><button key={page.id} type="button" class={activeTab===tab.id&&selectedSubpage===page.id?'active':''} onClick={()=>selectSubpage(tab.id,page.id)}>{page.label}</button>)
                :sections.map(section=><button key={section.id} type="button" class={activeSection===section.id?'active':''} onClick={()=>{scrollToSection(section.id);setNavOpen(false)}}>{section.label}</button>)}
            </div>}
          </Fragment>
        })}
      </div>)}
    </nav>
    {narrow&&navOpen&&<button type="button" class="settings-nav-scrim" aria-label="Close settings sections" onClick={()=>setNavOpen(false)} />}
  </Fragment>
  // Before the bundle lands the panel renders its full chrome — header, section
  // list, footer — with a placeholder in the content area, so opening Settings
  // paints immediately and the chosen tab can be selected while data loads.
  {/* Disabled twin of the real search box: the index needs the config, and a control
      that appears late would shift the chrome out from under a tap. Narrow it lives in
      the header; wide it sits above the section list, at the top of the nav column. */}
  const loadingSearch = <div class="settings-search"><input type="search" disabled placeholder="Search settings…" aria-label="Search settings (loading)" /></div>
  if (!draft) return <div class="settings-layer" onMouseDown={event=>event.target===event.currentTarget&&requestClose()}><section class="settings-panel" ref={panel} role="dialog" aria-modal="true" aria-label="Settings">
    <header>{navTrigger}{heading}
      {narrow&&loadingSearch}
      <button class="settings-close" aria-label="Close Settings" onClick={()=>requestClose()}>×</button></header>
    <main class="settings-body">
      {narrow?tabNav:<div class="settings-nav-col">{loadingSearch}{tabNav}</div>}
      <div class="settings-content"><section class="settings-loading" role="status" aria-live="polite">{status}</section></div>
    </main>
    <footer><span aria-live="polite">{status}</span><button onClick={()=>requestClose()}>Cancel</button></footer>
  </section></div>
  const modelOptions=(selected:string)=>includeSelectedModel(provider?.models.models||[],selected)
  // What the *active* endpoint was measured to publish, off the entry for the
  // provider actually selected rather than off the payload's top level: the panel
  // shows both endpoints and only one of them is answering calls.
  const activeEndpoint=provider?.providers.find(entry=>entry.id===draft.llm_provider)
  const catalogKnown=(activeEndpoint?.verification.capabilities?.catalog??'none')!=='none'
  // Null unless the endpoint publishes no catalog at all, in which case it serves
  // one model and every route resolves to it (`modelRouting.ts`). Derived from the
  // measurement rather than from the provider id, which is the whole point: being
  // `custom` no longer implies anything about what the endpoint can do.
  const endpointOverride=customProviderOverride(draft,catalogKnown)
  /** What one route resolves to right now, for the read-only rows on feature tabs. */
  const routedModel=(key:keyof ModelRoutingConfig)=>
    resolveRoute(MODEL_ROUTES.find(route=>route.key===key)!,draft,endpointOverride).model||'not set'
  // Diagnostics reuses the app's own command registry rather than re-implementing
  // the three reload paths, so a change to what "reload daemon" means reaches this
  // panel for free. The registry arrives as a prop; a command that is absent (an
  // older host, a build without the desktop shell) disables its button.
  const appCommand=(id:string)=>voiceCommands.find(command=>command.id===id&&command.available)
  const runAppCommand=(id:string)=>{ void appCommand(id)?.run() }
  // One function renders every tab, and it takes the tab id as an argument
  // instead of reading `activeTab` from state, so the search index can build the
  // vnode tree of a tab that is not mounted. Building vnodes only allocates plain
  // objects — no DOM, no effects, no child-component bodies run — which is what
  // lets the index be derived from the same JSX that renders the form rather than
  // from a hand-maintained duplicate list of every setting.
  const tabContent = (activeTab: SettingsTab) => <Fragment>
        {activeTab==='general'&&<Fragment>
          <section><h3>Defaults</h3>
            <label>Startup directory<input value={draft.startup_cwd} onInput={e=>change('startup_cwd',e.currentTarget.value)} /></label>
            <label>Default backend<Dropdown value={draft.default_backend} onChange={value=>change('default_backend',value)} options={allBackendNames().map(name=>({value:name,label:name==='shell'?'Shell':harnessDisplayName(name)}))}/></label>
            <p>What a new session starts as, and where it starts, when nothing more specific applies. A Project's own default overrides both.</p>
          </section>

          <section><h3>Getting started tutorial</h3>
            <div class="settings-tutorial-reset"><div><p>Replay the guided tour of Projects, provider accounts, tabs, pane splits, resources, and the main navigation.</p></div><button onClick={()=>requestClose('tutorial')}>Reset &amp; run tutorial</button></div>
          </section>

          {/* Config-file actions live here rather than in the footer: they act on the
              whole configuration, not the visible tab, so repeating them under every
              tab implied a per-tab scope they never had — and on a phone they pushed
              Cancel/Save into a horizontally scrolling footer. */}
          <section><h3>Configuration file</h3>
          <div class="settings-config-actions"><div><p>Stored in <code>{draft.data_dir}</code>. Exports omit credentials. Restore defaults saves immediately and discards this draft.</p></div>
              <div><button onClick={()=>void api('POST','/api/reveal',{path:draft.data_dir})}>Reveal config directory</button><button onClick={exportConfig}>Export sanitized</button><button class="danger" onClick={()=>setResetIntent(true)}>Restore defaults</button></div>
            </div>
          </section>
        </Fragment>}

        {/* Per-Project options are NOT here: the Projects registry is the single
            per-Project editor (menu → Manage projects, or a Project's context menu →
            Project settings…). This tab is the global half only, and each section
            names the per-Project field it composes with. */}
        {activeTab==='projects'&&<Fragment>
          {/* Where the assistant may create new project folders. Name-only at the tool:
              the model never supplies a path, so this one directory is the whole surface
              a chat message can create a folder in. The Add-project dialog is unaffected. */}
          <section><h3>New project location</h3>
            <label data-setting="new_project_parent">Default parent folder<input value={draft.new_project_parent} placeholder={projectParentHint||'e.g. D:\\PROJECTS'} onInput={e=>change('new_project_parent',e.currentTarget.value)}/></label>
            <p>The only directory the Mux assistant may create new project folders inside; the Add project dialog can use any parent.{projectParentHint&&<Fragment> Most of your projects live in <code>{projectParentHint}</code>.</Fragment>}</p>
          </section>

          {/* Setup commands offered when a Project is registered. They are yours, typed
              here, stored on this machine — nothing is ever read out of a repository, so
              there is no trust prompt and no fingerprint to approve. */}
          <section><h3>Project setup commands</h3>
            <div class="settings-init-scripts">
              <div class="settings-init-scripts-head"><div><strong>Offered when you add a Project</strong><p>Offered as unchecked options in Add project. Each selected command opens its own one-shot terminal at the new Project root, started in this order.</p></div><button onClick={addInitScript}>+ Add command</button></div>
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
          </section>

          <section><h3>Global project ignores</h3>
            <label data-setting="project_ignore_patterns">Ignore patterns<textarea value={draft.project_ignore_patterns.join('\n')} onInput={e=>change('project_ignore_patterns',parseIgnorePatternDraft(e.currentTarget.value))}/></label>
            <p>One glob per line, matched at any depth. Affects the file tree and resource watchers, not Git. Each Project adds its own list on top under <strong>Manage projects → Repository options</strong>.</p>
          </section>
        </Fragment>}

        {activeTab==='terminals'&&<Fragment>
          <section><h3>Rendering</h3>
            <label>Renderer<Dropdown value={draft.terminal_renderer} onChange={value=>change('terminal_renderer',value as Config['terminal_renderer'])} options={[{value:'auto',label:'Auto (WebGL with DOM fallback)'},{value:'webgl',label:'Prefer WebGL'},{value:'dom',label:'DOM compatibility mode'}]}/></label>
          <p>Mobile and Claude use the DOM renderer. Auto also selects it for terminals that repaint scrollback. Harness width limits are under Harnesses.</p>
          </section>

          {/* Retention, first replay, and crash checkpoint are three different byte
              figures over the same stream, and reading one without the other two is
              how "why did my pane come back short" stayed unanswerable. They are one
              section for that reason, ordered as the bytes travel: what is kept, what
              a fresh attach is handed, what survives losing the daemon and its PTY
              owner together. */}
          <section><h3>Scrollback</h3>
            <label>Scrollback bytes<input type="number" min="1024" max="1073741824" value={draft.scrollback_bytes} onInput={e=>change('scrollback_bytes',Number(e.currentTarget.value))} /><small>Output kept per live session for replay after a reconnect; larger costs memory.</small></label>
            <label data-setting="attach_replay_bytes">Replay bytes on a fresh attach<input type="number" min="1024" max="1073741824" value={draft.attach_replay_bytes} onInput={e=>change('attach_replay_bytes',Number(e.currentTarget.value))} /><small>Handed to a client attaching with no position of its own; every byte is parsed before render, so this is reconnect latency.</small></label>
            <h4>Crash recovery</h4>
          <p>These limits cover a full daemon and PTY-supervisor failure. Recovered sessions return as readable ended panes; <code>session_recovery_enabled</code> is a config-file switch.</p>
            <label data-setting="session_recovery_checkpoint_bytes">Recovery checkpoint bytes<input type="number" min="0" max="67108864" value={draft.session_recovery_checkpoint_bytes} onInput={e=>change('session_recovery_checkpoint_bytes',Number(e.currentTarget.value))} /><small>Terminal bytes kept per session for the recovered pane; 0 keeps recovery on and stores no output.</small></label>
            <label data-setting="session_recovery_retention_days">Recovery retention days<input type="number" min="0" max="365" value={draft.session_recovery_retention_days} onInput={e=>change('session_recovery_retention_days',Number(e.currentTarget.value))} /><small>How long an <em>ended</em> session's recovery data is kept.</small></label>
            <label data-setting="session_recovery_max_sessions">Recoverable sessions kept<input type="number" min="0" max="1000" value={draft.session_recovery_max_sessions} onInput={e=>change('session_recovery_max_sessions',Number(e.currentTarget.value))} /><small>Newest first, so repeated crashes cannot accumulate cold rows forever.</small></label>
          </section>

          <section><h3>Default profile</h3>
            <label>Global default terminal profile<Dropdown value={draft.default_shell_profile} onChange={value=>change('default_shell_profile',value)} options={draft.shell_profiles.filter(profile=>profile.enabled&&profile.backend==='shell').map(profile=>({value:profile.id,label:profile.label}))}/></label>
          <p>A profile names an executable, arguments, and environment. Shell profiles open terminals; agent profiles start a harness variant. Project defaults are under Projects → Options.</p>
          </section>

          <section><h3>Typing an agent's name in a terminal</h3>
            <label class="check" data-setting="agent_shims_on_shell_path"><span>Launch agents through swe-mux <em>· makes a typed <code>claude</code> a real session</em></span><input type="checkbox" checked={draft.agent_shims_on_shell_path} onChange={e=>change('agent_shims_on_shell_path',e.currentTarget.checked)} /></label>
            <p>On, a terminal's PATH starts with <code>~/.mux/bin</code>, so typing an agent's name launches it through swe-mux: the pane adopts the conversation, gets a name, and joins status detection, history, and the prompt queue.</p>
            <p class="profile-hint">Off, <code>claude</code> in a terminal means your own <code>claude</code> and the pane stays a shell. Choose this if you use swe-mux purely as a terminal multiplexer. Agents started from the Run menu are unaffected either way — they are spawned straight into their terminal and never go through this. Takes effect on the next daemon restart, for terminals opened after it.</p>
          </section>

          <section class="profile-settings"><h3>Launch profiles</h3>
          <div class="profile-browser">
            <div class="profile-index" aria-label="Configured launch profiles">
              {draft.shell_profiles.map(profile=><button class={selectedProfileId===profile.id?'active':''} onClick={()=>setSelectedProfileId(selectedProfileId===profile.id?null:profile.id)}><span>{profileTag(profile)}</span><strong>{profile.label}</strong><small>{profile.id} · {profile.backend==='shell'?'shell':harnessDisplayName(profile.backend)} · {profile.enabled?'on':'off'}</small></button>)}
              <div class="profile-index-actions"><button onClick={()=>addProfile()}>+ add shell</button>{harnesses().map(harness=><button key={harness.name} onClick={()=>addProfile(undefined,harness.name)}>+ add {harness.display_name}</button>)}<button onClick={restoreDetected}>restore detected</button></div>
            </div>
            {selectedProfile&&<article class="profile-editor">
              <header><strong>PROFILE::{selectedProfile.label}</strong><button aria-label="Collapse launch profile" onClick={()=>setSelectedProfileId(null)}>×</button></header>
              <label>Profile ID<input value={selectedProfile.id} onInput={e=>updateProfile(selectedProfileIndex,{id:e.currentTarget.value})}/></label>
              <label>Label<input value={selectedProfile.label} onInput={e=>updateProfile(selectedProfileIndex,{label:e.currentTarget.value})}/></label>
              <label>Backend<Dropdown value={selectedProfile.backend} onChange={value=>updateProfile(selectedProfileIndex,{backend:value as ProjectBackend})} options={[{value:'shell',label:'Shell'},...harnesses().map(harness=>({value:harness.name,label:harness.display_name}))]}/></label>
              <label>Executable{selectedProfile.backend!=='shell'&&<em> optional</em>}<input value={selectedProfile.executable} placeholder={selectedProfile.backend==='shell'?'':draft.harness_exe[selectedProfile.backend]||''} onInput={e=>updateProfile(selectedProfileIndex,{executable:e.currentTarget.value})}/></label>
              <label>Arguments<input value={argsText} spellcheck={false} placeholder={selectedProfile.backend==='shell'?'-NoLogo':'--model claude-opus-4-8'} onInput={e=>{const text=e.currentTarget.value;setArgsText(text);updateProfile(selectedProfileIndex,{args:parseCommandLine(text)})}}/></label>
              <p class="profile-hint">Type it as you would in a terminal. Quote anything containing a space: <code>--append-system-prompt "be terse"</code>. A backslash is literal, so Windows paths need no escaping.</p>
              {selectedProfile.backend!=='shell'&&reservedConflict(selectedProfile.backend,selectedProfile.args)&&<p class="error" role="alert">{reservedConflict(selectedProfile.backend,selectedProfile.args)} is built by swe-mux for {harnessDisplayName(selectedProfile.backend)} and cannot be set here.</p>}
              <label>Environment<textarea value={Object.entries(selectedProfile.env).map(([key,value])=>`${key}=${value}`).join('\n')} onInput={e=>updateProfile(selectedProfileIndex,{env:Object.fromEntries(e.currentTarget.value.split('\n').filter(line=>line.includes('=')).map(line=>{const at=line.indexOf('=');return [line.slice(0,at),line.slice(at+1)]}))})}/></label>
              {selectedProfile.backend==='shell'&&<><label>Cwd strategy<Dropdown value={selectedProfile.cwd_strategy} onChange={value=>updateProfile(selectedProfileIndex,{cwd_strategy:value as LaunchProfile['cwd_strategy']})} options={[{value:'native',label:'native'},{value:'home',label:'home'},{value:'wsl',label:'wsl'}]}/></label>
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
          </section>
        </Fragment>}

        {activeTab==='git'&&<section><h3>Git and worktrees</h3><label>Worktree root<input value={draft.worktree_root} onInput={e=>change('worktree_root',e.currentTarget.value)}/></label><p>New Run worktrees are grouped below this absolute directory by Project and branch. Clear the field to restore <code>{draft.data_dir}{draft.data_dir.includes('\\')?'\\':'/'}worktrees</code>. Existing worktrees and manually edited Run paths are not moved.</p><label>Git poll seconds<input type="number" step=".25" value={draft.git_poll_seconds} onInput={e=>change('git_poll_seconds',Number(e.currentTarget.value))} /></label><p>A Project's own worktree setup command lives in <strong>Manage projects → Git and worktrees</strong>. Which Git fields a session row shows is under <strong>Appearance → Session rows</strong>.</p></section>}

        {activeTab==='processes'&&<Fragment>
          <section><h3>Process evidence</h3><label>Process inspector poll seconds<input type="number" min=".5" max="60" step=".5" value={draft.process_poll_seconds} onInput={e=>change('process_poll_seconds',Number(e.currentTarget.value))}/></label><label>Suspected-orphan grace seconds<input type="number" min="1" max="3600" value={draft.process_orphan_grace_seconds} onInput={e=>change('process_orphan_grace_seconds',Number(e.currentTarget.value))}/></label><label>Process evidence retention days<input type="number" min="1" max="3650" value={draft.process_evidence_retention_days} onInput={e=>change('process_evidence_retention_days',Number(e.currentTarget.value))}/></label><p>Surviving descendants are flagged after the grace period and never killed automatically. What is running right now is the drawer's <strong>Processes</strong> tab.</p></section>

          {/* Its own section rather than another row under process evidence: the sweep
              moves something on screen, which is the one thing here that changes what
              the machine looks like rather than what swe-mux records about it. */}
          <section><h3>Ghost windows</h3>
          <p>Headless Chrome can leave a hidden window the compositor still draws over the desktop; the sweep parks those off screen without closing the browser.</p>
          <label class="check" data-setting="ghost_window_sweep_enabled"><span>Park ghost headless-browser windows off screen</span><input type="checkbox" checked={draft.ghost_window_sweep_enabled} onChange={e=>change('ghost_window_sweep_enabled',e.currentTarget.checked)}/><small>Matches only hidden headless-class windows whose process ran with <code>--headless</code>, so a deliberately shown window is never touched.</small></label>
          <label data-setting="ghost_window_poll_seconds">Sweep seconds<input type="number" min=".5" max="60" step=".5" value={draft.ghost_window_poll_seconds} onInput={e=>change('ghost_window_poll_seconds',Number(e.currentTarget.value))}/><small>How often the sweep looks. Windows-only and idempotent.</small></label>
          </section>

          {/* Detection telemetry rather than process telemetry, but it belongs beside the
              other evidence windows rather than under Usage: it is the per-session record
              an incident is reconstructed from, not a spend or quota figure. */}
          <section><h3>Detection timeline</h3>
          <label data-setting="status_timeline_retention_days">Status timeline retention days<input type="number" min="1" max="3650" value={draft.status_timeline_retention_days} onInput={e=>change('status_timeline_retention_days',Number(e.currentTarget.value))}/><small>The per-transition evidence behind past session states; kept shorter than process evidence because active turns write frequently.</small></label>
          </section>
        </Fragment>}

        {/* One editor renders every Markdown surface (notes,
            Markdown files from Files), so everything here applies to all of them.
            Colours are absent on purpose: the theme already drives the editor's
            colour variables, and a second source for them would fight it. */}
        {activeTab==='notes'&&<Fragment>
          <section><h3>Note editor</h3>
            <label class="check"><span>Spellcheck</span><input type="checkbox" checked={draft.note_spellcheck} onChange={e=>change('note_spellcheck',e.currentTarget.checked)}/><small>The browser's spellchecker; it also underlines code, paths, and identifiers.</small></label>
            <label class="check"><span>Indent guides</span><input type="checkbox" checked={draft.note_indent_guides} onChange={e=>change('note_indent_guides',e.currentTarget.checked)}/><small>Vertical rules marking each enclosing indent level; the caret's level draws brighter.</small></label>
            <label>Markdown<Dropdown value={draft.note_syntax} onChange={value=>change('note_syntax',value as Config['note_syntax'])} options={[{value:'markdown',label:'Render Markdown'},{value:'plain',label:'Show raw text'}]}/></label>
            <p>Raw text keeps every editing feature and only stops the Markdown projection.</p>
            <label>Tab key<Dropdown value={draft.note_tab_behavior} onChange={value=>change('note_tab_behavior',value as Config['note_tab_behavior'])} options={[{value:'indent',label:'Indent and outdent lines'},{value:'focus',label:'Move focus out of the editor'}]}/></label>
            <p>Escape then Tab always leaves the editor, so the keyboard is never trapped.</p>
          </section>

          <section><h3>Typography</h3>
            <label>Font family<input value={draft.note_font_family} placeholder="editor default: ui-monospace, Cascadia Mono, Consolas" onInput={e=>change('note_font_family',e.currentTarget.value)}/></label>
            <label>Font size<input type="number" min="0" max="48" value={draft.note_font_size_px} onInput={e=>change('note_font_size_px',Number(e.currentTarget.value))}/></label>
            <label>Line height<input type="number" min="0" max="3" step="0.05" value={draft.note_line_height} onInput={e=>change('note_line_height',Number(e.currentTarget.value))}/></label>
            <p>Pixels and a multiplier; zero keeps the editor's own default (16px, 1.55). Saving re-measures open notes in place with no lost undo history.</p>
          </section>

          <section><h3>Touch command rail</h3>
            <p>The editor's bottom quick-action strip, which runs each action without dismissing the on-screen keyboard.</p>
            <label>Show the rail<Dropdown value={draft.note_command_rail} onChange={value=>change('note_command_rail',value as Config['note_command_rail'])} options={[{value:'auto',label:'Automatic: touch devices only'},{value:'on',label:'Always'},{value:'off',label:'Never'}]}/></label>
            <label>Button size<input type="number" min="0" max="96" value={draft.note_rail_button_size_px} onInput={e=>change('note_rail_button_size_px',Number(e.currentTarget.value))}/><small>Pixels; zero keeps the default 48px button in a 56px rail.</small></label>
            <div class="keybinding-heading"><div><strong>RAIL::ARRANGEMENT</strong><p>Which buttons appear, and in what order, is chosen from the gear on the rail itself and saved per device, so Cancel does not undo a reset.</p></div><button type="button" onClick={resetNoteRailArrangement}>Reset rail arrangement</button></div>
          </section>

          <section><h3>Editor shortcuts</h3>
            <label>Policy<Dropdown value={draft.note_shortcut_policy} onChange={value=>change('note_shortcut_policy',value as ShortcutPolicy)} options={[{value:'browser-safe',label:'Browser-safe: leave browser chords alone'},{value:'editor-first',label:'Editor first: claim every chord'},{value:'none',label:'None: no editor shortcuts'}]}/></label>
            <p>Browser-safe releases the chords Chromium claims (reload, search, DevTools); editor-first suits the desktop app, where those chords are not wanted.</p>
            <div class="keybinding-heading"><div><strong>EDITOR::CHORDS</strong><p>Per-chord overrides on that policy, applied inside a note only. This app's own shortcuts are on the Input tab.</p></div><button type="button" onClick={()=>change('note_shortcut_overrides',{...DEFAULT_NOTE_SHORTCUT_OVERRIDES})}>Restore editor shortcut defaults</button></div>
            <input class="note-chord-filter" type="search" value={noteChordQuery} placeholder="Filter chords and commands…" aria-label="Filter editor shortcuts" spellcheck={false} onInput={e=>setNoteChordQuery(e.currentTarget.value)}/>
            <div class="note-chord-list">
              {NOTE_CHORDS.filter(binding=>{
                const needle=noteChordQuery.trim().toLowerCase()
                return !needle||binding.chord.includes(needle)||binding.command.includes(needle)
              }).map(binding=><article key={binding.chord}>
                <span class="note-chord"><kbd>{noteChordLabel(binding.chord)}</kbd>{!binding.isBrowserSafe&&<em title="A chord the browser claims first; the browser-safe policy releases it unless it is reclaimed here">browser chord</em>}</span>
                <small title={binding.command}>{binding.command}</small>
                <Dropdown ariaLabel={`Behaviour of ${noteChordLabel(binding.chord)} in the note editor`} value={noteChordState(draft.note_shortcut_overrides,binding.chord)} onChange={value=>setNoteChord(draft.note_shortcut_overrides,binding.chord,binding.command,value as NoteChordState)} options={[
                  {value:'default',label:'Policy default'},
                  {value:'bind',label:'Run the command'},
                  {value:'release',label:'Leave to the browser'},
                ]}/>
              </article>)}
            </div>
          </section>

        </Fragment>}

        {activeTab==='harnesses'&&<Fragment>
          {/* Above the per-harness list on purpose: it is the one setting here that
              is about the set rather than about a member of it, and reading it after
              five harness cards would make it look like a property of the last one. */}
          <section><h3>Default harness</h3>
          <p>Used when a tool needs an agent and none was named. This is separate from the Run menu default, which may be a shell.</p>
          <label data-setting="default_harness">Default harness<Dropdown value={draft.default_harness} onChange={value=>change('default_harness',value)} options={[{value:'',label:'Follow detection'},...allHarnessesIncludingDisabled().map(harness=>({value:harness.name,label:harness.display_name}))]}/></label>
          <p class="profile-hint">Follow detection tracks the available agent automatically. Choose explicitly when several are installed.</p>
          </section>

          <section class="settings-harnesses"><h3>Harnesses</h3>
          <p>Controls which harnesses appear in launchers and how they start. Disabled harnesses keep existing sessions and searchable history.</p>
          {allHarnessesIncludingDisabled().map(harness=><div class="settings-harness" key={harness.name}>
            <div class="settings-harness-head">
              <label class="check"><span>{harness.display_name}</span><input type="checkbox" checked={harnessEnabledDraft(harness.name)} onChange={e=>setHarnessEnabledChoice(harness.name,e.currentTarget.checked)} /></label>
              <small class={detectedInstalled(harness.name)?'settings-harness-detected':'settings-harness-missing'}>{detectedInstalled(harness.name)?(detectedPath(harness.name)?`Detected: ${detectedPath(harness.name)}`:'Detected (data present)'):'Not detected'}{typeof enablementChoice(harness.name)==='boolean'?` · manually ${enablementChoice(harness.name)?'enabled':'disabled'}`:' · following detection'}</small>
              {typeof enablementChoice(harness.name)==='boolean'&&<button type="button" onClick={()=>clearHarnessEnabledChoice(harness.name)}>follow detection</button>}
            </div>
            {harness.cli_version&&<p class={harness.version_untested?'settings-inline-error':'profile-hint'}>CLI version {harness.cli_version}{harness.version_untested?` · newer than the version mux was tested against (${harness.tested_cli_version}). Features degrade gracefully, but this pairing is untested.`:''}</p>}
            <label>Executable<input value={draft.harness_exe[harness.name]||''} placeholder={harness.name} onInput={e=>change('harness_exe',{...draft.harness_exe,[harness.name]:e.currentTarget.value})} /></label>
            <label>Default args<input value={harnessArgs[harness.name]||''} spellcheck={false} placeholder="--model claude-opus-4-8" onInput={e=>setHarnessArgs(current=>({...current,[harness.name]:e.currentTarget.value}))} /></label>
            {harnessDescriptor(harness.name)?.capabilities.mcp!==false&&<label class="check"><span>mux MCP server <em>· fleet visibility and messaging for this agent</em></span><input type="checkbox" checked={harnessDictOn('harness_mcp_enabled',harness.name)} onChange={e=>setHarnessDict('harness_mcp_enabled',harness.name,e.currentTarget.checked)} /></label>}
            {harnessDescriptor(harness.name)?.capabilities.lifecycle_hooks&&<Fragment>
              <label class="check"><span>Instrument with mux hooks <em>· off launches clean and unobserved</em></span><input type="checkbox" checked={harnessDictOn('harness_instrument_enabled',harness.name)} onChange={e=>setHarnessDict('harness_instrument_enabled',harness.name,e.currentTarget.checked)} /></label>
              {!harnessDictOn('harness_instrument_enabled',harness.name)&&<p class="settings-inline-error">Clean launch drops {harness.display_name} to unobserved: no status detection, history capture, or prompt queue for its sessions.</p>}
            </Fragment>}
            {(harnessDictOn('harness_mcp_enabled',harness.name)===false||harnessDictOn('harness_instrument_enabled',harness.name)===false)&&<p class="profile-hint">Instrumentation changes take effect on the next daemon restart (live sessions are preserved).</p>}
            {appliesWidthEnvelope(harness.name)&&<Fragment>
              <label data-setting="claude_max_columns">Width limit<Dropdown value={String(draft.claude_max_columns)} onChange={value=>change('claude_max_columns',Number(value) as ClaudeMaxColumns)} options={CLAUDE_MAX_COLUMN_STEPS.map(step=>({value:String(step),label:claudeMaxColumnsLabel(step)}))}/></label>
              <p class="profile-hint">Past this many columns a pane adds margin instead of resizing, because {harness.display_name}'s renderer can corrupt cells on large width changes. Raise it for wide diffs, or choose No limit; compact panes are never limited.</p>
            </Fragment>}
            <p class="profile-hint">Applies to every {harness.display_name} session; a named alternative is a launch profile under Terminals. Reserved: {(harness.reserved_launch_args||[]).join(' ')||'none'}.</p>
          </div>)}
          </section>

          {/* History indexing rather than harness configuration, but inseparable from
              it: the scan is scoped to exactly the harnesses enabled above, so the two
              are read together or not at all. */}
          <section><h3>Conversation history</h3>
          <p>Indexes conversations created outside swe-mux so they can be searched and resumed.</p>
          <label class="check"><span>Reconcile native history on startup</span><input type="checkbox" checked={draft.reconcile_external_history} onChange={e=>change('reconcile_external_history',e.currentTarget.checked)} /></label>
          <label>History page size<input type="number" min="1" max="10000" value={draft.history_limit} onInput={e=>change('history_limit',Number(e.currentTarget.value))} /><small>Conversations per history-browser page; bounds a request, not what is stored.</small></label>
          <div class="settings-history-scan">
            <div class="settings-history-scan-head">
              <div><strong>Scan native history now</strong><p>Indexes past conversations from the enabled harnesses in the background; a large first import is cancellable.</p></div>
              {scanJob?.status==='running'
                ?<button type="button" class="danger" onClick={()=>void cancelHistoryScan()}>{scanJob.phase==='cancelling'?'Cancelling…':'Cancel scan'}</button>
                :<button type="button" onClick={()=>void startHistoryScan()}>Scan now</button>}
            </div>
            {scanJob&&scanJob.status!=='idle'&&<p class="settings-history-scan-status" aria-live="polite">{
              scanJob.status==='running'
                ?(scanJob.phase==='indexing'
                    ?`Indexing ${scanJob.processed} of ${scanJob.scanned} conversations (${scanJob.imported} imported)…`
                    :`Scanning… ${scanJob.scanned} conversations found`)
                :scanJob.status==='completed'?`Done: ${scanJob.imported} imported of ${scanJob.scanned} found across ${scanJob.backends.join(', ')||'no enabled harness'}.`
                :scanJob.status==='cancelled'?`Cancelled after ${scanJob.imported} imported.`
                :`Scan failed: ${scanJob.error||'unknown error'}`
            }</p>}
          </div>
          </section>
        </Fragment>}

        {/* Delivery policy, not harness configuration: these bound how a queued
            message reaches an agent, whichever harness that agent is running. */}
        {activeTab==='queue'&&<Fragment>
          <section><h3>Overview</h3>
          <p>Effective install-wide queue policy. Conversation and Project grants can narrow it further.</p>
          <div class="settings-status-grid">
            <article><strong>Auto-delivery</strong><span>{draft.auto_delivery_enabled?`On · ${draft.auto_delivery_stable_seconds}s stability`:'Off · manual Send now only'}</span></article>
            <article><strong>Approvals</strong><span>{draft.approval_auto_enabled?'Available with a conversation grant':'Human answers every request'}</span></article>
            <article><strong>Agent messaging</strong><span>{draft.agent_messaging_enabled?'On · bounded threads':'Off'}</span></article>
            <article><strong>Agent actuation</strong><span>{draft.session_control_enabled?'Interrupt and end enabled':'Interrupt and end disabled'}</span></article>
          </div>
          <p class="settings-policy-floor"><strong>Safety floor:</strong> send-now, interrupts, session ends, forced Git operations, publishing, uploads, and credential access still require explicit authority or confirmation.</p>
          </section>
          <section><h3>Auto-delivery</h3>
          <p>New observed agent conversations start enabled with these bounds. Each conversation can opt out.</p>
          <label class="check" data-setting="auto_delivery_enabled"><span>Allow auto-delivery for agent conversations</span><input type="checkbox" checked={draft.auto_delivery_enabled} onChange={e=>change('auto_delivery_enabled',e.currentTarget.checked)} /></label>
          <label>Stability window seconds<input type="number" min="2" max="600" step="0.5" value={draft.auto_delivery_stable_seconds} onInput={e=>change('auto_delivery_stable_seconds',Number(e.currentTarget.value))} /></label>
          <label>Consecutive automatic sends before the grant disables itself<input type="number" min="1" max="50" value={draft.auto_delivery_max_consecutive} onInput={e=>change('auto_delivery_max_consecutive',Number(e.currentTarget.value))} /></label>
          <label data-setting="auto_delivery_session_ttl_minutes">Grant lapses after this many idle minutes<input type="number" min="1" max="1440" value={draft.auto_delivery_session_ttl_minutes} onInput={e=>change('auto_delivery_session_ttl_minutes',Number(e.currentTarget.value))} /></label>
          <label data-setting="auto_delivery_reply_window_minutes">Minutes a session keeps its grant while awaiting a reply<input type="number" min="0" max="1440" value={draft.auto_delivery_reply_window_minutes} onInput={e=>change('auto_delivery_reply_window_minutes',Number(e.currentTarget.value))} /><small>Extends only the current request-response exchange. Set 0 to disable.</small></label>
          <label>Back-off seconds after a refused attempt<input type="number" min="0" max="3600" step="0.5" value={draft.auto_delivery_refusal_backoff_seconds} onInput={e=>change('auto_delivery_refusal_backoff_seconds',Number(e.currentTarget.value))} /></label>
          <div class="quiet-hours"><label>Quiet from<input type="time" value={draft.auto_delivery_quiet_start} onInput={e=>change('auto_delivery_quiet_start',e.currentTarget.value)} /></label><label>Until<input type="time" value={draft.auto_delivery_quiet_end} onInput={e=>change('auto_delivery_quiet_end',e.currentTarget.value)} /></label></div>
          <p>Manual sends reset the consecutive count. Quiet hours pause automatic delivery only. The queue pane owns the immediate global pause.</p>

          {/* Mid-turn delivery belongs under auto-delivery rather than beside the
              messaging bounds: it is a *delivery* mode with its own strictly narrower
              readiness predicate, not a kind of message. */}
          <h4>Mid-turn delivery</h4>
          <p>Mid-turn delivery hands text to a busy CLI for its next boundary. The target Project may disable it.</p>
          <label class="check" data-setting="agent_interject_enabled"><span>Allow mid-turn delivery</span><input type="checkbox" checked={draft.agent_interject_enabled} onChange={e=>change('agent_interject_enabled',e.currentTarget.checked)} /></label>
          <label data-setting="agent_interject_hourly_budget">Mid-turn deliveries one session may ask for per hour<input type="number" min="0" max="1000" disabled={!draft.agent_message_limits_enabled} value={draft.agent_interject_hourly_budget} onInput={e=>change('agent_interject_hourly_budget',Number(e.currentTarget.value))} /><small>Tighter than the message budget because a mid-turn landing costs attention immediately. With limits off: 120/hour backstop.</small></label>
          <label data-setting="agent_interject_min_interval_seconds">Seconds between mid-turn deliveries into one session<input type="number" min="0" max="3600" step="1" disabled={!draft.agent_message_limits_enabled} value={draft.agent_interject_min_interval_seconds} onInput={e=>change('agent_interject_min_interval_seconds',Number(e.currentTarget.value))} /><small>Stops a peer machine-gunning a working session. With limits off: 5-second backstop.</small></label>
          </section>

          {/* Approvals sit beside auto-delivery deliberately: both answer "what
              does swe-mux do on my behalf", and both are one install switch over
              bounded per-conversation grants. */}
          <section><h3>Approvals</h3>
          <p>Makes conversation-scoped approval modes available for Claude. Off leaves every request to the operator.</p>
          <label class="check" data-setting="approval_auto_enabled"><span>Allow swe-mux to answer approvals</span><input type="checkbox" checked={draft.approval_auto_enabled} onChange={e=>change('approval_auto_enabled',e.currentTarget.checked)} /></label>
          <label class="check"><span>Offer the “allow all” mode</span><input type="checkbox" checked={draft.approval_allow_all_permitted} onChange={e=>change('approval_allow_all_permitted',e.currentTarget.checked)} /></label>
          <label>Grant expires after this many minutes<input type="number" min="1" max="480" value={draft.approval_grant_ttl_minutes} onInput={e=>change('approval_grant_ttl_minutes',Number(e.currentTarget.value))} /></label>
          <label>Requests one grant may answer<input type="number" min="1" max="5000" value={draft.approval_max_auto_per_grant} onInput={e=>change('approval_max_auto_per_grant',Number(e.currentTarget.value))} /></label>
          <label>Seconds the CLI waits for an answer<input type="number" min="1" max="60" step="0.5" value={draft.approval_hook_timeout_seconds} onInput={e=>change('approval_hook_timeout_seconds',Number(e.currentTarget.value))} /></label>
          <label class="check"><span>Deliver approvals by keypress when the CLI ignores the answer</span><input type="checkbox" checked={draft.approval_keystroke_delivery} onChange={e=>change('approval_keystroke_delivery',e.currentTarget.checked)} /></label>
          <label>Seconds to wait for the dialog before giving up<input type="number" min="1" max="300" step="1" value={draft.approval_keystroke_window_seconds} onInput={e=>change('approval_keystroke_window_seconds',Number(e.currentTarget.value))} /></label>
          <p><strong>Never automatic:</strong> forced Git operations, recursive deletes, sudo, uploads, publishing, infrastructure writes, and credential paths. Project policy defines which routine requests qualify.</p>
          </section>

          <section><h3>Agent messaging</h3>
          <p>Bounds on messages agents address to each other. A message still enters the target's queue under every auto-delivery rule; these limit how far one thread may travel.</p>
          <label class="check" data-setting="agent_messaging_enabled"><span>Allow agent-to-agent messages</span><input type="checkbox" checked={draft.agent_messaging_enabled} onChange={e=>change('agent_messaging_enabled',e.currentTarget.checked)} /></label>
          <label class="check" data-setting="agent_message_limits_enabled"><span>Enforce the rate limits below</span><input type="checkbox" checked={draft.agent_message_limits_enabled} onChange={e=>change('agent_message_limits_enabled',e.currentTarget.checked)} /><small>Off, the bounds run at generous runaway-loop backstops (500/hour, 50 pending, 10 hops, 1000 per thread). Size cap, ring detection, and expiry always apply.</small></label>
          <label>Relay hops before a thread must be restarted by a human<input type="number" min="1" max="10" disabled={!draft.agent_message_limits_enabled} value={draft.agent_message_max_chain_depth} onInput={e=>change('agent_message_max_chain_depth',Number(e.currentTarget.value))} /></label>
          <label>Messages in one thread<input type="number" min="1" max="100" disabled={!draft.agent_message_limits_enabled} value={draft.agent_message_max_thread_turns} onInput={e=>change('agent_message_max_thread_turns',Number(e.currentTarget.value))} /></label>
          <label>Messages one session may originate per hour<input type="number" min="1" max="1000" disabled={!draft.agent_message_limits_enabled} value={draft.agent_message_hourly_budget} onInput={e=>change('agent_message_hourly_budget',Number(e.currentTarget.value))} /></label>
          <label>Pending messages allowed per target<input type="number" min="1" max="100" disabled={!draft.agent_message_limits_enabled} value={draft.agent_message_pending_per_target} onInput={e=>change('agent_message_pending_per_target',Number(e.currentTarget.value))} /></label>
          <label data-setting="agent_message_max_chars">Characters one message may carry<input type="number" min="1" max="100000" value={draft.agent_message_max_chars} onInput={e=>change('agent_message_max_chars',Number(e.currentTarget.value))} /><small>Over this a message is refused, never truncated. Applies even with rate limits off.</small></label>
          </section>

          {/* Its own section rather than more rows under messaging, and a deliberate
              distinction: everything above delivers *text a human still reads*, while
              these tools act on a session directly. They share the tab because both are
              bounds on what one agent may do to another through swe-mux, and they used
              to share nothing at all - every field here was enforced with no control
              anywhere, reachable only by hand-editing the daemon's config file. */}
          <section><h3>Agent actuation</h3>
          <p>Install-wide limits for spawn, interrupt, end-session, and watch requests. Project policy decides availability and authority, edited in the Automation workspace.</p>
          <label class="check" data-setting="request_spawn_enabled"><span>Let agents request spawns</span><input type="checkbox" checked={draft.request_spawn_enabled} onChange={e=>change('request_spawn_enabled',e.currentTarget.checked)} /><small>A granted Project creates the session directly inside the budget below; a draft Project gets an inert Fleet Queue request instead.</small></label>
          <label data-setting="agent_spawn_hourly_budget">Spawns one session may make per hour<input type="number" min="0" max="1000" value={draft.agent_spawn_hourly_budget} onInput={e=>change('agent_spawn_hourly_budget',Number(e.currentTarget.value))} /><small>Bounds fan-out on the granted path.</small></label>
          <label class="check" data-setting="session_control_enabled"><span>Let agents interrupt and end sessions</span><input type="checkbox" checked={draft.session_control_enabled} onChange={e=>change('session_control_enabled',e.currentTarget.checked)} /></label>
          <label data-setting="session_control_hourly_budget">Control actions one session may take per hour<input type="number" min="0" max="1000" value={draft.session_control_hourly_budget} onInput={e=>change('session_control_hourly_budget',Number(e.currentTarget.value))} /></label>
          <label data-setting="session_control_graceful_timeout_s">Seconds a graceful end waits before a hard stop<input type="number" min="1" max="120" step="1" value={draft.session_control_graceful_timeout_s} onInput={e=>change('session_control_graceful_timeout_s',Number(e.currentTarget.value))} /></label>
          <label class="check" data-setting="session_watch_enabled"><span>Let agents watch a session until it settles</span><input type="checkbox" checked={draft.session_watch_enabled} onChange={e=>change('session_watch_enabled',e.currentTarget.checked)} /><small>A watch matures into one queue message addressed to the watcher itself, so it needs no grant — only the bounds below.</small></label>
          <label data-setting="session_watch_max_per_session">Watches one session may hold at once<input type="number" min="1" max="100" value={draft.session_watch_max_per_session} onInput={e=>change('session_watch_max_per_session',Number(e.currentTarget.value))} /><small>Sized for an orchestrator watching a handful of workers.</small></label>
          <label data-setting="session_watch_max_minutes">Longest a single watch may run<input type="number" min="1" max="1440" value={draft.session_watch_max_minutes} onInput={e=>change('session_watch_max_minutes',Number(e.currentTarget.value))} /><small>Minutes; watches live in daemon memory and do not survive its restarts.</small></label>
          </section>

          <section><h3>Queue history</h3>
          <p>Sent, cancelled, failed, and stranded items age out on this window along with their delivery audit. A <em>pending</em> item never does — nothing expires a message that has not been delivered.</p>
          <label data-setting="prompt_queue_retention_days">Prompt queue retention days<input type="number" min="1" max="3650" value={draft.prompt_queue_retention_days} onInput={e=>change('prompt_queue_retention_days',Number(e.currentTarget.value))} /></label>
          </section>
        </Fragment>}

        {/* The OpenRouter key is a provider credential, so it lives with the other
            provider credentials rather than inside the one feature that happened to
            need it first. Everything model-backed depends on it, and the model
            defaults it unlocks are chosen here for the same reason. */}
        {activeTab==='accounts'&&<Fragment>
          <AccountSettings/>
          {/* Which endpoint every model-backed feature calls. It sits above the OpenRouter
              key because it decides whether that key is the credential in play at all, and
              a reader who set it to "custom" should not have to scroll past a key section
              that no longer applies to find out why. */}
          <section><h3>Model provider</h3>
          <p>Choose OpenRouter or a self-hosted OpenAI-compatible <code>/chat/completions</code> endpoint. Speech recognition and synthesis remain local.</p>
            <label for="llm-provider-select" data-setting="llm_provider">Provider<Dropdown id="llm-provider-select" value={draft.llm_provider} onChange={value=>change('llm_provider',value)} options={[
              {value:'openrouter',label:'OpenRouter (hosted)'},
              {value:'custom',label:'Custom OpenAI-compatible endpoint'},
            ]}/><small>Every model-backed feature follows this: observers, scan timeline, spoken summaries, narration, titler, Project card, assistant.</small></label>
            <ProviderReadiness readiness={provider?.llm}/>
            {/* The one bound on the transport rather than on a feature, and it applies
                to whichever endpoint is selected - so it lives with the provider choice
                rather than inside a section that disappears when you switch away. */}
            <label data-setting="openrouter_request_timeout_seconds">Request timeout seconds<input type="number" min="1" max="120" step="1" value={draft.openrouter_request_timeout_seconds} onInput={event=>change('openrouter_request_timeout_seconds',Number(event.currentTarget.value))} /><small>How long any model-backed call waits, on either endpoint; raise it for a slow local server. <strong>Takes effect on the next daemon restart.</strong></small></label>
            {draft.llm_provider==='custom'&&<Fragment>
              <label data-setting="custom_llm_base_url">Base URL<input type="url" autocomplete="off" spellcheck={false} value={draft.custom_llm_base_url} placeholder="http://127.0.0.1:11434/v1" onInput={event=>change('custom_llm_base_url',event.currentTarget.value)} /><small>Everything up to but not including <code>/chat/completions</code> — e.g. Ollama's <code>http://127.0.0.1:11434/v1</code>.</small></label>
              <label data-setting="custom_llm_model">Model<input type="text" autocomplete="off" spellcheck={false} value={draft.custom_llm_model} placeholder="qwen2.5-coder:7b" onInput={event=>change('custom_llm_model',event.currentTarget.value)} /><small>Used only when the endpoint publishes no catalog; every setting under <strong>Models</strong> then resolves to it. Leave blank when a catalog exists.</small></label>
            <label data-setting="custom_llm_catalog_url">Model catalog URL<input type="text" autocomplete="off" spellcheck={false} value={draft.custom_llm_catalog_url} placeholder={`${draft.custom_llm_base_url||'http://host/v1'}/models`} onInput={event=>change('custom_llm_catalog_url',event.currentTarget.value)} /><small>Optional; blank uses <code>/models</code> beside the base URL. Accepts <code>{'{"data":[…]}'}</code>, <code>{'{"models":[…]}'}</code>, or a bare array; verify again after changing it.</small></label>
              <label>API key<input type="password" autocomplete="off" value={customKey} placeholder={customProvider?.secret.configured?'write only · enter to replace':'often unnecessary for a local server'} onInput={event=>setCustomKey(event.currentTarget.value)} /><small>Optional for local servers. Stored in the platform credential store, never in config.</small></label>
              <div class="theme-actions">
                <button class="primary" disabled={!customKey} onClick={()=>void providerKeyAction('set','custom')}>Store key</button>
                <button disabled={!customProvider?.secret.configured} onClick={()=>void providerKeyAction('clear','custom')}>Clear stored key</button>
              </div>
            </Fragment>}
            {/* One button per configured provider rather than one for the active one: an
                operator setting up a local endpoint wants to prove it before switching
                everything over to it, and a verify that only worked on the live provider
                would force exactly the risky ordering. */}
            <h4>Verification</h4>
          <p>Verification sends one small completion and shows the reply, because a reachable endpoint can still return unusable output.</p>
            <ul class="provider-verification-list">
              {(provider?.providers||[]).map(entry=><li key={entry.id}>
                <div class="provider-verification-head">
                  <span class="project-setting-name">{entry.label}{entry.active&&<em class="project-setting-chip">active</em>}</span>
                  <VerificationBadge entry={entry}/>
                </div>
                <p class="project-automation-deps">{entry.origin||'no base URL yet'}{entry.model?` · ${entry.model}`:''}{entry.requires_verification?'':' · configuring the key verifies it'}</p>
                {entry.verification.verified&&<p class="project-automation-deps">Replied “{entry.verification.sample||'(nothing)'}” in {entry.verification.latency_ms} ms{entry.verification.verified_at?`, ${new Date(entry.verification.verified_at*1000).toLocaleString()}`:''}.</p>}
                {entry.verification.stale&&<p class="project-automation-deps"><strong>Edited since it was verified.</strong> The stored proof covers {entry.verification.base_url||'a different URL'}{entry.verification.model?` · ${entry.verification.model}`:''}, so it no longer applies.</p>}
                {/* What the endpoint turned out to be, so an absent picker or absent
                    prices is explainable rather than only visible. Silent until proven:
                    "no catalog" before anyone looked is a guess, not a finding. */}
                {capabilitySummary(entry.verification.capabilities,entry.verification.verified)&&<p class="project-automation-deps">{capabilitySummary(entry.verification.capabilities,entry.verification.verified)}</p>}
                <button disabled={verifying===entry.id} onClick={()=>void verifyProvider(entry.id)}>{verifying===entry.id?'Verifying…':`Verify ${entry.label}`}</button>
              </li>)}
            </ul>
            {verifyResult&&<div class={verifyResult.ok?'provider-verify-result ok':'provider-verify-result failed'} role="status" aria-live="polite">
              {verifyResult.ok
                ? <Fragment>
                    <p><strong>{verifyResult.provider} answered</strong> in {verifyResult.latency_ms} ms as <code>{verifyResult.resolved_model}</code>.</p>
                    <blockquote>{verifyResult.output||(verifyResult.spent_budget_reasoning?'(this model spent the whole probe budget reasoning, so there was no text left — the endpoint is fine)':'(the endpoint replied with no text)')}</blockquote>
                  </Fragment>
                : <p><strong>{verifyResult.provider} did not answer.</strong> {verifyResult.error}</p>}
            </div>}
          </section>
          {/* Only while it is the provider in play. A key section for an endpoint
              nothing is routing through is a control that cannot do anything, and it
              was the largest thing standing between the provider choice and the models
              it decides. Switching the dropdown above brings it straight back - the
              draft updates without a save - so nothing is unreachable, only hidden
              while it would be inert. */}
          {draft.llm_provider==='openrouter'&&<section><h3>OpenRouter</h3>
            <p><span class={`state-dot ${provider?.secret.configured?'idle':'running'}`}/> key::{provider?.secret.configured?'configured':'not configured'} · source::{provider?.secret.source||'none'} · endpoint::{provider?.origin||'fixed OpenRouter API'}</p>
            <p class="profile-hint">One key unlocks every model-backed feature; without it they stay off rather than failing. Get a key at <a href="https://openrouter.ai/keys" target="_blank" rel="noreferrer">openrouter.ai/keys</a>.</p>
            <label>API key<input type="password" autocomplete="off" value={openRouterKey} placeholder={provider?.secret.configured?'write only · enter to replace':'sk-or-…'} onInput={event=>setOpenRouterKey(event.currentTarget.value)} /></label>
            <div class="theme-actions"><button disabled={!openRouterKey} onClick={()=>void providerKeyAction('test')}>Test entered key</button><button class="primary" disabled={!openRouterKey} onClick={()=>void providerKeyAction('set')}>Test + set/replace</button><button disabled={!provider?.secret.configured} onClick={()=>void providerKeyAction('clear')}>Clear stored key</button></div>
            <p aria-live="polite">{providerMessage||'The key is write-only and never appears in config, exports, logs, or browser reads.'}</p>
          </section>}

          <section><h3>Models</h3>
          <p>All model routes are edited here. Routed defaults are inherited; overrides may fall back; pinned models do not.</p>
            {/* Only truthful while the endpoint is doing no routing of its own. A server
                with no catalog serves one model, so every row resolves to it; one that
                serves a catalog collapses nothing, and saying otherwise would report the
                models you chose as silently replaced when they are not. */}
            {endpointOverride&&<p class="settings-warning">This endpoint publishes no model catalog, so it serves one model and every row below resolves to <code>{draft.custom_llm_model||'(no model set)'}</code> instead of the id it names. These settings are kept, and apply again the moment you point at an endpoint that has a catalog.</p>}
            <div class="theme-actions"><button onClick={()=>void refreshModels()}>Refresh models</button><span>{provider?.models.models.length||0} models{provider?.models.stale?' · stale':''}{provider?.models.error?` · ${provider.models.error}`:''}</span>{!!provider?.models.fetched_at&&<span>prices as of {new Date(provider.models.fetched_at*1000).toLocaleDateString()}</span>}</div>
            {!catalogKnown&&<p><small>This endpoint publishes no catalog swe-mux could read, so there is nothing to pick from — type an exact model id into any row and choose <em>Use&nbsp;…</em>. Setting a <strong>Model catalog URL</strong> above turns these back into pickers.</small></p>}
            <ModelRoutingSummary draft={draft} catalog={modelOptions(draft.openrouter_cheap_model)} override={endpointOverride} catalogKnown={catalogKnown} onChange={(key,value)=>change(key,value)}/>
          </section>
        </Fragment>}

        {activeTab==='input'&&<Fragment>
          <section class="input-settings"><h3>Pointer</h3>
          <label class="check"><span>Middle-click paste</span><input type="checkbox" checked={draft.middle_click_paste} onChange={e=>change('middle_click_paste',e.currentTarget.checked)} /></label>
          <label class="check"><span>Broadcast by default</span><input type="checkbox" checked={draft.broadcast_default} onChange={e=>change('broadcast_default',e.currentTarget.checked)} /></label>
          </section>

          <section class="input-settings"><h3>Mobile terminal</h3>
          <p>Touch settings apply on coarse-pointer devices. Text input goes directly to the focused terminal.</p>
          <label>Vertical drag<Dropdown value={draft.mobile_vertical_drag} onChange={value=>change('mobile_vertical_drag',value as Config['mobile_vertical_drag'])} options={[{value:'smart',label:'Smart: app wheel or scrollback'},{value:'terminal',label:'Terminal scrollback only'},{value:'application',label:'Application wheel'},{value:'disabled',label:'Disabled'}]}/></label>
          <label>Scroll direction<Dropdown value={draft.mobile_scroll_direction} onChange={value=>change('mobile_scroll_direction',value as Config['mobile_scroll_direction'])} options={[{value:'natural',label:'Natural touch'},{value:'wheel',label:'Mouse wheel'}]}/></label>
          <label>Scroll sensitivity<input type="number" min="0.25" max="4" step="0.25" value={draft.mobile_scroll_sensitivity} onInput={e=>change('mobile_scroll_sensitivity',Number(e.currentTarget.value))} /></label>
          <label>Long press<Dropdown value={draft.mobile_long_press} onChange={value=>change('mobile_long_press',value as Config['mobile_long_press'])} options={[{value:'context_menu',label:'Select terminal text'},{value:'disabled',label:'Disabled'}]}/></label>
          <label class="check"><span>Copy terminal selection automatically</span><input type="checkbox" checked={draft.terminal_auto_copy_selection} onChange={e=>change('terminal_auto_copy_selection',e.currentTarget.checked)}/></label>
          </section>

          <section class="input-settings"><h3>Clipboard history</h3>
          <p>Keeps copies made inside swe-mux. The OS clipboard is never read. Disk persistence may retain sensitive text.</p>
          <label class="check" data-setting="clipboard_history_enabled"><span>Keep clipboard history</span><input type="checkbox" checked={draft.clipboard_history_enabled} onChange={e=>change('clipboard_history_enabled',e.currentTarget.checked)}/></label>
          <label class="check"><span>Save history to disk (survives daemon restarts)</span><input type="checkbox" checked={draft.clipboard_history_persist} onChange={e=>change('clipboard_history_persist',e.currentTarget.checked)}/></label>
          <label class="check"><span>Skip secret-shaped copies (API keys, tokens, JWTs, private keys)</span><input type="checkbox" checked={draft.clipboard_history_redact_secrets} onChange={e=>change('clipboard_history_redact_secrets',e.currentTarget.checked)}/></label>
          <label>Entries kept<input type="number" min="1" max="2000" value={draft.clipboard_history_limit} onInput={e=>change('clipboard_history_limit',Number(e.currentTarget.value))}/></label>
          <label>Retention hours (0 keeps until evicted)<input type="number" min="0" max="8760" value={draft.clipboard_history_retention_hours} onInput={e=>change('clipboard_history_retention_hours',Number(e.currentTarget.value))}/></label>
          <label>Maximum characters per entry<input type="number" min="256" max="1000000" value={draft.clipboard_history_entry_max_chars} onInput={e=>change('clipboard_history_entry_max_chars',Number(e.currentTarget.value))}/></label>
          <p>Oversized copies are skipped, never truncated. Pinned entries survive retention and Clear.</p>
          </section>

          <section class="input-settings">
          <div class="keybinding-heading"><div><h3>Touch gestures</h3><p>Map multi-finger and workspace swipes. Single-finger vertical drags remain terminal scrolling; OS edge gestures remain reserved.</p></div><button onClick={()=>change('mobile_gestures',{...defaultMobileGestureSettings})}>Restore gesture defaults</button></div>
          {GESTURE_SLOTS.map(slot=><label>{GESTURE_LABELS[slot]}<Dropdown value={draft.mobile_gestures?.[slot]??''} onChange={value=>change('mobile_gestures',{...draft.mobile_gestures,[slot]:value})} options={[{value:'',label:'Disabled'},...bindingCommands.map(command=>({value:command.id,label:command.label}))]}/></label>)}
          <label class="check"><span>Horizontal swipe closes the open edge panel</span><input type="checkbox" checked={draft.mobile_gesture_swipe_away_close!==false} onChange={e=>change('mobile_gesture_swipe_away_close',e.currentTarget.checked)}/></label>
          <label class="check"><span>Swipe right closes one overlay level</span><input type="checkbox" checked={draft.mobile_gesture_overlay_back!==false} onChange={e=>change('mobile_gesture_overlay_back',e.currentTarget.checked)}/></label>
          <label class="check"><span>Contextual swipes on toolbars, tabs, voice, and editor rails</span><input type="checkbox" checked={draft.mobile_surface_gestures!==false} onChange={e=>change('mobile_surface_gestures',e.currentTarget.checked)}/></label>
          <label class="check"><span>System back visits recent tabs before leaving the app</span><input type="checkbox" checked={draft.mobile_back_view_history!==false} onChange={e=>change('mobile_back_view_history',e.currentTarget.checked)}/></label>
          </section>

          <section class="input-settings">
          <div class="keybinding-heading"><div><h3>Keyboard shortcuts</h3><p>Click a command, then press the new shortcut. Changes apply when Settings is saved.</p></div><button onClick={()=>{setBindings({...bindingDefaults});setCapturingCommand(null);setBindingError('')}}>Restore shortcut defaults</button></div>
          {capturingCommand&&<div class="keybinding-capture" role="status"><span>PRESS KEYS FOR</span><strong>{bindingCommands.find(command=>command.id===capturingCommand)?.label||capturingCommand}</strong><button onClick={()=>{setCapturingCommand(null);setBindingError('')}}>Cancel</button></div>}
          {bindingError&&<p class="keybinding-error" role="alert">{bindingError}</p>}
          <div class="keybinding-list">
            {[...new Set(bindingCommands.map(command=>command.category))].map(category=><section class="keybinding-group" aria-label={`${category} shortcuts`}><h4>{category}</h4>{bindingCommands.filter(command=>command.category===category).map(command=>{const chord=bindingForCommand(command.id);return <article class={capturingCommand===command.id?'capturing':''}><button class="keybinding-command" onClick={()=>{setCapturingCommand(command.id);setBindingError('')}} title={command.id}><span>{command.label}</span><small>{command.id}</small></button><button class="keybinding-chord" onClick={()=>{setCapturingCommand(command.id);setBindingError('')}} aria-label={`Set shortcut for ${command.label}`}><kbd>{chord?displayChord(chord):'not set'}</kbd></button><button class="keybinding-clear" disabled={!chord} onClick={()=>clearBinding(command.id)} aria-label={`Clear shortcut for ${command.label}`}>×</button></article>})}</section>)}
          </div>
          <details class="keybinding-policy"><summary>Reserved shortcut policy</summary><ul>{bindingPolicy.rules.map(rule=><li>{rule}</li>)}</ul><div><strong>BROWSER</strong>{bindingPolicy.browser_reserved.map(chord=><kbd>{displayChord(chord)}</kbd>)}</div><div><strong>DESKTOP APP</strong>{bindingPolicy.desktop_only.map(chord=><kbd>{displayChord(chord)}</kbd>)}</div><div><strong>APPLICATION</strong>{bindingPolicy.application_reserved.map(chord=><kbd>{displayChord(chord)}</kbd>)}</div><div><strong>TERMINAL</strong>{bindingPolicy.terminal_reserved.map(chord=><kbd>{displayChord(chord)}</kbd>)}</div></details>
          </section>
        </Fragment>}

        {activeTab==='usage'&&<Fragment><section><h3>Operational telemetry</h3><div class="theme-actions"><button class="primary" onClick={onOpenUsage}>Open telemetry dashboard</button><button disabled={!config?.ccusage_enabled || usage?.refreshing || usageRefreshMessage.startsWith('Refreshing')} onClick={()=>void refreshUsage()}>{usageRefreshMessage.startsWith('Refreshing')?'Refreshing…':'Refresh historical usage'}</button><button onClick={()=>void clearUsage()}>Clear ccusage cache</button></div><label>Operational telemetry retention days<input type="number" min="1" max="3650" value={draft.operational_telemetry_retention_days} onInput={e=>change('operational_telemetry_retention_days',Number(e.currentTarget.value))}/></label><label>Provider quota poll minutes<input type="number" min="5" max="1440" value={draft.provider_quota_poll_minutes} onInput={e=>change('provider_quota_poll_minutes',Number(e.currentTarget.value))}/></label><label class="check"><span>Refresh active quota after eligible root turns</span><input type="checkbox" checked={draft.provider_quota_turn_refresh_enabled} onChange={e=>change('provider_quota_turn_refresh_enabled',e.currentTarget.checked)}/></label><label>Minimum minutes between turn-triggered refreshes<input type="number" min="1" max="1440" value={draft.provider_quota_turn_refresh_min_minutes} onInput={e=>change('provider_quota_turn_refresh_min_minutes',Number(e.currentTarget.value))}/><small>Globally rate limited and selected-account only.</small></label></section><section><h3>Historical ccusage</h3><p class={usageRefreshMessage.startsWith('Refresh failed')?'settings-inline-error':''} aria-live="polite">{usageRefreshMessage || (usage ? `${usage.collector.id}: ${usage.collector.status}${usage.collector.error?` (${usage.collector.error})`:''}` : 'usage status unavailable')}</p>{draft.ccusage_enabled&&!config?.ccusage_enabled&&<p>Save these settings before refreshing.</p>}<label class="check" data-setting="ccusage_enabled"><span>Enable ccusage refresh</span><input type="checkbox" checked={draft.ccusage_enabled} onChange={e=>change('ccusage_enabled',e.currentTarget.checked)} /></label><label>Background refresh minutes<input type="number" min="0" max="1440" value={draft.ccusage_refresh_minutes} onInput={e=>change('ccusage_refresh_minutes',Number(e.currentTarget.value))} /></label><label>Install/update command<input readonly value={usage?.install_command||'npm install -g ccusage@latest'} onFocus={event=>event.currentTarget.select()} /></label><button onClick={()=>void navigator.clipboard.writeText(usage?.install_command||'npm install -g ccusage@latest')}>Copy install command</button><details class="settings-advanced"><summary>Advanced collector command</summary><label>ccusage collector command<textarea value={draft.usage_command.join('\n')} onInput={e=>change('usage_command',e.currentTarget.value.split('\n').filter(Boolean))} /></label>{Object.entries(draft.usage_commands).map(([name,command])=><label>{name} legacy source override<textarea value={command.join('\n')} onInput={e=>change('usage_commands',{...draft.usage_commands,[name]:e.currentTarget.value.split('\n').filter(Boolean)})} /></label>)}</details></section></Fragment>}

        {activeTab==='automation'&&<section><h3>Automation workspace</h3>
          <p>Graph definitions, rules, Project policy, global limits, spend, learned fixes, and diagnostics now share one Automation workspace.</p>
          <div class="settings-status-grid"><article><strong>Engine</strong><span>{draft.automation_enabled?'On':'Off'}</span></article><article><strong>Rules</strong><span>{automation?.rules.length||0} custom</span></article><article><strong>Queue</strong><span>{automation?.queue.size||0}/{automation?.queue.capacity||0}</span></article><article><strong>Runtime</strong><span>{automation?.diagnostic?'Needs review':'Healthy'}</span></article></div>
          <div class="theme-actions"><button class="primary" onClick={onOpenAutomation}>Open Automation workspace</button></div>
        </section>}

        {activeTab==='notifications'&&<NotificationAlertSettings/>}

        {/* Voice is the largest tab in the panel, and it used to be one `<section>` with
            eight headings inside it: read-aloud policy, engine, pronunciation, summary
            budgets, the microphone, the phrase table, the full command catalog, the
            latency readout, the tester, and mobile setup, in one unbroken column. That
            is a reference manual and a control panel stacked on top of each other, and
            the only way to find anything in it was to scroll past everything else.
            It is five separate capability pages now: Read aloud, Talk & dictation,
            Voice commands, Mux assistant, and Diagnostics. The expandable sidebar exposes
            the same pages on desktop and mobile. Reference material inside a page still
            collapses behind a disclosure. Nothing
            moved tabs and nothing gained a second owner: every install-wide switch is
            still edited in exactly one place, and every `data-setting` mark travelled
            with its control (`settingTargets.ts`, `test/settingTargets.test.ts`).
            A marked control deliberately never sits inside a *collapsed* disclosure. */}
        {activeTab==='voice'&&<Fragment>
          <section><h3>Read aloud</h3>
          <p aria-live="polite"><span class={`state-dot ${voiceInfo?.engine_available?'idle':'running'}`}/> engine::{voiceInfo?.engine||draft.tts_engine} {voiceInfo?.engine_available?'available':'unavailable'}{voiceInfo?.diagnostic?` · ${voiceInfo.diagnostic}`:''} · clips::{voiceInfo?.clip_count??0} · cache::{Math.round((voiceInfo?.cache_bytes||0)/1048576)}/{Math.round((voiceInfo?.cache_limit_bytes||0)/1048576)} MB · summary spend today::${(voiceInfo?.spend_today.cost_usd||0).toFixed(3)}</p>
          {/* Three switches decide whether a word is ever spoken, and they used to sit in
              three unrelated places — a checkbox here, a pane chip there, a button on a
              floating strip — so the honest answer to "why is it talking / why is it
              silent" needed all three. They are one numbered block now: each layer says
              what it governs and where its per-item control lives, in the order the
              question is actually asked. */}
          <div class="policy-stack" data-policy="read-aloud">
            <h4>Policy · what speaks, and where</h4>
            <div class="policy-layer">
              <span class="policy-step">1</span>
              <div class="policy-body">
                <label class="check" data-setting="tts_enabled"><span>Read aloud is on (master)</span><input type="checkbox" checked={draft.tts_enabled} onChange={e=>change('tts_enabled',e.currentTarget.checked)} /></label>
                <small>Global: off stops audio generation and playback everywhere.</small>
              </div>
            </div>
            <div class={`policy-layer ${draft.tts_enabled?'':'inert'}`}>
              <span class="policy-step">2</span>
              <div class="policy-body">
                <label data-setting="tts_default_mode">Each session: does it generate?<Dropdown value={draft.tts_default_mode} onChange={value=>change('tts_default_mode',value as Config['tts_default_mode'])} options={[{value:'off',label:'Off until marked'},{value:'on_demand',label:'On demand (speak button)'},{value:'auto',label:'Auto on every reply'}]}/></label>
                <small>Per session: the Read aloud panel can override this default.</small>
              </div>
            </div>
            <div class={`policy-layer ${draft.tts_enabled?'':'inert'}`}>
              <span class="policy-step">3</span>
              <div class="policy-body">
                <label class="check"><span>Autoplay on this device (browser)</span><input type="checkbox" checked={deviceAutoplay} onChange={e=>{const next=e.currentTarget.checked;setAutoplayEnabled(next);setDeviceAutoplay(next)}} /></label>
                <small>Per device: only the focused session plays; other clips are held.</small>
              </div>
            </div>
          </div>
          </section>

          {/* Provider-specific panels are mutually exclusive in the DOM, while their
              values remain independent fields in the complete Settings draft. */}
          <section><h3>TTS provider</h3>
          <p>Provider and voice used for the next speech stream.</p>
          <label data-setting="tts_engine">Provider<Dropdown value={draft.tts_engine} onChange={value=>change('tts_engine',value as Config['tts_engine'])} options={[{value:'sapi',label:'OS voice (offline, no download)'},{value:'kokoro',label:'Kokoro-82M (local neural, one-time download)'},{value:'edge',label:'Edge TTS (experimental external, online)'}]}/></label>
          {draft.tts_engine==='sapi'&&<>
            <label>SAPI voice (blank = system default)<input value={draft.tts_sapi_voice} onInput={e=>change('tts_sapi_voice',e.currentTarget.value)} /></label>
            <label>SAPI rate (-10 slow … 10 fast)<input type="number" min="-10" max="10" value={draft.tts_sapi_rate} onInput={e=>change('tts_sapi_rate',Number(e.currentTarget.value))} /></label>
          </>}
          {draft.tts_engine==='kokoro'&&<>
            {/* Fifty-odd chips, each of them worth tapping to audition and none of them
                worth scrolling past every time. Folded, with the current voice named on
                the summary so the closed state still answers which one is selected -
                the same rule the tab's reference sections follow, applied to a control
                that is long rather than rarely read. */}
            <details class="settings-disclosure kokoro-voice-disclosure">
              <summary>Kokoro voice <em>· {kokoroVoiceLabel(draft.tts_kokoro_voice)}</em></summary>
              <KokoroVoicePicker
                voices={voiceInfo?.kokoro_model?.voices||['af_heart']}
                ready={voiceInfo?.kokoro_model?.status==='ready'}
                selected={draft.tts_kokoro_voice}
                onSelect={voice=>change('tts_kokoro_voice',voice)}
              />
            </details>
            <label>Speed (0.5–2.0)<input type="number" step="0.05" min="0.5" max="2" value={draft.tts_kokoro_speed} onInput={e=>change('tts_kokoro_speed',Number(e.currentTarget.value))} /></label>
            <KokoroModelPanel initial={voiceInfo?.kokoro_model||null}/>
          </>}
          {draft.tts_engine==='edge'&&<EdgeTtsSettings value={draft} onChange={(field,value)=>change(field,value as never)}/>}
          </section>

          {/* Kokoro owns the G2P, respellings, and observed unknown-word history.
              Other providers never render or interpret those controls. */}
          {draft.tts_engine==='kokoro'&&<section><h3>Pronunciation</h3>
            <details class="settings-disclosure">
              <summary>Respellings and spelled-word history <em>· {Object.keys(draft.tts_kokoro_lexicon||{}).length} entr{Object.keys(draft.tts_kokoro_lexicon||{}).length===1?'y':'ies'}</em></summary>
          <p>Teach Kokoro project names and jargon as <code>word → pronunciation</code>. Use ✨ for phonemes, ♪ to preview, and the recent-words list to fix spelled-out terms.</p>
              <TtsLexiconEditor lexicon={draft.tts_kokoro_lexicon||{}} spelled={voiceInfo?.kokoro_spelled_words||[]} onChange={next=>change('tts_kokoro_lexicon',next)}/>
            </details>
          </section>}

          {/* What gets spoken, and what it costs. `tts_content` chooses between the two
              halves, so it leads them rather than sitting a heading above. */}
          <section><h3>Spoken summary</h3>
          <p>Summary uses the configured model and daily budget. Verbatim uses no model.</p>
          <label>Content<Dropdown value={draft.tts_content} onChange={value=>change('tts_content',value as Config['tts_content'])} options={[{value:'summary',label:'Spoken summary (LLM)'},{value:'verbatim',label:'Verbatim reply (markdown stripped)'}]}/><small>The default for every session. The voice panel's <code>tts</code> tab and the pane's player strip both override it for that session alone.</small></label>
          <div class="model-routing-elsewhere" data-setting="tts_summary_model"><span>Summary model</span><code>{routedModel('tts_summary_model')}</code><button type="button" onClick={()=>goToSetting('accounts','tts_summary_model')}>Edit in Accounts → Models</button></div><small>An override. Left blank it follows the cheap model.</small>
          <label>Summary max tokens<input type="number" min="64" max="2000" value={draft.tts_summary_max_tokens} onInput={e=>change('tts_summary_max_tokens',Number(e.currentTarget.value))} /></label>
          <BudgetControl name="tts_daily_budget" label="Read-aloud summaries, daily" value={draft.tts_daily_budget} onChange={value=>change('tts_daily_budget',value)} maxTokens={100000000} maxUsd={100} reportsCost={provider?.llm?.reports_cost} unpricedCalls={voiceInfo?.spend_today?.unpriced_calls}/>
          <label>Verbatim character cap<input type="number" min="200" max="40000" value={draft.tts_verbatim_max_chars} onInput={e=>change('tts_verbatim_max_chars',Number(e.currentTarget.value))} /><small>Applies to the verbatim mode only, where no model is involved to shorten anything.</small></label>
          </section>

          <section><h3>Clip storage</h3>
          <label>Audio cache limit (MB)<input type="number" min="10" max="5000" value={draft.tts_cache_mb} onInput={e=>change('tts_cache_mb',Number(e.currentTarget.value))} /><small>Shared by every provider. Generated clips are files under the data directory; the oldest complete streams are dropped past this limit.</small></label>
          </section>

          {/* The whole capture half in one place: the switch, the decoders it runs, and
              the trigger word they are listening for. The wake words used to sit four
              headings below the microphone switch that makes them do anything. */}
          <section><h3>Talk &amp; dictation</h3>
          <p><strong>Talk means you speak to swe-mux.</strong> It owns microphone capture, dictation, wake words, and voice commands.</p>
          <label class="check" data-setting="stt_enabled"><span>Enable Talk &amp; dictation</span><input type="checkbox" checked={draft.stt_enabled} onChange={e=>change('stt_enabled',e.currentTarget.checked)} /></label>
          {draft.stt_enabled&&draft.stt_engine==='whisper'&&<p class="profile-hint">The first Talk downloads the local Whisper speech model (several hundred MB) plus the browser voice-activity runtime. It runs once, then transcription is offline.</p>}
          <label>Daemon transcription engine<Dropdown value={draft.stt_engine} onChange={value=>change('stt_engine',value as Config['stt_engine'])} options={[{value:'whisper',label:'Whisper Turbo (local, recommended)'},{value:'sapi',label:'Windows Speech Recognition (legacy)'}]}/></label>
          <label>Recognition language<input value={draft.stt_language} placeholder="en-US" onInput={e=>change('stt_language',e.currentTarget.value)} /><small>A first-use choice, not a fixed assumption: set the language and model that match how you speak.</small></label>
          {draft.stt_engine==='whisper'&&<label>Dictation model<input value={draft.stt_whisper_model} placeholder="turbo" onInput={e=>change('stt_whisper_model',e.currentTarget.value)} /></label>}
          {draft.stt_engine==='whisper'&&<label title="Used for the speculative pass that only has to recognize a wake word and a command phrase. Blank decodes commands on the dictation model: correct, but slower.">Routing model (spoken commands)<input value={draft.stt_routing_model} placeholder="small.en" onInput={e=>change('stt_routing_model',e.currentTarget.value)} /></label>}
          <p>STT::{voiceInfo?.stt_available?'available':'unavailable'} · engine::{voiceInfo?.stt_engine||draft.stt_engine}{voiceInfo?.stt_diagnostic?` · ${voiceInfo.stt_diagnostic}`:''}</p>
          <p>Talk keeps listening across pauses. Wake-word commands act immediately; other speech becomes dictation. Raw audio is deleted after transcription.</p>
          <h4>Wake words</h4>
          <p>Comma-separated spellings the recognizer may produce. Test them under Diagnostics.</p>
          <label>Wake words<input value={(draft.voice_wake_words||[]).join(', ')} placeholder="mux, mucks, max" onInput={e=>change('voice_wake_words',e.currentTarget.value.split(',').map(item=>item.trim()).filter(Boolean))} /></label>
          </section>

          {/* The phrase table, on its own. Seventeen rows is the largest single control in
              the tab, and it used to share a heading with the wake words that trigger it
              and with two paragraphs about built-in queries it does not configure. */}
          <section><h3>Voice commands</h3>
          <p>Commands run through Talk after a wake word. Leave a phrase blank to disable it.</p>
          {draft.stt_enabled?<div class="voice-commands">
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
          </div>:<div class="settings-capability-flag"><span aria-hidden="true">⚑</span><div><strong>Talk is off.</strong><small>Command phrases are hidden until microphone input is enabled.</small></div><button type="button" onClick={()=>change('stt_enabled',true)}>Enable Talk &amp; dictation</button></div>}
          </section>

          {/* Reference, not a control: the complete live catalog, read once and then rarely.
              Collapsed by default so it stops standing between the phrase table above and
              the assistant below. Nothing inside carries a `data-setting`: the reveal does
              open the disclosures above its target (pinned by `setting-reveal.spec.ts`), but
              a marked control behind a fold is one a search result and a deep link both
              reach through an extra state change, so the marks stay above it. */}
          <section><h3>Command reference</h3>
          {draft.stt_enabled?<details class="settings-disclosure">
          <summary>Every spoken command, as currently configured</summary>
          <p>Start commands with a wake word. Project numbers follow the sidebar; session numbers use the selected Project. Braces mark spoken values.</p>
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
          </details>:<p class="settings-muted-reference">Command reference hidden while Talk &amp; dictation is off.</p>}
          </section>

          <section><h3>Mux assistant</h3>
          <p><strong>Mux Assistant is independent from Talk.</strong> Text chat works whenever the assistant is on. Unmatched spoken requests reach it only when Talk &amp; dictation is also on.</p>
          <label class="check" data-setting="assistant_enabled"><span>Enable the Mux assistant</span><input type="checkbox" checked={draft.assistant_enabled} onChange={e=>change('assistant_enabled',e.currentTarget.checked)} /></label>
          {draft.assistant_enabled&&!draft.stt_enabled&&<div class="settings-capability-status"><strong>Text chat available · spoken fallback unavailable</strong><span>Enable Talk &amp; dictation to speak to the assistant.</span></div>}
          <div class="model-routing-elsewhere" data-setting="assistant_model"><span>Assistant model</span><code>{routedModel('assistant_model')}</code><button type="button" onClick={()=>goToSetting('accounts','assistant_model')}>Edit in Accounts → Models</button></div><small>Pinned rather than routed: the assistant is a tool-calling loop and needs a model that reliably emits well-formed calls. <code>openai/gpt-5.6-terra</code> is verified; <code>openai/gpt-5.6-luna</code> is the cheap alternative.</small>
          <BudgetControl name="assistant_daily_budget" label="Assistant, daily" value={draft.assistant_daily_budget} onChange={value=>change('assistant_daily_budget',value)} maxTokens={100000000} maxUsd={1000} usdStep={0.05} reportsCost={provider?.llm?.reports_cost}/>
          <label>Reversible-action trust<Dropdown value={draft.assistant_trust_reversible} onChange={value=>change('assistant_trust_reversible',value as Config['assistant_trust_reversible'])} options={[{value:'cancel_window',label:'Announce with a cancel window (default)'},{value:'confirm',label:'Always confirm'},{value:'auto',label:'Run silently'}]}/><small>Applies to queueing drafts, note appends, and spawns. Interrupt, send-now, and end-session always confirm.</small></label>
          <label class="check" data-setting="assistant_stream_replies"><span>Stream the reply as it is written</span><input type="checkbox" checked={draft.assistant_stream_replies} onChange={e=>change('assistant_stream_replies',e.currentTarget.checked)} /></label>
          <p>Streaming releases the first sentence while the rest is generated. Off buffers the full reply.</p>
          <label>Reply max tokens<input type="number" min="128" max="8192" value={draft.assistant_max_output_tokens} onInput={e=>change('assistant_max_output_tokens',Number(e.currentTarget.value))} /></label>
          <label>Dialog memory (messages per turn)<input type="number" min="2" max="200" value={draft.assistant_context_messages} onInput={e=>change('assistant_context_messages',Number(e.currentTarget.value))} /></label>
          <label>Spoken-chat patience (ms)<input type="number" min="0" max="5000" step="100" value={draft.voice_chat_patience_ms} disabled={!draft.stt_enabled} onInput={e=>change('voice_chat_patience_ms',Number(e.currentTarget.value))} /><small>{draft.stt_enabled?'Extra pause before plain Talk speech becomes an assistant turn.':'Inactive while Talk & dictation is off.'}</small></label>
          </section>

          {/* The two measuring instruments, together and folded away. Neither is a setting:
              the tester scores the wake word against the *saved* configuration, and the
              latency report reads back samples the browser already posted. They are
              reached when a trigger word is being chosen or when spoken commands feel
              slow, which is not most visits to this tab. */}
          <section><h3>Testing and latency</h3>
          <details class="settings-disclosure">
          <summary>Measure what the recognizer hears, and how fast it acts</summary>
          <h4>Wake-word tester</h4>
          <p>Tests the real transcription and command matcher. Try each wake word several times and save changes before testing.</p>
          <WakeWordTester
            wakeWords={voiceInfo?.wake_words||draft.voice_wake_words||[]}
            commands={voiceInfo?.commands||draft.voice_commands||[]}
            available={!!voiceInfo?.stt_available}
            diagnostic={voiceInfo?.stt_diagnostic||''}
          />
          <h4>Spoken command latency</h4>
          <p>End of speech to executed action, broken into the four stages it passes through. Samples are recorded by the browser after each utterance and also written to <code>daemon.log</code>. The target is under 500 ms for a short command.</p>
          <VoiceLatencyReport report={latencyReport} onRefresh={loadLatency} onReset={resetLatency} />
          </details>
          </section>

          {/* One-time setup, and a deliberate second copy of what Remote owns: someone
              setting up dictation should not have to leave this tab (`features/ui.md`).
              Folded away because it is done once and then never again. */}
          <section><h3>Mobile voice</h3>
          <details class="settings-disclosure">
          <summary>Set up microphone access from a phone (HTTPS)</summary>
          <p>Mobile microphone capture requires HTTPS. Use the private Tailscale Serve address below; setup may need one approval.</p>
          <TailscaleConnection status={remote} />
          <div class="theme-actions"><button class="primary" disabled={mobileVoiceBusy||!draft.tailnet_enabled} onClick={()=>void setupMobileVoice()}>{mobileVoiceBusy?'Setting up…':remote?.mobile_voice_configured?'Repair secure mobile voice':'Enable secure mobile voice'}</button>{remote?.mobile_voice_url&&<a href={remote.mobile_voice_url} target="_blank" rel="noreferrer">Open secure mobile voice</a>}</div>
          {mobileVoiceMessage&&<p class={mobileVoiceMessage.toLowerCase().includes('failed')?'settings-inline-error':''} aria-live="polite">{mobileVoiceMessage}</p>}
          <PhoneDnsChecklist />
          </details>
          </section>
        </Fragment>}

        {activeTab==='remote'&&<Fragment>
          <section><h3>Tailnet listener</h3>
          <p class="settings-inline-error">Any device on this tailnet reaches this daemon with no login, and an admitted device has full terminal and code-execution authority. Do not enable the tailnet listener on a shared tailnet.</p>
          <label class="check"><span>Listen on Tailscale IPv4</span><input type="checkbox" checked={draft.tailnet_enabled} onChange={event=>change('tailnet_enabled',event.currentTarget.checked)} /><small>Applies on the next daemon restart. Binds localhost plus the Tailscale address only, never every LAN interface.</small></label>
          <TailscaleConnection status={remote} />
          <dl><dt>Local URL</dt><dd>{remote?.listen_url||`http://${draft.host}:${draft.port}`}</dd><dt>Direct tailnet</dt><dd>{remote?.direct_available?'active':draft.tailnet_enabled?'Tailscale address unavailable':'disabled'}</dd>{remote?.tailnet_urls.map(url=><Fragment key={url}><dt>Tailnet URL</dt><dd><a href={url} target="_blank" rel="noreferrer">{url}</a></dd></Fragment>)}</dl>
          <p>Tailnet HTTP is encrypted in transit by Tailscale; mobile microphone access additionally needs the private HTTPS address.</p>
          </section>

          <section><h3>Connect a phone</h3>
          <p>Walks one device through admission: the address to open, the Tailscale state it needs, and a scannable code for it.</p>
          <div class="theme-actions"><button onClick={()=>setConnectPhoneOpen(true)}>Connect a phone…</button></div>
          {connectPhoneOpen&&<ConnectPhone onClose={()=>setConnectPhoneOpen(false)} />}
          </section>

          {/* Both panels render nothing on a host that does not support them. Now that
              each owns a heading, that would leave a heading promising content that is
              not there, so the unsupported case says so instead of going quiet. */}
          <section><h3>Firewall</h3>
          {firewall?.supported
            ?<FirewallPanel status={firewall} busy={firewallBusy} message={firewallMessage} onRepair={()=>void repairFirewall()} />
            :<p>Firewall inspection and repair are only available in the packaged Windows app.</p>}
          </section>

          <section><h3>WSL bridge</h3>
          {wsl?.supported
            ?<WslBridgePanel status={wsl} busy={wslBusy} message={wslMessage} probing={wslProbing}
              onToggle={enabled=>void toggleWsl(enabled)} onProbe={()=>void probeWsl()}
              onInstall={distro=>void installWslBridge(distro)} onRepairFirewall={()=>void repairWslFirewall()} />
            :<p>This host has no WSL, so there is no distribution to run an agent inside.</p>}
          </section>

          <section><h3>Secure HTTPS access</h3>
          <p>Optional HTTPS with Tailscale Serve. {remote?.diagnostic||'Checking the private HTTPS address…'}</p>
          {remote?.funnel_detected&&<p class="settings-inline-error">Tailscale Funnel appears enabled. Public ingress is unsupported; swe-mux only configures private tailnet access.</p>}
          <div class="theme-actions"><button class="primary" disabled={mobileVoiceBusy||!draft.tailnet_enabled} onClick={()=>void setupMobileVoice()}>{mobileVoiceBusy?'Setting up…':remote?.mobile_voice_configured?'Repair secure mobile access':'Enable secure mobile access'}</button>{remote?.mobile_voice_url&&<a href={remote.mobile_voice_url} target="_blank" rel="noreferrer">Open secure address</a>}<button onClick={()=>{void api<RemoteStatus>('GET','/api/remote/status').then(setRemote);void api<FirewallStatus>('GET','/api/remote/firewall').then(setFirewall)}}>Recheck</button></div>
          {mobileVoiceMessage&&<p class={mobileVoiceMessage.toLowerCase().includes('failed')?'settings-inline-error':''} aria-live="polite">{mobileVoiceMessage}</p>}
          <p>No public Funnel access is ever enabled; Tailscale access policy controls which devices connect.</p>
          </section>

          <section><h3>Phone DNS</h3>
          <PhoneDnsChecklist />
          </section>
        </Fragment>}

        {/* Support tooling, not remote configuration: what the host is missing, how to
            push new code into the running app, and one bundle to hand over when
            something is wrong. */}
        {activeTab==='diagnostics'&&<Fragment>
          <section><h3>System prerequisites</h3>
          <p class="profile-hint">These back specific features. Each fails gracefully when absent, so a missing one reads as unconfigured, not broken.</p>
          {prerequisites
            ?<div class="settings-prerequisites"><ul>{prerequisites.map(prereq=><li key={prereq.id} class={prereq.present?'prereq-ok':'prereq-missing'}>
              <span>{prereq.present?'✓':'✗'} {prereq.label}</span>
              <small>{prereq.purpose}{prereq.present&&prereq.path?` · ${prereq.path}`:''}</small>
              {!prereq.present&&<small><code>{prereq.install_command}</code> · <a href={prereq.download_url} target="_blank" rel="noreferrer">download</a></small>}
            </li>)}</ul></div>
            :<p>Prerequisite status is unavailable.</p>}
          </section>

          {/* The same three commands the app menu carries, reachable from Settings
              because this is where you already are when a change has not appeared.
              Each is session-preserving: none of them reaps a live agent or terminal. */}
          <section><h3>Rebuild and reload</h3>
          <div class="settings-config-actions"><div><p>Every one of these keeps live sessions. <strong>Reload UI</strong> refreshes the page, <strong>Reload daemon</strong> restarts the backend, <strong>Rebuild + redeploy</strong> updates the frozen desktop app.</p></div>
            <div>
              <button disabled={!appCommand('ui.reload')} onClick={()=>runAppCommand('ui.reload')}>Reload UI</button>
              <button disabled={!appCommand('daemon.reload')} onClick={()=>runAppCommand('daemon.reload')}>Reload daemon (keep sessions)</button>
              <button disabled={!appCommand('app.redeploy')} onClick={()=>runAppCommand('app.redeploy')}>Rebuild + redeploy app</button>
            </div>
          </div>
          </section>

          {/* Beside the bundle rather than under Processes: this is what decides how
              much the log that ends up in that bundle actually says. */}
          <section><h3>Logging</h3>
          <p>Sets the daemon log and console level immediately. Use <code>DEBUG</code> before reproducing a problem for export.</p>
          <label data-setting="log_level">Daemon log level<Dropdown value={draft.log_level} onChange={value=>change('log_level',value as Config['log_level'])} options={[
            {value:'DEBUG',label:'DEBUG · everything, including per-request detail'},
            {value:'INFO',label:'INFO · the default'},
            {value:'WARNING',label:'WARNING'},
            {value:'ERROR',label:'ERROR'},
          ]}/><small><code>DEBUG</code> is verbose enough to rotate the log quickly on a busy fleet, so it is worth putting back afterwards.</small></label>
          </section>

          {/* Above Export diagnostics, because it is the same errand one step
              earlier: "collect a bundle" is what you do when you are going to ask
              somebody, and this is asking somebody. */}
          {onLaunchConfigurator&&<section><h3>Ask an agent about this install</h3>
          <p>Starts the default agent against swe-mux with its settings inventory, automation graph, and health report. Changes still require specific approval.</p>
          <div class="theme-actions"><button class="primary" onClick={()=>{onClose();onLaunchConfigurator()}}>Launch the configurator</button></div>
          <p class="profile-hint">Runs as an ordinary session in the current Project. Which agent it uses is Harnesses → Default harness.</p>
          </section>}

          <section><h3>Export diagnostics</h3>
          <p>Exports connection state, firewall status, counters, sanitized config, and recent logs. Secrets are omitted.</p>
          <div class="theme-actions"><button class="primary" disabled={diagnosticsBusy} onClick={()=>void exportDiagnostics()}>{diagnosticsBusy?'Collecting…':'Export diagnostics'}</button></div>
          {diagnosticsMessage&&<p aria-live="polite">{diagnosticsMessage}</p>}
          {diagnosticsText&&<label>Diagnostics bundle<textarea readOnly rows={10} value={diagnosticsText} onClick={event=>event.currentTarget.select()} /></label>}
          </section>
        </Fragment>}

        {activeTab==='appearance'&&<Fragment><section><h3>Theme</h3>
          <div class="theme-field">
            <span>Theme</span>
            <ThemePicker value={draft.theme} customTheme={draft.custom_theme} open={themePickerOpen} onOpenChange={setThemePickerOpen} onChange={value=>{change('theme',value);applyTheme(value)}} onPreview={previewTheme} />
          </div>
          {draft.theme==='custom' && <div class="theme-tokens">{Object.entries(draft.custom_theme).map(([key,value])=><label>{key}<input value={value} onInput={e=>{const custom={...draft.custom_theme,[key]:e.currentTarget.value};change('custom_theme',custom);configureCustomTheme(custom);applyTheme('custom')}} /></label>)}</div>}
          <input class="file-input" ref={themeFile} type="file" accept="application/json" onChange={e=>void importTheme(e.currentTarget.files?.[0])} />
          <div class="theme-actions"><button onClick={()=>themeFile.current?.click()}>Import theme</button><button onClick={exportTheme}>Export theme</button></div>
          <p>Settings, menus, controls, and terminal chrome use the same monospace font token.</p>
          </section><section><SessionRowSettings /></section><section>
          <h3>Right sidebar</h3>
          <label>Drawer tabs<Dropdown value={draft.drawer_tab_display} onChange={value=>change('drawer_tab_display',value as Config['drawer_tab_display'])} options={[{value:'icon',label:'Icons'},{value:'title',label:'Titles'}]}/></label>
          <label>Right rail<Dropdown value={draft.utility_rail_display} onChange={value=>change('utility_rail_display',value as Config['utility_rail_display'])} options={[{value:'icon',label:'Icons'},{value:'title',label:'Titles'}]}/></label>
          <div class="drawer-tab-visibility" role="group" aria-label="Visible side panels">{DRAWER_TABS.map(tab=>{
            const shown=!drawerHiddenTabs.includes(tab.id)
            const blocked=shown&&!canHideDrawerTab(drawerHiddenTabs,tab.id)
            return <label key={tab.id} title={blocked?'The side panel must keep at least one tab.':tab.title}>
              <input type="checkbox" checked={shown} disabled={blocked||!onDrawerTabHidden} onChange={()=>onDrawerTabHidden?.(tab.id,shown)} />
              <span>{tab.label}</span>
            </label>
          })}</div>
          <div class="theme-actions"><button disabled={!drawerHiddenTabs.length||!onShowAllDrawerTabs} onClick={()=>onShowAllDrawerTabs?.()}>Show all panels</button></div>
          <p>Unchecked panels leave the tab strips and the rail without losing their arrangement; commands and menus can still open them. Stored on this device.</p>
          </section><section><h3>Interface scale</h3>
          <label>Desktop interface scale<Dropdown value={String(draft.ui_scale_desktop)} onChange={value=>changeUiScale('ui_scale_desktop',value)} options={UI_SCALE_STEPS.map(step=>({value:String(step),label:uiScaleLabel(step)}))}/></label>
          <label>Mobile interface scale<Dropdown value={String(draft.ui_scale_mobile)} onChange={value=>changeUiScale('ui_scale_mobile',value)} options={UI_SCALE_STEPS.map(step=>({value:String(step),label:uiScaleLabel(step)}))}/></label>
          <p class="settings-scale-active">This window is using the <strong>{currentProfile()==='mobile'?'mobile':'desktop'}</strong> value — the other one will not change anything you can see from here.</p>
          <p><kbd>Ctrl</kbd>+wheel, <kbd>Ctrl</kbd>+<kbd>+</kbd>/<kbd>-</kbd>, and <kbd>Ctrl</kbd>+<kbd>0</kbd> also drive the active value. The note editor keeps its own typography under <strong>Text editor</strong>.</p>
          </section><section><h3>Rail density</h3>
          <label data-setting="rail_density_desktop">Desktop rail density<Dropdown value={draft.rail_density_desktop} onChange={value=>changeRailDensity('rail_density_desktop',value)} options={RAIL_DENSITIES.map(step=>({value:step,label:railDensityLabel(step)}))}/></label>
          <label data-setting="rail_density_mobile">Mobile rail density<Dropdown value={draft.rail_density_mobile} onChange={value=>changeRailDensity('rail_density_mobile',value)} options={RAIL_DENSITIES.map(step=>({value:step,label:railDensityLabel(step)}))}/></label>
          <p>How tightly the Action rail under each terminal packs its buttons. Below Comfortable, a phone's buttons drop under the 44px touch target, which is why the two devices are set separately.</p></section></Fragment>}
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
    selectTab(entry.tab as SettingsTab)
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
  // One search box, drawn in the place each layout reads first: inline in the header on
  // a phone, above the section list on a desktop — beside the navigation it drives.
  const searchBox = <div class="settings-search">
    <input ref={searchInput} type="search" value={query} placeholder="Search settings…" aria-label="Search settings" role="combobox" aria-expanded={searchResults.length>0} aria-controls="settings-search-results" autocomplete="off" spellcheck={false} onInput={event=>{setQuery(event.currentTarget.value);setHighlight(0)}} onKeyDown={onSearchKey} />
    {!!query.trim()&&<div id="settings-search-results" class="settings-search-results" role="listbox" aria-label="Search results">
      {searchResults.length?searchResults.map((entry,index)=><button type="button" role="option" aria-selected={index===activeResult} class={index===activeResult?'active':''} key={`${entry.tab}:${entry.kind}:${entry.key}:${entry.occurrence}`} onPointerDown={event=>event.preventDefault()} onClick={()=>openResult(entry)}><strong>{entry.label}</strong><small>{entry.tabLabel}{entry.section?` · ${entry.section}`:''}</small></button>):<p>No setting matches “{query.trim()}”.</p>}
    </div>}
  </div>
  return <div class="settings-layer" onMouseDown={event=>event.target===event.currentTarget&&requestClose()}><section class="settings-panel" ref={panel} role="dialog" aria-modal={!closeIntent&&!resetIntent} aria-hidden={Boolean(closeIntent||resetIntent)} aria-label="Settings">
    <header>{navTrigger}{heading}
      {narrow&&searchBox}
      <button class="settings-close" aria-label="Close Settings" onClick={()=>requestClose()}>×</button></header>
    {!!query.trim()&&<div class="settings-search-scrim" onPointerDown={()=>setQuery('')} />}
    <main class="settings-body">
      {narrow?tabNav:<div class="settings-nav-col">{searchBox}{tabNav}</div>}
      <div class="settings-content">
        {Object.keys(errors).length > 0 && <section class="settings-errors" aria-live="assertive"><h3>Validation errors</h3>{Object.entries(errors).map(([field,message])=><p><strong>{field}</strong> — {message}</p>)}</section>}

        {tabContent(activeTab)}
      </div>
    </main>
    {/* The dirty hint is a *default*, not an override. It used to win over everything but
        "saving…", which meant a rejected save's own explanation never reached the footer:
        the draft is still dirty after a failure, so the honest status was replaced by
        "unsaved changes" and only the errors block said what happened. A write in flight,
        or one that failed, has something to say that the hint does not. */}
    <footer><span aria-live="polite">{writeBusy||writeFailed?status:dirty?'unsaved changes':status}</span><button onClick={()=>requestClose()}>Cancel</button><button class={`primary${dirty?' unsaved':''}`} disabled={!dirty||writeBusy} onClick={()=>void save()}>{status===SAVING?'Saving…':dirty?'Save changes':'Saved'}</button></footer>
  </section>
  {closeIntent&&<div class="modal-layer settings-confirm-layer" onMouseDown={event=>event.target===event.currentTarget&&setCloseIntent(null)}>
    <section class="modal settings-confirm" ref={confirmPanel} role="alertdialog" aria-modal="true" aria-label="Unsaved settings" onMouseDown={event=>event.stopPropagation()}>
      <div class="modal-heading"><div><span>SETTINGS::UNSAVED</span><h2>Save your changes?</h2></div><button aria-label="Keep editing" onClick={()=>setCloseIntent(null)}>×</button></div>
      <div class="settings-confirm-body"><p>You have changes that have not been saved. Save them before leaving Settings, or discard them and restore the last saved configuration.</p></div>
      <div class="modal-footer"><span>Settings stay open if saving fails.</span><button onClick={()=>setCloseIntent(null)}>Keep editing</button><button class="danger" onClick={discardAndLeave}>Discard</button><button class="primary" disabled={writeBusy} onClick={()=>void saveAndLeave()}>Save changes</button></div>
    </section>
  </div>}
  {resetIntent&&<div class="modal-layer settings-confirm-layer" onMouseDown={event=>event.target===event.currentTarget&&setResetIntent(false)}>
    <section class="modal settings-confirm settings-reset-confirm" ref={resetPanel} role="alertdialog" aria-modal="true" aria-label="Restore default settings" onMouseDown={event=>event.stopPropagation()}>
      <div class="modal-heading"><div><span>SETTINGS::RESTORE</span><h2>Restore every setting to its default?</h2></div><button aria-label="Keep current settings" onClick={()=>setResetIntent(false)}>×</button></div>
      <div class="settings-confirm-body"><p>This rewrites the saved configuration in <code>{draft.data_dir}</code> immediately — it is not staged behind <em>Save changes</em>, it discards unsaved edits, and there is no undo. Keyboard shortcuts, Projects, and provider accounts are kept; everything on these tabs goes back to its default.</p></div>
      <div class="modal-footer"><span>Nothing changes unless you confirm.</span><button onClick={()=>setResetIntent(false)}>Cancel</button><button class="danger" disabled={writeBusy} onClick={()=>void reset()}>Restore defaults</button></div>
    </section>
  </div>}
  </div>
}

/**
 * Kokoro model acquisition: pinned revision, per-file SHA-256, explicit
 * not-downloaded / downloading / ready / error state — a partial download can
 * never be loaded. Progress arrives over the event stream (any device may have
 * started the download); a poll backstops a missed event.
 */
/**
 * The voice picker works like the theme picker: tap a chip to hear that voice
 * immediately and make it the draft selection — nothing is locked in until
 * Settings is saved, and the audition never touches the saved configuration
 * (the daemon synthesizes the sample with the tapped voice regardless of the
 * configured engine, and caches it per voice).
 */
function KokoroVoicePicker({voices,ready,selected,onSelect}:{
  voices:string[];ready:boolean;selected:string;onSelect:(voice:string)=>void
}){
  const [playing,setPlaying]=useState<string|null>(null)
  const [error,setError]=useState<string|null>(null)
  const audioRef=useRef<HTMLAudioElement|null>(null)
  useEffect(()=>()=>{audioRef.current?.pause()},[])
  const audition=async(voice:string)=>{
    onSelect(voice)
    if(!ready)return
    setError(null);setPlaying(voice)
    try{
      // A same-origin URL, never a blob: the document CSP has no media-src, so
      // default-src 'self' governs media and refuses blob: sources outright —
      // the same reason read-aloud clips stream from their /audio URL.
      audioRef.current?.pause()
      const audio=new Audio(`/api/voice/models/kokoro/preview?voice=${encodeURIComponent(voice)}`)
      audioRef.current=audio
      audio.onended=()=>setPlaying(current=>current===voice?null:current)
      audio.onerror=()=>{
        setPlaying(current=>current===voice?null:current)
        setError('The sample could not be played; is the Kokoro model downloaded?')
      }
      await audio.play()
    }catch(cause){
      setPlaying(current=>current===voice?null:current)
      setError(cause instanceof Error?cause.message:String(cause))
    }
  }
  return <div class="kokoro-voice-picker">
    {!ready&&<p class="profile-hint">Download the Kokoro model below to audition voices; tapping still selects.</p>}
    <div class="kokoro-voice-grid" role="listbox" aria-label="Kokoro voices">
      {sortKokoroVoices(voices).map(voice=>{
        const label=kokoroVoiceLabel(voice)
        return <button
          type="button"
          key={voice}
          role="option"
          aria-selected={voice===selected}
          class={`kokoro-voice-chip${voice===selected?' selected':''}${playing===voice?' playing':''}`}
          title={`${label.name} — ${label.flavor||voice}. Tap to hear a sample and select; Save commits.`}
          onClick={()=>void audition(voice)}
        >
          <strong>{playing===voice?'♪ ':''}{label.name}</strong>
          <span>{label.flavor||voice}</span>
        </button>
      })}
    </div>
    {error&&<p class="assistant-error" role="alert">{error}</p>}
  </div>
}

function KokoroModelPanel({initial}:{initial:KokoroModelInfo|null}){
  const [model,setModel]=useState<KokoroModelInfo|null>(initial)
  const [starting,setStarting]=useState(false)
  // The panel is unmounted while another provider is selected. Re-read on each
  // mount so a download that progressed or finished while hidden returns with
  // its real state instead of the stale `/api/voice` snapshot from Settings open.
  useEffect(()=>{
    setModel(initial)
    void api<KokoroModelInfo>('GET','/api/voice/models/kokoro').then(setModel).catch(()=>{})
  },[initial])
  useEffect(()=>{
    const handler=(raw:Event)=>{
      const detail=(raw as CustomEvent).detail as Partial<KokoroModelInfo>&{model?:string}
      if(detail&&(detail.model===undefined||detail.model==='kokoro')&&detail.status)setModel(current=>({...(current||{total_bytes:0,downloaded_bytes:0,voices:[]}),...detail} as KokoroModelInfo))
    }
    window.addEventListener('mux:voice-model',handler)
    return()=>window.removeEventListener('mux:voice-model',handler)
  },[])
  useEffect(()=>{
    if(model?.status!=='downloading')return
    const timer=setInterval(()=>{void api<KokoroModelInfo>('GET','/api/voice/models/kokoro').then(setModel).catch(()=>{})},2000)
    return()=>clearInterval(timer)
  },[model?.status])
  const download=async()=>{
    setStarting(true)
    try{const next=await api<KokoroModelInfo&{started:boolean}>('POST','/api/voice/models/kokoro/download');setModel(next)}
    catch{/* surfaced by the next status refresh */}
    finally{setStarting(false)}
  }
  const status=model?.status||'not_downloaded'
  const total=model?.total_bytes||0
  const done=model?.downloaded_bytes||0
  const pct=total?Math.min(100,Math.round(done/total*100)):0
  return <div class="kokoro-model-panel">
    <p aria-live="polite">
      <span class={`state-dot ${status==='ready'?'idle':status==='downloading'?'running':'stopped'}`}/>
      Kokoro model::{status}
      {status==='downloading'&&` · ${pct}% (${Math.round(done/1048576)}/${Math.round(total/1048576)} MB)${model?.current_file?` · ${model.current_file}`:''}`}
      {status==='ready'&&` · ${Math.round(total/1048576)} MB, hash-verified`}
      {status==='error'&&model?.error&&` · ${model.error}`}
    </p>
    {status!=='ready'&&status!=='downloading'&&<button disabled={starting} onClick={()=>void download()}>{status==='error'?'Retry download':'Download Kokoro voices (~106 MB)'}</button>}
  </div>
}

type LexiconVerdict={ok:boolean;phonemes?:string|null;spoken_as?:string|null;unspeakable?:string[]}
type LexiconCheck={available:boolean;diagnostic?:string|null;results:Record<string,LexiconVerdict>}

/**
 * The user half of the Kokoro repair ladder: a word→respelling map merged over
 * the built-in project lexicon, plus the telemetry list of words the ladder had
 * to spell out letter by letter. Respelling one of those writes a lexicon entry
 * into the draft; Save commits it and the daemon hot-applies (the engine's
 * per-word cache and the audition previews are invalidated server-side).
 *
 * Each row carries a live verdict from `/api/voice/lexicon/check` — the ladder
 * re-verifies every respelling, so a value that is not itself pronounceable
 * (e.g. an invented word like "swee") would be silently rejected in speech and
 * the word spelled anyway; the ✗ makes that visible before Save. The ♪ button
 * auditions the value through the real pipeline via a same-origin GET (the CSP
 * has no media-src, so blob: audio is refused).
 */
function TtsLexiconEditor({lexicon,spelled,onChange}:{
  lexicon:Record<string,string>
  spelled:{word:string;count:number;last_seen:number}[]
  onChange:(next:Record<string,string>)=>void
}){
  const [newWord,setNewWord]=useState('')
  const [newSpoken,setNewSpoken]=useState('')
  const [respell,setRespell]=useState<Record<string,string>>({})
  const [check,setCheck]=useState<LexiconCheck|null>(null)
  const audioRef=useRef<HTMLAudioElement|null>(null)
  useEffect(()=>()=>{audioRef.current?.pause()},[])
  const lexiconJson=JSON.stringify(lexicon)
  useEffect(()=>{
    if(!Object.keys(lexicon).length){setCheck(null);return}
    const timer=setTimeout(()=>{
      void api<LexiconCheck>('POST','/api/voice/lexicon/check',{entries:lexicon}).then(setCheck).catch(()=>setCheck(null))
    },600)
    return()=>clearTimeout(timer)
  // Keyed by content, not object identity: every keystroke makes a new map.
  },[lexiconJson])
  const hear=(value:string)=>{
    if(!value.trim())return
    audioRef.current?.pause()
    const audio=new Audio(`/api/voice/lexicon/preview?text=${encodeURIComponent(value.trim())}`)
    audioRef.current=audio
    void audio.play().catch(()=>{})
  }
  const [buildError,setBuildError]=useState<string|null>(null)
  // The phoneme builder: the daemon derives an exact [word](/phonemes/) value
  // from a plain spelled-how-it-sounds input (or from the word itself when the
  // input is empty), so nobody has to type IPA by hand.
  const build=async(word:string,value:string):Promise<string|null>=>{
    setBuildError(null)
    try{
      const result=await api<{ok:boolean;value?:string|null;diagnostic?:string|null}>('POST','/api/voice/lexicon/build',{word,value})
      if(!result.ok||!result.value){setBuildError(result.diagnostic||'Could not build a pronunciation from that spelling.');return null}
      return result.value
    }catch(cause){setBuildError(cause instanceof Error?cause.message:String(cause));return null}
  }
  const verdict=(word:string):LexiconVerdict|null=>check?.available?check.results[word]??null:null
  const add=(word:string,spoken:string)=>{
    const key=word.trim().toLowerCase();const value=spoken.trim()
    if(!key||!value)return
    onChange({...lexicon,[key]:value})
  }
  const entries=Object.entries(lexicon).sort(([a],[b])=>a.localeCompare(b))
  const pending=spelled.filter(item=>!(item.word in lexicon))
  return <div class="tts-lexicon">
    {entries.map(([word,spoken])=>{
      const state=verdict(word)
      return <Fragment key={word}>
        <div class="tts-lexicon-row">
          <code>{word}</code>
          <input aria-label={`Pronunciation for ${word}`} value={spoken} onInput={e=>onChange({...lexicon,[word]:e.currentTarget.value})}/>
          {state&&<span class={`tts-lexicon-verdict ${state.ok?'ok':'bad'}`} title={state.ok?(state.phonemes?`Speaks as written · ${state.phonemes}`:'Speaks as written'):'This respelling would be rejected and the word spelled out'}>{state.ok?'✓':'✗'}</span>}
          {state&&!state.ok&&<button type="button" title="Build exact phonemes from this spelling" onClick={()=>void build(word,spoken).then(value=>{if(value)onChange({...lexicon,[word]:value})})}>✨</button>}
          <button type="button" title="Hear this pronunciation" onClick={()=>hear(spoken)}>♪</button>
          <button type="button" title={`Remove ${word}`} onClick={()=>{const next={...lexicon};delete next[word];onChange(next)}}>✕</button>
        </div>
        {state&&!state.ok&&<p class="tts-lexicon-hint">{state.unspeakable?.length?<>Not pronounceable: {state.unspeakable.map(piece=><code key={piece}>{piece}</code>)} — tap ✨ to build the exact phonemes from this spelling.</>:<>This respelling cannot be pronounced as written.</>}</p>}
      </Fragment>
    })}
    <div class="tts-lexicon-row">
      <input placeholder="word" aria-label="New lexicon word" value={newWord} onInput={e=>setNewWord(e.currentTarget.value)}/>
      <input placeholder="spoken as… e.g. vault spaces" aria-label="New lexicon pronunciation" value={newSpoken} onInput={e=>setNewSpoken(e.currentTarget.value)}/>
      <button type="button" title="Build exact phonemes from the spelling (uses the word itself if blank)" disabled={!newWord.trim()&&!newSpoken.trim()} onClick={()=>void build(newWord,newSpoken).then(value=>{if(value)setNewSpoken(value)})}>✨</button>
      <button type="button" title="Hear this pronunciation" disabled={!newSpoken.trim()} onClick={()=>hear(newSpoken)}>♪</button>
      <button type="button" disabled={!newWord.trim()||!newSpoken.trim()} onClick={()=>{add(newWord,newSpoken);setNewWord('');setNewSpoken('')}}>Add</button>
    </div>
    {buildError&&<p class="tts-lexicon-hint tts-lexicon-error" role="alert">{buildError}</p>}
    {check&&!check.available&&<p class="tts-lexicon-hint">{check.diagnostic||'Pronunciation checking needs the Kokoro model.'}</p>}
    {!!pending.length&&<div class="tts-lexicon-spelled">
      <h5>Words the voice had to spell out</h5>
      {pending.map(item=><div class="tts-lexicon-row" key={item.word}>
        <code>{item.word}</code><small>×{item.count}</small>
        <input placeholder="spoken as…" aria-label={`Respell ${item.word}`} value={respell[item.word]??''} onInput={e=>{const value=e.currentTarget.value;setRespell(current=>({...current,[item.word]:value}))}}/>
        <button type="button" title="Build exact phonemes (from your spelling, or from the word itself if blank)" onClick={()=>void build(item.word,respell[item.word]||'').then(value=>{if(value)setRespell(current=>({...current,[item.word]:value}))})}>✨</button>
        <button type="button" title="Hear this pronunciation" disabled={!(respell[item.word]||'').trim()} onClick={()=>hear(respell[item.word]||'')}>♪</button>
        <button type="button" disabled={!(respell[item.word]||'').trim()} onClick={()=>add(item.word,respell[item.word]||'')}>Respell</button>
      </div>)}
    </div>}
  </div>
}
