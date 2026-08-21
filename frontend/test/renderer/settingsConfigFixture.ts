// The daemon's own default public config, dumped from `Config(data_dir=...).public_dict()`
// so the Settings harness renders against the real field set rather than a hand-kept
// subset that quietly goes stale. Regenerate by dumping `public_dict()` again if a field
// is added. It had gone stale exactly that way: every `assistant_*` field was missing and
// four removed `tts_edge_*` fields were still here, so the assistant tab rendered against
// values the daemon has not served for months. A *value* can go stale the same way and is
// harder to see: `tts_engine` sat at the removed `"edge"`, which `validate_config` rejects
// and schema 26 migrates away, and which matches neither engine branch — so the Voice tab
// rendered with no engine controls at all and nothing said so.
//
// Most of it only has to be complete enough that every tab renders. The exception is the
// model ids, which `model-picker.spec.ts` resolves against a stubbed catalog, so a default
// that moves in `config.py` surfaces there as a failed assertion rather than as drift.
export const SETTINGS_CONFIG_FIXTURE = {
  "access_mode": "local+tailnet",
  "agent_interject_enabled": true,
  "agent_interject_hourly_budget": 10,
  "agent_interject_min_interval_seconds": 60.0,
  "agent_message_hourly_budget": 20,
  "agent_message_max_chain_depth": 6,
  "agent_message_max_chars": 4000,
  "agent_message_max_thread_turns": 40,
  "agent_message_pending_per_target": 5,
  "agent_messaging_enabled": true,
  "agent_spawn_hourly_budget": 10,
  "approval_allow_all_permitted": true,
  "approval_auto_enabled": false,
  "approval_grant_ttl_minutes": 30,
  "approval_hook_timeout_seconds": 5.0,
  "approval_keystroke_delivery": true,
  "approval_keystroke_window_seconds": 30.0,
  "approval_max_auto_per_grant": 200,
  "assistant_context_messages": 30,
  "assistant_daily_budget": {
    "tokens": null,
    "usd": 2.0,
    "mode": "usd"
  },
  "assistant_enabled": false,
  "assistant_max_output_tokens": 700,
  "assistant_model": "openai/gpt-5.6-terra",
  "assistant_stream_replies": true,
  "assistant_trust_reversible": "cancel_window",
  "attach_replay_bytes": 524288,
  "attention_breakpoint_markers": true,
  "attention_daily_interrupt_budget": 4,
  "attention_hourly_interrupt_cap": 2,
  "attention_incident_window_seconds": 3600.0,
  "attention_narration_daily_budget": {
    "tokens": null,
    "usd": 0.1,
    "mode": "usd"
  },
  "attention_narration_enabled": false,
  "attention_narration_max_output_tokens": 200,
  "attention_narration_model": "",
  "auto_delivery_enabled": false,
  "auto_delivery_max_consecutive": 3,
  "auto_delivery_quiet_end": "",
  "auto_delivery_quiet_start": "",
  "auto_delivery_refusal_backoff_seconds": 30.0,
  "auto_delivery_reply_window_minutes": 30,
  "auto_delivery_session_ttl_minutes": 60,
  "auto_delivery_stable_seconds": 8.0,
  "automation_concurrency": 2,
  "automation_daily_budget": {
    "tokens": 10000000,
    "usd": 20.0,
    "mode": "either"
  },
  "automation_enabled": false,
  "automation_hourly_call_cap": 1200,
  "automation_max_input_tokens": 4096,
  "automation_max_output_tokens": 256,
  "automation_queue_size": 256,
  "automation_retention_days": 90,
  "automation_rule_daily_budget": {
    "tokens": 4000000,
    "usd": 10.0,
    "mode": "either"
  },
  "automation_rule_hourly_call_cap": 600,
  "broadcast_default": false,
  "ccusage_enabled": false,
  "ccusage_refresh_minutes": 0,
  "claude_max_columns": 120,
  "clipboard_history_enabled": true,
  "clipboard_history_entry_max_chars": 100000,
  "clipboard_history_limit": 200,
  "clipboard_history_persist": false,
  "clipboard_history_redact_secrets": true,
  "clipboard_history_retention_hours": 24,
  "custom_llm_base_url": "",
  "custom_llm_model": "",
  "custom_theme": {
    "background": "#090a0c",
    "panel": "#0d0f12",
    "line": "#2a2e34",
    "foreground": "#d9dde2",
    "muted": "#848b94",
    "accent": "#8bd450",
    "error": "#f07178"
  },
  "data_dir": "C:\\Users\\demo\\.mux",
  "default_backend": "shell",
  "default_shell_profile": "default",
  "drawer_tab_display": "icon",
  "ghost_window_poll_seconds": 5.0,
  "ghost_window_sweep_enabled": true,
  "git_poll_seconds": 5.0,
  "harness_args": {
    "claude": [],
    "codex": [],
    "omp": [],
    "pi": [],
    "opencode": []
  },
  "harness_enabled": {},
  "harness_exe": {
    "claude": "claude.exe",
    "codex": "codex.exe",
    "omp": "omp",
    "pi": "pi",
    "opencode": "opencode"
  },
  "harness_instrument_enabled": {},
  "harness_mcp_enabled": {},
  "harness_setup_complete": false,
  "history_limit": 200,
  "host": "127.0.0.1",
  "land_hold_timeout_seconds": 1800.0,
  "land_hourly_budget": 12,
  "land_queue_enabled": true,
  "land_retry_verification": false,
  "land_verify_memo_seconds": 86400.0,
  "llm_provider": "openrouter",
  "log_level": "INFO",
  "middle_click_paste": true,
  "mobile_back_view_history": true,
  "mobile_gesture_overlay_back": true,
  "mobile_gesture_swipe_away_close": true,
  "mobile_gestures": {
    "swipe_left": "mobileTab.next",
    "swipe_right": "mobileTab.previous",
    "two_finger_swipe_left": "drawer.toggle",
    "two_finger_swipe_right": "sidebar.toggle",
    "two_finger_swipe_up": "notes.open",
    "two_finger_swipe_down": "terminal.keyboardToggle",
    "two_finger_tap": "palette.open"
  },
  "mobile_long_press": "context_menu",
  "mobile_scroll_direction": "natural",
  "mobile_scroll_sensitivity": 1.0,
  "mobile_vertical_drag": "smart",
  "new_project_parent": "",
  "note_command_rail": "auto",
  "note_font_family": "",
  "note_font_size_px": 0,
  "note_indent_guides": true,
  "note_line_height": 0.0,
  "note_rail_button_size_px": 0,
  "note_shortcut_overrides": {
    "mod+r": "editor.toggle_bullet_at_line_start",
    "mod+e": "markdown.toggle_task"
  },
  "note_shortcut_policy": "browser-safe",
  "note_spellcheck": false,
  "note_syntax": "markdown",
  "note_tab_behavior": "indent",
  "observer_titler_enabled": false,
  "openrouter_cheap_model": "",
  "openrouter_request_timeout_seconds": 30.0,
  "openrouter_standard_model": "",
  "operational_telemetry_retention_days": 180,
  "phase7_observers_enabled": false,
  "pinned_directories": [],
  "port": 8765,
  "process_evidence_retention_days": 30,
  "process_orphan_grace_seconds": 15.0,
  "process_poll_seconds": 5.0,
  "project_card_daily_budget": {
    "tokens": null,
    "usd": 0.25,
    "mode": "usd"
  },
  "project_card_max_input_tokens": 6000,
  "project_card_max_output_tokens": 600,
  "project_card_model": "",
  "project_ignore_patterns": [
    ".git",
    ".swe-mux",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "node_modules",
    ".next",
    ".nuxt",
    ".turbo",
    ".cache",
    "dist",
    "build",
    "coverage",
    "*.pyc",
    "*.pyo",
    "*.code-workspace",
    "uv.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock"
  ],
  "project_init_scripts": [],
  "prompt_queue_retention_days": 90,
  "provider_quota_poll_minutes": 15,
  "provider_quota_turn_refresh_enabled": false,
  "provider_quota_turn_refresh_min_minutes": 5,
  "pty_supervisor_enabled": false,
  "pty_windows": {
    "backend": "conpty",
    "build_number": 19045
  },
  "reconcile_external_history": true,
  "request_spawn_enabled": true,
  "requires_auth": false,
  "revision": 1,
  "scan_timeline_daily_budget": {
    "tokens": 3000000,
    "usd": 5.0,
    "mode": "either"
  },
  "scan_timeline_enabled": false,
  "scan_timeline_hourly_call_cap": 600,
  "scan_timeline_max_output_tokens": 900,
  "scan_timeline_model": "deepseek/deepseek-v4-flash",
  "scan_timeline_run_budget": {
    "tokens": 500000,
    "usd": null,
    "mode": "tokens"
  },
  "scheduled_run_retention_days": 60,
  "scheduled_runs_enabled": true,
  "scheduled_runs_max_concurrent": 3,
  "scheduled_runs_poll_seconds": 5.0,
  "schema_version": 30,
  "scrollback_bytes": 5242880,
  "session_control_enabled": true,
  "session_control_graceful_timeout_s": 12.0,
  "session_control_hourly_budget": 30,
  "session_recovery_checkpoint_bytes": 262144,
  "session_recovery_enabled": true,
  "session_recovery_max_sessions": 40,
  "session_recovery_retention_days": 7,
  "session_watch_enabled": true,
  "session_watch_max_minutes": 240,
  "session_watch_max_per_session": 8,
  "shell_exe": "powershell.exe",
  "shell_profiles": [],
  "startup_cwd": "",
  "status_timeline_retention_days": 30,
  "stt_enabled": false,
  "stt_engine": "whisper",
  "stt_language": "en-US",
  "stt_routing_model": "small.en",
  "stt_whisper_model": "turbo",
  "tailnet_enabled": true,
  "terminal_auto_copy_selection": true,
  "terminal_renderer": "auto",
  "theme": "dark",
  "tts_cache_mb": 200,
  "tts_content": "summary",
  "tts_daily_budget": {
    "tokens": null,
    "usd": 1.0,
    "mode": "usd"
  },
  "tts_default_mode": "off",
  "tts_enabled": false,
  "tts_engine": "sapi",
  "tts_kokoro_speed": 1.0,
  "tts_kokoro_voice": "af_heart",
  "tts_lexicon": {},
  "tts_sapi_rate": 0,
  "tts_sapi_voice": "",
  "tts_summary_max_tokens": 500,
  "tts_summary_model": "",
  "tts_verbatim_max_chars": 6000,
  "ui_scale_desktop": 1.0,
  "ui_scale_mobile": 1.0,
  "usage_command": [
    "ccusage",
    "daily",
    "--json",
    "--by-agent"
  ],
  "usage_commands": {},
  "utility_rail_display": "icon",
  "voice_chat_patience_ms": 1200,
  "voice_commands": [
    {
      "action": "send",
      "phrases": [
        "send",
        "send it",
        "send that",
        "send message",
        "submit",
        "submit it",
        "submit that",
        "submit message"
      ]
    },
    {
      "action": "append",
      "phrases": [
        "append",
        "append it",
        "append that",
        "append message",
        "insert",
        "insert it",
        "insert that"
      ]
    },
    {
      "action": "cancel",
      "phrases": [
        "cancel",
        "cancel that",
        "clear",
        "clear that"
      ]
    },
    {
      "action": "undo",
      "phrases": [
        "undo",
        "undo that",
        "undo last",
        "undo last phrase",
        "delete last",
        "delete last phrase"
      ]
    },
    {
      "action": "mute",
      "phrases": [
        "mute",
        "stop",
        "stop speaking",
        "stop playback",
        "stop audio"
      ]
    },
    {
      "action": "read",
      "phrases": [
        "read",
        "read reply",
        "read the reply",
        "read reply again",
        "read the reply again",
        "read response",
        "speak reply",
        "speak the reply"
      ]
    },
    {
      "action": "summary",
      "phrases": [
        "summary",
        "summary mode",
        "use summaries"
      ]
    },
    {
      "action": "verbatim",
      "phrases": [
        "verbatim",
        "verbatim mode",
        "read verbatim"
      ]
    },
    {
      "action": "interrupt",
      "phrases": [
        "interrupt",
        "interrupt agent",
        "interrupt the agent"
      ]
    },
    {
      "action": "help",
      "phrases": [
        "help",
        "list commands",
        "what can i say"
      ]
    },
    {
      "action": "standby",
      "phrases": [
        "sleep",
        "go to sleep",
        "stand by",
        "standby",
        "pause listening"
      ]
    },
    {
      "action": "resume",
      "phrases": [
        "wake",
        "wake up",
        "resume",
        "start listening"
      ]
    },
    {
      "action": "hold",
      "phrases": [
        "listen",
        "just listen",
        "brainstorm",
        "hold that thought",
        "let me think"
      ]
    },
    {
      "action": "proceed",
      "phrases": [
        "go ahead",
        "your turn",
        "over to you",
        "proceed"
      ]
    },
    {
      "action": "comms_on",
      "phrases": [
        "voice comms",
        "voice comms on",
        "start voice comms",
        "enter voice comms"
      ]
    },
    {
      "action": "comms_off",
      "phrases": [
        "voice comms off",
        "stop voice comms",
        "exit voice comms"
      ]
    },
    {
      "action": "stop",
      "phrases": [
        "stop listening",
        "turn off",
        "shut down"
      ]
    }
  ],
  "voice_wake_words": [
    "mux",
    "mucks",
    "max"
  ],
  "worktree_root": "C:\\Users\\demo\\worktrees",
  "wsl_bridge_enabled": false,
  "xterm_scrollback_lines": 32768
}
