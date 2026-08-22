import type { PaneLayout } from './layout'

/**
 * Which axes of a spending cap are enforced. `either` enforces both and trips on
 * whichever is reached first. Mirrors `src/swe_mux/budget.py`.
 */
export type BudgetMode = 'tokens' | 'usd' | 'either'

/**
 * One spending ceiling. An axis the mode does not name may still carry a figure -
 * it is remembered so switching modes does not lose it, and it is not enforced.
 * `null` means no figure at all, which is distinct from `0` (a total ceiling).
 */
export type Budget = { tokens: number | null; usd: number | null; mode: BudgetMode }

/** What a budget check concluded, as the daemon reports it beside spend. */
export type BudgetVerdict = {
  exhausted: boolean
  axis: 'tokens' | 'usd' | ''
  reason: string
  unpriced_calls: number
  cost_blind: boolean
  note: string
}

export type SessionState = 'starting' | 'running' | 'working' | 'idle' | 'awaiting' | 'exited' | 'crashed'

/** Typed sub-reason set whenever state === 'awaiting'; mirrors the backend contract. */
export type AwaitingReason = 'approval' | 'question' | 'elicitation' | 'rate_limit' | 'authentication'

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

/** What mux answers on this conversation's behalf. See `.docs/design/features/approvals.md`. */
export type ApprovalMode = 'wait' | 'allowlisted' | 'allow_all'

export interface ApprovalPolicy {
  mode: ApprovalMode
  /** The conversation the grant was made against; a mismatch reads as `wait`. */
  run_id: string | null
  expires_at: number | null
  granted_at: number | null
  set_by: string
  rules: string[]
  auto_approved: number
  max_auto: number
  last_decision_at: number | null
  last_request: string | null
  floor_deferred: number
}

/** `GET /api/sessions/{id}/approvals`, and the body every mutation returns. */
export interface ApprovalStatus {
  supported: boolean
  enabled: boolean
  ceiling: ApprovalMode
  rules: string[]
  rules_source: 'project' | 'default'
  /** Present when no mode above `wait` can be selected, phrased for display. */
  unavailable: string | null
  ttl_seconds: number
  max_auto: number
  policy: ApprovalPolicy
  /** What is actually in force now — an expired or re-keyed grant reads `wait`. */
  effective_mode: ApprovalMode
  modes: ApprovalMode[]
}

