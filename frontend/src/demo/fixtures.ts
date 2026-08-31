/**
 * The demo's invented install: two Projects, four sessions, a static preview,
 * a couple of notes, and a config close enough to a real daemon's `/api/config`
 * that `applyConfig` finds everything it reads.
 *
 * Everything here is fiction. Names, paths, and numbers are made up; no field
 * may ever be copied from a real install (the same rule `trailer/capture_env.py`
 * enforces for the site's screenshots).
 */
import type { Preview } from '../processFleet.ts'
import type { PaneLayout } from '../layout.ts'
import type { Project, Session } from '../types.ts'
import type { DemoNote, DemoState } from './store.ts'
import { initialTimelines, initialTranscripts } from './conversation.ts'
import {
  claudeScrollback, codexScrollback, composerInfo, rageScrollback,
  shellScrollback, spawnScrollback, vibeScrollback, workingScrollback,
  type ComposerInfo,
} from './terminalSim.ts'

const now = Math.floor(Date.now() / 1000)

export const DEMO_PROJECT_ID = 'p-rocket'
export const DEMO_PROJECT2_ID = 'p-garden'
export const DEMO_PREVIEW_ID = 'demo-preview'
export const DEMO_ROOT = '/code/rocket-shop'
export const DEMO_ROOT2 = '/code/meme-garden'
/** Linked checkouts of the first Project, so the Git tab's Map has more than one
 *  row and a couple of them have a live session standing in them. */
export const DEMO_WORKTREE_COUPON = '/code/.worktrees/coupon-table'
export const DEMO_WORKTREE_PROFILE = '/code/.worktrees/cart-profile'
/** Static pages committed under site/preview/<id>/ - the iframe target of a
 *  preview pane is the absolute path `/preview/<id>/`, so every preview the
 *  demo can ever mint must map to one of these. */
export const PREVIEW_PAGE_IDS = [DEMO_PREVIEW_ID, 'demo-preview-2'] as const

function makeSession(input: {
  id: string; name: string; project: string; backend: string
  state: Session['state']; model?: string; tokens?: number; cost?: number
  contextPct?: number; turnSeq?: number; git?: Partial<Session['git']>
  workedMs?: number; ageSeconds?: number
  /** Where this session is standing. Defaults to the Project root; a linked
   *  worktree is what puts a live row on that checkout in the Git tab's Map. */
  cwd?: string
  /** Seconds this session's open turn has been running. Only for `working`
   *  rows: it is what makes the status dot pulse and the turn clock tick, and
   *  the clock ticks by itself because the UI measures it against wall time. */
  workingForSeconds?: number
}): Session {
  const created = now - (input.ageSeconds ?? 3600)
  const cwd = input.cwd ?? DEMO_ROOT
  const turnStarted = input.workingForSeconds === undefined
    ? undefined
    : now - input.workingForSeconds
  return {
    id: input.id,
    name: input.name,
    project_id: input.project,
    backend: input.backend,
    native_session_id: `native-${input.id}`,
    cwd,
    exe: input.backend === 'shell' ? 'bash' : input.backend,
    args: [],
    pid: 40000 + Math.abs(hash(input.id)) % 9000,
    created_at: created,
    state: input.state,
    state_since: turnStarted ?? now - 90,
    ...(turnStarted === undefined ? {} : {
      turn_started_at: turnStarted,
      running_work_since: turnStarted,
      turn_epoch: 1,
      active_turn_id: `turn-${input.id}`,
      last_human_prompt_at: turnStarted,
    }),
    tokens_in: input.tokens ?? 0,
    tokens_out: Math.floor((input.tokens ?? 0) / 4),
    tokens_cache_read: (input.tokens ?? 0) * 6,
    tokens_cache_write: Math.floor((input.tokens ?? 0) / 2),
    cost_usd: input.cost ?? 0,
    context_window: 200000,
    context_pct: input.contextPct ?? 0,
    context_peak_pct: Math.min(0.97, (input.contextPct ?? 0) + 0.06),
    last_activity_ts: now - 45,
    last_turn_ms: 48_000,
    worked_ms: input.workedMs ?? 0,
    turn_seq: input.turnSeq ?? 0,
    read_turn_seq: input.turnSeq ?? 0,
    last_turn_end_ts: now - 120,
    pinned_attention: false,
    broadcast: false,
    process_job_assignment: 'assigned',
    compaction_count: 0,
    model: input.model,
    measurement_source: input.backend === 'shell' ? undefined : 'transcript',
    runtime_cwd: cwd,
    runtime_cwd_live: true,
    runtime_cwd_source: 'demo',
    runtime_cwd_dropped: 0,
    runtime_boundary: 'local',
    agent_run_id: input.backend === 'shell' ? undefined : `run-${input.id}`,
    agent_run_started_at: created,
    git: {
      branch: 'feature/faster-cart', dirty: 2, ahead: 2, behind: 0,
      added: 18, removed: 6, root: DEMO_ROOT,
      compare_ref: 'master', compare_added: 64, compare_removed: 12, compare_files: 5,
      ...input.git,
    },
    delivery_readiness: {
      state: 'safe', reason: '', reasons: [], protected: [],
      observed_at: now, authorized: false,
    },
  }
}

