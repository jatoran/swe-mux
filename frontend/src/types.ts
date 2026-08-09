import type { PaneLayout } from './layout'

export type SessionState = 'starting' | 'running' | 'working' | 'idle' | 'awaiting' | 'exited' | 'crashed'

/** Typed sub-reason set whenever state === 'awaiting'; mirrors the backend contract. */
export type AwaitingReason = 'approval' | 'question' | 'elicitation' | 'rate_limit'

/** Idle-axis sibling: the turn ended but the agent will resume itself. */
export type IdleReason = 'waiting_on_background'

/** Standing-engagement annotation kinds — the fifth status axis, never states. */
export type StandingActivityKind = 'loop' | 'cron' | 'background_tasks' | 'subagents'

/**
 * A standing engagement that outlives the turn: an armed /loop wakeup, a cron
 * schedule, running background tasks, live subagents. Idle stays idle and
 * delivery stays safe; annotations only add information.
 */
export interface StandingActivity {
  kind: StandingActivityKind
  source: string
  evidence: string
  since: number
  expires_at: number | null
  count: number
  detail: string | null
}

export interface Session {
  id: string; name: string; project_id: string; backend: string
  native_session_id: string; cwd: string; exe: string; args: string[]; pid: number
  created_at: number; state: SessionState; state_detail?: string; tokens_in: number
  awaiting_reason?: AwaitingReason | null
  idle_reason?: IdleReason | null
  standing_activity?: StandingActivity[]
  process_job_assignment:string
  tokens_out: number; tokens_cache_read:number; tokens_cache_write:number; cost_usd:number
  provider?:string|null; provider_account_hashes?:Record<string,string>
  context_window: number; context_pct: number; last_activity_ts: number
  git: { branch?: string; dirty: number; ahead: number; behind: number }
  pinned_attention: boolean; broadcast: boolean
  startup_timing_ms?: Record<string, number>
  client_startup_timing_ms?: Record<string, number>
  shell_profile_id?: string
  context_peak_pct:number;model?:string;measurement_source?:string
  compaction_count:number;last_compaction_at?:number;compaction_capability?:string;compaction_confidence?:string
  repository_id?:string;project_label?:string;project_root?:string
  project_scope_id?:string;repo_group_id?:string
  spawn_cwd?:string;spawn_project_scope_id?:string;spawn_repo_group_id?:string;spawn_project_label?:string;spawn_project_root?:string
  runtime_cwd?:string;runtime_cwd_live:boolean;runtime_cwd_source:string;runtime_cwd_updated_at?:number
  runtime_project_scope_id?:string;runtime_cwd_dropped:number;agent_run_id?:string;agent_run_started_at?:number;agent_loaded_at?:number
  /** Conversation replacements (`/clear`, `/new`) on this PTY; 0 is the run it spawned with. */
  agent_run_seq?:number
  run_cwd?:string;run_project_scope_id?:string;run_repo_group_id?:string
  parser_status?:string;parser_diagnostic?:string;parser_events_seen?:number
  /** Set when the followed transcript is no longer this PTY's conversation. */
  observation_stale_since?:number
  observation_diagnostic?:string
  delivery_readiness?:{state:'safe'|'blocked'|'unknown';reason:string;authorized:false}
  auto_named?:boolean;generated_title?:string
  generated_title_annotation?:{id:string;provenance:string;resolved_model?:string;confidence?:number;cost_usd?:number;created_at:number}
  voice_mode?: VoiceMode | null
  voice_content?: VoiceContent | null
  /** Task/Project-Action shell whose exact spawn argv can be relaunched in place. */
  relaunchable?: boolean
  /** Client-only optimistic row/tab shown while POST /api/sessions is in flight. */
  pending?: boolean
  /** Daemon process generation plus session-local ordering for multi-channel snapshots. */
  _snapshot_generation?: string
  _snapshot_revision?: number
  /** True only for REST snapshots that authoritatively include generated-title fields. */
  _snapshot_enriched?: boolean
}

export type VoiceContent = 'summary' | 'verbatim'