export interface Session {
  id: string; name: string; project_id: string; backend: string
  native_session_id: string; cwd: string; exe: string; args: string[]; pid: number
  created_at: number; state: SessionState; state_detail?: string; tokens_in: number
  /** Epoch seconds of the transition into `state`; 0 when the daemon never dated it. */
  state_since?: number
  /** Wall-clock length of the last completed root turn, milliseconds. Run-scoped. */
  last_turn_ms?: number | null
  /** Epoch seconds the current root turn began; absent while no turn is open. */
  turn_started_at?: number | null
  /** Monotonic identity of the current root-turn generation. */
  turn_epoch?: number
  /** Provider-native or mux-synthesized id of the open root turn. */
  active_turn_id?: string | null
  /** Operator interruption intent awaiting provider or PTY confirmation. */
  interrupt_pending_at?: number | null
  interrupt_pending_source?: string | null
  /** Epoch seconds a human last submitted a request here; absent when unknown.
   *  Not the same question as `turn_started_at`: auto-delivery and injected
   *  teammate messages open turns nobody asked for. Run-scoped. */
  last_human_prompt_at?: number | null
  /** Epoch seconds the current stretch of running work began; absent when none
   *  is open. Latched when a `subagents`/`background_tasks` annotation opens and
   *  released only when a root turn closes with nothing running, so it spans a
   *  harness that ends its turn to hand off to background agents — the case where
   *  `turn_started_at` is absent and `last_turn_ms` describes a finished fragment
   *  of a request that is still going. Run-scoped. */
  running_work_since?: number | null
  awaiting_reason?: AwaitingReason | null
  idle_reason?: IdleReason | null
  standing_activity?: StandingActivity[]
  /** Absent from a daemon predating control-plane approvals, which reads as `wait`. */
  approval_policy?: ApprovalPolicy
  process_job_assignment:string
  tokens_out: number; tokens_cache_read:number; tokens_cache_write:number; cost_usd:number
  provider?:string|null; provider_account_hashes?:Record<string,string>
  context_window: number; context_pct: number; last_activity_ts: number
  /**
   * Semantic turn completions, and the highest one a human has acknowledged.
   * These, never `last_activity_ts`, drive the sidebar's unread tier: activity
   * moves on any PTY byte, including the repaint a resize provokes. Optional
   * because a daemon predating them serves neither, in which case every row
   * reads as caught up rather than as a wall of false unread.
   */
  turn_seq?: number; read_turn_seq?: number; last_turn_end_ts?: number
  /**
   * Set by an explicit "Mark as unread". Forces the unread tier and suppresses
   * the dwell acknowledgement, so a pane the user is still looking at stays
   * flagged; the daemon retires it when the agent completes another turn.
   */
  unread_pin?: boolean
  /**
   * What Git says about the checkout this session works in — never about the
   * session. Sessions sharing a working tree share one measurement, because
   * `git status` answers for the whole repository however it is invoked; `root`
   * is how a client tells that two rows are reporting the same checkout.
   *
   * `worktree` is the leaf name of a *linked* worktree checkout, absent for the
   * primary one. `added`/`removed` are lines changed against HEAD across tracked
   * files. `compare_*` are the branch-scoped equivalents, measured from the merge
   * base with `compare_ref`, and so include committed work the HEAD diff has
   * already lost. Absent means "not measured", which is not the same as zero.
   */
  git: {
    branch?: string; dirty: number; ahead: number; behind: number
    worktree?: string | null; added?: number | null; removed?: number | null
    root?: string | null; compare_ref?: string | null
    compare_added?: number | null; compare_removed?: number | null
    compare_files?: number | null
    head?: string | null
  }
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
  runtime_project_scope_id?:string;runtime_cwd_dropped:number;runtime_boundary?:'local'|'remote'|'unknown';remote_authority?:string;remote_since?:number;remote_transport_state?:'connected'|'authentication'|'ended'|null;agent_run_id?:string;agent_run_started_at?:number;agent_loaded_at?:number
  /** Conversation replacements (`/clear`, `/new`) on this PTY; 0 is the run it spawned with. */
  agent_run_seq?:number
  run_cwd?:string;run_project_scope_id?:string;run_repo_group_id?:string
  parser_status?:string;parser_diagnostic?:string;parser_events_seen?:number
  /** Set when the followed transcript is no longer this PTY's conversation. */
  observation_stale_since?:number
  observation_diagnostic?:string
  delivery_readiness?:{state:'safe'|'blocked'|'unknown';reason:string;authorized:false}
  /**
   * Text sitting unsent in this session's composer, estimated by the daemon from
   * the bytes written to the PTY (`composer_input.py`). Present only while
   * something is there, so presence is the whole signal; `since` is when the
   * composer last went from empty to non-empty, in epoch seconds.
   *
   * Cross-device by construction — it reports text typed from any client, on any
   * machine — and process-scoped: a daemon restart forgets it, because the byte
   * history it is derived from does not survive one.
   */
  unsent_input?:{since:number}|null
  auto_named?:boolean;generated_title?:string
  generated_title_annotation?:{id:string;provenance:string;resolved_model?:string;confidence?:number;cost_usd?:number;created_at:number}
  voice_mode?: VoiceMode | null
  voice_content?: VoiceContent | null
  /** Task/Project-Action shell whose exact spawn argv can be relaunched in place. */
  relaunchable?: boolean
  /**
   * Recovered from durable recovery data rather than from a running process: the
   * daemon and its PTY owner both died without recording how this session ended,
   * so it comes back visible-but-dead instead of vanishing from the sidebar and
   * the layout. Always accompanied by `state: 'crashed'`, so anything that
   * already gates on a terminal state needs no change; this only distinguishes a
   * recovered session from one that merely exited.
   */
  cold?: boolean
  cold_since?: number | null
  cold_reason?: string | null
  /** When the replayed terminal bytes were captured, bounding how stale they are. */
  cold_terminal_at?: number | null
  /**
   * Why no terminal bytes were kept. An alternate-screen or repaint-heavy harness
   * is excluded on purpose: its retained bytes are a differential frame stream
   * that reconstructs to a blank or half-drawn screen, and repairing that needs a
   * live child to pulse.
   */
  cold_terminal_skipped?: string | null
  /** Client-only optimistic row/tab shown while POST /api/sessions is in flight. */
  pending?: boolean
  /** Client-only copy for a pending pane whose preparation is more specific than startup. */
  pending_label?: string
  pending_detail?: string
  /** Daemon process generation plus session-local ordering for multi-channel snapshots. */
  _snapshot_generation?: string
  _snapshot_revision?: number
  /** True only for REST snapshots that authoritatively include generated-title fields. */
  _snapshot_enriched?: boolean
}

export type VoiceContent = 'summary' | 'verbatim'

export type VoiceMode = 'off' | 'on_demand' | 'auto'

/** One synthesized segment of a clip. Several of these are still one clip. */
export interface VoiceClipPart {
  id:string; segment_index:number
  status:'synthesizing'|'ready'|'failed'
  duration_hint_s?:number|null; size_bytes:number; error?:string|null
}

/**
 * One reply's audio.
 *
 * A clip is a *stream*, not a row: a reply is synthesized in segments so its first
 * sentence can play while the rest is being made, and every field here describes
 * the whole reply - the text is the segments' text joined, the duration is their
 * sum, the status is their verdict. `parts` is what playback needs to get through
 * a reply the daemon has not joined into one file yet.
 */
