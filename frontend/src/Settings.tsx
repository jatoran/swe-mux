import { codeLabel } from './localization/codeLabels.ts'
import { sourceText, applyLanguagePreference } from './localization/index.ts'
import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { LanguagePicker } from './LanguagePicker'
import { deviceMode, watchDeviceMode } from './deviceMode'
import { ProviderConnectionFields, providerKeyOperation } from './ProviderSetup'
import { ExperiencePreview } from './ExperiencePreview'
import { Fragment } from 'preact'
import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api, type ApiError } from './api'
import { saveFailureStatus, type SettingsApplyResponse } from './settingsSave'
import { commandCategoryLabel, displayChord, shortcutMatches, unboundChord, type Command } from './commands'
import { isFocusTraversalKey, keyChord } from './keys'
import { hostQuery } from './hostProfile.ts'
import { KeyboardProbe } from './KeyboardProbe.tsx'
import { dismissStack } from './dismissStack.ts'
import { useDismissLevel } from './modalFocus'
import { AccountSettings } from './ProviderAccounts'
import { NotificationAlertSettings } from './NotificationPushSettings'
import { SessionRowSettings } from './SessionRowSettings'
import { SessionTopbarSettings } from './SessionTopbarSettings'
import { ActionEditorPanel } from './ActionEditorModal'
import { normalizeIgnorePatterns, parseIgnorePatternDraft, sameDraftValue } from './settingsDraft'
import { commonestParent } from './projectCreate'
import { listShortcutBindings, type ShortcutPolicy } from '@continuity-editor/editor'
import { applyNoteEditorConfig, DEFAULT_NOTE_SHORTCUT_OVERRIDES, resetNoteRailArrangement } from './noteEditorSettings'
import { KokoroModelPanel, VoiceRuntimePanel, WhisperModelPanel, type KokoroModelInfo, type VoiceRuntimeInfo, type WhisperModelInfo } from './voiceModelPanels'
import { applyTheme, configureCustomTheme, type CustomTheme, type ThemeName } from './theme'
import { kokoroVoiceLabel, sortKokoroVoices } from './kokoroVoices'
import { ThemePicker } from './ThemePicker'
import { BudgetControl } from './BudgetControl'
import type { Budget } from './types'
import { uiScaleKeyboardIntent, uiScaleLabel, UI_SCALE_STEPS, type UiScale } from './uiScale'
import { applyRailDensity, railDensityLabel, RAIL_DENSITIES, type RailDensity } from './railDensity'
import { CLAUDE_MAX_COLUMN_STEPS, claudeMaxColumnsLabel, type ClaudeMaxColumns } from './terminalViewport'
import { canvasTextMeasurer, DEFAULT_TERMINAL_FONT_FAMILIES, TERMINAL_FONT_FAMILY_MAX_CHARS, terminalFontAvailability } from './terminalFont'
import { DeviceModeSetting } from './DeviceModeSetting'
import { currentProfile } from './deviceSettings'
import { DRAWER_TABS, type DrawerTabId } from './drawerTabs'
import { canHideDrawerTab } from './drawerVisibility'
import { enableMobileVoice } from './mobileVoice'
import { autoplayEnabled, setAutoplayEnabled } from './voice'
import { TailscaleConnection, PhoneDnsChecklist, FirewallPanel, type RemoteStatus, type FirewallStatus } from './remoteConnection'
import { WslBridgePanel } from './WslBridgePanel'
import { describeLastRun, describeUsageActivity, runUsageRefresh, type UsageCollector } from './usageRefresh'
import { type WslBridgeStatus } from './wslBridge'
import { ConnectPhone } from './ConnectPhone'
import { UpdateDialog } from './UpdateDialog'
import { FactoryResetDialog } from './FactoryResetDialog'
import { VoiceLatencyReport } from './VoiceLatencyReport'
import { WakeWordTester } from './WakeWordTester'
import {
  completeVoiceReference, VOICE_ACTION_META, VOICE_ACTION_ORDER,
} from './voiceCommandReference'
import type { LatencyReportPayload } from './voiceLatency'
import { GESTURE_SLOTS, GESTURE_LABELS, defaultMobileGestureSettings } from './mobileGestures'
import { allBackendNames, allHarnessesIncludingDisabled, appliesWidthEnvelope, harnessDescriptor, harnessDisplayName, harnesses } from './harnessRegistry'
import { domVNode, harvestHeadings, harvestSettings, kindSelector, matchIndex, searchSettings, tabEntry, type SettingsSearchEntry } from './settingsSearch'
import { applySettingsAliases } from './settingsSearchAliases'
import { cueSettingsSection, flashSetting, flashSettingsSection, revealSetting, settingSelector } from './settingReveal'
import {
  railSectionIds, rememberedSections, rememberedTab, rememberSection, rememberTab,
  sameRailSections, SECTION_RAIL_MIN, settingsBreadcrumb, settingsSubpageId, settingsSubpages, settingsTabGroups, settingsTabs, tabForSection,
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
import { PluginsSettings } from './PluginsSettings'
import {
  fetchUpdateStatus, lastCheckedLabel, requestUpdateCheck, runningVersionLabel,
  updateStatusSummary,
  type UpdateStatus,
} from './updateCheck'
import {
  fetchOverlayStatus, installedAtLabel, overlayStatusSummary, restoreOverlay,
  revertOverlay, shouldShowOverlaySection, type OverlayStatus,
} from './frontendOverlay'
import {
  customProviderOverride, MODEL_ROUTES, resolveRoute, type ModelRoutingConfig,
} from './modelRouting'

type Config = {
  ui_language?:string
  revision:number; host:string; port:number; data_dir:string; requires_auth:boolean; access_mode:string; tailnet_enabled:boolean
  startup_cwd:string; default_backend:string; default_harness:string; shell_exe:string
  harness_exe:Record<string,string>; scrollback_bytes:number; attach_replay_bytes:number; history_limit:number
  session_recovery_checkpoint_bytes:number;session_recovery_retention_days:number
  session_recovery_max_sessions:number
  log_level:'DEBUG'|'INFO'|'WARNING'|'ERROR'
  update_check_enabled:boolean
  frontend_overlay_enabled:boolean
  terminal_renderer:'auto'|'dom'|'webgl'
  terminal_font_family:string
  harness_args:Record<string,string[]>
  harness_enabled:Record<string,boolean>
  harness_mcp_enabled:Record<string,boolean>
  harness_cli_enabled:Record<string,boolean>
  harness_skill_enabled:Record<string,boolean>
  experience_tier:''|'terminal'|'deterministic'|'automations'
  harness_instrument_enabled:Record<string,boolean>
  // Absolute paths the user pointed swe-mux at, keyed by prerequisite id. The
  // escape hatch for install layouts detection cannot guess; consulted ahead of
  // PATH and ahead of the well-known locations.
  tool_paths:Record<string,string>
  git_poll_seconds:number;worktree_root:string;git_swe_mux_prompt_enabled:boolean
  git_swe_mux_prompt_decisions:Record<string,'keep_visible'|'ignore_all'>
  new_project_parent:string;reconcile_external_history:boolean;theme:ThemeName
  drawer_tab_display:'icon'|'title'
  utility_rail_display:'icon'|'title'
  process_poll_seconds:number;process_orphan_grace_seconds:number;process_evidence_retention_days:number
  session_process_priority:'normal'|'below_normal';daemon_process_priority:'normal'|'above_normal';gil_switch_interval_ms:number;worker_process_enabled:boolean
  terminal_gateway_enabled:boolean;terminal_gateway_port:number
  ghost_window_sweep_enabled:boolean;ghost_window_poll_seconds:number
  status_timeline_retention_days:number
  operational_telemetry_retention_days:number;canonical_telemetry_native_otel_enabled:boolean
  canonical_telemetry_legacy_dashboard_enabled:boolean;provider_quota_poll_minutes:number
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
  note_scratchpad_enabled:boolean
  note_spellcheck:boolean;note_syntax:'markdown'|'plain'
  note_tab_behavior:'indent'|'focus';note_shortcut_policy:ShortcutPolicy
  note_font_family:string;note_font_size_px:number;note_line_height:number
  note_command_rail:'auto'|'on'|'off';note_rail_button_size_px:number
  note_indent_guides:boolean
  ui_scale_desktop:UiScale;ui_scale_mobile:UiScale
  rail_density_desktop:RailDensity;rail_density_mobile:RailDensity
  rail_enabled_desktop:boolean;rail_enabled_mobile:boolean
  rail_hover_desktop:boolean
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
  scan_timeline_enabled:boolean;scan_timeline_model:string
  attention_daily_interrupt_budget:number;attention_hourly_interrupt_cap:number
  attention_incident_window_seconds:number;attention_breakpoint_markers:boolean
  attention_narration_enabled:boolean;attention_narration_model:string
  openrouter_standard_model:string;openrouter_request_timeout_seconds:number
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
type VoiceStatusInfo = {
  enabled:boolean;engine:string;engine_available:boolean;diagnostic?:string|null;voice:string
  summary_model:string;spend_today:{tokens:number;cost_usd:number;unpriced_calls?:number}
  daily_budget:Budget
  cache_bytes:number;cache_limit_bytes:number;clip_count:number;stt_enabled:boolean
  kokoro_model?:KokoroModelInfo;kokoro_voice?:string;voice_runtime?:VoiceRuntimeInfo
  kokoro_spelled_words?:{word:string;count:number;first_seen:number;last_seen:number}[]
  providers?:{sapi?:{available:boolean;diagnostic?:string|null};kokoro?:{available:boolean;diagnostic?:string|null};edge?:EdgeProviderStatus}
  stt_engine:'sapi'|'whisper';stt_available:boolean;stt_diagnostic?:string|null
  stt_language:string;stt_whisper_model:string;stt_routing_model?:string
  stt_models?:WhisperModelInfo[]
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
  collector:UsageCollector
  cache?:{updated_at?:number;sources?:Record<string,{totals?:Record<string,number>}>}
}

// `state` is three-valued and `present` is the older two-valued view of the same
// answer, true for both found states. The middle state exists because collapsing
// it is what told users to install software they already had: an installer that
// does not touch PATH (Tailscale's, on every Windows machine) left a working tool
// reading as absent.
type PrerequisiteState = 'present'|'off_path'|'missing'
type Prerequisite = {id:string;label:string;purpose:string;present:boolean;state:PrerequisiteState;source:string;path:string|null;download_url:string;install_command:string;path_remedy:string|null}
type KeybindingCommand = {id:string;label:string;category:string}
type ChordWarning = {severity:'blocked'|'contested';scope:string;message:string}
type BindingRule = {keys:string;command:string;host?:string;platform?:string;when?:string;note?:string}
type KeymapPresetSummary = {
  id:string;title:string;description:string;leader:string
  prefix:string;prefix_alternates:string[];warning:string;bindings:number
}
type KeybindingPolicy = {
  hosts:string[];platforms:string[];max_sequence:number
  browser_unreachable:string[];browser_contested:Record<string,string>
  wm_reserved:Record<string,Record<string,string>>
  application_reserved:string[];terminal_reserved:Record<string,string>;rules:string[]
}
/** What `/api/keybindings?host=…&platform=…` answers. `rules` is the durable
 *  document and is what a save writes back; `resolved` is what THIS host actually
 *  dispatches on, and `undeliverable` names the rules it dropped and why - which is
 *  how the editor can say "works in the desktop app" instead of drawing a dead chord
 *  as though it were live. */
type KeybindingsResponse = {
  preset:string;presets:KeymapPresetSummary[];host:string;platform:string
  rules:BindingRule[];resolved:Record<string,Array<{command:string;when:string}>>
  prefixes:string[];labels:Record<string,string>
  undeliverable:Array<{keys:string;command:string;warnings:ChordWarning[]}>
  contested:Array<{keys:string;command:string;warnings:ChordWarning[]}>
  commands:KeybindingCommand[];groups:Array<{category:string;key:string;title:string}>
  when_flags:string[];policy:KeybindingPolicy;rejected:Record<string,string>
}
type CloseIntent = 'close'|'usage'|'automation'|'tutorial'
/** The two in-flight statuses, named because the footer and the dialogs both test for them. */
const SAVING = 'saving…'
const RESTORING = "restoring defaults\u2026"
/** The one atomic-save answer: the new config, the keybindings as re-read, and what committed. */
type SettingsApplyResult = SettingsApplyResponse<Config, KeybindingsResponse>

// One round trip for everything the panel needs on open. `config` is required;
// every other part arrives null when its section failed server-side, with the
// reason under `errors`.
type SettingsBundle = {
  config:Config
  keybindings:KeybindingsResponse|null
  /** Deferred. Requested by the Terminals tab (`?parts=profiles`); it stats
   *  executables to detect shells, which made it the slowest paint part. */
  profiles?:{profiles:LaunchProfile[];detected:LaunchProfile[]}|null
  projects:Project[]|null
  /** Deferred. Requested by the Automation tab (`?parts=automation`). */
  automation?:AutomationStatus|null
  /** Deferred. Absent from the paint response; requested by the Accounts tab
   *  (`?parts=provider`) because it is 177 KiB of model catalogue. */
  provider?:ProviderStatus|null
  /** Deferred. Absent from the paint response; requested by the Usage tab
   *  (`?parts=usage`) because it is 320 KiB of historical spend. */
  usage?:UsageStatus|null
  errors:Record<string,string>
}

/** How long a rail click owns the active section before scroll-spy resumes. Long
 *  enough to cover a smooth scroll, short enough that a manual scroll during it is
 *  not noticeably ignored. */
const SCROLL_CLAIM_MS = 800

// Search entries harvested from a tab's real DOM while it was on screen. Module
// scope, not component state: a tab visited in one Settings session stays fully
// searchable in the next one, for as long as the page lives.
const liveTabEntries = new Map<SettingsTab,SettingsSearchEntry[]>()

/**
 * The panel-wide search index: per tab, its own entry, then what it rendered when it
 * was last on screen, else what `fallback` harvests from its vnodes; then the curated
 * aliases on top. A tab whose fallback throws keeps only its own entry.
 */
function settingsSearchEntries(fallback:((tab:SettingsTab,label:string,index:number)=>SettingsSearchEntry[])|null):SettingsSearchEntry[]{
  return applySettingsAliases(settingsTabs.flatMap((tab,index)=>{
    const own=liveTabEntries.get(tab.id)
    if(own)return [tabEntry(tab.id,tab.label,index),...own]
    try{return [tabEntry(tab.id,tab.label,index),...(fallback?.(tab.id,tab.label,index)||[])]}
    catch{return [tabEntry(tab.id,tab.label,index)]}
  }))
}

/**
 * The index exactly as the search box would build it once every tab has been on screen,
 * plus which tabs have been. For the alias audit (`test/renderer/settings-search.spec.ts`),
 * which needs the labels child components render - a vnode walk cannot see those.
 */
export const harvestedSettingsSearch = ():{harvested:SettingsTab[];entries:SettingsSearchEntry[]} =>
  ({harvested:[...liveTabEntries.keys()],entries:settingsSearchEntries(null)})

/** One empty list, so "this tab has no sections yet" is a stable effect dependency. */
const NO_SECTIONS: SettingsRailSection[] = []

// The note editor's own binding table, enumerated from the editor package so the
// list can never drift from what it actually binds. `isBrowserSafe` is false for
// the chords Chromium claims first (Ctrl+R, Ctrl+K, …): the default browser-safe
// policy releases those unless an explicit override reclaims one.
const NOTE_CHORDS = listShortcutBindings()
const CHORD_PARTS:Record<string,string> = {mod:'Mod',ctrl:'Ctrl',meta:'Meta',get alt() { return t("Settings.c91da2e020") },shift:'Shift'}
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
/**
 * Whether each family the draft names resolves on *this* device, read from a canvas
 * measurement as it is typed. The setting is daemon-wide and a font is per device, so
 * this is the one place the two can be seen to disagree - and a blank draft says nothing,
 * because the default stack is not a claim anybody made.
 */
function TerminalFontAvailability({family}:{family:string}){
  const readings=useMemo(()=>terminalFontAvailability(family,canvasTextMeasurer()), [family, uiLocale.value, catalogRevision.value])
  if(!readings.length)return null
  return <p class="settings-hint terminal-font-availability" aria-live="polite">{readings.map(reading=>
    <span key={reading.name} class={reading.available===false?'warn':''}>
      <code>{reading.name}</code>{' '}
      {reading.available===null ? t("Settings.2220b037d0") : reading.available ? t("Settings.2435b40050") : t("Settings.353dc0a50d")}
    </span>)}</p>
}

function VerificationBadge({entry}:{entry:LlmProviderEntry}){
  if(entry.verification.verified) return <em class="project-setting-chip">{t("Settings.1c34f88707")}</em>
  if(entry.verification.stale) return <em class="project-setting-chip spends">{t("Settings.51c3ffce97")}</em>
  if(!entry.requires_verification) return <em class="project-setting-chip">{t("Settings.face7ae8db")}</em>
  return <em class="project-setting-chip spends">{t("Settings.64f365a9a9")}</em>
}


export function Settings({ activeUiScale, onUiScalePreview, onClose, onOpenUsage:openUsage, onOpenAutomation:openAutomation, onStartTutorial, onStartVoiceSetup, onLaunchConfigurator, focusedProjectId, initialSection, initialSetting, revealToken, voiceCommands=[], navOpen=false, onNavOpenChange, drawerHiddenTabs=[], onDrawerTabHidden, onShowAllDrawerTabs }: { activeUiScale:UiScale;onUiScalePreview:(config:Record<string,unknown>)=>UiScale;onClose: () => void; onOpenUsage?:() => void;onOpenAutomation?:()=>void;onStartTutorial?:()=>void;onStartVoiceSetup?:()=>void;
  /** Start the configurator agent. Owned by the composition root, like the tutorial:
   *  a launch places a pane in the workspace, which this panel does not have. */
  onLaunchConfigurator?:(harness?:string)=>void;focusedProjectId?:string;initialSection?:string;
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
  // The keymap is edited as the rule LIST the daemon stores, not as the resolved map
  // it dispatches: a rule can be scoped to a host, a platform or a `when`, and the
  // resolved map has already thrown all three away. Editing the resolved map and
  // writing it back is how a desktop-only binding would silently disappear the first
  // time a phone opened Settings.
  const [bindingRules, setBindingRules] = useState<BindingRule[]>([])
  const [bindingCommands, setBindingCommands] = useState<KeybindingCommand[]>([])
  const [bindingPolicy, setBindingPolicy] = useState<KeybindingPolicy|null>(null)
  const [keymapPresets, setKeymapPresets] = useState<KeymapPresetSummary[]>([])
  const [keymapPreset, setKeymapPreset] = useState('')
  const [keymapHost, setKeymapHost] = useState<{host:string;platform:string}>({host:'browser',platform:'win'})
  const [keymapWarnings, setKeymapWarnings] = useState<KeybindingsResponse['undeliverable']>([])
  const [presetIntent, setPresetIntent] = useState<KeymapPresetSummary|null>(null)
  const [presetApplying, setPresetApplying] = useState('')
  const [capturingCommand, setCapturingCommand] = useState<string|null>(null)
  /** Chords typed so far while recording, for a binding that is a sequence. */
  const [captureSequence, setCaptureSequence] = useState<string[]>([])
  // The shortcut table's own filter. Separate from the panel-wide search because it
  // answers a different question: that one asks where a setting lives, this one narrows
  // a 200-row table you are already looking at, and typing into it must not navigate away.
  const [shortcutQuery, setShortcutQuery] = useState('')
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
  const completeVoiceCatalog=useMemo(()=>completeVoiceReference(voiceCommands,draft?.voice_commands||[]), [voiceCommands, draft?.voice_commands, uiLocale.value, catalogRevision.value])
  const [usageRefreshText, setUsageRefreshText] = useState<(() => string) | null>(null)
  const usageRefreshMessage = usageRefreshText?.() || ''
  const setUsageRefreshMessage = (render: () => string) => setUsageRefreshText(() => render)
  const [usageRefreshBusy, setUsageRefreshBusy] = useState(false)
  const [usageRefreshFailed, setUsageRefreshFailed] = useState(false)
  const [remote, setRemote] = useState<RemoteStatus | null>(null)
  const [mobileVoiceBusy,setMobileVoiceBusy]=useState(false)
  const [mobileVoiceMessage,setMobileVoiceMessage]=useState('')
  const [mobileVoiceFailed,setMobileVoiceFailed]=useState(false)
  const [firewall,setFirewall]=useState<FirewallStatus | null>(null)
  const [firewallBusy,setFirewallBusy]=useState(false)
  const [firewallMessage,setFirewallMessage]=useState('')
  const [wsl,setWsl]=useState<WslBridgeStatus | null>(null)
  // Which WSL action is in flight: a distro name for an install, 'firewall' for
  // the rule. One value rather than a flag per button, so two cannot run at once.
  const [wslBusy,setWslBusy]=useState('')
  const [wslProbing,setWslProbing]=useState(false)
  const [wslMessage,setWslMessage]=useState('')
  const [factoryResetOpen,setFactoryResetOpen]=useState(false)
  const [diagnosticsBusy,setDiagnosticsBusy]=useState(false)
  const [diagnosticsMessage,setDiagnosticsMessage]=useState('')
  const [diagnosticsText,setDiagnosticsText]=useState('')
  const [connectPhoneOpen,setConnectPhoneOpen]=useState(false)
  // The release update check. Read passively on mount - the daemon already holds
  // the answer, so opening Settings costs nothing; only the button reaches out.
  const [updateStatus,setUpdateStatus]=useState<UpdateStatus|null>(null)
  const [updateBusy,setUpdateBusy]=useState(false)
  const [updateError,setUpdateError]=useState('')
  // The same dialog the banner opens: it asks the daemon what installing would
  // do and shows that before the press. Settings is where someone who dismissed
  // the banner comes back to it.
  const [updateInstallOpen,setUpdateInstallOpen]=useState(false)
  useEffect(()=>{void fetchUpdateStatus().then(setUpdateStatus).catch(()=>{})},[])
  const updateSummary=updateStatusSummary(updateStatus)
  const updateChecked=lastCheckedLabel(updateStatus)
  const runningVersion=runningVersionLabel(updateStatus)
  const checkForUpdates=async()=>{
    if(updateBusy)return
    setUpdateBusy(true);setUpdateError('')
    try{setUpdateStatus(await requestUpdateCheck())}
    catch(cause){setUpdateError(cause instanceof Error?cause.message:String(cause))}
    finally{setUpdateBusy(false)}
  }
  // The frontend overlay. Read passively for the same reason the update check is:
  // the daemon already decided at start which tree it serves, so opening Settings
  // costs one small state read and never re-hashes anything.
  const [overlayStatus,setOverlayStatus]=useState<OverlayStatus|null>(null)
  const [overlayBusy,setOverlayBusy]=useState(false)
  const [overlayError,setOverlayError]=useState('')
  useEffect(()=>{void fetchOverlayStatus().then(setOverlayStatus).catch(()=>{})},[])
  const overlaySummary=overlayStatusSummary(overlayStatus)
  const overlayInstalled=installedAtLabel(overlayStatus)
  const overlayPress=async(action:()=>Promise<OverlayStatus>)=>{
    if(overlayBusy)return
    setOverlayBusy(true);setOverlayError('')
    try{setOverlayStatus(await action())}
    catch(cause){setOverlayError(cause instanceof Error?cause.message:String(cause))}
    finally{setOverlayBusy(false)}
  }
  const [prerequisites,setPrerequisites]=useState<Prerequisite[]|null>(null)
  const [prereqBusy,setPrereqBusy]=useState(false)
  // What the last re-scan actually did. Kept separate from the rows because "I
  // looked again and PATH really is unchanged" is a different answer from "I
  // looked again", and the difference is the whole reason the button is not just
  // a re-fetch: a daemon inherits its environment once, so a tool installed while
  // swe-mux was running is invisible until PATH is re-read from the OS.
  const [prereqRescan,setPrereqRescan]=useState<{path_refreshed:boolean;path_refresh_supported:boolean}|null>(null)
  const rescanPrerequisites=async():Promise<void>=>{
    if(prereqBusy)return
    setPrereqBusy(true)
    try{
      const result=await api<{prerequisites:Prerequisite[];path_refreshed:boolean;path_refresh_supported:boolean}>(
        'POST','/api/diagnostics/prerequisites/refresh')
      setPrerequisites(result.prerequisites)
      setPrereqRescan({path_refreshed:result.path_refreshed,path_refresh_supported:result.path_refresh_supported})
    }catch{setPrereqRescan(null)}
    finally{setPrereqBusy(false)}
  }
  // Placeholder for the assistant's new-project location: the parent directory most
  // of the registered projects already live in. A hint only - never written back.
  // Derived from the bundle's own `projects` part rather than a second
  // `/api/projects` call: the panel was fetching the same 25 KiB twice on every
  // open, once inside the bundle and once here.
  const [projectParentHint,setProjectParentHint]=useState('')
  const [savedBindings, setSavedBindings] = useState<BindingRule[]>([])

  /**
   * Take a fresh keybindings payload into every piece of state that reads it.
   *
   * One function rather than eight setters at three call sites (open, save, apply a
   * preset): the pieces are not independent - `rules` is what a save writes back and
   * `undeliverable` describes exactly those rules on exactly this host, so adopting
   * one without the other draws warnings about a keymap that is no longer there.
   */
  const adoptKeybindings = useCallback((data:KeybindingsResponse) => {
    setBindingRules(data.rules||[])
    setSavedBindings(data.rules||[])
    setBindingCommands(data.commands||[])
    // Field-by-field, not `data.policy||null`: the Input tab maps over every one
    // of these collections unconditionally, so a payload that carries a `policy`
    // object missing a field (an older daemon, a partial fixture) crashed the
    // whole tab rather than drawing an empty list. Same defaulting rule every
    // other adopter in this function already follows.
    setBindingPolicy(data.policy?{
      hosts:data.policy.hosts||[],
      platforms:data.policy.platforms||[],
      max_sequence:data.policy.max_sequence??3,
      browser_unreachable:data.policy.browser_unreachable||[],
      browser_contested:data.policy.browser_contested||{},
      wm_reserved:data.policy.wm_reserved||{},
      application_reserved:data.policy.application_reserved||[],
      terminal_reserved:data.policy.terminal_reserved||{},
      rules:data.policy.rules||[],
    }:null)
    setKeymapPresets(data.presets||[])
    setKeymapPreset(data.preset||'')
    setKeymapHost({host:data.host||'browser',platform:data.platform||'win'})
    setKeymapWarnings(data.undeliverable||[])
    setBindingError(Object.keys(data.rejected||{}).length
      ? t("Settings.c3554b7e29", { value1: Object.entries(data.rejected).map(([keys,message])=>`${displayChord(keys,data.platform)}: ${message}`).join(' · ') })
      : '')
  },[])
  const [status, setStatus] = useState('loading…')
  const [errors, setErrors] = useState<Record<string,string>>({})
  const [scanJob, setScanJob] = useState<HistoryScanJob|null>(null)
  const [activeTab,setActiveTab] = useState<SettingsTab>(()=>initialSection?tabForSection(initialSection):rememberedTab())
  // Keep the section drawer and profile labels synchronized with the shared policy.
  const [deviceEnvironment,setDeviceEnvironment] = useState(deviceMode)
  const narrow=deviceEnvironment.layout==='mobile'
  /**
   * The active tab's rendered sections, carrying the tab they were read from.
   *
   * They are read in an effect, so between the click that switches tabs and that
   * effect there is a render where this state still describes the tab just left. A bare
   * list cannot say so, and the sidebar drew it as the new tab's own sections for that
   * frame — visible now that it also draws sections for tabs that are not active.
   * Pairing them makes the mismatch derivable instead of invisible.
   */
  const [rail,setRail] = useState<{tab:SettingsTab;sections:SettingsRailSection[]}>(()=>({tab:activeTab,sections:NO_SECTIONS}))
  const railSections = rail.tab===activeTab?rail.sections:NO_SECTIONS
  const [activeSection,setActiveSection] = useState('')
  const [selectedSubpages,setSelectedSubpages] = useState<Record<string,string>>(()=>rememberedSections())
  /** Tabs whose section restoration already happened this mount; a deep link's
   *  page choice claims its tab here so the restore effect cannot override it. */
  const restoredFor = useRef('')
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
  const [sectionCue,setSectionCue] = useState<{tab:SettingsTab;id:string;token:number}|null>(null)
  const [themePickerOpen,setThemePickerOpen] = useState(false)
  const declaredSubpages=settingsSubpages[activeTab]||[]
  const pagedSubpages=declaredSubpages.length>1
  const selectedSubpage=selectedSubpages[activeTab]||declaredSubpages[0]?.id||''
  const panel = useRef<HTMLElement>(null)
  const tabNavRef = useRef<HTMLElement>(null)
  const searchInput = useRef<HTMLInputElement>(null)
  const searchIndex = useRef<{source:Config|null;entries:SettingsSearchEntry[]}|null>(null)
  /**
   * The section list of every unpaged tab, so the sidebar can disclose one before that
   * tab has mounted. Built once per open from the tabs' vnodes and overwritten per tab
   * by the live read on arrival, which is the only thing that can see a child
   * component's headings.
   *
   * Not rebuilt per render, and deliberately not keyed on the draft the way the search
   * index is: `change()` replaces the draft on every keystroke, and rebuilding thirteen
   * vnode trees per keypress is the cost that index exists to avoid paying. What that
   * gives up is a heading whose *rendering is conditional* on an edit made in this
   * session — there are four, all of them on paged tabs or on a tab that has six other
   * headings, so none can change whether a chevron is drawn, and visiting the tab
   * corrects the list either way.
   */
  const railPreview = useRef<Map<SettingsTab,SettingsRailSection[]>|null>(null)
  /** A section chosen on a tab that was not on screen, scrolled to once its rail exists. */
  const pendingSection = useRef('')
  const wasSearching = useRef(false)
  const localizationCache = useRef('')
  const confirmPanel = useRef<HTMLElement>(null)
  const resetPanel = useRef<HTMLElement>(null)
  const themeFile = useRef<HTMLInputElement>(null)
  const restoreFocus = useRef<HTMLElement | null>(document.activeElement as HTMLElement | null)

  const selectPageForSetting=(root:ParentNode,setting:string)=>{
    if(!pagedSubpages)return
    const page=root.querySelector<HTMLElement>(settingSelector(setting))?.closest<HTMLElement>('section[data-settings-subpage]')?.dataset.settingsSubpage
    if(!page)return
    setSelectedSubpages(current=>({...current,[activeTab]:page}))
    // This choice IS this visit's section restoration. The restore effect below
    // fires later, once the rail has been read out of the DOM, and without this
    // it overwrote the deep link's page with the remembered one - the control
    // flashed on the right page and was then hidden under the wrong one.
    restoredFor.current=activeTab
  }

  // The experience tier applies through its own endpoint (the key sets are
  // policy, computed in the daemon), then adopts the returned config wholesale:
  // a tier press is a deliberate batch reset of its own keys, so the draft is
  // refreshed rather than left holding values the daemon just rewrote.
  const [tierChoice, setTierChoice] = useState<''|'terminal'|'deterministic'|'automations'>('')
  const [tierStatus, setTierStatus] = useState('')
  const applyExperienceTier = async (): Promise<void> => {
    if (!tierChoice) return
    setTierStatus('')
    try {
      const next = await api<Config & {restart_required: string[]}>(
        'POST', '/api/experience-tier', {tier: tierChoice},
      )
      adoptConfig(next)
      setTierStatus(next.restart_required.length
        ? t("Settings.8621e92324")
        : t("Settings.954b4955ec"))
    } catch (cause) {
      setTierStatus(t("Settings.7fa716b353", { value1: cause instanceof Error ? cause.message : String(cause) }))
      if(['provider_required','models_required','model_verification_required'].includes(String((cause as {detail?:{code?:string}}).detail?.code)))window.dispatchEvent(new Event('mux:setup-provider'))
    }
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
    applyLanguagePreference(next.ui_language)
    setTierChoice(next.experience_tier)
    setHarnessArgs(Object.fromEntries(Object.entries(next.harness_args).map(([name,args])=>[name,formatCommandLine(args)])))
    configureCustomTheme(next.custom_theme)
    applyTheme(next.theme)
    applyNoteEditorConfig(next)
    onUiScalePreview(next)
    applyRailDensity(next)
  },[onUiScalePreview])

  useEffect(() => {
    // Re-read on open: the player strip and the command palette also flip it.
    setDeviceAutoplay(autoplayEnabled())
    // One bundled request instead of nine — on a high-RTT client (phone over
    // Tailscale) per-request connection setup dominated the panel's open delay.
    //
    // Since 2026-08-30 it carries only what the *first paint* needs, and every
    // other fetch this effect used to fire has moved to the tab that reads it
    // (`loadTabData` below). Measured before that split, against the development
    // host: the panel blocked on 518 KiB, of which `usage` was 319.8 KiB and
    // `provider` 177.3 KiB — two tabs the operator usually has not opened —
    // while the config it renders from is 12.7 KiB. Alongside it went a
    // `/api/remote/firewall` call that takes 809ms and gates nothing at all.
    api<SettingsBundle>('GET',`/api/settings/bundle?${hostQuery()}`).then(bundle => {
      const { config: next, keybindings: keyData } = bundle
      // Saving unconditionally PUTs keybindings back; rendering without them
      // would let a Save overwrite their file with empty defaults. (The rules
      // file left this transaction: the Automation dashboard owns its editor.)
      if (!keyData) {
        setStatus(bundle.errors.keybindings?t("Settings.10cc0c58dc", { value1: bundle.errors.keybindings }):t("Settings.3bd15ed78f"))
        return
      }
      adoptConfig(next)
      adoptKeybindings(keyData)
      setStatus(t("Settings.b24d6d3373"))
      // Both halves of the profile payload are kept. `detected` seeds "restore
      // detected"; `profiles` carries the daemon's *derived* capabilities, which
      // /api/config does not (it returns the stored list, which is now vestigial).
      if(bundle.projects)setProjectParentHint(commonestParent(bundle.projects.map(row=>row.root||'')))
    }).catch(error => setStatus(error.message))
  }, [])

  // Everything a *specific* tab reads, fetched when that tab is first opened and
  // never again while the panel stays open. Each entry is one tab's whole data
  // dependency, which is what keeps this honest: a control that needs a fetch
  // has to declare which tab pays for it, rather than quietly joining the
  // open-the-panel burst.
  //
  // The costs that drove the move, measured 2026-08-30 on loopback (a phone over
  // Tailscale pays RTT on top of every one of these):
  //   /api/remote/firewall          809ms
  //   /api/settings/bundle?usage    319.8 KiB
  //   /api/settings/bundle?provider 177.3 KiB
  //   /api/wsl/bridge                85ms
  //   /api/voice                     85.6 KiB
  const fetchedTabs = useRef<Set<string>>(new Set())
  useEffect(() => {
    const first = (key:string):boolean => {
      if(fetchedTabs.current.has(key))return false
      fetchedTabs.current.add(key)
      return true
    }
    if(activeTab==='remote'&&first('remote')){
      api<RemoteStatus>('GET','/api/remote/status').then(setRemote).catch(()=>setRemote(null))
      api<FirewallStatus>('GET','/api/remote/firewall').then(setFirewall).catch(()=>setFirewall(null))
      // Without `probe`, so opening this tab never starts a stopped distribution.
      api<WslBridgeStatus>('GET','/api/wsl/bridge').then(setWsl).catch(()=>setWsl(null))
    }
    if(activeTab==='voice'&&first('voice')){
      api<VoiceStatusInfo>('GET','/api/voice').then(setVoiceInfo).catch(()=>setVoiceInfo(null))
      api<LatencyReportPayload>('GET','/api/voice/stt-latency').then(setLatencyReport).catch(()=>setLatencyReport(null))
    }
    if(activeTab==='maintenance'&&first('maintenance'))
      api<{prerequisites:Prerequisite[]}>('GET','/api/diagnostics/prerequisites')
        .then(p=>setPrerequisites(p.prerequisites)).catch(()=>setPrerequisites(null))
    if(activeTab==='terminals'&&first('terminals'))
      api<SettingsBundle>('GET','/api/settings/bundle?parts=profiles')
        .then(bundle=>{if(bundle.profiles){setDetectedProfiles(bundle.profiles.detected);setSavedProfiles(bundle.profiles.profiles)}}).catch(()=>{})
    if(activeTab==='automation'&&first('automation'))
      api<SettingsBundle>('GET','/api/settings/bundle?parts=automation')
        .then(bundle=>setAutomation(bundle.automation??null)).catch(()=>{})
    if(activeTab==='usage'&&first('usage'))
      api<SettingsBundle>('GET','/api/settings/bundle?parts=usage')
        .then(bundle=>setUsage(bundle.usage??null)).catch(()=>{})
    if(activeTab==='accounts'&&first('accounts'))
      api<SettingsBundle>('GET','/api/settings/bundle?parts=provider')
        .then(bundle=>setProvider(bundle.provider??null)).catch(()=>{})
  }, [activeTab])

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
    setActiveSection('')
    if(!draft)return
    const content=contentEl()
    if(!content)return
    let frame=0
    const read=()=>{
      frame=0
      // The observer fires on the commit that swapped tabs, and its callback is a
      // microtask while this effect's cleanup is not — so a read scheduled by the
      // outgoing tab can run against the incoming tab's DOM and report its headings
      // under the wrong tab. That used to self-correct one render later; it stopped
      // being harmless once the sidebar kept the answer.
      if(content.dataset.settingsTab!==activeTab)return
      const headings=[...content.querySelectorAll<HTMLElement>('h3')]
      const sections=pagedSubpages?declaredSubpages:railSectionIds(headings.map(heading=>heading.textContent||''),headings.map(heading=>heading.dataset.headingMessage?sourceText(heading.dataset.headingMessage):heading.textContent||''))
      let at=0
      const claimed=new Set<HTMLElement>()
      for(const heading of headings){
        const label=(heading.textContent||'').trim()
        if(!label)continue
        const id=pagedSubpages?settingsSubpageId(activeTab,heading.dataset.headingMessage?sourceText(heading.dataset.headingMessage):label):(sections[at]?.id||'')
        heading.dataset.settingsSection=id
        const owner=heading.closest<HTMLElement>('section')
        // First heading wins. A section is named by the heading that opens it;
        // anything below is a sub-topic of the same page. Letting each heading
        // overwrite meant a section with three of them took the *last* one's
        // slug, which is how the shortcuts page came to be labelled
        // `what-this-browser-gives-the-app` and matched no declared page.
        if(owner&&id&&!claimed.has(owner)){owner.dataset.settingsSubpage=id;claimed.add(owner)}
        at+=1
      }
      // The live read is authoritative and replaces the vnode preview for this tab:
      // it is the only one that can see what a child component rendered.
      if(!pagedSubpages)railPreview.current?.set(activeTab,sections)
      setRail(current=>current.tab===activeTab&&sameRailSections(current.sections,sections)?current:{tab:activeTab,sections})
    }
    read()
    const observer=new MutationObserver(()=>{ if(!frame)frame=requestAnimationFrame(read) })
    observer.observe(content,{childList:true,subtree:true,characterData:true})
    return()=>{observer.disconnect();if(frame)window.cancelAnimationFrame(frame)}
  },[activeTab,draft!==null,pagedSubpages,declaredSubpages,uiLocale.value,catalogRevision.value])

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
  // A section picked in the sidebar for a tab that was not on screen outranks the
  // remembered one for the same reason — it is the reason this tab was opened.
  // It may name a section the live read does not have (the preview it was drawn from
  // cannot see a child component's headings), which lands on the tab and scrolls
  // nowhere rather than guessing at a neighbour.
  useEffect(()=>{
    if(!railSections.length||restoredFor.current===activeTab)return
    restoredFor.current=activeTab
    const requested=pendingSection.current
    pendingSection.current=''
    if(jump)return
    const remembered=requested||rememberedSections()[activeTab]
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

  useEffect(()=>watchDeviceMode(setDeviceEnvironment),[])

  const setNavOpen=useCallback((open:boolean)=>onNavOpenChange?.(open),[onNavOpenChange])
  // Switching to desktop layout turns the drawer back into a docked column, and a
  // column that is permanently on screen must not leave a dismiss level standing —
  // back would then swallow a press doing nothing visible.
  useEffect(()=>{if(!narrow&&navOpen)setNavOpen(false)},[narrow,navOpen,setNavOpen])
  /** Every route to a different tab, so none of them leaves the drawer standing open
   *  over the tab it just switched to: the rail, a search result, a deep link. */
  const selectTab=useCallback((tab:SettingsTab)=>{setActiveTab(tab);setNavOpen(false)},[setNavOpen])
  const requestSectionCue=(tab:SettingsTab,id:string)=>
    setSectionCue(current=>({tab,id,token:(current?.token||0)+1}))
  const toggleTabExpanded=(tab:SettingsTab)=>setExpandedTabs(current=>{
    const next=new Set(current)
    if(next.has(tab))next.delete(tab);else next.add(tab)
    return next
  })
  const selectSubpage=(tab:SettingsTab,id:string)=>{
    requestSectionCue(tab,id)
    setSelectedSubpages(current=>({...current,[tab]:id}))
    setExpandedTabs(current=>new Set(current).add(tab))
    selectTab(tab)
  }
  /**
   * The scroll-anchor half of the same control, for an unpaged tab. A section on the
   * tab already on screen scrolls to it; one on any other tab selects that tab first,
   * because the heading it names does not exist in the DOM until then.
   */
  const openSection=(tab:SettingsTab,id:string)=>{
    requestSectionCue(tab,id)
    if(tab===activeTab){scrollToSection(id);setNavOpen(false);return}
    pendingSection.current=id
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
    ||!sameDraftValue(bindingRules,savedBindings)
  )), [config, draft, harnessArgs, bindingRules, savedBindings, uiLocale.value, catalogRevision.value])

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
    setMobileVoiceFailed(false);setMobileVoiceBusy(true);setMobileVoiceMessage(t("Settings.34862d91a2"))
    try{
      const result=await enableMobileVoice()
      if(!result.url){setMobileVoiceFailed(true);setMobileVoiceMessage(t("Settings.e748f0ba4a"));return}
      setMobileVoiceMessage(result.diagnostic)
      setRemote(current=>current&&{...current,mobile_voice_configured:true,mobile_voice_url:result.url,mobile_voice_https_port:result.https_port||443,serve_configured:false,serve_url:null,diagnostic:result.diagnostic})
    }catch(cause){setMobileVoiceFailed(true);setMobileVoiceMessage(cause instanceof Error?cause.message:String(cause))}
    finally{setMobileVoiceBusy(false)}
  }

  const repairFirewall=async()=>{
    if(firewallBusy)return
    setFirewallBusy(true);setFirewallMessage(t("Settings.39082c27d5"))
    try{
      // Raw fetch (not the api helper) so the explicit user-gesture header the
      // daemon requires before triggering a UAC prompt is sent.
      const response=await fetch('/api/remote/firewall/repair',{method:'POST',headers:{'Content-Type':'application/json','X-Mux-User-Gesture':'firewall-repair'}})
      const result=await response.json().catch(()=>({}))
      if(response.ok&&result.ok){
        setFirewallMessage(t("Settings.5edc7c8f56"))
        setFirewall(await api<FirewallStatus>('GET','/api/remote/firewall'))
        setFirewallMessage(t("Settings.d22a72ac6f"))
      }else{
        setFirewallMessage(result.reason==='cancelled'?t("Settings.4c10b44589"):result.reason==='unsupported'?t("Settings.3cffd0ae81"):t("Settings.81c939c069"))
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
    setWslProbing(true);setWslMessage(t("Settings.76781be616"))
    try{await refreshWsl(true);setWslMessage('')}
    finally{setWslProbing(false)}
  }

  const toggleWsl=async(enabled:boolean)=>{
    // Written straight through rather than into the settings draft: this is a
    // one-switch decision with its own explanation, and burying it in a Save
    // button would leave the panel's other actions acting on a state the daemon
    // does not have yet.
    setWslMessage(enabled?t("Settings.e22c24238e"):t("Settings.fb7bdec372"))
    try{
      // `PATCH /api/config` with a flat field map. It was `PUT` with a `{values:{…}}`
      // wrapper, which is neither the method the daemon routes nor the body it parses,
      // so this switch answered 405 every time it was pressed.
      await api('PATCH','/api/config',{wsl_bridge_enabled:enabled})
      await refreshWsl(false)
      setWslMessage(enabled?t("Settings.830c380805"):t("Settings.fe05fa4611"))
    }catch(cause){setWslMessage(cause instanceof Error?cause.message:String(cause))}
  }

  const installWslBridge=async(distro:string)=>{
    if(wslBusy)return
    setWslBusy(distro);setWslMessage(t("Settings.e5a1b0bbf9", { value1: distro }))
    try{
      // Raw fetch for the user-gesture header the daemon requires before it will
      // write into a distribution.
      const response=await fetch('/api/wsl/bridge/install',{method:'POST',headers:{'Content-Type':'application/json','X-Mux-User-Gesture':'wsl-bridge-install'},body:JSON.stringify({distro})})
      const result=await response.json().catch(()=>({}))
      if(response.ok&&result.ok){await refreshWsl(true);setWslMessage(t("Settings.bd2463d48f", { value1: distro }))}
      else setWslMessage(result.reason||t("Settings.3954429732", { value1: distro }))
    }catch(cause){setWslMessage(cause instanceof Error?cause.message:String(cause))}
    finally{setWslBusy('')}
  }

  const repairWslFirewall=async()=>{
    if(wslBusy)return
    setWslBusy('firewall');setWslMessage(t("Settings.b669af169d"))
    try{
      const response=await fetch('/api/wsl/bridge/firewall/repair',{method:'POST',headers:{'Content-Type':'application/json','X-Mux-User-Gesture':'wsl-firewall-repair'}})
      const result=await response.json().catch(()=>({}))
      if(response.ok&&result.ok){await refreshWsl(true);setWslMessage(t("Settings.e491550b52"))}
      else setWslMessage(result.reason==='cancelled'?t("Settings.4c10b44589"):result.reason==='no_wsl_adapter'?t("Settings.b46df5474d"):result.reason==='unsupported'?t("Settings.1a459add2c"):t("Settings.4ac6454068"))
    }catch(cause){setWslMessage(cause instanceof Error?cause.message:String(cause))}
    finally{setWslBusy('')}
  }

  const exportDiagnostics=async()=>{
    if(diagnosticsBusy)return
    setDiagnosticsBusy(true);setDiagnosticsMessage(t("Settings.f2464951b4"))
    try{
      const bundle=await api<unknown>('GET','/api/diagnostics/export')
      const text=JSON.stringify(bundle,null,2)
      setDiagnosticsText(text)
      // Clipboard write needs a secure context; over plain-HTTP tailnet it can
      // reject, so the copy failure falls back to the textarea below.
      try{await navigator.clipboard.writeText(text);setDiagnosticsMessage(t("Settings.764ad7529b"))}
      catch{setDiagnosticsMessage(t("Settings.9b24d678be"))}
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
  },[activeTab,draft!==null,uiLocale.value,catalogRevision.value])

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
      const clearTarget=flashSetting(target)
      const clearSection=flashSettingsSection(target)
      return()=>{clearTarget();clearSection()}
    }
    const page=target.closest<HTMLElement>('section[data-settings-subpage]')?.dataset.settingsSubpage
    if(pagedSubpages&&page&&page!==selectedSubpage){
      setSelectedSubpages(current=>({...current,[activeTab]:page}))
      const timer=window.setTimeout(arrive,0)
      return()=>window.clearTimeout(timer)
    }
    return arrive()
  },[jump,pagedSubpages,selectedSubpage,activeTab])

  // Sidebar pages and scroll anchors get the same contextual arrival cue as search.
  // The target can belong to a tab that has not rendered yet, or to a page that is still
  // hidden during this commit, so the cue waits for the first visible matching heading.
  useEffect(() => {
    if(!sectionCue||sectionCue.tab!==activeTab)return
    const root=panel.current
    if(!root)return
    return cueSettingsSection(root,sectionCue.id)
  },[sectionCue,activeTab,draft!==null])

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
      if(event.key==='Escape'){setCapturingCommand(null);setCaptureSequence([]);setBindingError('');return}
      if(event.repeat||['Control','Shift','Alt','Meta'].includes(event.key))return
      void captureBinding(event,capturingCommand)
    }
    window.addEventListener('keydown',capture,true)
    return()=>window.removeEventListener('keydown',capture,true)
  },[capturingCommand,captureSequence,bindingRules,bindingCommands])

  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      // A field with an unsaved edit that Escape reverts (a saved account's name) marks
      // itself, because this capture-phase listener would otherwise close the panel
      // before the field saw the key. It stops the key's propagation itself.
      const ownsEscape=event.target instanceof Element&&!!event.target.closest('[data-owns-escape]')
      if (event.key === 'Escape'&&!capturingCommand&&!ownsEscape) {
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

  /**
   * Record one chord, or extend a sequence that is already being recorded.
   *
   * A sequence is recorded the way it is typed. The first keystroke that lands on a
   * chord which already *begins* a longer binding keeps the recorder open rather
   * than committing, because committing there would silently delete every binding
   * under that prefix - press Ctrl+Shift+Space meaning "the leader, then p" and the
   * whole leader tree would be gone. Anything else commits immediately, which is
   * every ordinary single-chord case.
   */
  async function captureBinding(event:KeyboardEvent,commandId:string) {
    const chord=keyChord(event)
    if(!chord){setBindingError(t("Settings.a4eca39cae"));return}
    const sequence=[...captureSequence,chord]
    const keys=sequence.join(' ')
    const continues=bindingRules.some(rule=>rule.keys!==keys&&rule.keys.startsWith(`${keys} `))
    if(continues&&sequence.length<(bindingPolicy?.max_sequence??3)){
      setCaptureSequence(sequence)
      setBindingError(t("Settings.29f1e32ee1", { value1: displayChord(keys,keymapHost.platform) }))
      return
    }
    const conflict=bindingRules.find(rule=>rule.keys===keys&&rule.command!==commandId&&!rule.host&&!rule.platform)
    if(conflict){
      const label=bindingCommands.find(command=>command.id===conflict.command)?.label||conflict.command
      setBindingError(t("Settings.d3057c783d", { value1: displayChord(keys,keymapHost.platform), value2: label }))
      return
    }
    // The rule keeps this host's scope only when the recording device is the one it
    // will not work on anywhere else. A chord recorded in the desktop app that a
    // browser cannot receive is saved as desktop-only rather than saved as broken.
    const next:BindingRule[]=[
      ...bindingRules.filter(rule=>rule.command!==commandId&&rule.keys!==keys),
      {keys,command:commandId},
    ]
    try {
      await api('PUT','/api/keybindings?validate=1',{rules:next})
      setBindingRules(next);setCapturingCommand(null);setCaptureSequence([]);setBindingError('')
    } catch(error) {
      const typed=error as Error&{fields?:Record<string,string>}
      setBindingError(typed.fields?.[keys]||typed.message)
    }
  }

  // One pass over the rules instead of a scan per row: the shortcut table asks this
  // for all 200 commands on every render, and each answer used to walk the whole list.
  // First rule wins, which is what the scan it replaces returned.
  const commandChords=useMemo(()=>{
    const chords=new Map<string,string>()
    for(const rule of bindingRules)if(rule.command&&!chords.has(rule.command))chords.set(rule.command,rule.keys)
    return chords
  }, [bindingRules, uiLocale.value, catalogRevision.value])
  const bindingForCommand=(commandId:string)=>commandChords.get(commandId)

  // Which shortcut rows the filter leaves showing. Rows are *hidden*, never dropped
  // from the tree, so filtering never re-mounts them. The panel-wide search does not
  // index these rows at all (`data-settings-search="skip"`); it counts matches with the
  // same predicate and hands the query to this filter.
  const visibleShortcuts=useMemo(()=>new Set(bindingCommands
    .filter(command=>shortcutMatches(command,commandChords.get(command.id),shortcutQuery,keymapHost.platform))
    .map(command=>command.id)), [bindingCommands, commandChords, shortcutQuery, keymapHost.platform, uiLocale.value, catalogRevision.value])
  const clearBinding=(commandId:string)=>{
    setBindingRules(current=>current.filter(rule=>rule.command!==commandId))
    if(capturingCommand===commandId){setCapturingCommand(null);setCaptureSequence([])}
    setBindingError('')
  }

  /**
   * Replace the whole document from one shipped preset.
   *
   * Outside the draft/Save cycle on purpose, exactly like "restore defaults": the
   * assignment is absolute rather than a delta, so it overwrites hand-edited
   * bindings, and a change that large should not be something a user discovers they
   * made when they pressed Save for an unrelated reason. It applies on an explicit
   * press, after a confirmation that names what the preset takes.
   */
  const applyPreset=async(preset:KeymapPresetSummary)=>{
    setPresetApplying(preset.id);setPresetIntent(null)
    try {
      const result=await api<{keybindings:KeybindingsResponse}>('POST',`/api/keymap-preset?${hostQuery()}`,{preset:preset.id})
      adoptKeybindings(result.keybindings)
      setBindingError('')
    } catch(error) {
      setBindingError(error instanceof Error?error.message:String(error))
    } finally { setPresetApplying('') }
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
      const result = await api<SettingsApplyResult>('POST',`/api/settings/apply?${hostQuery()}`,{
        _revision: config.revision,
        config: changes,
        // `custom` once the rules have been hand-edited: the preset id records what
        // they were derived from, and a hand edit means they no longer are.
        keybindings: {preset: sameDraftValue(bindingRules,savedBindings) ? keymapPreset : 'custom', rules: bindingRules},
      })
      const next = result.config
      adoptConfig(next); setErrors({})
      if (result.keybindings) adoptKeybindings(result.keybindings)
      else setSavedBindings(bindingRules)
      setStatus(next.restart_required.length ? t("Settings.552eb2b36b", { value1: next.restart_required.join(', ') }) : t("Settings.3a6c0e6211"))
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
      setStatus(t("Settings.cc441de714"))
    } catch (error) {
      // A failed reset used to be an unhandled rejection: the button reported nothing,
      // and the panel went on showing a draft that no longer matched the daemon.
      const typed = error as ApiError
      setErrors(typed.fields || {settings:typed.message})
      setStatus(t("Settings.4b70d8d894", { message: typed.message }))
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
    } catch { setErrors({get custom_theme() { return t("Settings.c53fae7495") }}) }
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
      ? {id:'shell',get label() { return t("Settings.8a926ed734") },executable:'',args:[],env:{},platforms:['windows'],cwd_strategy:'native',marker:'sh',capabilities:['interactive'],cwd_integration:false,enabled:true,backend:'shell'}
      // An agent profile leaves `executable` empty so it inherits `harness_exe`, and
      // carries no shell capabilities: it contributes arguments, not a command line.
      // `platforms` is empty rather than ['windows'] because nothing about an
      // argument list is platform-specific, and a stray default would make the
      // profile unavailable on a host it would have worked on.
      : {id:backend,get label() { return t("Settings.cc0601a4a7", { value1: harnessDescriptor(backend)?.display_name||backend }) },executable:'',args:[],env:{},platforms:[],cwd_strategy:'native',marker:'ag',capabilities:[],cwd_integration:false,enabled:true,backend})
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
  // The MCP, CLI, and instrumentation toggles are per-harness dicts where an absent
  // key means on. Writing false records an override; setting back to true clears it
  // so the stored map stays minimal, matching harness_enabled's three-state shape.
  const harnessDictOn = (fieldKey:'harness_mcp_enabled'|'harness_cli_enabled'|'harness_instrument_enabled', name:string):boolean =>
    draft![fieldKey]?.[name] ?? true
  const setHarnessDict = (fieldKey:'harness_mcp_enabled'|'harness_cli_enabled'|'harness_instrument_enabled', name:string, on:boolean):void => {
    const next={...(draft![fieldKey]||{})}
    if(on) delete next[name]; else next[name]=false
    change(fieldKey,next)
  }
  // The skill map has the opposite default (absent key = OFF), because its
  // non-Claude half writes into the user's checkout at spawn - a write nobody
  // asked for must not be a default. The stored map stays minimal the same way:
  // only explicit ON entries are kept.
  const harnessSkillOn = (name:string):boolean => draft!.harness_skill_enabled?.[name] ?? false
  const setHarnessSkill = (name:string, on:boolean):void => {
    const next={...(draft!.harness_skill_enabled||{})}
    if(on) next[name]=true; else delete next[name]
    change('harness_skill_enabled',next)
  }
  // An empty box clears the override rather than storing "", so the stored map
  // only ever holds answers the user actually gave and a cleared one falls all
  // the way back to ordinary detection.
  const setToolPath = (id:string, value:string):void => {
    const next={...(draft!.tool_paths||{})}
    if(value.trim()) next[id]=value.trim(); else delete next[id]
    change('tool_paths',next)
  }
  const startHistoryScan = async ():Promise<void> => {
    try { setScanJob((await api<{job:HistoryScanJob}>('POST','/api/history/scan')).job) }
    catch(cause){ setStatus(t("Settings.165390f773", { value1: cause instanceof Error?cause.message:String(cause) })) }
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
    setUsageRefreshBusy(true)
    setUsageRefreshFailed(false)
    setUsageRefreshMessage(() => t("Settings.c42ff1d4f4"))
    setErrors(current=>{const next={...current};delete next.ccusage;return next})
    try {
      // The daemon answers at once and runs the contained scan in the background;
      // follow it until it ends rather than holding a request open for minutes.
      const next=await runUsageRefresh(
        ()=>api<UsageStatus>('POST','/api/usage/refresh',{mode:'auto'}),
        ()=>api<UsageStatus>('GET','/api/usage'),
        status=>{
          setUsage(status)
          const activity=describeUsageActivity(status.collector,Date.now()/1000)
          if(status.refreshing)setUsageRefreshMessage(() => t("Settings.4268b76361", { value1: activity??'' }).trim())
        },
      )
      if(next.collector.error)throw new Error(next.collector.error)
      setUsageRefreshMessage(() => t("Settings.505493b917", { value1: next.collector.id, value2: codeLabel(next.collector.status), value3: describeLastRun(next.collector.last_run)?` · ${describeLastRun(next.collector.last_run)}`:'' }))
    } catch (error) {
      const message=(error as Error).message
      setUsageRefreshMessage(() => t("Settings.bab4005814", { value1: message }))
      setUsageRefreshFailed(true)
      setErrors(current=>({...current,ccusage:message}))
    } finally {
      setUsageRefreshBusy(false)
    }
  }
  const clearUsage = async () => {
    try { setUsage(await api<UsageStatus>('DELETE','/api/usage/cache')) }
    catch (error) { setUsageRefreshFailed(true); setUsageRefreshMessage(() => t("Settings.a54468e4c9", { value1: (error as Error).message })) }
  }
  const providerKeyAction=async(operation:'test'|'set'|'clear',target:'openrouter'|'custom'='openrouter')=>{
    const key=target==='custom'?customKey:openRouterKey
    const label=target==='custom'?'Endpoint':'OpenRouter'
    setProviderMessage(operation==='clear'?t("Settings.ae46205194"):operation==='set'?t("Settings.e74bd3fc97"):t("Settings.cb874cc434"))
    try{
      const result=await providerKeyOperation(operation,target,key)
      // The whole payload is refetched rather than patched in: storing or clearing a key
      // drops the endpoint's verification (the key is part of its fingerprint), so the
      // verified badge and the readiness sentence both change with the key status and
      // patching only `secret` would leave a stale "verified" beside a new credential.
      forgetLlmProvider()
      await reloadProviderStatus()
      if(target==='custom')setCustomKey('');else if(operation==='set'||operation==='clear')setOpenRouterKey('')
      setProviderMessage(operation==='clear'?t("Settings.353dbe46b0", { value1: label }):operation==='set'?t("Settings.7b7ebb8dc4", { value1: label }):t("Settings.c5c2a4482c", { value1: label }))
      void result
    }catch(error){setProviderMessage(t("Settings.90b756f119", { value1: label, value2: (error as Error).message }))}
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
    setProviderMessage(t("Settings.257a35d3de"))
    try{const models=await api<ProviderStatus['models']>('POST','/api/automation/provider/models/refresh',{});setProvider(current=>current?{...current,models}:current);setProviderMessage(t("Settings.f3b44d4b91", { value1: models.models.length }))}catch(error){setProviderMessage(t("Settings.48282261e1", { value1: (error as Error).message }))}
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
    <span>{narrow ? t("Settings.02f6ea1281") : t("Settings.0590b57a08")}</span>
    {narrow ? <h2><button type="button" class="settings-heading-trigger" aria-expanded={navOpen} aria-controls="settings-tab-nav" onClick={toggleNav}>{activeTabLabel}<i aria-hidden="true">▾</i></button></h2> : <h2 data-heading-message="Settings.74a883a037">{t("Settings.74a883a037")}</h2>}
  </div>
  const navTrigger = narrow
    ? <button type="button" class="settings-nav-trigger" aria-label={t("Settings.e26d51d367")} aria-expanded={navOpen} aria-controls="settings-tab-nav" onClick={toggleNav}>
        <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" aria-hidden="true"><line x1="2.5" y1="4" x2="13.5" y2="4"/><line x1="2.5" y1="8" x2="13.5" y2="8"/><line x1="2.5" y1="12" x2="13.5" y2="12"/></svg>
      </button>
    : null
  // Grouped from one array in both layouts. The wrapper is `display:contents` so its
  // buttons stay direct flex children of the nav, and `role=presentation` because a
  // tablist admits only tabs — the heading is a visual affordance.
  // Narrow, the same list slides in over the content instead of docking beside it;
  // closed it is `visibility:hidden` rather than `aria-hidden`, which is what keeps its
  // buttons out of the focus order without hiding elements that are still focusable.
  const renderTabNav = (previews:Map<SettingsTab,SettingsRailSection[]>) => <Fragment>
    <nav ref={tabNavRef} id="settings-tab-nav" class={`settings-tabs${narrow?' settings-tabs-drawer':''}${narrow&&navOpen?' open':''}`} role="tablist" aria-label={t("Settings.e26d51d367")}>
      {settingsTabGroups().map(group=><div class="settings-tab-group" role="presentation" key={group.group}>
        <span aria-hidden="true">{group.group}</span>
        {group.tabs.map(tab=>{
          // A paged tab lists its declared pages. An unpaged tab lists the sections it
          // renders, as scroll anchors. Those come from the live DOM while it is the
          // active tab and from its vnodes otherwise, so a tab discloses the same
          // sections whether or not it has been opened — reading only the DOM is what
          // made the chevron appear on the second visit and not the first, and made
          // the sidebar describe two kinds of tab that are not different.
          const pages=settingsSubpages[tab.id]||[]
          const live=tab.id===activeTab?railSections:[]
          // The live read starts empty on every tab switch, so the preview also covers
          // the frame between arriving and reading, where the chevron would blink out.
          const rendered=pages.length?[]:(live.length?live:previews.get(tab.id)||[])
          const sections=rendered.length>=SECTION_RAIL_MIN?rendered:[]
          const entries=pages.length?pages:sections
          const expanded=expandedTabs.has(tab.id)&&entries.length>0
          return <Fragment key={tab.id}>
            <div class="settings-tab-row" role="presentation">
              {/* `data-settings-tab` is the tab's id in the DOM. The only other handle on
                  one is its visible label, which is copy: a renamed tab silently breaks
                  whatever selected on it, and the demo drives this nav to show the keymap
                  presets. */}
              <button role="tab" data-settings-tab={tab.id} aria-selected={activeTab===tab.id} class={activeTab===tab.id?'active':''} onClick={()=>selectTab(tab.id)}>{tab.label}</button>
              {!!entries.length && <button type="button" data-settings-tab-expand={tab.id} class="settings-tab-expand" aria-label={t("Settings.9a6d5164b0", { value1: (expanded) ? 'yes' : 'no', label: tab.label })} aria-expanded={expanded} onClick={()=>toggleTabExpanded(tab.id)}>{expanded ? '▾' : '▸'}</button>}
            </div>
            {expanded && <div class="settings-subtabs" role="group" aria-label={t("Settings.05e9eb0145", { label: tab.label })}>
              {pages.length ? pages.map(page=><button key={page.id} type="button" data-settings-subpage-nav={page.id} class={activeTab===tab.id&&selectedSubpage===page.id?'active':''} onClick={()=>selectSubpage(tab.id,page.id)}>{page.label}</button>) : sections.map(section=><button key={section.id} type="button" class={tab.id===activeTab&&activeSection===section.id?'active':''} onClick={()=>openSection(tab.id,section.id)}>{section.label}</button>)}
            </div>}
          </Fragment>
        })}
      </div>)}
    </nav>
    {narrow&&navOpen && <button type="button" class="settings-nav-scrim" aria-label={t("Settings.304028d971")} onClick={()=>setNavOpen(false)} />}
  </Fragment>
  /** Nothing is previewable before the config lands: no tab can be built without it. */
  const NO_PREVIEWS:Map<SettingsTab,SettingsRailSection[]> = new Map()
  // Before the bundle lands the panel renders its full chrome — header, section
  // list, footer — with a placeholder in the content area, so opening Settings
  // paints immediately and the chosen tab can be selected while data loads.
  {/* Disabled twin of the real search box: the index needs the config, and a control
      that appears late would shift the chrome out from under a tap. Narrow it lives in
      the header; wide it sits above the section list, at the top of the nav column. */}
  const loadingSearch = <div class="settings-search"><input type="search" disabled placeholder={t("Settings.57054c5a04")} aria-label={t("Settings.9c927bd29c")} /></div>
  if (!draft) return <div class="settings-layer" onMouseDown={event=>event.target===event.currentTarget&&requestClose()}><section class="settings-panel" ref={panel} role="dialog" aria-modal="true" aria-label={t("Settings.74a883a037")}>
    <header>{navTrigger}{heading}
      {narrow && loadingSearch}
      <button class="settings-close" aria-label={t("Settings.2da15e4dad")} onClick={()=>requestClose()}>×</button></header>
    <main class="settings-body">
      {narrow ? renderTabNav(NO_PREVIEWS) : <div class="settings-nav-col">{loadingSearch}{renderTabNav(NO_PREVIEWS)}</div>}
      <div class="settings-content"><section class="settings-loading" role="status" aria-live="polite">{status}</section></div>
    </main>
    <footer><span aria-live="polite">{status}</span><button onClick={()=>requestClose()}>{t("Settings.19766ed6cc")}</button></footer>
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
    resolveRoute(MODEL_ROUTES.find(route=>route.key===key)!,draft,endpointOverride).model||t("Settings.1aef939917")
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
        {activeTab==='general' && <Fragment>
          <section data-setting="ui_language"><LanguagePicker onSaved={saved=>{setConfig(current=>current?{...current,ui_language:saved.ui_language,revision:saved.revision}:current);setDraft(current=>current?{...current,ui_language:saved.ui_language,revision:saved.revision}:current)}} /></section>
          <section><h3 data-heading-message="Settings.610b7468ce">{t("Settings.610b7468ce")}</h3>
            <label data-setting="startup_cwd">{rich("Settings.4093a17a2f", { value1: <input value={draft.startup_cwd} onInput={e=>change('startup_cwd',e.currentTarget.value)} /> })}</label>
            <label>{rich("Settings.52c56b5713", { value1: <Dropdown value={draft.default_backend} onChange={value=>change('default_backend',value)} options={allBackendNames().map(name=>({value:name,label:name==='shell'?t("Settings.a733285486"):harnessDisplayName(name)}))}/> })}</label>
            <p>{t("Settings.b97dbb1da6")}</p>
          </section>

          <section data-setting="experience_tier"><h3 data-heading-message="Settings.96d00c9bca">{t("Settings.96d00c9bca")}</h3>
            <p>{t("Settings.6b8492beae")}</p>
            <label>{rich("Settings.c55d7900d9", { value1: <Dropdown value={tierChoice} onChange={value=>setTierChoice(value as typeof tierChoice)} options={[...(draft.experience_tier===''?[{value:'',get label() { return t("Settings.2f7b5f0c93") }}]:[]),{value:'terminal',get label() { return t("Settings.95f1428d07") }},{value:'deterministic',get label() { return t("Settings.ec6b656e17") }},{value:'automations',get label() { return t("Settings.ad1fb9ec0c") }}]}/> })}</label>
            {tierChoice && <ExperiencePreview tier={tierChoice}/>}
            <div class="settings-tutorial-reset"><div><p>{t("Settings.0bed26d526")}</p></div><button disabled={!tierChoice} onClick={()=>void applyExperienceTier()}>{t("Settings.1d9ec1f990")}</button></div>
            {tierStatus && <p class="profile-hint" role="status">{tierStatus}</p>}
          </section>

          <DesktopIntegrationSection/>

          <section><h3 data-heading-message="Settings.12f2e0cbaf">{t("Settings.12f2e0cbaf")}</h3>
            <div class="settings-tutorial-reset"><div><p>{t("Settings.3d59314e38")}</p></div><button onClick={()=>requestClose('tutorial')}>{t("Settings.708bf8b65a")}</button></div>
          </section>

          {/* Config-file actions live here rather than in the footer: they act on the
              whole configuration, not the visible tab, so repeating them under every
              tab implied a per-tab scope they never had — and on a phone they pushed
              Cancel/Save into a horizontally scrolling footer. */}
          <section><h3 data-heading-message="Settings.f1c216ddf2">{t("Settings.f1c216ddf2")}</h3>
          <div class="settings-config-actions"><div><p>{rich("Settings.607aa1a896", { value1: <code>{draft.data_dir}</code> })}</p></div>
              <div><button onClick={()=>void api('POST','/api/reveal',{path:draft.data_dir})}>{t("Settings.1f2df45014")}</button><button onClick={exportConfig}>{t("Settings.cf675af0cc")}</button><button class="danger" onClick={()=>setResetIntent(true)}>{t("Settings.602f8e6370")}</button></div>
            </div>
          </section>
        </Fragment>}

        {activeTab==='usage' && <section><h3 data-heading-message="Settings.a652bdce64">{t("Settings.a652bdce64")}</h3>
          <label class="check"><span>{t("Settings.79dd867432")}</span><input type="checkbox" checked={draft.canonical_telemetry_native_otel_enabled} onChange={e=>change('canonical_telemetry_native_otel_enabled',e.currentTarget.checked)}/></label>
          <p>{t("Settings.9be4b0b3ac")}</p>
          <p>{t("Settings.9826d8a4ee")}</p>
          <label class="check"><span>{t("Settings.ea63bde02d")}</span><input type="checkbox" checked={draft.canonical_telemetry_legacy_dashboard_enabled} onChange={e=>change('canonical_telemetry_legacy_dashboard_enabled',e.currentTarget.checked)}/></label>
          <p>{t("Settings.5b0da44ddc")}</p>
        </section>}

        {/* Per-Project options are NOT here: the Projects registry is the single
            per-Project editor (menu → Manage projects, or a Project's context menu →
            Project settings…). This tab is the global half only, and each section
            names the per-Project field it composes with. */}
        {activeTab==='projects' && <Fragment>
          {/* Where the assistant may create new project folders. Name-only at the tool:
              the model never supplies a path, so this one directory is the whole surface
              a chat message can create a folder in. The Add-project dialog is unaffected. */}
          <section><h3 data-heading-message="Settings.d597f45a94">{t("Settings.d597f45a94")}</h3>
            <label data-setting="new_project_parent">{rich("Settings.eae5876f47", { value1: <input value={draft.new_project_parent} placeholder={projectParentHint || t("Settings.8535f6ae6a")} onInput={e=>change('new_project_parent',e.currentTarget.value)}/> })}</label>
            <p>{rich("Settings.2742dd4f3c", { value1: projectParentHint && <Fragment>{rich("Settings.ab34b4cc9f", { value1: <code>{projectParentHint}</code> })}</Fragment> })}</p>
          </section>

          {/* Setup commands offered when a Project is registered. They are yours, typed
              here, stored on this machine — nothing is ever read out of a repository, so
              there is no trust prompt and no fingerprint to approve. */}
          <section><h3 data-heading-message="Settings.d76e4fae48">{t("Settings.d76e4fae48")}</h3>
            <div class="settings-init-scripts">
              <div class="settings-init-scripts-head"><div><strong>{t("Settings.b852090eca")}</strong><p>{t("Settings.705dd738d8")}</p></div><button onClick={addInitScript}>{t("Settings.8415a53c5e")}</button></div>
              {initScripts().map((script,index)=><div class="settings-init-script" key={script.id}>
                <label>{rich("Settings.5b326d4444", { value1: <input value={script.label} placeholder={t("Settings.75b7b54fd0")} onInput={e=>updateInitScript(index,{label:e.currentTarget.value})} /> })}</label>
                <label>{rich("Settings.624ed75acc", { value1: <textarea value={script.command} placeholder={t("Settings.dd1f763fbb")} onInput={e=>updateInitScript(index,{command:e.currentTarget.value})} /> })}</label>
                <div class="settings-init-script-actions">
                  <label class="check"><span>{t("Settings.73e88dbcfe")}</span><input type="checkbox" checked={!!script.default_enabled} onChange={e=>updateInitScript(index,{default_enabled:e.currentTarget.checked})} /></label>
                  <button disabled={index===0} onClick={()=>moveInitScript(index,-1)}>↑</button>
                  <button disabled={index===initScripts().length-1} onClick={()=>moveInitScript(index,1)}>↓</button>
                  <button class="danger" onClick={()=>removeInitScript(index)}>{t("Settings.c3812fc4ac")}</button>
                </div>
              </div>)}
              {!initScripts().length && <p>{t("Settings.aaf875d75c")}</p>}
            </div>
          </section>

          <section><h3 data-heading-message="Settings.32fd0c7bc8">{t("Settings.32fd0c7bc8")}</h3>
            <label data-setting="project_ignore_patterns">{rich("Settings.1ef5e70f2c", { value1: <textarea value={draft.project_ignore_patterns.join('\n')} onInput={e=>change('project_ignore_patterns',parseIgnorePatternDraft(e.currentTarget.value))}/> })}</label>
            <p>{rich("Settings.b4878aeb0e", { value1: <strong>{t("Settings.6388650422")}</strong> })}</p>
          </section>
        </Fragment>}

        {activeTab==='terminals' && <Fragment>
          <section><h3 data-heading-message="Settings.e2fe353986">{t("Settings.e2fe353986")}</h3>
            <label data-setting="terminal_renderer">{rich("Settings.e57095b0ec", { value1: <Dropdown value={draft.terminal_renderer} onChange={value=>change('terminal_renderer',value as Config['terminal_renderer'])} options={[{value:'auto',get label() { return t("Settings.79003a7b93") }},{value:'webgl',get label() { return t("Settings.54953cd5cd") }},{value:'dom',get label() { return t("Settings.45f5d80817") }}]}/> })}</label>
          <p>{t("Settings.8e3970531d")}</p>
          </section>

          {/* Retention, first replay, and crash checkpoint are three different byte
              figures over the same stream, and reading one without the other two is
              how "why did my pane come back short" stayed unanswerable. They are one
              section for that reason, ordered as the bytes travel: what is kept, what
              a fresh attach is handed, what survives losing the daemon and its PTY
              owner together. */}
          <section><h3 data-heading-message="Settings.e15c93639f">{t("Settings.e15c93639f")}</h3>
            <label>{rich("Settings.feb4d74fa3", { value1: <input type="number" min="1024" max="1073741824" value={draft.scrollback_bytes} onInput={e=>change('scrollback_bytes',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.55ec2f69cf")}</small> })}</label>
            <label data-setting="attach_replay_bytes">{rich("Settings.7931662b58", { value1: <input type="number" min="1024" max="1073741824" value={draft.attach_replay_bytes} onInput={e=>change('attach_replay_bytes',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.1210398c67")}</small> })}</label>
            <h4 data-heading-message="Settings.3aef96e640">{t("Settings.3aef96e640")}</h4>
          <p>{rich("Settings.d3eaa29455", { value1: <code>{t("Settings.1e768c98f8")}</code> })}</p>
            <label data-setting="session_recovery_checkpoint_bytes">{rich("Settings.453bc00217", { value1: <input type="number" min="0" max="67108864" value={draft.session_recovery_checkpoint_bytes} onInput={e=>change('session_recovery_checkpoint_bytes',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.d646181b8c")}</small> })}</label>
            <label data-setting="session_recovery_retention_days">{rich("Settings.e745537ac7", { value1: <input type="number" min="0" max="365" value={draft.session_recovery_retention_days} onInput={e=>change('session_recovery_retention_days',Number(e.currentTarget.value))} />, value2: <small>{rich("Settings.c52d762da9", { value1: <em>{t("Settings.e87ba7a0b7")}</em> })}</small> })}</label>
            <label data-setting="session_recovery_max_sessions">{rich("Settings.73f599952e", { value1: <input type="number" min="0" max="1000" value={draft.session_recovery_max_sessions} onInput={e=>change('session_recovery_max_sessions',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.6bdbb7f402")}</small> })}</label>
          </section>

          <section><h3 data-heading-message="Settings.ad30abd187">{t("Settings.ad30abd187")}</h3>
            <label>{rich("Settings.77a91ae469", { value1: <Dropdown value={draft.default_shell_profile} onChange={value=>change('default_shell_profile',value)} options={draft.shell_profiles.filter(profile=>profile.enabled&&profile.backend==='shell').map(profile=>({value:profile.id,label:profile.label}))}/> })}</label>
          <p>{t("Settings.04bd3e78d7")}</p>
          </section>

          <section><h3 data-heading-message="Settings.66907cf217">{t("Settings.66907cf217")}</h3>
            <label class="check" data-setting="agent_shims_on_shell_path"><span>{rich("Settings.1037accf0f", { value1: <em>{rich("Settings.8277415b2e", { value1: <code>{t("Settings.c857d09db2")}</code> })}</em> })}</span><input type="checkbox" checked={draft.agent_shims_on_shell_path} onChange={e=>change('agent_shims_on_shell_path',e.currentTarget.checked)} /></label>
            <p>{rich("Settings.85be6ef7a8", { value1: <code>{t("Settings.204fcb78d9")}</code> })}</p>
            <p class="profile-hint">{rich("Settings.437109e789", { value1: <code>{t("Settings.c857d09db2")}</code>, value2: <code>{t("Settings.c857d09db2")}</code> })}</p>
          </section>

          <section class="profile-settings"><h3 data-heading-message="Settings.1fce70b800">{t("Settings.1fce70b800")}</h3>
          <div class="profile-browser">
            <div class="profile-index" aria-label={t("Settings.82016fe5c7")}>
              {draft.shell_profiles.map(profile=><button class={selectedProfileId===profile.id?'active':''} onClick={()=>setSelectedProfileId(selectedProfileId===profile.id?null:profile.id)}><span>{profileTag(profile)}</span><strong>{profile.label}</strong><small>{profile.id} · {profile.backend==='shell' ? t("Settings.ce635c4eab") : harnessDisplayName(profile.backend)} · {profile.enabled ? t("Settings.b8d31e8527") : t("Settings.b4dc66dde8")}</small></button>)}
              <div class="profile-index-actions"><button onClick={()=>addProfile()}>{t("Settings.bd6a79817b")}</button>{harnesses().map(harness=><button key={harness.name} onClick={()=>addProfile(undefined,harness.name)}>{rich("Settings.86a86c70f9", { display_name: harness.display_name })}</button>)}<button onClick={restoreDetected}>{t("Settings.63b2db5c07")}</button></div>
            </div>
            {selectedProfile && <article class="profile-editor">
              <header><strong>{rich("Settings.ff86ba0777", { label: selectedProfile.label })}</strong><button aria-label={t("Settings.4ffba55e5d")} onClick={()=>setSelectedProfileId(null)}>×</button></header>
              <label>{rich("Settings.62db5ee889", { value1: <input value={selectedProfile.id} onInput={e=>updateProfile(selectedProfileIndex,{id:e.currentTarget.value})}/> })}</label>
              <label>{rich("Settings.babe8d3238", { value1: <input value={selectedProfile.label} onInput={e=>updateProfile(selectedProfileIndex,{label:e.currentTarget.value})}/> })}</label>
              <label>{rich("Settings.5f008d7b9b", { value1: <Dropdown value={selectedProfile.backend} onChange={value=>updateProfile(selectedProfileIndex,{backend:value as ProjectBackend})} options={[{value:'shell',get label() { return t("Settings.a733285486") }},...harnesses().map(harness=>({value:harness.name,label:harness.display_name}))]}/> })}</label>
              <label>{rich("Settings.85e00278f6", { value1: selectedProfile.backend!=='shell' && <em>{t("Settings.d422020daa")}</em>, value2: <input value={selectedProfile.executable} placeholder={selectedProfile.backend==='shell' ? '' : draft.harness_exe[selectedProfile.backend] || ''} onInput={e=>updateProfile(selectedProfileIndex,{executable:e.currentTarget.value})}/> })}</label>
              <label>{rich("Settings.dcd9b293a3", { value1: <input value={argsText} spellcheck={false} placeholder={selectedProfile.backend==='shell' ? t("Settings.b90a5d2477") : t("Settings.ece27d0c6a")} onInput={e=>{const text=e.currentTarget.value;setArgsText(text);updateProfile(selectedProfileIndex,{args:parseCommandLine(text)})}}/> })}</label>
              <p class="profile-hint">{rich("Settings.c12839d10a", { value1: <code>{t("Settings.364d30c5f3")}</code> })}</p>
              {selectedProfile.backend!=='shell'&&reservedConflict(selectedProfile.backend,selectedProfile.args) && <p class="error" role="alert">{rich("Settings.49b40019aa", { value1: reservedConflict(selectedProfile.backend,selectedProfile.args), value2: harnessDisplayName(selectedProfile.backend) })}</p>}
              <label>{rich("Settings.8667a37964", { value1: <textarea value={Object.entries(selectedProfile.env).map(([key,value])=>`${key}=${value}`).join('\n')} onInput={e=>updateProfile(selectedProfileIndex,{env:Object.fromEntries(e.currentTarget.value.split('\n').filter(line=>line.includes('=')).map(line=>{const at=line.indexOf('=');return [line.slice(0,at),line.slice(at+1)]}))})}/> })}</label>
              {selectedProfile.backend==='shell' && <><label>{rich("Settings.779525d2aa", { value1: <Dropdown value={selectedProfile.cwd_strategy} onChange={value=>updateProfile(selectedProfileIndex,{cwd_strategy:value as LaunchProfile['cwd_strategy']})} options={[{value:'native',get label() { return t("Settings.bef32d2c31") }},{value:'home',get label() { return t("Settings.4ea1405881") }},{value:'wsl',get label() { return t("Settings.42bda76ffe") }}]}/> })}</label>
              <label class="check"><span>{t("Settings.45eb01ad80")}</span><input type="checkbox" checked={selectedProfile.cwd_integration} onChange={e=>updateProfile(selectedProfileIndex,{cwd_integration:e.currentTarget.checked})}/></label></>}
              <div class="profile-preview"><span>{t("Settings.7276b6d954")}</span><code>{launchPreview(selectedProfile.executable||(selectedProfile.backend!=='shell'?draft.harness_exe[selectedProfile.backend]||selectedProfile.backend:''),selectedProfile.backend==='shell'?[]:parseCommandLine(harnessArgs[selectedProfile.backend]||''),selectedProfile.args)}</code>
                <small>{selectedProfile.backend==='shell' ? t("Settings.0de2bfdfe9") : t("Settings.8e6876be17")}</small>
              </div>
              <div class="profile-capabilities"><span>{t("Settings.f60f612dfe")}</span>
                {(savedCapabilities(selectedProfile)||[]).map(item=><em key={item}>{item}</em>)}
                {!(savedCapabilities(selectedProfile)||[]).length && <em class="muted">{t("Settings.1e17fa46dd")}</em>}
                <small>{capabilitiesStale(selectedProfile) ? t("Settings.489161eace") : t("Settings.2fa60cb489")}</small>
              </div>
              <div class="profile-editor-actions"><button onClick={()=>moveProfile(selectedProfileIndex,-1)}>{t("Settings.4b6bad1ea4")}</button><button onClick={()=>moveProfile(selectedProfileIndex,1)}>{t("Settings.08bc837a97")}</button><button onClick={()=>addProfile(selectedProfile)}>{t("Settings.e24a5a32c9")}</button><button onClick={()=>updateProfile(selectedProfileIndex,{enabled:!selectedProfile.enabled})}>{selectedProfile.enabled ? t("Settings.e9d8992f34") : t("Settings.e97166b545")}</button>{/* The last *shell* profile is what cannot go: `default_shell_profile` must name
    one, and with none left the select above has no options, so the save would fail
    on a field the user could no longer set. Agent profiles are freely removable. */}
<button class="danger" disabled={selectedProfile.backend==='shell'&&draft.shell_profiles.filter(profile=>profile.backend==='shell').length===1} onClick={()=>{change('shell_profiles',draft.shell_profiles.filter((_,index)=>index!==selectedProfileIndex));setSelectedProfileId(null)}}>{t("Settings.7e5608ab61")}</button></div>
              <small>{selectedProfile.backend==='shell' ? t("Settings.3727d237a1") : t("Settings.15073d5d0f", { value1: harnessDisplayName(selectedProfile.backend), value2: reservedArgs(selectedProfile.backend).join(' ')||'none' })}</small>
            </article>}
            {!selectedProfile && <div class="profile-placeholder"><span>{t("Settings.546d26c986")}</span><strong>{t("Settings.ff0a1d133a")}</strong><p>{t("Settings.4775f76657")}</p></div>}
          </div>
          </section>
        </Fragment>}

        {activeTab==='git' && <section><h3 data-heading-message="Settings.71b6da4552">{t("Settings.71b6da4552")}</h3><label>{rich("Settings.f4c6a15182", { value1: <input value={draft.worktree_root} onInput={e=>change('worktree_root',e.currentTarget.value)}/> })}</label><p>{rich("Settings.f8fc2d7627", { value1: <code>{rich("Settings.3889fda2be", { data_dir: draft.data_dir, value2: (draft.data_dir.includes('\\')) ? 'yes' : 'no' })}</code> })}</p><label>{rich("Settings.58f6edf104", { value1: <input type="number" step=".25" value={draft.git_poll_seconds} onInput={e=>change('git_poll_seconds',Number(e.currentTarget.value))} /> })}</label><label>{rich("Settings.cecdecba54", { value1: <input type="checkbox" checked={draft.git_swe_mux_prompt_enabled} onChange={e=>change('git_swe_mux_prompt_enabled',e.currentTarget.checked)}/> })}</label><p>{rich("Settings.9ca0fac057", { value1: <code>{t("Settings.64f9020bac")}</code> })}</p><div class="settings-config-actions"><div><strong>{t("Settings.ae176d3571")}</strong><p>{rich("Settings.aeae0524ec", { length: Object.keys(draft.git_swe_mux_prompt_decisions||{}).length, value2: (Object.keys(draft.git_swe_mux_prompt_decisions||{}).length===1) ? 'yes' : 'no' })}</p></div><div><button disabled={!Object.keys(draft.git_swe_mux_prompt_decisions||{}).length} onClick={()=>change('git_swe_mux_prompt_decisions',{})}>{t("Settings.b9b2de18eb")}</button></div></div><p>{rich("Settings.a5a5938bcf", { value1: <strong>{t("Settings.31c05b41e5")}</strong>, value2: <strong>{t("Settings.0e750756c8")}</strong> })}</p></section>}

        {activeTab==='processes' && <Fragment>
          <section><h3 data-heading-message="Settings.2ed6993b61">{t("Settings.2ed6993b61")}</h3><label>{rich("Settings.977c0a9783", { value1: <input type="number" min=".5" max="60" step=".5" value={draft.process_poll_seconds} onInput={e=>change('process_poll_seconds',Number(e.currentTarget.value))}/> })}</label><label>{rich("Settings.d97a0a476a", { value1: <input type="number" min="1" max="3600" value={draft.process_orphan_grace_seconds} onInput={e=>change('process_orphan_grace_seconds',Number(e.currentTarget.value))}/> })}</label><label>{rich("Settings.93fce66a81", { value1: <input type="number" min="1" max="3650" value={draft.process_evidence_retention_days} onInput={e=>change('process_evidence_retention_days',Number(e.currentTarget.value))}/> })}</label><p>{rich("Settings.4f2826faba", { value1: <strong>{t("Settings.4a81a7ba53")}</strong> })}</p>
            <label>{rich("Settings.b35161bef3", { value1: <Dropdown value={draft.session_process_priority} onChange={value=>change('session_process_priority',value as Config['session_process_priority'])} options={[{value:'below_normal',get label() { return t("Settings.d28e231ef0") }},{value:'normal',get label() { return t("Settings.ea875c6854") }}]}/> })}</label>
            <label>{rich("Settings.fa39d2056b", { value1: <Dropdown value={draft.daemon_process_priority} onChange={value=>change('daemon_process_priority',value as Config['daemon_process_priority'])} options={[{value:'above_normal',get label() { return t("Settings.ec47b10a17") }},{value:'normal',get label() { return t("Settings.a7248eeb45") }}]}/> })}</label>
            <p>{t("Settings.846a31434a")}</p>
            <label data-setting="gil_switch_interval_ms">{rich("Settings.a10ce4071b", { value1: <input type="number" min="1" max="20" step=".5" value={draft.gil_switch_interval_ms} onInput={e=>change('gil_switch_interval_ms',Number(e.currentTarget.value))}/>, value2: <small>{t("Settings.360031df94")}</small> })}</label>
            <label class="check" data-setting="worker_process_enabled"><span>{t("Settings.3622b018ac")}</span><input type="checkbox" checked={draft.worker_process_enabled} onChange={e=>change('worker_process_enabled',e.currentTarget.checked)}/><small>{t("Settings.ceef3d6ef2")}</small></label>
            <label class="check" data-setting="terminal_gateway_enabled"><span>{t("Settings.959667c0c0")}</span><input type="checkbox" checked={draft.terminal_gateway_enabled} onChange={e=>change('terminal_gateway_enabled',e.currentTarget.checked)}/><small>{t("Settings.25da30eeb7")}</small></label>
            <label data-setting="terminal_gateway_port">{rich("Settings.4dc456a205", { value1: <input type="number" min="0" max="65535" value={draft.terminal_gateway_port} onInput={e=>change('terminal_gateway_port',Number(e.currentTarget.value))}/>, value2: <small>{t("Settings.687ee3f1d6")}</small> })}</label></section>

          {/* Its own section rather than another row under process evidence: the sweep
              moves something on screen, which is the one thing here that changes what
              the machine looks like rather than what swe-mux records about it. */}
          <section><h3 data-heading-message="Settings.2a8b718d0b">{t("Settings.2a8b718d0b")}</h3>
          <p>{t("Settings.2680b29635")}</p>
          <label class="check" data-setting="ghost_window_sweep_enabled"><span>{t("Settings.06c6c793a7")}</span><input type="checkbox" checked={draft.ghost_window_sweep_enabled} onChange={e=>change('ghost_window_sweep_enabled',e.currentTarget.checked)}/><small>{rich("Settings.6fa12cdb55", { value1: <code>{t("Settings.0b991fefcf")}</code> })}</small></label>
          <label data-setting="ghost_window_poll_seconds">{rich("Settings.f1f3d39c21", { value1: <input type="number" min=".5" max="60" step=".5" value={draft.ghost_window_poll_seconds} onInput={e=>change('ghost_window_poll_seconds',Number(e.currentTarget.value))}/>, value2: <small>{t("Settings.839235ac8e")}</small> })}</label>
          </section>

          {/* Detection telemetry rather than process telemetry, but it belongs beside the
              other evidence windows rather than under Usage: it is the per-session record
              an incident is reconstructed from, not a spend or quota figure. */}
          <section><h3 data-heading-message="Settings.88bfa9d4b6">{t("Settings.88bfa9d4b6")}</h3>
          <label data-setting="status_timeline_retention_days">{rich("Settings.d46fe50b53", { value1: <input type="number" min="1" max="3650" value={draft.status_timeline_retention_days} onInput={e=>change('status_timeline_retention_days',Number(e.currentTarget.value))}/>, value2: <small>{t("Settings.e169612cbb")}</small> })}</label>
          </section>
        </Fragment>}

        {/* One editor renders every Markdown surface (notes,
            Markdown files from Files), so everything here applies to all of them.
            Colours are absent on purpose: the theme already drives the editor's
            colour variables, and a second source for them would fight it. */}
        {activeTab==='notes' && <Fragment>
          <section><h3 data-heading-message="Settings.67ba93d745">{t("Settings.67ba93d745")}</h3>
            <label class="check" data-setting="note_scratchpad_enabled"><span>{t("Settings.b9b69191a6")}</span><input type="checkbox" checked={draft.note_scratchpad_enabled} onChange={e=>change('note_scratchpad_enabled',e.currentTarget.checked)}/><small>{t("Settings.05b557b651")}</small></label>
          </section>
          <section><h3 data-heading-message="Settings.2b1a1a8efc">{t("Settings.2b1a1a8efc")}</h3>
            <label class="check"><span>{t("Settings.8691e20252")}</span><input type="checkbox" checked={draft.note_spellcheck} onChange={e=>change('note_spellcheck',e.currentTarget.checked)}/><small>{t("Settings.6c163dc252")}</small></label>
            <label class="check"><span>{t("Settings.15da0874d2")}</span><input type="checkbox" checked={draft.note_indent_guides} onChange={e=>change('note_indent_guides',e.currentTarget.checked)}/><small>{t("Settings.749abfbd08")}</small></label>
            <label>{rich("Settings.470cdab19f", { value1: <Dropdown value={draft.note_syntax} onChange={value=>change('note_syntax',value as Config['note_syntax'])} options={[{value:'markdown',get label() { return t("Settings.62337e12ce") }},{value:'plain',get label() { return t("Settings.0c1575ba8e") }}]}/> })}</label>
            <p>{t("Settings.d6b0d71e98")}</p>
            <label>{rich("Settings.99da907ba1", { value1: <Dropdown value={draft.note_tab_behavior} onChange={value=>change('note_tab_behavior',value as Config['note_tab_behavior'])} options={[{value:'indent',get label() { return t("Settings.f0a27473ee") }},{value:'focus',get label() { return t("Settings.8140666b1b") }}]}/> })}</label>
            <p>{t("Settings.0f94087686")}</p>
          </section>

          <section><h3 data-heading-message="Settings.1758bbe940">{t("Settings.1758bbe940")}</h3>
            <label data-setting="note_font_family">{rich("Settings.b0603de3db", { value1: <input value={draft.note_font_family} placeholder={t("Settings.71fb1f9cb5")} onInput={e=>change('note_font_family',e.currentTarget.value)}/> })}</label>
            <label>{rich("Settings.3323adcfd3", { value1: <input type="number" min="0" max="48" value={draft.note_font_size_px} onInput={e=>change('note_font_size_px',Number(e.currentTarget.value))}/> })}</label>
            <label>{rich("Settings.146c05dec2", { value1: <input type="number" min="0" max="3" step="0.05" value={draft.note_line_height} onInput={e=>change('note_line_height',Number(e.currentTarget.value))}/> })}</label>
            <p>{rich("Settings.2c6fabdb76", { value1: <strong>{t("Settings.371fe46d31")}</strong> })}</p>
          </section>

          <section><h3 data-heading-message="Settings.95468a8905">{t("Settings.95468a8905")}</h3>
            <p>{t("Settings.7f69793973")}</p>
            <label>{rich("Settings.33f513a038", { value1: <Dropdown value={draft.note_command_rail} onChange={value=>change('note_command_rail',value as Config['note_command_rail'])} options={[{value:'auto',get label() { return t("Settings.99da46f473") }},{value:'on',get label() { return t("Settings.de9f057a47") }},{value:'off',get label() { return t("Settings.6300ef800b") }}]}/> })}</label>
            <label>{rich("Settings.e8bc37b079", { value1: <input type="number" min="0" max="96" value={draft.note_rail_button_size_px} onInput={e=>change('note_rail_button_size_px',Number(e.currentTarget.value))}/>, value2: <small>{t("Settings.fb6ca3312e")}</small> })}</label>
            <div class="keybinding-heading"><div><strong>{t("Settings.253bcafe6f")}</strong><p>{t("Settings.0ce7deb840")}</p></div><button type="button" onClick={resetNoteRailArrangement}>{t("Settings.3172ed0d7a")}</button></div>
          </section>

          <section><h3 data-heading-message="Settings.8178e6c324">{t("Settings.8178e6c324")}</h3>
            <label>{rich("Settings.419ccae7ac", { value1: <Dropdown value={draft.note_shortcut_policy} onChange={value=>change('note_shortcut_policy',value as ShortcutPolicy)} options={[{value:'browser-safe',get label() { return t("Settings.d137c73eca") }},{value:'editor-first',get label() { return t("Settings.90136025f0") }},{value:'none',get label() { return t("Settings.5dcf283732") }}]}/> })}</label>
            <p>{t("Settings.3ece23954e")}</p>
            <div class="keybinding-heading"><div><strong>{t("Settings.9ff4ec4a01")}</strong><p>{t("Settings.7dc336aa8b")}</p></div><button type="button" onClick={()=>change('note_shortcut_overrides',{...DEFAULT_NOTE_SHORTCUT_OVERRIDES})}>{t("Settings.6be24d9e75")}</button></div>
            <input class="note-chord-filter" type="search" value={noteChordQuery} placeholder={t("Settings.a884e0e9dc")} aria-label={t("Settings.d773c44e7f")} spellcheck={false} onInput={e=>setNoteChordQuery(e.currentTarget.value)}/>
            <div class="note-chord-list" data-settings-search="skip">
              {NOTE_CHORDS.filter(binding=>{
                const needle=noteChordQuery.trim().toLowerCase()
                return !needle||binding.chord.includes(needle)||binding.command.includes(needle)
              }).map(binding=><article key={binding.chord}>
                <span class="note-chord"><kbd>{noteChordLabel(binding.chord)}</kbd>{!binding.isBrowserSafe && <em title={t("Settings.68c64a3df3")}>{t("Settings.1ec18f6e0e")}</em>}</span>
                <small title={binding.command}>{binding.command}</small>
                <Dropdown ariaLabel={t("Settings.3097450f6c", { value1: noteChordLabel(binding.chord) })} value={noteChordState(draft.note_shortcut_overrides,binding.chord)} onChange={value=>setNoteChord(draft.note_shortcut_overrides,binding.chord,binding.command,value as NoteChordState)} options={[
                  {value:'default',get label() { return t("Settings.dfea46e31d") }},
                  {value:'bind',get label() { return t("Settings.abac1508d2") }},
                  {value:'release',get label() { return t("Settings.97989ab1cd") }},
                ]}/>
              </article>)}
            </div>
          </section>

        </Fragment>}

        {activeTab==='harnesses' && <Fragment>
          {/* Above the per-harness list on purpose: it is the one setting here that
              is about the set rather than about a member of it, and reading it after
              five harness cards would make it look like a property of the last one. */}
          <section><h3 data-heading-message="Settings.c039f423b8">{t("Settings.c039f423b8")}</h3>
          <p>{t("Settings.7655b44078")}</p>
          <label data-setting="default_harness">{rich("Settings.ed99b5afc7", { value1: <Dropdown value={draft.default_harness} onChange={value=>change('default_harness',value)} options={[{value:'',get label() { return t("Settings.1696e54928") }},...allHarnessesIncludingDisabled().map(harness=>({value:harness.name,label:harness.display_name}))]}/> })}</label>
          <p class="profile-hint">{t("Settings.1a4b9f37fd")}</p>
          </section>

          {/* Marked as an *area* rather than per row: the deep link's promise is "the
              switches are these", and there is one per registered harness. The reveal
              focuses the first checkbox inside, which is the right landing. */}
          <section class="settings-harnesses" data-setting="harness_enabled"><h3 data-heading-message="Settings.f817714a1a">{t("Settings.f817714a1a")}</h3>
          <p>{t("Settings.d9f64bd7ee")}</p>
          {allHarnessesIncludingDisabled().map(harness=><div class="settings-harness" key={harness.name}>
            <div class="settings-harness-head">
              <label class="check"><span>{harness.display_name}</span><input type="checkbox" checked={harnessEnabledDraft(harness.name)} onChange={e=>setHarnessEnabledChoice(harness.name,e.currentTarget.checked)} /></label>
              <small class={detectedInstalled(harness.name)?'settings-harness-detected':'settings-harness-missing'}>{detectedInstalled(harness.name) ? (detectedPath(harness.name) ? t("Settings.60922bba81", { value1: detectedPath(harness.name) }) : t("Settings.4aa307b579")) : t("Settings.9f4a106271")}{typeof enablementChoice(harness.name)==='boolean' ? t("Settings.c8d4b58277", { value1: (enablementChoice(harness.name)) ? 'yes' : 'no' }) : t("Settings.882a3b849c")}</small>
              {typeof enablementChoice(harness.name)==='boolean' && <button type="button" onClick={()=>clearHarnessEnabledChoice(harness.name)}>{t("Settings.ea4a192a7d")}</button>}
            </div>
            {harness.cli_version && <p class={harness.version_untested?'settings-inline-error':'profile-hint'}>{rich("Settings.3edf3221c9", { cli_version: harness.cli_version, value2: harness.version_untested ? t("Settings.d110d389a5", { tested_cli_version: harness.tested_cli_version }) : '' })}</p>}
            <label>{rich("Settings.a0d21df092", { value1: <input value={draft.harness_exe[harness.name]||''} placeholder={harness.name} onInput={e=>change('harness_exe',{...draft.harness_exe,[harness.name]:e.currentTarget.value})} /> })}</label>
            <label>{rich("Settings.25e8f75d56", { value1: <input value={harnessArgs[harness.name]||''} spellcheck={false} placeholder={t("Settings.ece27d0c6a")} onInput={e=>setHarnessArgs(current=>({...current,[harness.name]:e.currentTarget.value}))} /> })}</label>
            {(()=>{
              // Fleet access is two *capability* surfaces plus one *instruction*
              // layer, and the split is the model, not decoration: MCP tools
              // advertise themselves in the session's tool list; the agent CLI
              // (`swemux agent`, the same tools over a second transport) is mute
              // and only the skill or MCP ever tells an agent it exists; and the
              // skill is documentation for whichever capability is present, not
              // a capability itself. Three checkboxes rather than one dropdown
              // because the four dropdown states stopped being enumerable once
              // the CLI surface arrived - and the incoherent combinations get a
              // named warning instead of being unrepresentable, because an
              // operator mid-reconfiguration passes through them legitimately.
              const mcpCapable=harnessDescriptor(harness.name)?.capabilities.mcp!==false
              const skillDelivery=harnessDescriptor(harness.name)?.capabilities.skill_delivery??'project'
              const mcpOn=mcpCapable&&harnessDictOn('harness_mcp_enabled',harness.name)
              const cliOn=harnessDictOn('harness_cli_enabled',harness.name)
              const skillOn=harnessSkillOn(harness.name)
              const skillLabel=skillDelivery==='session'?t("Settings.6637a3d49d"):t("Settings.4390e35da6")
              return <Fragment>
                <div class="settings-harness-fleet" data-setting="harness_skill_enabled">
                  <span>{t("Settings.0d43c90518")}</span>
                  {mcpCapable && <label class="check"><span>{rich("Settings.8063b300d8", { value1: <em>{t("Settings.9491d29b33")}</em> })}</span><input type="checkbox" checked={mcpOn} onChange={e=>setHarnessDict('harness_mcp_enabled',harness.name,e.currentTarget.checked)} /></label>}
                  <label class="check" data-setting="harness_cli_enabled"><span>{rich("Settings.a09793cc5c", { value1: <em>{t("Settings.e310d3558b")}</em> })}</span><input type="checkbox" checked={cliOn} onChange={e=>setHarnessDict('harness_cli_enabled',harness.name,e.currentTarget.checked)} /></label>
                  <label class="check"><span>{skillLabel} <em>{t("Settings.e9bd014beb")}</em></span><input type="checkbox" checked={skillOn} onChange={e=>setHarnessSkill(harness.name,e.currentTarget.checked)} /></label>
                </div>
                <p class="profile-hint">{rich("Settings.271490250f", { display_name: harness.display_name, value2: mcpCapable ? t("Settings.12b4f0aea7") : t("Settings.87dcf52869", { display_name: harness.display_name }) })}</p>
                {skillOn&&!mcpOn&&!cliOn && <p class="settings-inline-error">{t("Settings.4a818f6bf3")}</p>}
                {cliOn&&!mcpOn&&!skillOn && <p class="settings-inline-error">{rich("Settings.6e0687dc54", { display_name: harness.display_name })}</p>}
                {skillOn && (skillDelivery==='session' ? <p class="profile-hint">{t("Settings.82a96c602b")}</p> : <p class="profile-hint">{rich("Settings.b5f492c37d", { value1: <code>{t("Settings.ca6d2d714f")}</code>, display_name: harness.display_name, value3: <code>{t("Settings.c394175813")}</code> })}</p>)}
              </Fragment>
            })()}
            {harnessDescriptor(harness.name)?.capabilities.lifecycle_hooks && <Fragment>
              <label class="check"><span>{rich("Settings.444ba75cec", { value1: <em>{t("Settings.c040421576")}</em> })}</span><input type="checkbox" checked={harnessDictOn('harness_instrument_enabled',harness.name)} onChange={e=>setHarnessDict('harness_instrument_enabled',harness.name,e.currentTarget.checked)} /></label>
              {!harnessDictOn('harness_instrument_enabled',harness.name) && <p class="settings-inline-error">{rich("Settings.5761a17e44", { display_name: harness.display_name })}</p>}
            </Fragment>}
            {(harnessDictOn('harness_mcp_enabled',harness.name)===false||harnessDictOn('harness_cli_enabled',harness.name)===false||harnessDictOn('harness_instrument_enabled',harness.name)===false||harnessSkillOn(harness.name)) && <p class="profile-hint">{t("Settings.713c910218")}</p>}
            {appliesWidthEnvelope(harness.name) && <Fragment>
              <label data-setting="claude_max_columns">{rich("Settings.6141b55522", { value1: <Dropdown value={String(draft.claude_max_columns)} onChange={value=>change('claude_max_columns',Number(value) as ClaudeMaxColumns)} options={CLAUDE_MAX_COLUMN_STEPS.map(step=>({value:String(step),label:claudeMaxColumnsLabel(step)}))}/> })}</label>
              <p class="profile-hint">{rich("Settings.1cea781564", { display_name: harness.display_name })}</p>
            </Fragment>}
            <p class="profile-hint">{rich("Settings.aafa73b53f", { display_name: harness.display_name, value2: (harness.reserved_launch_args||[]).join(' ') || t("Settings.140bedbf9c") })}</p>
          </div>)}
          </section>

          {/* History indexing rather than harness configuration, but inseparable from
              it: the scan is scoped to exactly the harnesses enabled above, so the two
              are read together or not at all. */}
          <section><h3 data-heading-message="Settings.80c250f3db">{t("Settings.80c250f3db")}</h3>
          <p>{t("Settings.5ed19b9885")}</p>
          <label class="check"><span>{t("Settings.1f280417b0")}</span><input type="checkbox" checked={draft.reconcile_external_history} onChange={e=>change('reconcile_external_history',e.currentTarget.checked)} /></label>
          <label>{rich("Settings.babf8188ae", { value1: <input type="number" min="1" max="10000" value={draft.history_limit} onInput={e=>change('history_limit',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.a7f84f4c25")}</small> })}</label>
          <div class="settings-history-scan">
            <div class="settings-history-scan-head">
              <div><strong>{t("Settings.99d8c3676c")}</strong><p>{t("Settings.290fc26f3d")}</p></div>
              {scanJob?.status==='running' ? <button type="button" class="danger" onClick={()=>void cancelHistoryScan()}>{scanJob.phase==='cancelling' ? t("Settings.91b104db05") : t("Settings.298360e0b1")}</button> : <button type="button" onClick={()=>void startHistoryScan()}>{t("Settings.c01d65df01")}</button>}
            </div>
            {scanJob&&scanJob.status!=='idle' && <p class="settings-history-scan-status" aria-live="polite">{scanJob.status==='running' ? (scanJob.phase==='indexing' ? t("Settings.078ffdc731", { processed: scanJob.processed, scanned: scanJob.scanned, imported: scanJob.imported }) : t("Settings.7d8c668dc5", { scanned: scanJob.scanned })) : scanJob.status==='completed' ? t("Settings.51a9ad7971", { imported: scanJob.imported, scanned: scanJob.scanned, value3: scanJob.backends.join(', ')||t("Settings.a9460f3eaa") }) : scanJob.status==='cancelled' ? t("Settings.9f118efc15", { imported: scanJob.imported }) : t("Settings.6893223441", { value1: scanJob.error||t("Settings.3e4443e522") })}</p>}
          </div>
          </section>
        </Fragment>}

        {/* Delivery policy, not harness configuration: these bound how a queued
            message reaches an agent, whichever harness that agent is running. */}
        {activeTab==='queue' && <Fragment>
          <section><h3 data-heading-message="Settings.d4b1ea5708">{t("Settings.d4b1ea5708")}</h3>
          <p>{t("Settings.ad06b10257")}</p>
          <div class="settings-status-grid">
            <article><strong>{t("Settings.a5c30a1abf")}</strong><span>{draft.auto_delivery_enabled ? t("Settings.71f9fdbd1a", { auto_delivery_stable_seconds: draft.auto_delivery_stable_seconds }) : t("Settings.877792b6f2")}</span></article>
            <article><strong>{t("Settings.2bfc347157")}</strong><span>{draft.approval_auto_enabled ? t("Settings.864978c6fb") : t("Settings.822872c3d7")}</span></article>
            <article><strong>{t("Settings.be19f68d0c")}</strong><span>{draft.agent_messaging_enabled ? t("Settings.292c448d09") : t("Settings.ca7981b46e")}</span></article>
            <article><strong>{t("Settings.14be9614c7")}</strong><span>{draft.session_control_enabled ? t("Settings.175ef8fb52") : t("Settings.46eadcf1ee")}</span></article>
          </div>
          <p class="settings-policy-floor">{rich("Settings.43a4c4ad66", { value1: <strong>{t("Settings.93193607d7")}</strong> })}</p>
          </section>
          <section><h3 data-heading-message="Settings.a5c30a1abf">{t("Settings.a5c30a1abf")}</h3>
          <p>{t("Settings.34a9fb70ee")}</p>
          <label class="check" data-setting="auto_delivery_enabled"><span>{t("Settings.a7ed28c4c2")}</span><input type="checkbox" checked={draft.auto_delivery_enabled} onChange={e=>change('auto_delivery_enabled',e.currentTarget.checked)} /></label>
          <label>{rich("Settings.7d738775f2", { value1: <input type="number" min="2" max="600" step="0.5" value={draft.auto_delivery_stable_seconds} onInput={e=>change('auto_delivery_stable_seconds',Number(e.currentTarget.value))} /> })}</label>
          <label>{rich("Settings.35a3bfe847", { value1: <input type="number" min="1" max="50" value={draft.auto_delivery_max_consecutive} onInput={e=>change('auto_delivery_max_consecutive',Number(e.currentTarget.value))} /> })}</label>
          <label data-setting="auto_delivery_session_ttl_minutes">{rich("Settings.ab0de6e002", { value1: <input type="number" min="1" max="1440" value={draft.auto_delivery_session_ttl_minutes} onInput={e=>change('auto_delivery_session_ttl_minutes',Number(e.currentTarget.value))} /> })}</label>
          <label data-setting="auto_delivery_reply_window_minutes">{rich("Settings.6fe3a650dc", { value1: <input type="number" min="0" max="1440" value={draft.auto_delivery_reply_window_minutes} onInput={e=>change('auto_delivery_reply_window_minutes',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.9286b51995")}</small> })}</label>
          <label>{rich("Settings.46a9b670ad", { value1: <input type="number" min="0" max="3600" step="0.5" value={draft.auto_delivery_refusal_backoff_seconds} onInput={e=>change('auto_delivery_refusal_backoff_seconds',Number(e.currentTarget.value))} /> })}</label>
          <div class="quiet-hours"><label>{rich("Settings.32be2f94a7", { value1: <input type="time" value={draft.auto_delivery_quiet_start} onInput={e=>change('auto_delivery_quiet_start',e.currentTarget.value)} /> })}</label><label>{rich("Settings.6c5bf405fb", { value1: <input type="time" value={draft.auto_delivery_quiet_end} onInput={e=>change('auto_delivery_quiet_end',e.currentTarget.value)} /> })}</label></div>
          <p>{t("Settings.111dc8995a")}</p>

          {/* Mid-turn delivery belongs under auto-delivery rather than beside the
              messaging bounds: it is a *delivery* mode with its own strictly narrower
              readiness predicate, not a kind of message. */}
          <h4 data-heading-message="Settings.eca2f107c3">{t("Settings.eca2f107c3")}</h4>
          <p>{t("Settings.8cb1be7eb3")}</p>
          <label class="check" data-setting="agent_interject_enabled"><span>{t("Settings.a8eceb520f")}</span><input type="checkbox" checked={draft.agent_interject_enabled} onChange={e=>change('agent_interject_enabled',e.currentTarget.checked)} /></label>
          <label data-setting="agent_interject_hourly_budget">{rich("Settings.ca34dedfda", { value1: <input type="number" min="0" max="1000" disabled={!draft.agent_message_limits_enabled} value={draft.agent_interject_hourly_budget} onInput={e=>change('agent_interject_hourly_budget',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.aa7c0ea1d1")}</small> })}</label>
          <label data-setting="agent_interject_min_interval_seconds">{rich("Settings.b85f7ffed7", { value1: <input type="number" min="0" max="3600" step="1" disabled={!draft.agent_message_limits_enabled} value={draft.agent_interject_min_interval_seconds} onInput={e=>change('agent_interject_min_interval_seconds',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.38dc1131d8")}</small> })}</label>
          </section>

          {/* Approvals sit beside auto-delivery deliberately: both answer "what
              does swe-mux do on my behalf", and both are one install switch over
              bounded per-conversation grants. */}
          <section><h3 data-heading-message="Settings.2bfc347157">{t("Settings.2bfc347157")}</h3>
          <p>{t("Settings.8cfbaf9589")}</p>
          <label class="check" data-setting="approval_auto_enabled"><span>{t("Settings.98473e42a5")}</span><input type="checkbox" checked={draft.approval_auto_enabled} onChange={e=>change('approval_auto_enabled',e.currentTarget.checked)} /></label>
          <label class="check"><span>{t("Settings.8715729de1")}</span><input type="checkbox" checked={draft.approval_allow_all_permitted} onChange={e=>change('approval_allow_all_permitted',e.currentTarget.checked)} /></label>
          <label>{rich("Settings.af857df0e5", { value1: <input type="number" min="1" max="480" value={draft.approval_grant_ttl_minutes} onInput={e=>change('approval_grant_ttl_minutes',Number(e.currentTarget.value))} /> })}</label>
          <label>{rich("Settings.f0a3211cad", { value1: <input type="number" min="1" max="5000" value={draft.approval_max_auto_per_grant} onInput={e=>change('approval_max_auto_per_grant',Number(e.currentTarget.value))} /> })}</label>
          <label>{rich("Settings.0bcc46f25d", { value1: <input type="number" min="1" max="60" step="0.5" value={draft.approval_hook_timeout_seconds} onInput={e=>change('approval_hook_timeout_seconds',Number(e.currentTarget.value))} /> })}</label>
          <label class="check"><span>{t("Settings.3d1ee6916a")}</span><input type="checkbox" checked={draft.approval_keystroke_delivery} onChange={e=>change('approval_keystroke_delivery',e.currentTarget.checked)} /></label>
          <label>{rich("Settings.26d22370ad", { value1: <input type="number" min="1" max="300" step="1" value={draft.approval_keystroke_window_seconds} onInput={e=>change('approval_keystroke_window_seconds',Number(e.currentTarget.value))} /> })}</label>
          <p>{rich("Settings.f01a8c0c90", { value1: <strong>{t("Settings.2aff632d47")}</strong> })}</p>
          </section>

          <section><h3 data-heading-message="Settings.be19f68d0c">{t("Settings.be19f68d0c")}</h3>
          <p>{t("Settings.b343443ffa")}</p>
          <label class="check" data-setting="agent_messaging_enabled"><span>{t("Settings.6671bb2f54")}</span><input type="checkbox" checked={draft.agent_messaging_enabled} onChange={e=>change('agent_messaging_enabled',e.currentTarget.checked)} /></label>
          <label class="check" data-setting="agent_message_limits_enabled"><span>{t("Settings.05255bdaca")}</span><input type="checkbox" checked={draft.agent_message_limits_enabled} onChange={e=>change('agent_message_limits_enabled',e.currentTarget.checked)} /><small>{t("Settings.b6bad9ec57")}</small></label>
          <label>{rich("Settings.fe4170da7b", { value1: <input type="number" min="1" max="10" disabled={!draft.agent_message_limits_enabled} value={draft.agent_message_max_chain_depth} onInput={e=>change('agent_message_max_chain_depth',Number(e.currentTarget.value))} /> })}</label>
          <label>{rich("Settings.bd8360f4ee", { value1: <input type="number" min="1" max="100" disabled={!draft.agent_message_limits_enabled} value={draft.agent_message_max_thread_turns} onInput={e=>change('agent_message_max_thread_turns',Number(e.currentTarget.value))} /> })}</label>
          <label>{rich("Settings.9dcc57f24d", { value1: <input type="number" min="1" max="1000" disabled={!draft.agent_message_limits_enabled} value={draft.agent_message_hourly_budget} onInput={e=>change('agent_message_hourly_budget',Number(e.currentTarget.value))} /> })}</label>
          <label>{rich("Settings.a46074d4d0", { value1: <input type="number" min="1" max="100" disabled={!draft.agent_message_limits_enabled} value={draft.agent_message_pending_per_target} onInput={e=>change('agent_message_pending_per_target',Number(e.currentTarget.value))} /> })}</label>
          <label data-setting="agent_message_max_chars">{rich("Settings.bcc36055f2", { value1: <input type="number" min="1" max="100000" value={draft.agent_message_max_chars} onInput={e=>change('agent_message_max_chars',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.3d89b59858")}</small> })}</label>
          </section>

          {/* Its own section rather than more rows under messaging, and a deliberate
              distinction: everything above delivers *text a human still reads*, while
              these tools act on a session directly. They share the tab because both are
              bounds on what one agent may do to another through swe-mux, and they used
              to share nothing at all - every field here was enforced with no control
              anywhere, reachable only by hand-editing the daemon's config file. */}
          <section><h3 data-heading-message="Settings.14be9614c7">{t("Settings.14be9614c7")}</h3>
          <p>{t("Settings.c4e467b4a3")}</p>
          <label class="check" data-setting="request_spawn_enabled"><span>{t("Settings.dc57c727ca")}</span><input type="checkbox" checked={draft.request_spawn_enabled} onChange={e=>change('request_spawn_enabled',e.currentTarget.checked)} /><small>{t("Settings.370087f10d")}</small></label>
          <label data-setting="agent_spawn_hourly_budget">{rich("Settings.7d318e36e3", { value1: <input type="number" min="0" max="1000" value={draft.agent_spawn_hourly_budget} onInput={e=>change('agent_spawn_hourly_budget',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.5e52f0eb53")}</small> })}</label>
          <label class="check" data-setting="session_control_enabled"><span>{t("Settings.b02851d3ef")}</span><input type="checkbox" checked={draft.session_control_enabled} onChange={e=>change('session_control_enabled',e.currentTarget.checked)} /></label>
          <label data-setting="session_control_hourly_budget">{rich("Settings.eda25c1a19", { value1: <input type="number" min="0" max="1000" value={draft.session_control_hourly_budget} onInput={e=>change('session_control_hourly_budget',Number(e.currentTarget.value))} /> })}</label>
          <label data-setting="session_control_graceful_timeout_s">{rich("Settings.8007aa3154", { value1: <input type="number" min="1" max="120" step="1" value={draft.session_control_graceful_timeout_s} onInput={e=>change('session_control_graceful_timeout_s',Number(e.currentTarget.value))} /> })}</label>
          <label class="check" data-setting="session_watch_enabled"><span>{t("Settings.02b48ef0db")}</span><input type="checkbox" checked={draft.session_watch_enabled} onChange={e=>change('session_watch_enabled',e.currentTarget.checked)} /><small>{t("Settings.37e38a1f6c")}</small></label>
          <label data-setting="session_watch_max_per_session">{rich("Settings.7b7df55100", { value1: <input type="number" min="1" max="100" value={draft.session_watch_max_per_session} onInput={e=>change('session_watch_max_per_session',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.067352455a")}</small> })}</label>
          <label data-setting="session_watch_max_minutes">{rich("Settings.b34b6b45be", { value1: <input type="number" min="1" max="1440" value={draft.session_watch_max_minutes} onInput={e=>change('session_watch_max_minutes',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.203fcc5d83")}</small> })}</label>
          </section>

          <section><h3 data-heading-message="Settings.4a7178cdec">{t("Settings.4a7178cdec")}</h3>
          <p>{rich("Settings.dcaeb311f4", { value1: <em>{t("Settings.62a2fed3d6")}</em> })}</p>
          <label data-setting="prompt_queue_retention_days">{rich("Settings.8047eaa8af", { value1: <input type="number" min="1" max="3650" value={draft.prompt_queue_retention_days} onInput={e=>change('prompt_queue_retention_days',Number(e.currentTarget.value))} /> })}</label>
          </section>
        </Fragment>}

        {/* The OpenRouter key is a provider credential, so it lives with the other
            provider credentials rather than inside the one feature that happened to
            need it first. Everything model-backed depends on it, and the model
            defaults it unlocks are chosen here for the same reason. */}
        {activeTab==='accounts' && <Fragment>
          <AccountSettings/>
          {/* Which endpoint every model-backed feature calls. It sits above the OpenRouter
              key because it decides whether that key is the credential in play at all, and
              a reader who set it to "custom" should not have to scroll past a key section
              that no longer applies to find out why. */}
          <section><h3 data-heading-message="Settings.ea70384c46">{t("Settings.ea70384c46")}</h3>
          <p>{rich("Settings.f67765370d", { value1: <code>{t("Settings.99f784fabe")}</code> })}</p>
            <ProviderConnectionFields draft={draft} onChange={(key,value)=>change(key,value)} apiKey={draft.llm_provider==='custom'?customKey:openRouterKey} onKeyChange={draft.llm_provider==='custom'?setCustomKey:setOpenRouterKey} configured={!!activeEndpoint?.secret.configured}/>
            {/* Where to get the credential belongs beside the field that takes it, and
                only while that endpoint is the one in play - a key link for an endpoint
                nothing routes through is advice that cannot be acted on. Switching the
                dropdown above brings it straight back, because the draft updates without
                a save. */}
            {draft.llm_provider==='openrouter' && <p>{rich("Settings.0c133d40b9", { value1: <a href="https://openrouter.ai/keys" target="_blank" rel="noreferrer">{t("heading.eb70c3bc14")}</a> })}</p>}
            <ProviderReadiness readiness={provider?.llm}/>
            <div class="theme-actions"><button disabled={!(draft.llm_provider==='custom'?customKey:openRouterKey)} onClick={()=>void providerKeyAction('test',draft.llm_provider==='custom'?'custom':'openrouter')}>{t("Settings.e8b793d21d")}</button><button disabled={!(draft.llm_provider==='custom'?customKey:openRouterKey)} onClick={()=>void providerKeyAction('set',draft.llm_provider==='custom'?'custom':'openrouter')}>{t("Settings.4b07dcdbc3")}</button><button disabled={!activeEndpoint?.secret.configured} onClick={()=>void providerKeyAction('clear',draft.llm_provider==='custom'?'custom':'openrouter')}>{t("Settings.b0728b74a6")}</button></div>
            <p aria-live="polite">{providerMessage || t("Settings.1047f5664b")}</p>
            {/* One button per configured provider rather than one for the active one: an
                operator setting up a local endpoint wants to prove it before switching
                everything over to it, and a verify that only worked on the live provider
                would force exactly the risky ordering. */}
            <h4 data-heading-message="Settings.7140f4f19d">{t("Settings.7140f4f19d")}</h4>
          <p>{t("Settings.9c3f3106f6")}</p>
            <ul class="provider-verification-list">
              {(provider?.providers||[]).map(entry=><li key={entry.id}>
                <div class="provider-verification-head">
                  <span class="project-setting-name">{entry.label}{entry.active && <em class="project-setting-chip">{t("Settings.9687961165")}</em>}</span>
                  <VerificationBadge entry={entry}/>
                </div>
                <p class="project-automation-deps">{entry.origin || t("Settings.47ae4fe851")}{entry.model ? t("Settings.1c4e8eeac2", { model: entry.model }) : ''}{entry.requires_verification ? '' : t("Settings.b86532800e")}</p>
                {entry.verification.verified && <p class="project-automation-deps">{rich("Settings.716361bdaa", { value1: entry.verification.sample || t("Settings.00b0257114"), latency_ms: entry.verification.latency_ms, value3: entry.verification.verified_at ? t("Settings.c4ab2248b9", { value1: new Date(entry.verification.verified_at*1000).toLocaleString(uiLocale.value) }) : '' })}</p>}
                {entry.verification.stale && <p class="project-automation-deps">{rich("Settings.0d4f39f698", { value1: <strong>{t("Settings.941b310964")}</strong>, value2: entry.verification.base_url || t("Settings.a75b5f840d"), value3: entry.verification.model ? t("Settings.1c4e8eeac2", { model: entry.verification.model }) : '' })}</p>}
                {/* What the endpoint turned out to be, so an absent picker or absent
                    prices is explainable rather than only visible. Silent until proven:
                    "no catalog" before anyone looked is a guess, not a finding. */}
                {capabilitySummary(entry.verification.capabilities,entry.verification.verified) && <p class="project-automation-deps">{capabilitySummary(entry.verification.capabilities,entry.verification.verified)}</p>}
                <button disabled={verifying===entry.id} onClick={()=>void verifyProvider(entry.id)}>{verifying===entry.id ? t("Settings.63bbd08c91") : t("Settings.c9d293565e", { label: entry.label })}</button>
              </li>)}
            </ul>
            {verifyResult && <div class={verifyResult.ok?'provider-verify-result ok':'provider-verify-result failed'} role="status" aria-live="polite">
              {verifyResult.ok ? <Fragment>
                    <p>{rich("Settings.f1b837c46e", { value1: <strong>{rich("Settings.1c7c902302", { provider: verifyResult.provider })}</strong>, latency_ms: verifyResult.latency_ms, value3: <code>{verifyResult.resolved_model}</code> })}</p>
                    <blockquote>{verifyResult.output || (verifyResult.spent_budget_reasoning ? t("Settings.c9cffa00a7") : t("Settings.dd36815024"))}</blockquote>
                  </Fragment> : <p><strong>{rich("Settings.d383c54bfd", { provider: verifyResult.provider })}</strong> {verifyResult.error}</p>}
            </div>}
            {/* Last, because it tunes an endpoint that already works. Sitting between the
                key fields and Verification, it separated the credential from the thing
                that proves it. */}
            <label data-setting="openrouter_request_timeout_seconds">{rich("Settings.c6cc5ab037", { value1: <input type="number" min="1" max="120" step="1" value={draft.openrouter_request_timeout_seconds} onInput={event=>change('openrouter_request_timeout_seconds',Number(event.currentTarget.value))}/>, value2: <small>{t("Settings.3ef516bbf9")}</small> })}</label>
          </section>

          <section><h3 data-heading-message="Settings.d17d2d78d7">{t("Settings.d17d2d78d7")}</h3>
          <p>{t("Settings.be18367df1")}</p>
            {/* Only truthful while the endpoint is doing no routing of its own. A server
                with no catalog serves one model, so every row resolves to it; one that
                serves a catalog collapses nothing, and saying otherwise would report the
                models you chose as silently replaced when they are not. */}
            {endpointOverride && <p class="settings-warning">{rich("Settings.f62e0ff7ff", { value1: <code>{draft.custom_llm_model || t("Settings.8330949bdc")}</code> })}</p>}
            <div class="theme-actions"><button onClick={()=>void refreshModels()}>{t("Settings.04903063b2")}</button><span>{rich("Settings.62d1788186", { value1: provider?.models.models.length || 0, value2: (provider?.models.stale) ? 'yes' : 'no', value3: provider?.models.error ? t("Settings.f3b99994b4", { error: provider.models.error }) : '' })}</span>{!!provider?.models.fetched_at && <span>{rich("Settings.81cb65d3ec", { value1: new Date(provider.models.fetched_at*1000).toLocaleDateString(uiLocale.value) })}</span>}</div>
            {!catalogKnown && <p><small>{rich("Settings.a09f2441f2", { value1: <em>{t("Settings.d53585ea14")}</em>, value2: <strong>{t("Settings.2cb99db5de")}</strong> })}</small></p>}
            <ModelRoutingSummary draft={draft} catalog={modelOptions(draft.openrouter_cheap_model)} override={endpointOverride} catalogKnown={catalogKnown} onChange={(key,value)=>change(key,value)}/>
          </section>
        </Fragment>}

        {activeTab==='input' && <Fragment>
          <section class="input-settings"><h3 data-heading-message="Settings.d00f1e4dc0">{t("Settings.d00f1e4dc0")}</h3>
          <label class="check"><span>{t("Settings.42bdbfd3e3")}</span><input type="checkbox" checked={draft.middle_click_paste} onChange={e=>change('middle_click_paste',e.currentTarget.checked)} /></label>
          <label class="check"><span>{t("Settings.fc1a062179")}</span><input type="checkbox" checked={draft.broadcast_default} onChange={e=>change('broadcast_default',e.currentTarget.checked)} /></label>
          </section>

          <section class="input-settings"><h3 data-heading-message="Settings.114b9bbcf1">{t("Settings.114b9bbcf1")}</h3>
          <p>{t("Settings.d6fb1faf81")}</p>
          <label>{rich("Settings.b27c4f6ecb", { value1: <Dropdown value={draft.mobile_vertical_drag} onChange={value=>change('mobile_vertical_drag',value as Config['mobile_vertical_drag'])} options={[{value:'smart',get label() { return t("Settings.7e033650c4") }},{value:'terminal',get label() { return t("Settings.0b10fbad3b") }},{value:'application',get label() { return t("Settings.549844eaa2") }},{value:'disabled',get label() { return t("Settings.75081b593d") }}]}/> })}</label>
          <label>{rich("Settings.c8a5697129", { value1: <Dropdown value={draft.mobile_scroll_direction} onChange={value=>change('mobile_scroll_direction',value as Config['mobile_scroll_direction'])} options={[{value:'natural',get label() { return t("Settings.485677d14b") }},{value:'wheel',get label() { return t("Settings.8bcec96308") }}]}/> })}</label>
          <label>{rich("Settings.2d58bfeb5a", { value1: <input type="number" min="0.25" max="4" step="0.25" value={draft.mobile_scroll_sensitivity} onInput={e=>change('mobile_scroll_sensitivity',Number(e.currentTarget.value))} /> })}</label>
          <label>{rich("Settings.2683a40792", { value1: <Dropdown value={draft.mobile_long_press} onChange={value=>change('mobile_long_press',value as Config['mobile_long_press'])} options={[{value:'context_menu',get label() { return t("Settings.7b3336ad1e") }},{value:'disabled',get label() { return t("Settings.75081b593d") }}]}/> })}</label>
          <label class="check"><span>{t("Settings.2d43587ee4")}</span><input type="checkbox" checked={draft.terminal_auto_copy_selection} onChange={e=>change('terminal_auto_copy_selection',e.currentTarget.checked)}/></label>
          </section>

          <section class="input-settings"><h3 data-heading-message="Settings.7ca62c6a45">{t("Settings.7ca62c6a45")}</h3>
          <p>{t("Settings.5540449a20")}</p>
          <label class="check" data-setting="clipboard_history_enabled"><span>{t("Settings.5194a6b4e2")}</span><input type="checkbox" checked={draft.clipboard_history_enabled} onChange={e=>change('clipboard_history_enabled',e.currentTarget.checked)}/></label>
          <label class="check"><span>{t("Settings.2427a2f323")}</span><input type="checkbox" checked={draft.clipboard_history_persist} onChange={e=>change('clipboard_history_persist',e.currentTarget.checked)}/></label>
          <label class="check"><span>{t("Settings.fbd48019c6")}</span><input type="checkbox" checked={draft.clipboard_history_redact_secrets} onChange={e=>change('clipboard_history_redact_secrets',e.currentTarget.checked)}/></label>
          <label>{rich("Settings.f0165ce19b", { value1: <input type="number" min="1" max="2000" value={draft.clipboard_history_limit} onInput={e=>change('clipboard_history_limit',Number(e.currentTarget.value))}/> })}</label>
          <label>{rich("Settings.df8d2cc5b6", { value1: <input type="number" min="0" max="8760" value={draft.clipboard_history_retention_hours} onInput={e=>change('clipboard_history_retention_hours',Number(e.currentTarget.value))}/> })}</label>
          <label>{rich("Settings.976a7c24ce", { value1: <input type="number" min="256" max="1000000" value={draft.clipboard_history_entry_max_chars} onInput={e=>change('clipboard_history_entry_max_chars',Number(e.currentTarget.value))}/> })}</label>
          <p>{t("Settings.2684bc3b5f")}</p>
          </section>

          <section class="input-settings">
          <div class="keybinding-heading"><div><h3 data-heading-message="Settings.f3e334b454">{t("Settings.f3e334b454")}</h3><p>{t("Settings.95490f8640")}</p></div><button onClick={()=>change('mobile_gestures',{...defaultMobileGestureSettings})}>{t("Settings.e9b9eb34c5")}</button></div>
          {GESTURE_SLOTS.map(slot=><label data-settings-search="no-options">{GESTURE_LABELS[slot]}<Dropdown value={draft.mobile_gestures?.[slot]??''} onChange={value=>change('mobile_gestures',{...draft.mobile_gestures,[slot]:value})} options={[{value:'',get label() { return t("Settings.75081b593d") }},...bindingCommands.map(command=>({value:command.id,label:command.label}))]}/></label>)}
          <label class="check"><span>{t("Settings.c724ff46d1")}</span><input type="checkbox" checked={draft.mobile_gesture_swipe_away_close!==false} onChange={e=>change('mobile_gesture_swipe_away_close',e.currentTarget.checked)}/></label>
          <label class="check"><span>{t("Settings.f73a924fb5")}</span><input type="checkbox" checked={draft.mobile_gesture_overlay_back!==false} onChange={e=>change('mobile_gesture_overlay_back',e.currentTarget.checked)}/></label>
          <label class="check"><span>{t("Settings.a638f3a3f6")}</span><input type="checkbox" checked={draft.mobile_surface_gestures!==false} onChange={e=>change('mobile_surface_gestures',e.currentTarget.checked)}/></label>
          <label class="check"><span>{t("Settings.21ff6d239a")}</span><input type="checkbox" checked={draft.mobile_back_view_history!==false} onChange={e=>change('mobile_back_view_history',e.currentTarget.checked)}/></label>
          </section>

          <section class="input-settings">
          {/* The preset picker. Outside the draft/Save cycle deliberately, like
              "restore defaults": applying one rewrites the whole document, and a
              change that large must not arrive as a side effect of an unrelated Save. */}
          <div class="keybinding-heading" data-setting="keymap_preset"><div><h3 data-heading-message="heading.f5281d5e03">{t("heading.f5281d5e03")}</h3>
            <p>{t("Settings.af60b0ae49")}</p></div></div>
          <div class="keymap-presets">
            {keymapPresets.map(preset=><article key={preset.id} data-keymap-preset={preset.id} class={preset.id===keymapPreset?'active':''}>
              <div><strong>{preset.title}</strong>{preset.id===keymapPreset && <span class="keymap-current">{t("Settings.64c6c17ec0")}</span>}
                <small class="keymap-preset-count">{rich("Settings.98969d9219", { bindings: preset.bindings, value2: displayChord(preset.leader,keymapHost.platform), value3: preset.prefix ? t("Settings.c7cd4ea1e2", { value1: displayChord(preset.prefix,keymapHost.platform) }) : '' })}</small></div>
              <p>{preset.description}</p>
              {preset.warning && <p class="keymap-warning">⚠ {preset.warning}</p>}
              <button type="button" class="keymap-preset-apply" disabled={!!presetApplying} onClick={()=>setPresetIntent(preset)}>
                {presetApplying===preset.id ? t("Settings.39c0725ac0") : preset.id===keymapPreset ? t("Settings.909168517f") : t("Settings.46087b34e3")}</button>
            </article>)}
            {keymapPreset==='custom' && <p class="keymap-custom">{t("Settings.7cc962ed72")}</p>}
          </div>
          {presetIntent && <div class="keymap-confirm" role="alertdialog" aria-label={t("Settings.66d2236389", { title: presetIntent.title })}>
            <p>{rich("Settings.3f112e7cd5", { value1: <strong>{rich("Settings.e103549569", { title: presetIntent.title })}</strong> })}</p>
            {presetIntent.warning && <p class="keymap-warning">⚠ {presetIntent.warning}</p>}
            <div><button type="button" onClick={()=>setPresetIntent(null)}>{t("Settings.19766ed6cc")}</button><button type="button" class="primary" onClick={()=>void applyPreset(presetIntent)}>{rich("Settings.0357f241c4", { title: presetIntent.title })}</button></div>
          </div>}

          <div class="keybinding-heading"><div><h3 data-heading-message="heading.e9bef0b0f3">{t("heading.e9bef0b0f3")}</h3>
            <p>{t("Settings.f602db76ef")}</p></div>
            <button onClick={()=>setPresetIntent(keymapPresets.find(preset=>preset.id===(keymapPreset==='custom'?'swemux':keymapPreset))||keymapPresets[0]||null)} disabled={!keymapPresets.length}>{t("Settings.1759c54262")}</button></div>
          {/* Which keyboard the list is describing. Not decoration: a chord that works
              here may not work on the phone reading the same daemon, and the
              undeliverable list below is computed for THIS host only. */}
          <p class="keymap-host">{rich("Settings.9f9b32826e", { value1: <strong>{keymapHost.host==='desktop' ? t("Settings.81b5511666") : t("Settings.51d4ec07e9")}</strong>, value2: <strong>{keymapHost.platform==='mac' ? t("Settings.aed6b7aa2a") : keymapHost.platform==='linux' ? t("Settings.4828e60247") : t("Settings.d598026a9c")}</strong> })}</p>
          {!!keymapWarnings.length && <details class="keymap-undeliverable"><summary>{rich("Settings.9c4e80eb20", { length: keymapWarnings.length, value2: (keymapWarnings.length===1) ? 'yes' : 'no' })}</summary>
            <ul>{keymapWarnings.map(item=><li key={item.keys}><kbd>{displayChord(item.keys,keymapHost.platform)}</kbd> {bindingCommands.find(command=>command.id===item.command)?.label || item.command} — {item.warnings[0]?.message}</li>)}</ul>
          </details>}
          {/* Inert while a chord is being recorded: that recorder listens on the window in
              capture phase and swallows every key, so an enabled box here would eat the
              filter text and bind it. */}
          <input class="keybinding-filter" type="search" value={shortcutQuery} disabled={!!capturingCommand} placeholder={t("Settings.488e04a52e")} aria-label={t("Settings.b3a6e3c215")} autocomplete="off" spellcheck={false} onInput={event=>setShortcutQuery(event.currentTarget.value)}/>
          {capturingCommand && <div class="keybinding-capture" role="status"><span>{t("Settings.750fcf031a")}</span><strong>{bindingCommands.find(command=>command.id===capturingCommand)?.label || capturingCommand}</strong>{!!captureSequence.length && <kbd>{displayChord(captureSequence.join(' '),keymapHost.platform)} …</kbd>}<button onClick={()=>{setCapturingCommand(null);setCaptureSequence([]);setBindingError('')}}>{t("Settings.19766ed6cc")}</button></div>}
          {bindingError && <p class="keybinding-error" role="alert">{bindingError}</p>}
          {/* Not in the panel-wide search: one entry per command per button flooded every
              query that also matched a real setting, and the filter above already searches
              these rows by label, id, and chord. The search hands off to it instead. */}
          <div class="keybinding-list" data-settings-search="skip" hidden={!visibleShortcuts.size}>
            {[...new Set(bindingCommands.map(command=>command.category))].map(category=>{const rows=bindingCommands.filter(command=>command.category===category);return <section class="keybinding-group" aria-label={t("Settings.c98847c2f8", { value1: commandCategoryLabel(category) })} hidden={!rows.some(command=>visibleShortcuts.has(command.id))}><h4>{commandCategoryLabel(category)}</h4>{rows.map(command=>{const chord=bindingForCommand(command.id);return <article class={capturingCommand===command.id?'capturing':''} hidden={!visibleShortcuts.has(command.id)}><button class="keybinding-command" onClick={()=>{setCapturingCommand(command.id);setCaptureSequence([]);setBindingError('')}} title={command.id}><span>{command.label}</span><small>{command.id}</small></button><button class="keybinding-chord" onClick={()=>{setCapturingCommand(command.id);setCaptureSequence([]);setBindingError('')}} aria-label={t("Settings.97c7454bb3", { label: command.label })}><kbd>{chord ? displayChord(chord,keymapHost.platform) : unboundChord()}</kbd></button><button class="keybinding-clear" disabled={!chord} onClick={()=>clearBinding(command.id)} aria-label={t("Settings.954d1afa82", { label: command.label })}>×</button></article>})}</section>})}
          </div>
          {!!shortcutQuery.trim()&&!!bindingCommands.length&&!visibleShortcuts.size && <p class="keybinding-empty" role="status">{rich("Settings.b8f2ddf7c5", { value1: shortcutQuery.trim() })}</p>}
          {bindingPolicy && <KeyboardProbe policy={bindingPolicy} platform={keymapHost.platform}/>}
          {bindingPolicy && <details class="keybinding-policy"><summary>{t("Settings.13d5bc3903")}</summary>
            <ul>{bindingPolicy.rules.map(rule=><li>{rule}</li>)}</ul>
            {/* Split into "you cannot have this" and "you can have it but it costs you
                something", which the old single BROWSER list conflated - and that
                conflation is why Ctrl+F was refused for years while this very panel
                was intercepting it successfully in the same browser. */}
            <div data-settings-search="skip"><strong>{t("Settings.aa96b076ec")}</strong>{bindingPolicy.browser_unreachable.map(chord=><kbd>{displayChord(chord,keymapHost.platform)}</kbd>)}</div>
            <div data-settings-search="skip"><strong>{t("Settings.6f1916567a")}</strong>{Object.entries(bindingPolicy.browser_contested).map(([chord,what])=><kbd title={what}>{displayChord(chord,keymapHost.platform)}</kbd>)}</div>
            <div data-settings-search="skip"><strong>{rich("Settings.89eaedae86", { value1: keymapHost.platform==='mac' ? t("Settings.15eb6ff730") : keymapHost.platform==='linux' ? t("Settings.50f89f1aff") : t("Settings.ab7e5c15a1") })}</strong>{Object.entries(bindingPolicy.wm_reserved[keymapHost.platform]||{}).slice(0,40).map(([chord,what])=><kbd title={what}>{displayChord(chord,keymapHost.platform)}</kbd>)}</div>
            <div data-settings-search="skip"><strong>{t("Settings.9de03c564f")}</strong>{Object.entries(bindingPolicy.terminal_reserved).map(([chord,what])=><kbd title={what}>{displayChord(chord,keymapHost.platform)}</kbd>)}</div>
            <div data-settings-search="skip"><strong>{t("Settings.fbdc728a80")}</strong>{bindingPolicy.application_reserved.map(chord=><kbd>{displayChord(chord,keymapHost.platform)}</kbd>)}</div>
          </details>}
          </section>
        </Fragment>}

        {activeTab==='usage' && <Fragment><section><h3 data-heading-message="Settings.0f35a34d78">{t("Settings.0f35a34d78")}</h3><div class="theme-actions"><button class="primary" onClick={onOpenUsage}>{t("Settings.908e673dbf")}</button><button disabled={!config?.ccusage_enabled || usage?.refreshing || usageRefreshBusy} onClick={()=>void refreshUsage()}>{usageRefreshBusy ? t("Settings.1c0def7be0") : t("Settings.14b793509c")}</button><button disabled={usage?.refreshing || usageRefreshBusy} onClick={()=>void clearUsage()}>{t("Settings.064c8fec1a")}</button></div><label>{rich("Settings.4d78842bab", { value1: <input type="number" min="1" max="3650" value={draft.operational_telemetry_retention_days} onInput={e=>change('operational_telemetry_retention_days',Number(e.currentTarget.value))}/> })}</label><label>{rich("Settings.4fa7547d68", { value1: <input type="number" min="5" max="1440" value={draft.provider_quota_poll_minutes} onInput={e=>change('provider_quota_poll_minutes',Number(e.currentTarget.value))}/> })}</label><label class="check"><span>{t("Settings.748c990f77")}</span><input type="checkbox" checked={draft.provider_quota_turn_refresh_enabled} onChange={e=>change('provider_quota_turn_refresh_enabled',e.currentTarget.checked)}/></label><label>{rich("Settings.9334de77ea", { value1: <input type="number" min="1" max="1440" value={draft.provider_quota_turn_refresh_min_minutes} onInput={e=>change('provider_quota_turn_refresh_min_minutes',Number(e.currentTarget.value))}/>, value2: <small>{t("Settings.a181a7f8e5")}</small> })}</label></section><section><h3 data-heading-message="Settings.9e82f50d8d">{t("Settings.9e82f50d8d")}</h3><p class={usageRefreshFailed?'settings-inline-error':''} aria-live="polite">{usageRefreshMessage || (usage ? t("Settings.7e605ab36f", { id: usage.collector.id, status: codeLabel(usage.collector.status), value3: usage.collector.error?` (${usage.collector.error})`:'' }) : t("Settings.7dc32b8ddf"))}</p>{draft.ccusage_enabled&&!config?.ccusage_enabled && <p>{t("Settings.c7763caabd")}</p>}<label class="check" data-setting="ccusage_enabled"><span>{t("Settings.d2ede78359")}</span><input type="checkbox" checked={draft.ccusage_enabled} onChange={e=>change('ccusage_enabled',e.currentTarget.checked)} /></label><label>{rich("Settings.14256e81c4", { value1: <input type="number" min="0" max="1440" value={draft.ccusage_refresh_minutes} onInput={e=>change('ccusage_refresh_minutes',Number(e.currentTarget.value))} /> })}</label><label>{rich("Settings.1e9226e0ed", { value1: <input readonly value={usage?.install_command||'npm install -g ccusage@latest'} onFocus={event=>event.currentTarget.select()} /> })}</label><button onClick={()=>void navigator.clipboard.writeText(usage?.install_command||'npm install -g ccusage@latest')}>{t("Settings.d8222d3239")}</button><details class="settings-advanced"><summary>{t("Settings.5b4d942d5c")}</summary><label>{rich("Settings.12a3a76172", { value1: <textarea value={draft.usage_command.join('\n')} onInput={e=>change('usage_command',e.currentTarget.value.split('\n').filter(Boolean))} /> })}</label>{Object.entries(draft.usage_commands).map(([name,command])=><label>{rich("Settings.be205f376c", { name: name, value2: <textarea value={command.join('\n')} onInput={e=>change('usage_commands',{...draft.usage_commands,[name]:e.currentTarget.value.split('\n').filter(Boolean)})} /> })}</label>)}</details></section></Fragment>}

        {activeTab==='automation' && <section><h3 data-heading-message="Settings.235b14eb4e">{t("Settings.235b14eb4e")}</h3>
          <p>{t("Settings.60d1ff03f1")}</p>
          <div class="settings-status-grid"><article><strong>{t("Settings.8e75ebbdb2")}</strong><span>{draft.automation_enabled ? t("Settings.1300117561") : t("Settings.ca7981b46e")}</span></article><article><strong>{t("Settings.4228aeb07c")}</strong><span>{rich("Settings.e6f48714bd", { value1: automation?.rules.length || 0 })}</span></article><article><strong>{t("Settings.3b2fe03e36")}</strong><span>{automation?.queue.size || 0}/{automation?.queue.capacity || 0}</span></article><article><strong>{t("Settings.1093115897")}</strong><span>{automation?.diagnostic ? t("Settings.07297fa94a") : t("Settings.7f1e323b62")}</span></article></div>
          <div class="theme-actions"><button class="primary" onClick={onOpenAutomation}>{t("Settings.9e8cab89ac")}</button></div>
        </section>}
        {activeTab==='plugins' && <PluginsSettings/>}

        {activeTab==='notifications' && <NotificationAlertSettings/>}

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
        {activeTab==='voice' && <Fragment>
          <section><h3 data-heading-message="heading.0791ec6847">{t("heading.0791ec6847")}</h3>
          <p aria-live="polite">{rich("Settings.af13d7a94d", { value1: <span class={`state-dot ${voiceInfo?.engine_available?'idle':'running'}`}/>, value2: voiceInfo?.engine || draft.tts_engine, value3: (voiceInfo?.engine_available) ? 'yes' : 'no', value4: voiceInfo?.diagnostic ? t("Settings.2a54e33e60", { diagnostic: voiceInfo.diagnostic }) : '', value5: voiceInfo?.clip_count ?? 0, value6: Math.round((voiceInfo?.cache_bytes||0)/1048576), value7: Math.round((voiceInfo?.cache_limit_bytes||0)/1048576), value8: (voiceInfo?.spend_today.cost_usd||0).toFixed(3) })}</p>
          {onStartVoiceSetup && <div class="settings-tutorial-reset"><div><p>{t("Settings.d85a5650c2")}</p></div><button onClick={()=>{onClose();onStartVoiceSetup()}}>{t("Settings.e7e85b5eb4")}</button></div>}
          {/* Three switches decide whether a word is ever spoken, and they used to sit in
              three unrelated places — a checkbox here, a pane chip there, a button on a
              floating strip — so the honest answer to "why is it talking / why is it
              silent" needed all three. They are one numbered block now: each layer says
              what it governs and where its per-item control lives, in the order the
              question is actually asked. */}
          <div class="policy-stack" data-policy="read-aloud">
            <h4 data-heading-message="Settings.d6b872c698">{t("Settings.d6b872c698")}</h4>
            <div class="policy-layer">
              <span class="policy-step">1</span>
              <div class="policy-body">
                <label class="check" data-setting="tts_enabled"><span>{t("Settings.781d1ae86a")}</span><input type="checkbox" checked={draft.tts_enabled} onChange={e=>change('tts_enabled',e.currentTarget.checked)} /></label>
                <small>{t("Settings.19405ffadd")}</small>
              </div>
            </div>
            <div class={`policy-layer ${draft.tts_enabled?'':'inert'}`}>
              <span class="policy-step">2</span>
              <div class="policy-body">
                <label data-setting="tts_default_mode">{rich("Settings.4e735a3f48", { value1: <Dropdown value={draft.tts_default_mode} onChange={value=>change('tts_default_mode',value as Config['tts_default_mode'])} options={[{value:'off',get label() { return t("Settings.fa188f48b8") }},{value:'on_demand',get label() { return t("Settings.7fb69a3e88") }},{value:'auto',get label() { return t("Settings.76f5031f15") }}]}/> })}</label>
                <small>{t("Settings.9e4eaab4b5")}</small>
              </div>
            </div>
            <div class={`policy-layer ${draft.tts_enabled?'':'inert'}`}>
              <span class="policy-step">3</span>
              <div class="policy-body">
                <label class="check"><span>{t("Settings.e89593e26a")}</span><input type="checkbox" checked={deviceAutoplay} onChange={e=>{const next=e.currentTarget.checked;setAutoplayEnabled(next);setDeviceAutoplay(next)}} /></label>
                <small>{t("Settings.f4cd17345f")}</small>
              </div>
            </div>
          </div>
          </section>

          {/* Provider-specific panels are mutually exclusive in the DOM, while their
              values remain independent fields in the complete Settings draft. */}
          <section><h3 data-heading-message="heading.6491c8fb07">{t("heading.6491c8fb07")}</h3>
          <p>{t("Settings.19ac8ae5f6")}</p>
          <label data-setting="tts_engine">{rich("Settings.bb5a4bd4ec", { value1: <Dropdown value={draft.tts_engine} onChange={value=>change('tts_engine',value as Config['tts_engine'])} options={[{value:'sapi',get label() { return t("Settings.d05bc04945") }},{value:'kokoro',get label() { return t("Settings.2d39191ebd") }},{value:'edge',get label() { return t("Settings.c71cf9b29c") }}]}/> })}</label>
          {/* At selection time, not at synthesis time. The engine's own status
              line sits at the top of this tab among six other facts, and an
              operator who switches provider here and finds the next thing to
              press below it reasonably reads that as the whole errand - which is
              what happened on 2026-08-29. This says, next to the control that
              caused it, that the chosen provider cannot speak yet. */}
          {voiceInfo&&voiceInfo.engine===draft.tts_engine&&!voiceInfo.engine_available&&voiceInfo.providers?.[draft.tts_engine as 'sapi'|'kokoro']?.diagnostic && <p class="tts-lexicon-hint tts-lexicon-error" role="alert">{draft.tts_engine==='kokoro' ? t("Settings.36828f0f9d") : t("Settings.3ad436906d")}: {voiceInfo.providers?.[draft.tts_engine as 'sapi'|'kokoro']?.diagnostic}</p>}
          {draft.tts_engine==='sapi' && <>
            <label>{rich("Settings.115574e8f9", { value1: <input value={draft.tts_sapi_voice} onInput={e=>change('tts_sapi_voice',e.currentTarget.value)} /> })}</label>
            <label>{rich("Settings.032ddce8cf", { value1: <input type="number" min="-10" max="10" value={draft.tts_sapi_rate} onInput={e=>change('tts_sapi_rate',Number(e.currentTarget.value))} /> })}</label>
          </>}
          {draft.tts_engine==='kokoro' && <>
            {/* Fifty-odd chips, each of them worth tapping to audition and none of them
                worth scrolling past every time. Folded, with the current voice named on
                the summary so the closed state still answers which one is selected -
                the same rule the tab's reference sections follow, applied to a control
                that is long rather than rarely read. */}
            <details class="settings-disclosure kokoro-voice-disclosure">
              <summary>{rich("Settings.5130db0ac8", { value1: <em>· {kokoroVoiceLabel(draft.tts_kokoro_voice)}</em> })}</summary>
              <KokoroVoicePicker
                voices={voiceInfo?.kokoro_model?.voices||['af_heart']}
                ready={voiceInfo?.kokoro_model?.status==='ready'}
                selected={draft.tts_kokoro_voice}
                onSelect={voice=>change('tts_kokoro_voice',voice)}
              />
            </details>
            <label>{rich("Settings.d9ba5932b5", { value1: <input type="number" step="0.05" min="0.5" max="2" value={draft.tts_kokoro_speed} onInput={e=>change('tts_kokoro_speed',Number(e.currentTarget.value))} /> })}</label>
            <KokoroModelPanel initial={voiceInfo?.kokoro_model||null}/>
          </>}
          {draft.tts_engine==='edge' && <EdgeTtsSettings value={draft} onChange={(field,value)=>change(field,value as never)}/>}
          </section>

          {/* Kokoro owns the G2P, respellings, and observed unknown-word history.
              Other providers never render or interpret those controls. */}
          {draft.tts_engine==='kokoro' && <section><h3 data-heading-message="heading.75cb1e131c">{t("heading.75cb1e131c")}</h3>
            <details class="settings-disclosure">
              <summary>{rich("Settings.69b09cebd7", { value1: <em>{rich("Settings.f58d2e7a6f", { length: Object.keys(draft.tts_kokoro_lexicon||{}).length, value2: (Object.keys(draft.tts_kokoro_lexicon||{}).length===1) ? 'yes' : 'no' })}</em> })}</summary>
          <p>{rich("Settings.dc1cb7cb60", { value1: <code>{t("Settings.1534201ea0")}</code> })}</p>
              <TtsLexiconEditor lexicon={draft.tts_kokoro_lexicon||{}} spelled={voiceInfo?.kokoro_spelled_words||[]} onChange={next=>change('tts_kokoro_lexicon',next)}/>
            </details>
          </section>}

          {/* What gets spoken, and what it costs. `tts_content` chooses between the two
              halves, so it leads them rather than sitting a heading above. */}
          <section><h3 data-heading-message="heading.aeba66a52f">{t("heading.aeba66a52f")}</h3>
          <p>{t("Settings.ed4c2cfc9a")}</p>
          <label>{rich("Settings.d315703f43", { value1: <Dropdown value={draft.tts_content} onChange={value=>change('tts_content',value as Config['tts_content'])} options={[{value:'summary',get label() { return t("Settings.2cd818b2ca") }},{value:'verbatim',get label() { return t("Settings.eaa72ef65b") }}]}/>, value2: <small>{rich("Settings.46a1b41062", { value1: <code>{t("Settings.317e938a48")}</code> })}</small> })}</label>
          <div class="model-routing-elsewhere" data-setting="tts_summary_model"><span>{t("Settings.e271f60475")}</span><code>{routedModel('tts_summary_model')}</code><button type="button" onClick={()=>goToSetting('accounts','tts_summary_model')}>{t("Settings.4f0c966dec")}</button></div><small>{t("Settings.6ef36ef8ff")}</small>
          <label>{rich("Settings.e2750052b9", { value1: <input type="number" min="64" max="2000" value={draft.tts_summary_max_tokens} onInput={e=>change('tts_summary_max_tokens',Number(e.currentTarget.value))} /> })}</label>
          <BudgetControl name="tts_daily_budget" label={t("Settings.af92137de8")} value={draft.tts_daily_budget} onChange={value=>change('tts_daily_budget',value)} maxTokens={100000000} maxUsd={100} reportsCost={provider?.llm?.reports_cost} unpricedCalls={voiceInfo?.spend_today?.unpriced_calls}/>
          <label>{rich("Settings.d2b83c6da7", { value1: <input type="number" min="200" max="40000" value={draft.tts_verbatim_max_chars} onInput={e=>change('tts_verbatim_max_chars',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.3471d5712e")}</small> })}</label>
          </section>

          <section><h3 data-heading-message="heading.af71af7319">{t("heading.af71af7319")}</h3>
          <label>{rich("Settings.be2caad105", { value1: <input type="number" min="10" max="5000" value={draft.tts_cache_mb} onInput={e=>change('tts_cache_mb',Number(e.currentTarget.value))} />, value2: <small>{t("Settings.9b89889c11")}</small> })}</label>
          </section>

          {/* The whole capture half in one place: the switch, the decoders it runs, and
              the trigger word they are listening for. The wake words used to sit four
              headings below the microphone switch that makes them do anything. */}
          <section><h3 data-heading-message="heading.116fe62640">{t("heading.116fe62640")}</h3>
          <p>{rich("Settings.e658b470c5", { value1: <strong>{t("Settings.fb2b6737ad")}</strong> })}</p>
          <label class="check" data-setting="stt_enabled"><span>{t("Settings.c04a719695")}</span><input type="checkbox" checked={draft.stt_enabled} onChange={e=>change('stt_enabled',e.currentTarget.checked)} /></label>
          {draft.stt_enabled&&draft.stt_engine==='whisper' && <p class="profile-hint">{t("Settings.3c00265dee")}</p>}
          <label>{rich("Settings.3696304b53", { value1: <Dropdown value={draft.stt_engine} onChange={value=>change('stt_engine',value as Config['stt_engine'])} options={[{value:'whisper',get label() { return t("Settings.5fcab2edf5") }},{value:'sapi',get label() { return t("Settings.0457704efb") }}]}/> })}</label>
          <label>{rich("Settings.fb5ced330a", { value1: <input value={draft.stt_language} placeholder={t("Settings.5c49f88daf")} onInput={e=>change('stt_language',e.currentTarget.value)} />, value2: <small>{t("Settings.07aa13c9ee")}</small> })}</label>
          {draft.stt_engine==='whisper' && <label>{rich("Settings.a4bb95a06d", { value1: <input value={draft.stt_whisper_model} placeholder={t("Settings.c3449cd99d")} onInput={e=>change('stt_whisper_model',e.currentTarget.value)} /> })}</label>}
          {draft.stt_engine==='whisper' && <label title={t("Settings.14816a3f03")}>{rich("Settings.2ef3ca0f75", { value1: <input value={draft.stt_routing_model} placeholder={t("Settings.3c5abaac19")} onInput={e=>change('stt_routing_model',e.currentTarget.value)} /> })}</label>}
          <p>{rich("Settings.2fbda81ee0", { value1: (voiceInfo?.stt_available) ? 'yes' : 'no', value2: voiceInfo?.stt_engine || draft.stt_engine, value3: voiceInfo?.stt_diagnostic ? t("Settings.ba9fd14b65", { stt_diagnostic: voiceInfo.stt_diagnostic }) : '' })}</p>
          {draft.stt_engine==='whisper' && <WhisperModelPanel initial={voiceInfo?.stt_models||null} runtime={voiceInfo?.voice_runtime||null}/>}
          <p>{t("Settings.6066bb7be1")}</p>
          <h4 data-heading-message="Settings.f5f0563199">{t("Settings.f5f0563199")}</h4>
          <p>{t("Settings.5394f490cc")}</p>
          <label>{rich("Settings.0724540222", { value1: <input value={(draft.voice_wake_words||[]).join(', ')} placeholder={t("Settings.25064f1cff")} onInput={e=>change('voice_wake_words',e.currentTarget.value.split(',').map(item=>item.trim()).filter(Boolean))} /> })}</label>
          </section>

          {/* The phrase table, on its own. Seventeen rows is the largest single control in
              the tab, and it used to share a heading with the wake words that trigger it
              and with two paragraphs about built-in queries it does not configure. */}
          <section><h3 data-heading-message="heading.7fca441bba">{t("heading.7fca441bba")}</h3>
          <p>{t("Settings.834428db08")}</p>
          {draft.stt_enabled ? <div class="voice-commands">
            {VOICE_ACTION_ORDER.map(action=>{
              const meta=VOICE_ACTION_META[action]
              const phrases=(draft.voice_commands||[]).find(command=>command.action===action)?.phrases||[]
              return <label key={action} title={meta.hint}><span>{meta.label} <em>· {meta.hint}</em></span><input value={phrases.join(', ')} placeholder={t("Settings.5570379899")} onInput={e=>{
                const updated=e.currentTarget.value.split(',').map(item=>item.trim()).filter(Boolean)
                const byAction=new Map(VOICE_ACTION_ORDER.map(name=>[name as string,(draft.voice_commands||[]).find(command=>command.action===name)?.phrases||[]]))
                byAction.set(action,updated)
                change('voice_commands',VOICE_ACTION_ORDER.map(name=>({action:name,phrases:byAction.get(name)||[]})))
              }} /></label>
            })}
          </div> : <div class="settings-capability-flag"><span aria-hidden="true">⚑</span><div><strong>{t("Settings.f1d8e67df7")}</strong><small>{t("Settings.a7c9b74775")}</small></div><button type="button" onClick={()=>change('stt_enabled',true)}>{t("Settings.c04a719695")}</button></div>}
          </section>

          {/* Reference, not a control: the complete live catalog, read once and then rarely.
              Collapsed by default so it stops standing between the phrase table above and
              the assistant below. Nothing inside carries a `data-setting`: the reveal does
              open the disclosures above its target (pinned by `setting-reveal.spec.ts`), but
              a marked control behind a fold is one a search result and a deep link both
              reach through an extra state change, so the marks stay above it. */}
          <section><h3 data-heading-message="heading.64a0c611be">{t("heading.64a0c611be")}</h3>
          {draft.stt_enabled ? <details class="settings-disclosure">
          <summary>{t("Settings.e338cc2329")}</summary>
          <p>{t("Settings.2621c5feb6")}</p>
          <div class="voice-command-reference">
            {completeVoiceCatalog.map((section,index)=><details open={index===0} key={section.id}>
              <summary>{section.title} <span>{section.phrases.length+section.commands.length}</span></summary>
              {!!section.phrases.length && <div class="voice-reference-phrases">{section.phrases.map(phrase=><code key={phrase}>{phrase}</code>)}</div>}
              {!!section.commands.length && <div class="voice-reference-commands">{section.commands.map(command=><article key={command.id}>
                <strong>{command.label}</strong>
                {!command.available && <small>{command.disabledReason || t("Settings.61166d13af")}</small>}
                <div class="voice-reference-phrases">{command.phrases.map(phrase=><code key={phrase}>{phrase}</code>)}</div>
              </article>)}</div>}
            </details>)}
          </div>
          </details> : <p class="settings-muted-reference">{t("Settings.a472da6c22")}</p>}
          </section>

          <section><h3 data-heading-message="heading.0fe741492b">{t("heading.0fe741492b")}</h3>
          <p>{rich("Settings.86df6f4991", { value1: <strong>{t("Settings.3c8fed9d38")}</strong> })}</p>
          <label class="check" data-setting="assistant_enabled"><span>{t("Settings.dff4641557")}</span><input type="checkbox" checked={draft.assistant_enabled} onChange={e=>change('assistant_enabled',e.currentTarget.checked)} /></label>
          {draft.assistant_enabled&&!draft.stt_enabled && <div class="settings-capability-status"><strong>{t("Settings.a105d3011c")}</strong><span>{t("Settings.b8014fea63")}</span></div>}
          <div class="model-routing-elsewhere" data-setting="assistant_model"><span>{t("Settings.7c8dbb91df")}</span><code>{routedModel('assistant_model')}</code><button type="button" onClick={()=>goToSetting('accounts','assistant_model')}>{t("Settings.4f0c966dec")}</button></div><small>{rich("Settings.53e8d5073a", { value1: <code>{t("Settings.f8dbf8d16f")}</code>, value2: <code>{t("Settings.65a8fc1dec")}</code> })}</small>
          <BudgetControl name="assistant_daily_budget" label={t("Settings.9f65f59e1c")} value={draft.assistant_daily_budget} onChange={value=>change('assistant_daily_budget',value)} maxTokens={100000000} maxUsd={1000} usdStep={0.05} reportsCost={provider?.llm?.reports_cost}/>
          <label>{rich("Settings.1ead7b71d8", { value1: <Dropdown value={draft.assistant_trust_reversible} onChange={value=>change('assistant_trust_reversible',value as Config['assistant_trust_reversible'])} options={[{value:'cancel_window',get label() { return t("Settings.89bb11ccf9") }},{value:'confirm',get label() { return t("Settings.d106d42498") }},{value:'auto',get label() { return t("Settings.b16c1f3288") }}]}/>, value2: <small>{t("Settings.b384af6ab0")}</small> })}</label>
          <label class="check" data-setting="assistant_stream_replies"><span>{t("Settings.69c70bc61c")}</span><input type="checkbox" checked={draft.assistant_stream_replies} onChange={e=>change('assistant_stream_replies',e.currentTarget.checked)} /></label>
          <p>{t("Settings.5620ecbaa7")}</p>
          <label>{rich("Settings.36878b3bbe", { value1: <input type="number" min="128" max="8192" value={draft.assistant_max_output_tokens} onInput={e=>change('assistant_max_output_tokens',Number(e.currentTarget.value))} /> })}</label>
          <label>{rich("Settings.3ce9025c78", { value1: <input type="number" min="2" max="200" value={draft.assistant_context_messages} onInput={e=>change('assistant_context_messages',Number(e.currentTarget.value))} /> })}</label>
          <label>{rich("Settings.4e1d76efdb", { value1: <input type="number" min="0" max="5000" step="100" value={draft.voice_chat_patience_ms} disabled={!draft.stt_enabled} onInput={e=>change('voice_chat_patience_ms',Number(e.currentTarget.value))} />, value2: <small>{draft.stt_enabled ? t("Settings.a49f853964") : t("Settings.334d149375")}</small> })}</label>
          </section>

          {/* The two measuring instruments, together and folded away. Neither is a setting:
              the tester scores the wake word against the *saved* configuration, and the
              latency report reads back samples the browser already posted. They are
              reached when a trigger word is being chosen or when spoken commands feel
              slow, which is not most visits to this tab. */}
          <section><h3 data-heading-message="heading.cfc1c5858d">{t("heading.cfc1c5858d")}</h3>
          <details class="settings-disclosure">
          <summary>{t("Settings.24cfd6dc4d")}</summary>
          <h4 data-heading-message="Settings.34e55f5ee9">{t("Settings.34e55f5ee9")}</h4>
          <p>{t("Settings.c376f1d530")}</p>
          <WakeWordTester
            wakeWords={voiceInfo?.wake_words||draft.voice_wake_words||[]}
            commands={voiceInfo?.commands||draft.voice_commands||[]}
            available={!!voiceInfo?.stt_available}
            diagnostic={voiceInfo?.stt_diagnostic||''}
          />
          <h4 data-heading-message="Settings.a32a4a4e02">{t("Settings.a32a4a4e02")}</h4>
          <p>{rich("Settings.a3399ffb60", { value1: <code>{t("Settings.6131960bd0")}</code> })}</p>
          <VoiceLatencyReport report={latencyReport} onRefresh={loadLatency} onReset={resetLatency} />
          </details>
          </section>

          {/* One-time setup, and a deliberate second copy of what Remote owns: someone
              setting up dictation should not have to leave this tab (`features/ui.md`).
              Folded away because it is done once and then never again. */}
          <section><h3 data-heading-message="heading.732d8524f8">{t("heading.732d8524f8")}</h3>
          <details class="settings-disclosure">
          <summary>{t("Settings.93f3d2f9db")}</summary>
          <p>{t("Settings.b8afb6e9c9")}</p>
          <TailscaleConnection status={remote} />
          <div class="theme-actions"><button class="primary" disabled={mobileVoiceBusy||!draft.tailnet_enabled} onClick={()=>void setupMobileVoice()}>{mobileVoiceBusy ? t("Settings.c894bba9c3") : remote?.mobile_voice_configured ? t("Settings.0e7a37d085") : t("Settings.b63d1b949e")}</button>{remote?.mobile_voice_url && <a href={remote.mobile_voice_url} target="_blank" rel="noreferrer">{t("Settings.17833695a6")}</a>}</div>
          {mobileVoiceMessage && <p class={mobileVoiceFailed?'settings-inline-error':''} aria-live="polite">{mobileVoiceMessage}</p>}
          <PhoneDnsChecklist />
          </details>
          </section>
        </Fragment>}

        {activeTab==='remote' && <Fragment>
          <section><h3 data-heading-message="Settings.668c7def66">{t("Settings.668c7def66")}</h3>
          <p class="settings-inline-error">{t("Settings.aba207e063")}</p>
          <label class="check"><span>{t("Settings.b87f5d702f")}</span><input type="checkbox" checked={draft.tailnet_enabled} onChange={event=>change('tailnet_enabled',event.currentTarget.checked)} /><small>{t("Settings.6d171eab20")}</small></label>
          <TailscaleConnection status={remote} />
          <dl><dt>{t("Settings.58b471eed4")}</dt><dd>{remote?.listen_url || t("Settings.77f91a235f", { host: draft.host, port: draft.port })}</dd><dt>{t("Settings.ec575b1542")}</dt><dd>{remote?.direct_available ? t("Settings.9687961165") : draft.tailnet_enabled ? t("Settings.0251499a6e") : t("Settings.17eb3c0168")}</dd>{remote?.tailnet_urls.map(url=><Fragment key={url}><dt>{t("Settings.3170afddf2")}</dt><dd><a href={url} target="_blank" rel="noreferrer">{url}</a></dd></Fragment>)}</dl>
          <p>{t("Settings.88f432459c")}</p>
          </section>

          <section><h3 data-heading-message="Settings.8b4df73a36">{t("Settings.8b4df73a36")}</h3>
          <p>{t("Settings.a6dae8d816")}</p>
          <div class="theme-actions"><button onClick={()=>setConnectPhoneOpen(true)}>{t("Settings.5ea4f31d60")}</button></div>
          {connectPhoneOpen && <ConnectPhone onClose={()=>setConnectPhoneOpen(false)} />}
          </section>

          {/* Both panels render nothing on a host that does not support them. Now that
              each owns a heading, that would leave a heading promising content that is
              not there, so the unsupported case says so instead of going quiet. */}
          <section><h3 data-heading-message="Settings.f12efc4df2">{t("Settings.f12efc4df2")}</h3>
          {firewall?.supported ? <FirewallPanel status={firewall} busy={firewallBusy} message={firewallMessage} onRepair={()=>void repairFirewall()} /> : <p>{t("Settings.62ae343f37")}</p>}
          </section>

          <section><h3 data-heading-message="Settings.2b925074f3">{t("Settings.2b925074f3")}</h3>
          {wsl?.supported ? <WslBridgePanel status={wsl} busy={wslBusy} message={wslMessage} probing={wslProbing}
              onToggle={enabled=>void toggleWsl(enabled)} onProbe={()=>void probeWsl()}
              onInstall={distro=>void installWslBridge(distro)} onRepairFirewall={()=>void repairWslFirewall()} /> : <p>{t("Settings.e2f251c813")}</p>}
          </section>

          <section><h3 data-heading-message="Settings.88da55bde5">{t("Settings.88da55bde5")}</h3>
          <p>{rich("Settings.24cb39057d", { value1: remote?.diagnostic || t("Settings.118602dd87") })}</p>
          {remote?.funnel_detected && <p class="settings-inline-error">{t("Settings.d254e935d9")}</p>}
          <div class="theme-actions"><button class="primary" disabled={mobileVoiceBusy||!draft.tailnet_enabled} onClick={()=>void setupMobileVoice()}>{mobileVoiceBusy ? t("Settings.c894bba9c3") : remote?.mobile_voice_configured ? t("Settings.f46198787f") : t("Settings.51523c1565")}</button>{remote?.mobile_voice_url && <a href={remote.mobile_voice_url} target="_blank" rel="noreferrer">{t("Settings.6d13a49e47")}</a>}<button onClick={()=>{void api<RemoteStatus>('GET','/api/remote/status').then(setRemote);void api<FirewallStatus>('GET','/api/remote/firewall').then(setFirewall)}}>{t("Settings.1f47d83669")}</button></div>
          {mobileVoiceMessage && <p class={mobileVoiceFailed?'settings-inline-error':''} aria-live="polite">{mobileVoiceMessage}</p>}
          <p>{t("Settings.4e3273f678")}</p>
          </section>

          <section><h3 data-heading-message="Settings.131638b04e">{t("Settings.131638b04e")}</h3>
          <PhoneDnsChecklist />
          </section>
        </Fragment>}

        {/* Support tooling, not remote configuration: what the host is missing, how to
            push new code into the running app, and one bundle to hand over when
            something is wrong. */}
        {activeTab==='maintenance' && <Fragment>
          {/* First on the tab, because "which build am I on" is the question every
              other answer here is qualified by: a prerequisite report, a log level,
              and an exported bundle all describe a specific version, and the one a
              reader is standing in front of is the fact they came to check after
              pressing Install or redeploying. The running version is stated
              outright rather than left to be inferred from "This is the latest
              release", which says nothing while the check is off and reads the same
              on a daemon whose check cannot reach the manifest. */}
          <section><h3 data-heading-message="Settings.a7e863241f">{t("Settings.a7e863241f")}</h3>
          <p class="settings-running-version">{rich("Settings.3ab6ea948b", { value1: <strong>{rich("Settings.b277ccf9f1", { value1: runningVersion || t("Settings.a00d58b4a7") })}</strong>, value2: (runningVersion&&updateStatus?.status==='ok'&&!updateStatus.update_available) ? 'yes' : 'no' })}</p>
          <p>{rich("Settings.9f996710ad", { value1: <code>{updateStatus?.manifest_url || t("Settings.a2711588a8")}</code>, value2: <strong>{t("Settings.585dd00552")}</strong> })}</p>
          <p class="profile-hint">{t("Settings.029a85cb06")}</p>
          <label class="check" data-setting="update_check_enabled"><span>{t("Settings.4a99175ce6")}</span><input type="checkbox" checked={draft.update_check_enabled} onChange={e=>change('update_check_enabled',e.currentTarget.checked)}/><small>{t("Settings.bad8675c88")}</small></label>
          <div class="settings-config-actions"><div>
            <p>{updateSummary || ' '}</p>
            {updateChecked && <p class="profile-hint">{rich("Settings.ec3c366cff", { updateChecked: updateChecked })}</p>}
            {updateStatus?.latest?.changelog&&updateStatus.update_available ? <p><a href={updateStatus.latest.changelog} target="_blank" rel="noreferrer">{rich("Settings.6c1357ac49", { version: updateStatus.latest.version })}</a></p> : null}
          </div>
            <div class="settings-update-actions">
              <button disabled={updateBusy||!draft.update_check_enabled} onClick={()=>void checkForUpdates()}>{updateBusy ? t("Settings.ec963ffc91") : t("Settings.2937cffb37")}</button>
              {updateStatus?.update_available&&updateStatus.latest?.version && <button class="primary" onClick={()=>setUpdateInstallOpen(true)}>{rich("Settings.b69eb65b13", { version: updateStatus.latest.version })}</button>}
            </div>
          </div>
          {updateError && <p aria-live="polite">{updateError}</p>}
          {updateInstallOpen&&updateStatus?.latest?.version && <UpdateDialog version={updateStatus.latest.version} changelog={updateStatus.latest.changelog} onClose={()=>setUpdateInstallOpen(false)}/>}
          </section>

          <section><h3 data-heading-message="Settings.ae763e6d95">{t("Settings.ae763e6d95")}</h3>
          <p class="profile-hint">{t("Settings.74c8a0ca0b")}</p>
          {/* Re-scan is a POST, not a re-fetch: the daemon inherited its PATH once
              at startup, so a tool installed while swe-mux was running stays
              invisible until PATH is read back from the OS. A button that only
              re-ran detection would have returned the same wrong answer and taught
              the user that the feature does not work. */}
          <div class="settings-config-actions"><div>
            <button type="button" disabled={prereqBusy} onClick={()=>void rescanPrerequisites()}>{prereqBusy ? t("Settings.712ba0b969") : t("Settings.59b7d55a69")}</button>
            {prereqRescan && <small>{prereqRescan.path_refreshed ? t("Settings.52af894101") : prereqRescan.path_refresh_supported ? t("Settings.4d345465e9") : t("Settings.71d4a94286")}</small>}
          </div></div>
          {prerequisites ? <div class="settings-prerequisites"><ul>{prerequisites.map(prereq=><li key={prereq.id} class={prereq.state==='present'?'prereq-ok':prereq.state==='off_path'?'prereq-warn':'prereq-missing'}>
              <span>{prereq.state==='present' ? '✓' : prereq.state==='off_path' ? '!' : '✗'} {prereq.label}</span>
              <small>{prereq.purpose}{prereq.present&&prereq.path ? t("Settings.2d31151b5f", { path: prereq.path }) : ''}</small>
              {/* Three states, three sentences. Telling someone to install what
                  they already have is the failure this replaced. */}
              {prereq.state==='off_path' && <small class="settings-inline-error">{prereq.path_remedy}</small>}
              {prereq.state==='missing' && <small><code>{prereq.install_command}</code> · <a href={prereq.download_url} target="_blank" rel="noreferrer">{t("Settings.68ff63fb82")}</a></small>}
              <label class="check"><span>{t("Settings.94c01d6425")}</span><input type="text" placeholder={t("Settings.a4a5659f66")} value={draft.tool_paths?.[prereq.id]??''} onChange={e=>setToolPath(prereq.id,e.currentTarget.value)} /><small>{t("Settings.a485cc349e")}</small></label>
            </li>)}</ul></div> : <p>{t("Settings.5cdbf06792")}</p>}
          </section>

          {/* The cheap version of a whole-app update, which is why it sits between
              the version section and Rebuild and reload: the static
              tree is a few megabytes of the ~370 MB a bundle swap rewrites.
              Hidden entirely until an overlay exists - an empty panel explaining a
              mechanism nobody is using is noise. */}
          {shouldShowOverlaySection(overlayStatus) && <section data-section="frontend-overlay"><h3 data-heading-message="Settings.d77f40d58d">{t("Settings.d77f40d58d")}</h3>
          <p>{t("Settings.c1817945a6")}</p>
          <p class={overlayStatus?.serving?.faulted?'settings-inline-error':''} aria-live="polite">{overlaySummary || ' '}</p>
          {overlayInstalled && <p class="profile-hint">{rich("Settings.2ac78ac747", { overlayInstalled: overlayInstalled, value2: overlayStatus?.state?.requires_backend ? t("Settings.25d91a5442", { requires_backend: overlayStatus.state.requires_backend }) : '' })}</p>}
          {overlayStatus?.state?.installed_from && <p class="profile-hint">{rich("Settings.3079da5837", { value1: <code>{overlayStatus.state.installed_from}</code> })}</p>}
          <label class="check" data-setting="frontend_overlay_enabled"><span>{t("Settings.74a527cd05")}</span><input type="checkbox" checked={draft.frontend_overlay_enabled} onChange={e=>change('frontend_overlay_enabled',e.currentTarget.checked)}/><small>{t("Settings.0e159d5ea7")}</small></label>
          <div class="settings-config-actions"><div>
            <p class="profile-hint">{t("Settings.29c23dbb5c")}</p>
          </div>
            <div>
              {overlayStatus?.active ? <button disabled={overlayBusy} onClick={()=>void overlayPress(revertOverlay)}>{overlayBusy ? t("Settings.5474eef8d0") : t("Settings.66f746259c")}</button> : <button disabled={overlayBusy||!overlayStatus?.can_restore} onClick={()=>void overlayPress(restoreOverlay)}>{overlayBusy ? t("Settings.5474eef8d0") : t("Settings.dab6a71282")}</button>}
              <button disabled={!appCommand('daemon.reload')} onClick={()=>runAppCommand('daemon.reload')}>{t("Settings.b12a0f8d97")}</button>
            </div>
          </div>
          {overlayError && <p class="settings-inline-error" aria-live="polite">{overlayError}</p>}
          </section>}

          {/* The same three commands the app menu carries, reachable from Settings
              because this is where you already are when a change has not appeared.
              Each is session-preserving: none of them reaps a live agent or terminal. */}
          <section><h3 data-heading-message="Settings.6e3a8d0788">{t("Settings.6e3a8d0788")}</h3>
          <div class="settings-config-actions"><div><p>{rich("Settings.3b08d15313", { value1: <strong>{t("Settings.e71a8b379c")}</strong>, value2: <strong>{t("Settings.6e1cbd000b")}</strong>, value3: <strong>{t("Settings.9561c11798")}</strong> })}</p></div>
            <div>
              <button disabled={!appCommand('ui.reload')} onClick={()=>runAppCommand('ui.reload')}>{t("Settings.e71a8b379c")}</button>
              <button disabled={!appCommand('daemon.reload')} onClick={()=>runAppCommand('daemon.reload')}>{t("Settings.b12a0f8d97")}</button>
              <button disabled={!appCommand('app.redeploy')} onClick={()=>runAppCommand('app.redeploy')}>{t("Settings.84ab91dbd0")}</button>
            </div>
          </div>
          </section>

          {/* Beside the bundle rather than under Processes: this is what decides how
              much the log that ends up in that bundle actually says. */}
          <section><h3 data-heading-message="Settings.d3ef01b4a9">{t("Settings.d3ef01b4a9")}</h3>
          <p>{rich("Settings.c076417552", { value1: <code>{t("Settings.0ffd43f588")}</code> })}</p>
          <label data-setting="log_level">{rich("Settings.0898c2473b", { value1: <Dropdown value={draft.log_level} onChange={value=>change('log_level',value as Config['log_level'])} options={[
            {value:'DEBUG',get label() { return t("Settings.914c586e27") }},
            {value:'INFO',get label() { return t("Settings.221cb5a321") }},
            {value:'WARNING',get label() { return t("Settings.86f56fe822") }},
            {value:'ERROR',get label() { return t("Settings.d98ee0e5f9") }},
          ]}/>, value2: <small>{rich("Settings.73721ce857", { value1: <code>{t("Settings.0ffd43f588")}</code> })}</small> })}</label>
          </section>

          {/* Above Export diagnostics, because it is the same errand one step
              earlier: "collect a bundle" is what you do when you are going to ask
              somebody, and this is asking somebody. */}
          {onLaunchConfigurator && <section><h3 data-heading-message="Settings.9e252559e7">{t("Settings.9e252559e7")}</h3>
          <p>{t("Settings.e608f47623")}</p>
          <div class="theme-actions"><button class="primary" onClick={()=>{onClose();onLaunchConfigurator()}}>{t("Settings.fca2999d30")}</button></div>
          <p class="profile-hint">{t("Settings.4ec9cb75dd")}</p>
          </section>}

          <section><h3 data-heading-message="Settings.b4205182f0">{t("Settings.b4205182f0")}</h3>
          <p>{t("Settings.d5c754ab15")}</p>
          <div class="theme-actions"><button class="primary" disabled={diagnosticsBusy} onClick={()=>void exportDiagnostics()}>{diagnosticsBusy ? t("Settings.3c0e2e7dd7") : t("Settings.b4205182f0")}</button></div>
          {diagnosticsMessage && <p aria-live="polite">{diagnosticsMessage}</p>}
          {diagnosticsText && <label>{rich("Settings.08b8bd7d04", { value1: <textarea readOnly rows={10} value={diagnosticsText} onClick={event=>event.currentTarget.select()} /> })}</label>}
          </section>

          {/* Last on the tab, and the only control here that destroys anything.
              Everything above it is a way to understand or repair this install;
              this is the way to stop having it. The dialog does the explaining -
              this section is deliberately short, because a wall of warnings
              beside the button is read as decoration. */}
          <section data-section="factory-reset"><h3 data-heading-message="Settings.46a0b85a51">{t("Settings.46a0b85a51")}</h3>
          <p>{t("Settings.4b8c2a87cf")}</p>
          <p class="profile-hint">{rich("Settings.cf6794d265", { value1: <code>{t("Settings.e46d8b606c")}</code>, value2: <strong>{t("Settings.30fc04c0e1")}</strong> })}</p>
          <div class="theme-actions"><button class="danger" onClick={()=>setFactoryResetOpen(true)}>{t("Settings.8f6bc9df7a")}</button></div>
          </section>
          {factoryResetOpen && <FactoryResetDialog onClose={()=>setFactoryResetOpen(false)}/>}
        </Fragment>}

        {activeTab==='appearance' && <Fragment><DeviceModeSetting /><section><h3 data-heading-message="heading.efb52e7172">{t("heading.efb52e7172")}</h3>
          <div class="theme-field">
            <span>{t("heading.efb52e7172")}</span>
            <ThemePicker value={draft.theme} customTheme={draft.custom_theme} open={themePickerOpen} onOpenChange={setThemePickerOpen} onChange={value=>{change('theme',value);applyTheme(value)}} onPreview={previewTheme} />
          </div>
          {draft.theme==='custom' && <div class="theme-tokens">{Object.entries(draft.custom_theme).map(([key,value])=><label>{key}<input value={value} onInput={e=>{const custom={...draft.custom_theme,[key]:e.currentTarget.value};change('custom_theme',custom);configureCustomTheme(custom);applyTheme('custom')}} /></label>)}</div>}
          <input class="file-input" ref={themeFile} type="file" accept="application/json" onChange={e=>void importTheme(e.currentTarget.files?.[0])} />
          <div class="theme-actions"><button onClick={()=>themeFile.current?.click()}>{t("Settings.f9cc639285")}</button><button onClick={exportTheme}>{t("Settings.575ade1ff4")}</button></div>
          <p>{t("Settings.bcb3a1163c")}</p>
          </section><section><h3 data-heading-message="heading.c70da52b01">{t("heading.c70da52b01")}</h3>
          <label data-setting="terminal_font_family">{rich("Settings.6d0716a233", { value1: <input value={draft.terminal_font_family} maxLength={TERMINAL_FONT_FAMILY_MAX_CHARS} placeholder={t("Settings.227958e927", { value1: DEFAULT_TERMINAL_FONT_FAMILIES.join(', ') })} spellcheck={false} onInput={e=>change('terminal_font_family',e.currentTarget.value)}/> })}</label>
          <TerminalFontAvailability family={draft.terminal_font_family} />
          <p>{rich("Settings.cd14ff0297", { value1: <code>{t("Settings.56d61d7bc8")}</code> })}</p>
          </section><section><SessionRowSettings /></section><section><SessionTopbarSettings /></section><section>
          <h3 data-heading-message="heading.14721e2ef2">{t("heading.14721e2ef2")}</h3>
          <label>{rich("Settings.5234b067b5", { value1: <Dropdown value={draft.drawer_tab_display} onChange={value=>change('drawer_tab_display',value as Config['drawer_tab_display'])} options={[{value:'icon',get label() { return t("Settings.eae96e02bb") }},{value:'title',get label() { return t("Settings.3b23a123fa") }}]}/> })}</label>
          <label>{rich("Settings.8081e3347f", { value1: <Dropdown value={draft.utility_rail_display} onChange={value=>change('utility_rail_display',value as Config['utility_rail_display'])} options={[{value:'icon',get label() { return t("Settings.eae96e02bb") }},{value:'title',get label() { return t("Settings.3b23a123fa") }}]}/> })}</label>
          <div class="drawer-tab-visibility" role="group" aria-label={t("Settings.bbd666e760")}>{DRAWER_TABS.map(tab=>{
            const shown=!drawerHiddenTabs.includes(tab.id)
            const blocked=shown&&!canHideDrawerTab(drawerHiddenTabs,tab.id)
            return <label key={tab.id} title={blocked ? t("Settings.f7f20aa575") : tab.title}>
              <input type="checkbox" checked={shown} disabled={blocked||!onDrawerTabHidden} onChange={()=>onDrawerTabHidden?.(tab.id,shown)} />
              <span>{tab.label}</span>
            </label>
          })}</div>
          <div class="theme-actions"><button disabled={!drawerHiddenTabs.length||!onShowAllDrawerTabs} onClick={()=>onShowAllDrawerTabs?.()}>{t("Settings.421d20a7d2")}</button></div>
          <p>{t("Settings.2c9f742f6e")}</p>
          </section><section><h3 data-heading-message="heading.6ba5218d81">{t("heading.6ba5218d81")}</h3>
          <label>{rich("Settings.3ac388f04e", { value1: <Dropdown value={String(draft.ui_scale_desktop)} onChange={value=>changeUiScale('ui_scale_desktop',value)} options={UI_SCALE_STEPS.map(step=>({value:String(step),label:uiScaleLabel(step)}))}/> })}</label>
          <label>{rich("Settings.f0b81573e6", { value1: <Dropdown value={String(draft.ui_scale_mobile)} onChange={value=>changeUiScale('ui_scale_mobile',value)} options={UI_SCALE_STEPS.map(step=>({value:String(step),label:uiScaleLabel(step)}))}/> })}</label>
          <p class="settings-scale-active">{rich("Settings.30005ce0e1", { value1: <strong>{currentProfile()==='mobile' ? t("Settings.d524c1a081") : t("Settings.68693d02ab")}</strong> })}</p>
          <p>{rich("Settings.46f97430b4", { value1: <kbd>{t("Settings.e2ee2909fe")}</kbd>, value2: <kbd>{t("Settings.e2ee2909fe")}</kbd>, value3: <kbd>+</kbd>, value4: <kbd>-</kbd>, value5: <kbd>{t("Settings.e2ee2909fe")}</kbd>, value6: <kbd>0</kbd>, value7: <strong>{t("heading.c70da52b01")}</strong>, value8: <strong>{t("Settings.8a7525b149")}</strong> })}</p>
          </section></Fragment>}

        {activeTab==='actions' && <Fragment><section><h3 data-heading-message="Settings.f73cf84043">{t("Settings.f73cf84043")}</h3>
          <label class="check" data-setting="rail_enabled_desktop"><span>{t("Settings.e04b865f75")}</span><input type="checkbox" checked={draft.rail_enabled_desktop} onChange={e=>change('rail_enabled_desktop',e.currentTarget.checked)} /></label>
          <label class="check" data-setting="rail_enabled_mobile"><span>{t("Settings.38748adfc7")}</span><input type="checkbox" checked={draft.rail_enabled_mobile} onChange={e=>change('rail_enabled_mobile',e.currentTarget.checked)} /></label>
          <p>{t("Settings.d8236f10f4")}</p>
          <label class="check" data-setting="rail_hover_desktop"><span>{t("Settings.641b0f9dca")}</span><input type="checkbox" checked={draft.rail_hover_desktop} disabled={!draft.rail_enabled_desktop} onChange={e=>change('rail_hover_desktop',e.currentTarget.checked)} /></label>
          <p>{t("Settings.1d11026ad3")}</p>
          <label data-setting="rail_density_desktop">{rich("Settings.657b201d12", { value1: <Dropdown value={draft.rail_density_desktop} onChange={value=>changeRailDensity('rail_density_desktop',value)} options={RAIL_DENSITIES.map(step=>({value:step,label:railDensityLabel(step)}))}/> })}</label>
          <label data-setting="rail_density_mobile">{rich("Settings.9b8bca40cf", { value1: <Dropdown value={draft.rail_density_mobile} onChange={value=>changeRailDensity('rail_density_mobile',value)} options={RAIL_DENSITIES.map(step=>({value:step,label:railDensityLabel(step)}))}/> })}</label>
          <p>{t("Settings.24a536f5b0")}</p></section><section><h3 data-heading-message="Settings.36c88395a3">{t("Settings.36c88395a3")}</h3><ActionEditorPanel projectId={focusedProjectId}/></section></Fragment>}
  </Fragment>

  // Built here rather than beside the sidebar because it needs `tabContent`, and built
  // once because it is structure: a tab's headings are the same on the render that
  // opened the panel and the one after a checkbox moved. Each tab is built separately
  // so a latent throw in one costs that tab's chevron rather than the whole panel,
  // which is the rule the search index already follows for the same call.
  //
  // Measured in the renderer harness over five opens: 6.5ms on the first (cold) build
  // and 1.1-2.4ms after, for thirteen tabs. The five paged tabs are skipped and they
  // are the two largest — Voice's command reference and Accounts' model routing — so
  // the preview costs less than a search does for the same reason it is affordable
  // at all.
  const localizationKey=uiLocale.value+':'+catalogRevision.value
  if(localizationCache.current!==localizationKey){
    localizationCache.current=localizationKey
    railPreview.current=null
    searchIndex.current=null
    liveTabEntries.clear()
  }
  if(!railPreview.current){
    const preview=new Map<SettingsTab,SettingsRailSection[]>()
    for(const tab of settingsTabs){
      if(settingsSubpages[tab.id])continue
      try{const content=tabContent(tab.id);preview.set(tab.id,railSectionIds(harvestHeadings(content),harvestHeadings(content,'h3',true)))}
      catch{preview.set(tab.id,[])}
    }
    railPreview.current=preview
  }

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
    if(mounted?.length&&contentEl()?.dataset.uiLocale===uiLocale.value){
      const index=settingsTabs.findIndex(tab=>tab.id===activeTab)
      const label=settingsTabs[index]?.label||activeTab
      liveTabEntries.set(activeTab,[...mounted].flatMap(node=>harvestSettings(domVNode(node),activeTab,label,index)))
    }
    // Building a tab's vnodes evaluates the expressions inside its JSX, and those
    // now run for tabs nobody opened. A latent throw in one tab must cost that
    // tab's entries, not the whole panel — this runs during render.
    searchIndex.current={source:draft,entries:settingsSearchEntries((tab,label,index)=>harvestSettings(tabContent(tab),tab,label,index))}
  }
  wasSearching.current=searching
  const searchResults=searching?searchSettings(searchIndex.current?.entries||[],query):[]
  // Keyboard shortcuts are not in the index (see `.keybinding-list`), so a query that
  // names some is answered with one row that opens their own filter already holding it,
  // instead of a row per command crowding out the settings the query also matched.
  const shortcutHits=searching?bindingCommands.filter(command=>shortcutMatches(command,commandChords.get(command.id),query,keymapHost.platform)).length:0
  const resultCount=searchResults.length+(shortcutHits?1:0)
  const activeResult=Math.min(highlight,Math.max(0,resultCount-1))
  const openResult=(entry:SettingsSearchEntry)=>{
    selectTab(entry.tab as SettingsTab)
    setJump({entry})
    setQuery('')
    setHighlight(0)
    // A result can sit under a shortcut row the filter is hiding; the filter yields.
    setShortcutQuery('')
  }
  const openShortcuts=()=>{
    const shortcuts=searchIndex.current?.entries.find(entry=>entry.tab==='input'&&entry.kind==='section'&&entry.key==='keyboard shortcuts')
    const inputIndex=settingsTabs.findIndex(tab=>tab.id==='input')
    const heading=shortcuts||{...tabEntry('input','Input',inputIndex),get label() { return t("heading.e9bef0b0f3") },key:'keyboard shortcuts'}
    const filter=query.trim()
    openResult(heading)
    setShortcutQuery(filter)
  }
  const activate=(index:number)=>{
    if(index<searchResults.length)openResult(searchResults[index])
    else if(shortcutHits)openShortcuts()
  }
  const onSearchKey=(event:{key:string;preventDefault:()=>void})=>{
    if(!resultCount)return
    if(event.key==='ArrowDown'){event.preventDefault();setHighlight((activeResult+1)%resultCount)}
    else if(event.key==='ArrowUp'){event.preventDefault();setHighlight((activeResult-1+resultCount)%resultCount)}
    else if(event.key==='Enter'){event.preventDefault();activate(activeResult)}
  }
  // One search box, drawn in the place each layout reads first: inline in the header on
  // a phone, above the section list on a desktop — beside the navigation it drives.
  const searchBox = <div class="settings-search">
    <input ref={searchInput} type="search" value={query} placeholder={t("Settings.57054c5a04")} aria-label={t("Settings.255b076dd8")} role="combobox" aria-expanded={resultCount>0} aria-controls="settings-search-results" autocomplete="off" spellcheck={false} onInput={event=>{setQuery(event.currentTarget.value);setHighlight(0)}} onKeyDown={onSearchKey} />
    {!!query.trim() && <div id="settings-search-results" class="settings-search-results" role="listbox" aria-label={t("Settings.e978b00de4")}>
      {searchResults.map((entry,index)=><button type="button" role="option" aria-selected={index===activeResult} class={index===activeResult?'active':''} key={`${entry.tab}:${entry.kind}:${entry.key}:${entry.occurrence}`} onPointerDown={event=>event.preventDefault()} onClick={()=>openResult(entry)}><strong>{entry.label}</strong><small>{settingsBreadcrumb(entry.tab as SettingsTab,entry.tabLabel,entry.path)}</small></button>)}
      {!!shortcutHits && <button type="button" role="option" aria-selected={activeResult===searchResults.length} class={`settings-search-handoff${activeResult===searchResults.length?' active':''}`} key="keyboard-shortcuts" onPointerDown={event=>event.preventDefault()} onClick={openShortcuts}><strong>{rich("Settings.52c56456c4", { value1: query.trim() })}</strong><small>{rich("Settings.6309326206", { shortcutHits: shortcutHits, value2: (shortcutHits===1) ? 'yes' : 'no' })}</small></button>}
      {!resultCount && <p>{rich("Settings.6422f95e3b", { value1: query.trim() })}</p>}
    </div>}
  </div>
  return <div class="settings-layer" onMouseDown={event=>event.target===event.currentTarget&&requestClose()}><section class="settings-panel" ref={panel} role="dialog" aria-modal={!closeIntent&&!resetIntent} aria-hidden={Boolean(closeIntent||resetIntent)} aria-label={t("Settings.74a883a037")}>
    <header>{navTrigger}{heading}
      {narrow && searchBox}
      <button class="settings-close" aria-label={t("Settings.2da15e4dad")} onClick={()=>requestClose()}>×</button></header>
    {!!query.trim() && <div class="settings-search-scrim" onPointerDown={()=>setQuery('')} />}
    <main class="settings-body">
      {narrow ? renderTabNav(railPreview.current) : <div class="settings-nav-col">{searchBox}{renderTabNav(railPreview.current)}</div>}
      {/* Which tab this DOM belongs to, so a heading read cannot attribute it to another.
          The observer that drives that read fires on the commit that swaps tabs, while the
          effect holding the previous tab's id is still the one registered. */}
      <div class="settings-content" data-settings-tab={activeTab} data-ui-locale={uiLocale.value}>
        {Object.keys(errors).length > 0 && <section class="settings-errors" aria-live="assertive"><h3 data-heading-message="Settings.b81f8fe779">{t("Settings.b81f8fe779")}</h3>{Object.entries(errors).map(([field,message])=><p><strong>{field}</strong> — {message}</p>)}</section>}

        {tabContent(activeTab)}
      </div>
    </main>
    {/* The dirty hint is a *default*, not an override. It used to win over everything but
        "saving…", which meant a rejected save's own explanation never reached the footer:
        the draft is still dirty after a failure, so the honest status was replaced by
        "unsaved changes" and only the errors block said what happened. A write in flight,
        or one that failed, has something to say that the hint does not. */}
    <footer><span aria-live="polite">{writeBusy||writeFailed ? status : dirty ? t("Settings.c4751a7ab8") : status}</span><button onClick={()=>requestClose()}>{t("Settings.19766ed6cc")}</button><button class={`primary${dirty?' unsaved':''}`} disabled={!dirty||writeBusy} onClick={()=>void save()}>{status===SAVING ? t("Settings.23e39291d6") : dirty ? t("Settings.dd0ae7a5cb") : t("Settings.b5c120b316")}</button></footer>
  </section>
  {closeIntent && <div class="modal-layer settings-confirm-layer" onMouseDown={event=>event.target===event.currentTarget&&setCloseIntent(null)}>
    <section class="modal settings-confirm" ref={confirmPanel} role="alertdialog" aria-modal="true" aria-label={t("Settings.842e70aacb")} onMouseDown={event=>event.stopPropagation()}>
      <div class="modal-heading"><div><span>{t("Settings.4c1795372c")}</span><h2 data-heading-message="Settings.56dd16e7b1">{t("Settings.56dd16e7b1")}</h2></div><button aria-label={t("Settings.e76fd2add0")} onClick={()=>setCloseIntent(null)}>×</button></div>
      <div class="settings-confirm-body"><p>{t("Settings.77aee18f76")}</p></div>
      <div class="modal-footer"><span>{t("Settings.623ab0c19d")}</span><button onClick={()=>setCloseIntent(null)}>{t("Settings.e76fd2add0")}</button><button class="danger" onClick={discardAndLeave}>{t("Settings.eb1a70e392")}</button><button class="primary" disabled={writeBusy} onClick={()=>void saveAndLeave()}>{t("Settings.dd0ae7a5cb")}</button></div>
    </section>
  </div>}
  {resetIntent && <div class="modal-layer settings-confirm-layer" onMouseDown={event=>event.target===event.currentTarget&&setResetIntent(false)}>
    <section class="modal settings-confirm settings-reset-confirm" ref={resetPanel} role="alertdialog" aria-modal="true" aria-label={t("Settings.a3c85d9f8f")} onMouseDown={event=>event.stopPropagation()}>
      <div class="modal-heading"><div><span>{t("Settings.7cb9a23a6d")}</span><h2 data-heading-message="Settings.e1f16d4544">{t("Settings.e1f16d4544")}</h2></div><button aria-label={t("Settings.dc5f190d4f")} onClick={()=>setResetIntent(false)}>×</button></div>
      <div class="settings-confirm-body"><p>{rich("Settings.2bdc0490be", { value1: <code>{draft.data_dir}</code>, value2: <em>{t("Settings.dd0ae7a5cb")}</em> })}</p></div>
      <div class="modal-footer"><span>{t("Settings.cea63d4e0b")}</span><button onClick={()=>setResetIntent(false)}>{t("Settings.19766ed6cc")}</button><button class="danger" disabled={writeBusy} onClick={()=>void reset()}>{t("Settings.602f8e6370")}</button></div>
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
        setError(t("Settings.f3cf34b408"))
      }
      await audio.play()
    }catch(cause){
      setPlaying(current=>current===voice?null:current)
      setError(cause instanceof Error?cause.message:String(cause))
    }
  }
  return <div class="kokoro-voice-picker">
    {!ready && <p class="profile-hint">{t("Settings.afb1ca89ca")}</p>}
    <div class="kokoro-voice-grid" role="listbox" aria-label={t("Settings.a0df974b18")}>
      {sortKokoroVoices(voices).map(voice=>{
        const label=kokoroVoiceLabel(voice)
        return <button
          type="button"
          key={voice}
          role="option"
          aria-selected={voice===selected}
          class={`kokoro-voice-chip${voice===selected?' selected':''}${playing===voice?' playing':''}`}
          title={t("Settings.ac4d44b4ae", { name: label.name, value2: label.flavor||voice })}
          onClick={()=>void audition(voice)}
        >
          <strong>{playing===voice ? '♪ ' : ''}{label.name}</strong>
          <span>{label.flavor || voice}</span>
        </button>
      })}
    </div>
    {error && <p class="assistant-error" role="alert">{error}</p>}
  </div>
}


type DesktopIntegrationStatus = {
  supported:boolean
  shortcuts?:{slots:Record<string,{path:string;present:boolean}>}
  shell?:{
    importable:boolean;missing:string[];frozen:boolean;install_kind:string
    reinstall_command:string
  }
}

const SHORTCUT_SLOTS:{id:string;label:string;hint:string}[]=[
  {id:'start-menu',get label() { return t("Settings.03029b0319") },get hint() { return t("Settings.eb22827454") }},
  {id:'desktop',get label() { return t("Settings.1673f4411d") },get hint() { return t("Settings.178b7f5a03") }},
  {id:'startup',get label() { return t("Settings.0bfcd9317d") },get hint() { return t("Settings.7b4095a1f8") }},
]

/**
 * Desktop integration a PyPI install can turn on later (ROADMAP Phase 24):
 * the shortcuts a wheel install structurally cannot create, and a report of
 * whether this environment can run the tray at all. Windows only, by absence:
 * the daemon answers `supported:false` elsewhere and this renders nothing.
 *
 * Two things changed here on 2026-08-30 and both were the same bug seen from
 * different ends. The **run-at-login slot is now selectable**: this panel always
 * *reported* it and the write hard-coded the other two, so the one surface a
 * phone or a remote client can reach could remove a login entry and never make
 * one. And the **desktop-shell download is gone**, because `pystray`/`pywebview`
 * became base dependencies and there is nothing left to acquire - what remains
 * is a yes/no about this install, with a runnable reinstall command when the
 * answer is no.
 */
function DesktopIntegrationSection(){
  const [status,setStatus]=useState<DesktopIntegrationStatus|null>(null)
  const [note,setNote]=useState('')
  const [busy,setBusy]=useState(false)
  const [chosen,setChosen]=useState<Record<string,boolean>>({'start-menu':true,'desktop':false,'startup':false})
  const load=()=>{void api<DesktopIntegrationStatus>('GET','/api/desktop/integration').then(setStatus).catch(()=>setStatus(null))}
  useEffect(()=>{load()},[])
  // Seed the boxes from what is on disk, so the panel opens describing reality
  // rather than a default that would silently un-tick something already there.
  useEffect(()=>{
    const slots=status?.shortcuts?.slots
    if(!slots)return
    setChosen({
      'start-menu':slots['start-menu']?.present??true,
      'desktop':slots['desktop']?.present??false,
      'startup':slots['startup']?.present??false,
    })
  },[status?.shortcuts])
  if(!status?.supported)return null
  const slots=status.shortcuts?.slots||{}
  const present=SHORTCUT_SLOTS.filter(slot=>slots[slot.id]?.present).map(slot=>slot.label)
  const selected=SHORTCUT_SLOTS.filter(slot=>chosen[slot.id]).map(slot=>slot.id)
  const applyShortcuts=async(remove:boolean)=>{
    setBusy(true);setNote('')
    try{
      const body=remove?{remove:true}:{remove:false,slots:selected}
      const report=await api<{outcomes?:{slot:string;action:string}[];reason?:string;notes?:string[]}>('POST','/api/desktop/integration/shortcuts',body)
      const outcomes=(report.outcomes||[]).map(outcome=>`${outcome.slot}: ${outcome.action}`).join(' · ')
      setNote([report.reason||outcomes||t("Settings.b6150a5c59"),...(report.notes||[])].join(' — '))
      load()
    }catch(cause){setNote(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  }
  const shell=status.shell
  return <section><h3 data-heading-message="Settings.abb1f294e6">{t("Settings.abb1f294e6")}</h3>
    <p>{rich("Settings.aa1b2246f7", { value1: <code>{t("Settings.fed5038bf2")}</code>, value2: <code>{t("Settings.e6184ce10e")}</code> })}</p>
    <div class="settings-tutorial-reset"><div>
      <p>{rich("Settings.a8e6ecbb14", { value1: present.length ? t("Settings.dff7f2dec0", { value1: present.join(', ') }) : t("Settings.2a67beb3f4") })}</p>
      {SHORTCUT_SLOTS.map(slot=>
        <label key={slot.id} class="check" data-slot={slot.id}>
          <span>{slot.label} <em>· {slot.hint}</em></span>
          <input type="checkbox" checked={!!chosen[slot.id]} disabled={busy}
            onChange={e=>setChosen({...chosen,[slot.id]:e.currentTarget.checked})}/>
        </label>)}
      <p class="profile-hint">{rich("Settings.dda5e96744", { value1: <code>{t("Settings.62e7f29f9e")}</code> })}</p>
    </div>
      <div><button disabled={busy||!selected.length} onClick={()=>void applyShortcuts(false)}>{t("Settings.bf209e8494")}</button><button disabled={busy} onClick={()=>void applyShortcuts(true)}>{t("Settings.ef7b7e6abe")}</button></div>
    </div>
    {shell && (shell.importable ? <p class="profile-hint">{rich("Settings.8c6dbc27bd", { value1: <span class="state-dot idle"/>, value2: (shell.frozen) ? 'yes' : 'no' })}</p> : <div class="kokoro-model-panel">
        <p aria-live="polite">{rich("Settings.6bfba04360", { value1: <span class="state-dot stopped"/>, value2: shell.missing.join(', ') })}</p>
        <p class="profile-hint">{rich("Settings.77c2a1e56f", { value1: shell.reinstall_command ? <>{rich("Settings.d4fa4e03fa", { value1: <code>{shell.reinstall_command}</code> })}</> : t("Settings.4a81626dd4") })}</p>
      </div>)}
    {note && <p class="profile-hint" role="status">{note}</p>}
  </section>
}


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
type LexiconVerdict={ok:boolean;phonemes?:string|null;spoken_as?:string|null;unspeakable?:string[]}
type LexiconCheck={available:boolean;diagnostic?:string|null;results:Record<string,LexiconVerdict>}

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
      if(!result.ok||!result.value){setBuildError(result.diagnostic||t("Settings.97dbd398d6"));return null}
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
          <input aria-label={t("Settings.5dd5b63e92", { word: word })} value={spoken} onInput={e=>onChange({...lexicon,[word]:e.currentTarget.value})}/>
          {state && <span class={`tts-lexicon-verdict ${state.ok?'ok':'bad'}`} title={state.ok ? (state.phonemes ? t("Settings.587a1d9085", { phonemes: state.phonemes }) : t("Settings.dc586a1f6a")) : t("Settings.d6a7e802a6")}>{state.ok ? '✓' : '✗'}</span>}
          {state&&!state.ok && <button type="button" title={t("Settings.1ab10bf2a9")} onClick={()=>void build(word,spoken).then(value=>{if(value)onChange({...lexicon,[word]:value})})}>✨</button>}
          <button type="button" title={t("Settings.ad64ff185f")} onClick={()=>hear(spoken)}>♪</button>
          <button type="button" title={t("Settings.3f854e61f6", { word: word })} onClick={()=>{const next={...lexicon};delete next[word];onChange(next)}}>✕</button>
        </div>
        {state&&!state.ok && <p class="tts-lexicon-hint">{state.unspeakable?.length ? <>{rich("Settings.945ff3fcb2", { value1: state.unspeakable.map(piece=><code key={piece}>{piece}</code>) })}</> : <>{t("Settings.70da5d612d")}</>}</p>}
      </Fragment>
    })}
    <div class="tts-lexicon-row">
      <input placeholder={t("Settings.98c1eb4ee9")} aria-label={t("Settings.275229d9fa")} value={newWord} onInput={e=>setNewWord(e.currentTarget.value)}/>
      <input placeholder={t("Settings.24f1b8254b")} aria-label={t("Settings.f30058f551")} value={newSpoken} onInput={e=>setNewSpoken(e.currentTarget.value)}/>
      <button type="button" title={t("Settings.21c78f1d69")} disabled={!newWord.trim()&&!newSpoken.trim()} onClick={()=>void build(newWord,newSpoken).then(value=>{if(value)setNewSpoken(value)})}>✨</button>
      <button type="button" title={t("Settings.ad64ff185f")} disabled={!newSpoken.trim()} onClick={()=>hear(newSpoken)}>♪</button>
      <button type="button" disabled={!newWord.trim()||!newSpoken.trim()} onClick={()=>{add(newWord,newSpoken);setNewWord('');setNewSpoken('')}}>{t("Settings.9fd728c66c")}</button>
    </div>
    {buildError && <p class="tts-lexicon-hint tts-lexicon-error" role="alert">{buildError}</p>}
    {check&&!check.available && <p class="tts-lexicon-hint">{check.diagnostic || t("Settings.442a86ee10")}</p>}
    {!!pending.length && <div class="tts-lexicon-spelled">
      <h5 data-heading-message="Settings.0d11785a7f">{t("Settings.0d11785a7f")}</h5>
      {pending.map(item=><div class="tts-lexicon-row" key={item.word}>
        <code>{item.word}</code><small>×{item.count}</small>
        <input placeholder={t("Settings.bd8d2b7a80")} aria-label={t("Settings.78cdc43e57", { word: item.word })} value={respell[item.word]??''} onInput={e=>{const value=e.currentTarget.value;setRespell(current=>({...current,[item.word]:value}))}}/>
        <button type="button" title={t("Settings.ffe04ba9ce")} onClick={()=>void build(item.word,respell[item.word]||'').then(value=>{if(value)setRespell(current=>({...current,[item.word]:value}))})}>✨</button>
        <button type="button" title={t("Settings.ad64ff185f")} disabled={!(respell[item.word]||'').trim()} onClick={()=>hear(respell[item.word]||'')}>♪</button>
        <button type="button" disabled={!(respell[item.word]||'').trim()} onClick={()=>add(item.word,respell[item.word]||'')}>{t("Settings.5f4ac9161a")}</button>
      </div>)}
    </div>}
  </div>
}