export type VoiceMode = 'off' | 'on_demand' | 'auto'

export interface VoiceClip {
  id:string; session_id:string; agent_run_id?:string|null; created_at:number
  trigger:'auto'|'manual'; content_mode:'summary'|'verbatim'; engine:string; voice:string
  text:string; format:string; size_bytes:number; duration_hint_s?:number|null
  status:'ready'|'failed'; error?:string|null; model?:string|null; cost_usd?:number|null
}

export interface VoiceStatus {
  enabled:boolean; engine:string; engine_available:boolean; diagnostic?:string|null
  content:'summary'|'verbatim'; default_mode:VoiceMode; voice:string; summary_model:string
  spend_today:{tokens:number;cost_usd:number}; daily_budget_usd:number
  cache_bytes:number; cache_limit_bytes:number; clip_count:number; stt_enabled:boolean
  stt_engine:'sapi'|'whisper';stt_available:boolean;stt_diagnostic?:string|null
  stt_language:string;stt_whisper_model:string;stt_routing_model?:string
  wake_words?:string[];commands?:{action:string;phrases:string[]}[]
}

export type ProjectBackend=string
export type PromptLibraryScope='off'|'global'|'project'|'both'
export interface Project {
  id:string;name:string;root:string;position:number;group_id?:string|null;layout:PaneLayout|unknown;layout_revision:number
  sidebar_visible?:boolean
  /** Registration time, epoch seconds; 0 for Projects registered before the daemon
   *  dated them and never observed in history. Sidebar date ordering reads 0 as
   *  unknown and sorts it last. */
  created_at?:number
  /** Derived server-side from history: the latest session activity in this Project,
   *  epoch seconds, 0 if it has never run one. */
  last_activity?:number
  default_backend?:ProjectBackend;default_profile_id?:string
  /** Machine-local comparison override. Null/absent means automatic Git ref inference. */
  git_compare_ref?:string|null
  portable_options?:{default_shell_profile?:string;preferred_backend?:ProjectBackend;prompt_library_scope?:PromptLibraryScope;notification_sounds_enabled?:boolean;ignore_patterns?:string[]}
  effective_options?:{backend:ProjectBackend;profile_id:string;prompt_library_scope:PromptLibraryScope;notification_sounds_enabled:boolean}
  option_sources?:Record<string,'global'|'project_record'|'project_file'>;project_config_status?:string
}

export interface ProjectGroup { id:string;name:string;position:number }

export type ProjectActionSource='vscode'|'package'|'native'
export interface ProjectActionStep { name:string;kind:'shell'|'process';command:string }
export interface ProjectAction { id:string;label:string;source:ProjectActionSource;steps:ProjectActionStep[] }
export interface ProjectActionCatalog {
  project_root:string;fingerprint:string;trusted:boolean;sources:string[]
  actions:ProjectAction[];diagnostics:string[]
}

export interface ProjectScope {
  id:string;root:string;label:string;source:string;repo_group_id?:string;repo_group_label?:string
  hidden:number;created_at:number;last_activity:number;root_exists:boolean;live_count:number
  history_count:number;artifact_count:number
  inventory?:{root_exists:boolean;config_exists:boolean;rules_present_inert:boolean;unlinked:Array<{path:string;kind:string;filename:string}>;conflicting?:Array<{path:string;kind:string;filename:string;other_project_scope_ids:string[]}>;scan_truncated:boolean}
  artifacts?:Array<{id:string;kind:string;owner_type:string;owner_id:string;owner_label?:string;project_scope_id:string;relative_path:string;revision?:string;placement_acknowledged_scope_id?:string}>
  blockers?:{history:number;artifacts:number}
  detached_artifacts?:Array<{id:string;kind:string;owner_type:string;owner_id:string;owner_label?:string;relative_path:string}>
}

export interface ShellProfile {
  id:string; label:string; executable:string; args:string[]; env:Record<string,string>
  platforms:string[]; cwd_strategy:'native'|'home'|'wsl'; marker:string
  capabilities:string[]; cwd_integration:boolean; enabled:boolean; configured?:boolean
}