export interface VoiceClip {
  id:string; session_id:string; agent_run_id?:string|null; created_at:number
  trigger:'auto'|'manual'|'system'; content_mode:'summary'|'verbatim'; engine:string; voice:string
  text:string; format:string; size_bytes:number; duration_hint_s?:number|null
  /** The daemon's half of a clip's life. `held`, `played` and `dismissed` are NOT
   *  here: those are per-device facts (a clip played on the phone is unplayed on
   *  the desktop), overlaid by `voice.ts` rather than stored on the row.
   *  `synthesizing` covers a reply whose later segments are still coming, which is
   *  what makes a live clip one row that grows rather than a row per sentence. */
  status:'synthesizing'|'ready'|'failed'; error?:string|null; model?:string|null; cost_usd?:number|null
  /** When the message this clip speaks *arrived*, epoch seconds — null for
   *  application speech and for clips made before the anchor existed. Ordering a
   *  held backlog by synthesis time is exactly wrong, which is why it is captured. */
  source_ts?:number|null
  /** The `message_id` of the reply this clip renders, so the reader can find the
   *  audio for a message instead of generating it a second time. */
  message_anchor?:string|null
  stream_id?:string|null
  /** How many segments the producer said this reply has, null while it is still
   *  being spoken. `stream_open` is the same fact stated for a reader. */
  segment_count?:number|null; stream_open?:boolean
  /** The segments this clip is stored in, in spoken order. Absent (or a single
   *  entry) once the daemon has joined them into one file. */
  parts?:VoiceClipPart[]
  /** Set by the daemon when an existing clip answered a per-message request. */
  reused?:boolean
}

export interface KokoroModelStatus {
  status:'not_downloaded'|'downloading'|'ready'|'error'
  revision:string; repo:string; total_bytes:number; downloaded_bytes:number
  current_file?:string|null; error?:string|null; voices:string[]
}

export interface VoiceStatus {
  enabled:boolean; engine:string; engine_available:boolean; diagnostic?:string|null
  content:'summary'|'verbatim'; default_mode:VoiceMode; voice:string; summary_model:string
  spend_today:{tokens:number;cost_usd:number;unpriced_calls?:number}; daily_budget:Budget
  cache_bytes:number; cache_limit_bytes:number; clip_count:number; stt_enabled:boolean
  kokoro_model?:KokoroModelStatus; kokoro_voice?:string
  stt_engine:'sapi'|'whisper';stt_available:boolean;stt_diagnostic?:string|null
  stt_language:string;stt_whisper_model:string;stt_routing_model?:string
  wake_words?:string[];commands?:{action:string;phrases:string[]}[]
  chat_patience_ms?:number
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
  /** Latest explicit prompt submission or user-initiated session start, persisted
   *  by the daemon and shared by every client. */
  last_used_at?:number
  /** Derived server-side from history: the latest session activity in this Project,
   *  epoch seconds, 0 if it has never run one. */
  last_activity?:number
  /** Searchable conversations retained when this registration is removed. */
  history_count?:number
  /** False when the registered root is missing or no longer a directory. */
  root_available?:boolean
  /** Present on create responses when an earlier registration was restored. */
  restored?:boolean
  default_backend?:ProjectBackend;default_profile_id?:string
  /** Backend name to launch profile id, for agent sessions started here. */
  default_agent_profiles?:Record<string,string>
  /** Machine-local comparison override. Null/absent means automatic Git ref inference. */
  git_compare_ref?:string|null
  portable_options?:{default_shell_profile?:string;preferred_backend?:ProjectBackend;prompt_library_scope?:PromptLibraryScope;notification_sounds_enabled?:boolean;ignore_patterns?:string[]}
  effective_options?:{backend:ProjectBackend;profile_id:string;prompt_library_scope:PromptLibraryScope;notification_sounds_enabled:boolean;agent_profile_ids?:Record<string,string>}
  option_sources?:Record<string,'global'|'project_record'|'project_file'>;project_config_status?:string
}

export interface ProjectGroup { id:string;name:string;position:number }

export type ProjectActionSource='vscode'|'package'|'native'
export interface ProjectActionStep {
  name:string;kind:'shell'|'process';command:string
  cwd?:string;platforms?:string[];timeout_seconds?:number|null
}
export interface ProjectActionInput {
  id:string;label:string;default:string;kind:'string'|'choice';options:string[]
}
export interface ProjectAction {
  id:string;label:string;description?:string;source:ProjectActionSource
  /** Which task file declares this action. Trust is per file, so this is what a
   *  human approves before the action can run. */
  source_path?:string
  /** Whether this action's own source file is currently approved. */
  trusted?:boolean
  inputs?:ProjectActionInput[]
  steps:ProjectActionStep[]
}
export interface ProjectActionFile {
  path:string;present:boolean;fingerprint:string;trusted:boolean
}
export interface ProjectActionDiff extends ProjectActionFile { status:string;diff:string }
export interface ProjectActionCatalog {
  project_root:string;fingerprint:string;trusted:boolean;sources:string[]
  /** Per-file approval. `trusted` above is true only when every present file is. */
  files?:ProjectActionFile[]
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

export interface LaunchProfile {
  id:string; label:string; executable:string; args:string[]; env:Record<string,string>
  platforms:string[]; cwd_strategy:'native'|'home'|'wsl'; marker:string
  capabilities:string[]; cwd_integration:boolean; enabled:boolean; configured?:boolean
  /** Which backend this profile launches. `shell` for a terminal, otherwise a harness name. */
  backend:ProjectBackend
}