function hash(text: string): number {
  let value = 0
  for (let index = 0; index < text.length; index += 1) value = (value * 31 + text.charCodeAt(index)) | 0
  return value
}

/** Sessions the demo keeps permanently mid-turn. They pulse, their turn clock
 *  ticks, and typing into one is answered by `busyReply` instead of the joke
 *  responder - which is the honest demonstration, because the real product also
 *  refuses to interleave a keystroke into a running turn. */
export const BUSY_SESSION_IDS: readonly string[] = ['s-working', 's-migrate']

const GARDEN_GIT: Partial<Session['git']> = {
  branch: 'main', dirty: 0, ahead: 0, behind: 0, added: 0, removed: 0,
  root: DEMO_ROOT2, compare_ref: null, compare_added: null,
  compare_removed: null, compare_files: null,
}

/** The linked checkout `s-working` occupies, so the Map draws it beside that row. */
const COUPON_GIT: Partial<Session['git']> = {
  branch: 'agent/coupon-table', dirty: 4, ahead: 3, behind: 1,
  added: 91, removed: 34, root: DEMO_WORKTREE_COUPON,
  compare_ref: 'master', compare_added: 214, compare_removed: 66, compare_files: 9,
}

const PROFILE_GIT: Partial<Session['git']> = {
  branch: 'agent/cart-profile', dirty: 1, ahead: 1, behind: 0,
  added: 12, removed: 4, root: DEMO_WORKTREE_PROFILE,
  compare_ref: 'master', compare_added: 26, compare_removed: 8, compare_files: 2,
}

const SESSIONS: Session[] = [
  makeSession({
    id: 's-claude', name: 'fix flaky checkout test', project: DEMO_PROJECT_ID,
    backend: 'claude', state: 'idle', model: 'claude-opus-4-8',
    tokens: 48200, cost: 1.84, contextPct: 0.31, turnSeq: 4, workedMs: 8 * 60_000,
    ageSeconds: 2 * 3600,
  }),
  makeSession({
    id: 's-rage', name: 'prod is down (4h)', project: DEMO_PROJECT_ID,
    backend: 'claude', state: 'idle', model: 'claude-opus-4-8',
    tokens: 187400, cost: 6.42, contextPct: 0.86, turnSeq: 31, workedMs: 71 * 60_000,
    ageSeconds: 4 * 3600 + 12 * 60,
  }),
  makeSession({
    id: 's-working', name: 'refactor the coupon table', project: DEMO_PROJECT_ID,
    backend: 'claude', state: 'working', model: 'claude-opus-4-8',
    tokens: 33100, cost: 1.12, contextPct: 0.24, turnSeq: 6, workedMs: 12 * 60_000,
    ageSeconds: 55 * 60, workingForSeconds: 96,
    cwd: DEMO_WORKTREE_COUPON, git: COUPON_GIT,
  }),
  makeSession({
    id: 's-codex', name: 'profile cart endpoint', project: DEMO_PROJECT_ID,
    backend: 'codex', state: 'idle', model: 'gpt-demo',
    tokens: 21050, cost: 0.62, contextPct: 0.18, turnSeq: 2, workedMs: 5 * 60_000,
    ageSeconds: 90 * 60, cwd: DEMO_WORKTREE_PROFILE, git: PROFILE_GIT,
  }),
  makeSession({
    id: 's-shell', name: 'shell', project: DEMO_PROJECT_ID,
    backend: 'shell', state: 'running', ageSeconds: 40 * 60,
  }),
  makeSession({
    id: 's-garden', name: 'water the memes', project: DEMO_PROJECT2_ID,
    backend: 'claude', state: 'idle', model: 'claude-opus-4-8',
    tokens: 9800, cost: 0.31, contextPct: 0.09, turnSeq: 1, ageSeconds: 20 * 60,
    cwd: DEMO_ROOT2, git: GARDEN_GIT,
  }),
  makeSession({
    id: 's-vibe', name: 'make it work', project: DEMO_PROJECT2_ID,
    backend: 'codex', state: 'idle', model: 'gpt-demo',
    tokens: 64300, cost: 1.97, contextPct: 0.67, turnSeq: 9, workedMs: 23 * 60_000,
    ageSeconds: 70 * 60, cwd: DEMO_ROOT2, git: GARDEN_GIT,
  }),
  makeSession({
    id: 's-migrate', name: 'migrate the meme schema', project: DEMO_PROJECT2_ID,
    backend: 'codex', state: 'working', model: 'gpt-demo',
    tokens: 12750, cost: 0.38, contextPct: 0.44, turnSeq: 2, workedMs: 4 * 60_000,
    ageSeconds: 18 * 60, workingForSeconds: 402, cwd: DEMO_ROOT2, git: GARDEN_GIT,
  }),
]

/** The composer a seeded session draws, read off the session itself so the box's
 *  status line and the sidebar row can never disagree. */
function composerFor(id: string): ComposerInfo {
  const found = SESSIONS.find(item => item.id === id)
  if (!found) throw new Error(`no demo session ${id}`)
  return composerInfo(found)
}

const P1_LAYOUT: PaneLayout = {
  version: 7,
  root: {
    type: 'split', id: 'split-root', direction: 'horizontal', ratio: 0.55,
    first: {
      type: 'stack', id: 'stack-agents', active_child_id: 's-claude',
      children: [
        { type: 'leaf', kind: 'terminal', id: 's-claude' },
        { type: 'leaf', kind: 'terminal', id: 's-rage' },
        { type: 'leaf', kind: 'terminal', id: 's-working' },
        { type: 'leaf', kind: 'terminal', id: 's-codex' },
      ],
    },
    second: {
      type: 'split', id: 'split-side', direction: 'vertical', ratio: 0.5,
      first: {
        type: 'stack', id: 'stack-shell', active_child_id: 's-shell',
        children: [{ type: 'leaf', kind: 'terminal', id: 's-shell' }],
      },
      second: {
        type: 'stack', id: 'stack-preview', active_child_id: DEMO_PREVIEW_ID,
        children: [{ type: 'leaf', kind: 'preview', id: DEMO_PREVIEW_ID }],
      },
    },
  },
}

const P2_LAYOUT: PaneLayout = {
  version: 7,
  root: {
    type: 'split', id: 'split-garden', direction: 'horizontal', ratio: 0.5,
    first: {
      type: 'stack', id: 'stack-garden', active_child_id: 's-vibe',
      children: [
        { type: 'leaf', kind: 'terminal', id: 's-vibe' },
        { type: 'leaf', kind: 'terminal', id: 's-garden' },
      ],
    },
    second: {
      type: 'stack', id: 'stack-garden-work', active_child_id: 's-migrate',
      children: [{ type: 'leaf', kind: 'terminal', id: 's-migrate' }],
    },
  },
}

const PROJECTS: Project[] = [
  {
    id: DEMO_PROJECT_ID, name: 'rocket-shop', root: DEMO_ROOT,
    position: 0, group_id: null, layout: P1_LAYOUT, layout_revision: 3,
    sidebar_visible: true, created_at: now - 30 * 86400, last_used_at: now - 300,
    last_activity: now - 45, history_count: 12, root_available: true,
    default_backend: 'claude',
    effective_options: {
      backend: 'claude', profile_id: 'default', prompt_library_scope: 'both',
      notification_sounds_enabled: false,
    },
  },
  {
    id: DEMO_PROJECT2_ID, name: 'meme-garden', root: DEMO_ROOT2,
    position: 1, group_id: null, layout: P2_LAYOUT, layout_revision: 1,
    sidebar_visible: true, created_at: now - 9 * 86400, last_used_at: now - 7200,
    last_activity: now - 1200, history_count: 3, root_available: true,
    default_backend: 'claude',
    effective_options: {
      backend: 'claude', profile_id: 'default', prompt_library_scope: 'both',
      notification_sounds_enabled: false,
    },
  },
]

const PREVIEWS: Preview[] = [
  {
    id: DEMO_PREVIEW_ID, session_id: '', project_id: DEMO_PROJECT_ID,
    url: '', host: '', port: 0, source: 'demo', viewport: 'desktop', listed: true,
    kind: 'static', label: 'landing.html', entry: 'index.html',
    doc_root: `${DEMO_ROOT}/site`, doc_root_relative: 'site', worktree: '',
  },
]

const NOTES: DemoNote[] = [
  {
    note_id: 'n-launch', project_id: DEMO_PROJECT_ID, title: 'launch checklist',
    revision: 4, updated_at: now - 5400,
    content: [
      '# Launch checklist',
      '',
      '- [x] make the cart fast (codex says it was the 40MB coupon file)',
      '- [x] de-flake checkout test',
      '- [ ] convince the demo visitors this is all real',
      '- [ ] ship it',
      '',
      'Notes live beside the terminals so agents and humans share them.',
    ].join('\n'),
  },
  {
    note_id: 'n-ideas', project_id: DEMO_PROJECT2_ID, title: 'ideas',
    revision: 1, updated_at: now - 86400,
    content: '# ideas\n\n- a garden, but memes\n- that is the whole idea\n',
  },
]

export function demoConfig(): Record<string, unknown> {
  return {
    schema_version: 35,
    revision: 1,
    host: '127.0.0.1',
    port: 0,
    default_backend: 'claude',
    default_harness: '',
    shell_exe: 'bash',
    harness_exe: { claude: 'claude', codex: 'codex' },
    harness_args: { claude: [], codex: [] },
    harness_enabled: { claude: true, codex: true, omp: false, pi: false, opencode: false },
    harness_mcp_enabled: {},
    harness_skill_enabled: {},
    harness_instrument_enabled: {},
    scrollback_bytes: 5242880,
    attach_replay_bytes: 524288,
    history_limit: 200,
    git_poll_seconds: 5.0,
    process_poll_seconds: 5.0,
    usage_command: [],
    usage_commands: {},
    ignore_patterns: [],
    project_ignore_patterns: [],
    quests_enabled: false,
    // The remainder mirrors the daemon's full config surface with neutral,
    // fictional values so every Settings section renders rather than crashing
    // on a missing field. Nothing here is copied from a real install.
    tailnet_enabled: false,
    wsl_bridge_enabled: false,
    pty_supervisor_enabled: true,
    session_recovery_enabled: true,
    session_recovery_checkpoint_bytes: 262144,
    session_recovery_retention_days: 7,
    session_recovery_max_sessions: 40,
    log_level: 'INFO',
    frontend_overlay_enabled: false,
    git_swe_mux_prompt_enabled: false,
    git_swe_mux_prompt_decisions: {},
    worktree_root: '/code/.worktrees',
    new_project_parent: '/code/',
    process_orphan_grace_seconds: 15.0,
    ghost_window_sweep_enabled: false,
    ghost_window_poll_seconds: 5.0,
    process_evidence_retention_days: 30,
    operational_telemetry_retention_days: 180,
    status_timeline_retention_days: 30,
    provider_quota_poll_minutes: 15,
    provider_quota_turn_refresh_enabled: false,
    provider_quota_turn_refresh_min_minutes: 5,
    reconcile_external_history: false,
    startup_cwd: '',
    clipboard_history_persist: true,
    clipboard_history_limit: 200,
    clipboard_history_entry_max_chars: 100000,
    clipboard_history_retention_hours: 128,
    clipboard_history_redact_secrets: true,
    note_font_family: '',
    note_font_size_px: 0,
    note_line_height: 0.0,
    note_command_rail: 'auto',
    note_rail_button_size_px: 0,
    note_indent_guides: true,
    note_shortcut_overrides: {},
    ccusage_refresh_minutes: 180,
    agent_shims_on_shell_path: false,
    pinned_directories: [],
    project_init_scripts: [],
    automation_enabled: false,
    automation_global_allow: {},
    agent_authority_default: {},
    agent_authority_ceiling: {},
    automation_retention_days: 90,
    prompt_queue_retention_days: 90,
    auto_delivery_enabled: true,
    auto_delivery_stable_seconds: 8.0,
    auto_delivery_max_consecutive: 10,
    auto_delivery_session_ttl_minutes: 120,
    auto_delivery_reply_window_minutes: 60,
    auto_delivery_quiet_start: '',
    auto_delivery_quiet_end: '',
    auto_delivery_refusal_backoff_seconds: 30.0,
    approval_auto_enabled: false,
    approval_grant_ttl_minutes: 30,
    approval_max_auto_per_grant: 200,
    approval_hook_timeout_seconds: 30,
    approval_allow_all_permitted: false,
    approval_keystroke_delivery: true,
    approval_keystroke_window_seconds: 30.0,
    agent_messaging_enabled: false,
    agent_message_limits_enabled: false,
    agent_message_max_chars: 4000,
    agent_message_hourly_budget: 20,
    agent_message_pending_per_target: 5,
    agent_message_max_chain_depth: 6,
    agent_message_max_thread_turns: 40,
    agent_interject_enabled: false,
    agent_interject_hourly_budget: 10,
    agent_interject_min_interval_seconds: 60.0,
    request_spawn_enabled: false,
    session_control_enabled: false,
    session_control_hourly_budget: 30,
    session_control_graceful_timeout_s: 12.0,
    agent_spawn_hourly_budget: 20,
    session_watch_enabled: false,
    session_watch_max_per_session: 8,
    session_watch_max_minutes: 240,
    scheduled_runs_enabled: false,
    scheduled_runs_max_concurrent: 3,
    scheduled_runs_poll_seconds: 5.0,
    scheduled_run_retention_days: 60,
    land_queue_enabled: false,
    land_hourly_budget: 12,
    land_hold_timeout_seconds: 1800.0,
    land_retry_verification: false,
    land_verify_memo_seconds: 86400.0,
    automation_concurrency: 10,
    automation_queue_size: 256,
    automation_max_input_tokens: 4096,
    automation_max_output_tokens: 900,
    automation_daily_budget: { tokens: null, usd: 5.0, mode: 'usd' },
    automation_rule_daily_budget: { tokens: null, usd: 10.0, mode: 'usd' },
    automation_hourly_call_cap: 2400,
    automation_rule_hourly_call_cap: 1200,
    project_card_model: '',
    project_card_daily_budget: { tokens: null, usd: 0.25, mode: 'usd' },
    project_card_max_input_tokens: 6000,
    project_card_max_output_tokens: 600,
    // On, so the Activity tab's Timeline demonstrates the surface rather than the
    // grant gate in front of it. Every model-backed switch stays off; the demo's
    // records are fixtures, not observations, and nothing here can call a provider.
    scan_timeline_enabled: true,
    scan_timeline_model: 'demo-observer',
    attention_daily_interrupt_budget: 4,
    attention_hourly_interrupt_cap: 2,
    attention_incident_window_seconds: 3600.0,
    attention_breakpoint_markers: true,
    attention_narration_enabled: false,
    attention_narration_model: '',
    openrouter_cheap_model: '',
    openrouter_standard_model: '',
    openrouter_request_timeout_seconds: 30.0,
    llm_provider: 'openrouter',
    custom_llm_base_url: '',
    custom_llm_model: '',
    custom_llm_catalog_url: '',
    assistant_enabled: false,
    assistant_model: '',
    assistant_daily_budget: { tokens: null, usd: 2.0, mode: 'usd' },
    assistant_max_output_tokens: 700,
    assistant_context_messages: 30,
    assistant_trust_reversible: 'cancel_window',
    assistant_stream_replies: true,
    observer_titler_enabled: false,
    attention_observers_enabled: false,
    tts_default_mode: 'off',
    tts_content: 'summary',
    tts_engine: 'kokoro',
    tts_kokoro_voice: 'af_heart',
    tts_kokoro_speed: 1.0,
    tts_kokoro_lexicon: {},
    tts_sapi_voice: '',
    tts_sapi_rate: 0,
    tts_edge_python: '',
    tts_edge_voice: '',
    tts_edge_rate_percent: 0,
    tts_edge_volume_percent: 0,
    tts_edge_pitch_hz: 0,
    tts_edge_risk_ack_version: 0,
    tts_summary_model: '',
    tts_summary_max_tokens: 500,
    tts_verbatim_max_chars: 6000,
    tts_daily_budget: { tokens: null, usd: 1.0, mode: 'usd' },
    tts_cache_mb: 200,
    stt_engine: 'whisper',
    stt_language: 'en-US',
    stt_whisper_model: 'base',
    stt_routing_model: '',
    voice_wake_words: ['mux'],
    voice_commands: [],
    voice_chat_patience_ms: 1200,
    data_dir: '/home/demo/.mux',
    access_mode: 'local',
    requires_auth: false,
    pty_windows: null,
    ccusage_enabled: false,
    shell_profiles: [],
    harness_setup_complete: true,
    experience_tier: 'automations',
    quests_dismissed: ['worktrees', 'phone'],
    theme: 'tokyo-night',
    custom_theme: {
      background: '#090a0c', panel: '#0d0f12', line: '#2a2e34',
      foreground: '#d9dde2', muted: '#848b94', accent: '#8bd450', error: '#f07178',
    },
    drawer_tab_display: 'icon',
    utility_rail_display: 'icon',
    xterm_scrollback_lines: 4000,
    terminal_renderer: 'dom',
    claude_max_columns: 0,
    ui_scale_desktop: 1.0,
    ui_scale_mobile: 1.0,
    rail_density_desktop: 'comfortable',
    rail_density_mobile: 'comfortable',
    rail_enabled_desktop: true,
    rail_enabled_mobile: true,
    middle_click_paste: true,
    broadcast_default: false,
    mobile_vertical_drag: 'smart',
    mobile_scroll_direction: 'natural',
    mobile_scroll_sensitivity: 1.0,
    mobile_long_press: 'context_menu',
    mobile_gestures: {
      swipe_left: 'drawer.toggle', swipe_right: 'sidebar.toggle',
      two_finger_swipe_left: 'mobileTab.next', two_finger_swipe_right: 'mobileTab.previous',
      two_finger_swipe_up: 'notes.open', two_finger_swipe_down: 'terminal.keyboardToggle',
      two_finger_tap: 'palette.open', rail_swipe_up: 'menu.toggle',
    },
    mobile_gesture_swipe_away_close: true,
    mobile_gesture_overlay_back: true,
    mobile_back_view_history: true,
    mobile_surface_gestures: true,
    terminal_auto_copy_selection: true,
    clipboard_history_enabled: true,
    note_spellcheck: false,
    note_syntax: 'markdown',
    note_tab_behavior: 'indent',
    note_shortcut_policy: 'browser-safe',
    tts_enabled: false,
    stt_enabled: false,
    provider_accounts_prompt_dismissed: true,
    update_check_enabled: false,
    default_shell_profile: 'default',
  }
}

/** Bump when the seed shape changes so persisted visitor state is discarded. */
export const DEMO_STATE_VERSION = 9

export function initialDemoState(): DemoState {
  return {
    version: DEMO_STATE_VERSION,
    sessions: SESSIONS.map(item => ({ ...item })),
    projects: PROJECTS.map(item => ({ ...item })),
    groups: [],
    previews: PREVIEWS.map(item => ({ ...item })),
    notes: NOTES.map(item => ({ ...item })),
    config: demoConfig(),
    keymapPreset: 'swemux',
    // Each transcript ends with that session's own composer, so the status line
    // under the box reports the same model and context the sidebar row does.
    terminals: {
      's-claude': claudeScrollback(composerFor('s-claude')),
      's-rage': rageScrollback(composerFor('s-rage')),
      's-working': workingScrollback(composerFor('s-working'), 'pull the coupon table out of the request path'),
      's-codex': codexScrollback(composerFor('s-codex')),
      's-shell': shellScrollback(),
      's-garden': spawnScrollback(composerFor('s-garden')),
      's-vibe': vibeScrollback(composerFor('s-vibe')),
      's-migrate': workingScrollback(composerFor('s-migrate'), 'migrate the meme schema to v3, keep the old ids'),
    },
    transcripts: initialTranscripts(now),
    timelines: initialTimelines(now),
    seq: 1,
  }
}
