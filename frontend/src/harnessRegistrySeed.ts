// GENERATED FILE - do not edit by hand.
// Regenerate: uv run python packaging/generate_frontend_registry.py
// Source of truth: src/swe_mux/harness.py (public_harness_registry).
// tests/test_harness_registry.py fails when this file drifts from it.
import type { HarnessRegistryPayload } from './harnessRegistry.ts'

export const HARNESS_REGISTRY_SEED: HarnessRegistryPayload = {
  "version": 2,
  "harnesses": [
    {
      "name": "claude",
      "tested_cli_version": null,
      "display_name": "Claude Code",
      "level": "managed",
      "state_sources": [
        "transcript",
        "hook",
        "pty",
        "cli_state"
      ],
      "measurement_source": "transcript",
      "cli_name": "claude",
      "resume_argv": [
        "--resume"
      ],
      "reserved_launch_args": [
        "--session-id",
        "--settings",
        "--mcp-config",
        "--resume",
        "-r",
        "--continue",
        "-c",
        "--fork-session"
      ],
      "skill_invocation_prefix": "/",
      "composer_clear_keys": "\u001b\u001b",
      "composer_newline": "\u001b\r",
      "paste_leading_newline_submits": false,
      "capabilities": {
        "observed": true,
        "transcript": true,
        "measurement": true,
        "lifecycle_hooks": true,
        "mcp": true,
        "pty_delivery": true,
        "provider_accounts": true,
        "repaints_scrollback": false,
        "assigns_conversation_id": true,
        "resolves_transcript_by_cwd": true,
        "branch": true,
        "branch_from_message": true,
        "webgl_unsafe": true,
        "owns_scroll_viewport": true,
        "width_envelope": true,
        "min_desktop_columns": null,
        "suppresses_late_color_response": false,
        "touch_scroll_rows_per_report": 3
      }
    },
    {
      "name": "codex",
      "tested_cli_version": null,
      "display_name": "Codex",
      "level": "managed",
      "state_sources": [
        "transcript",
        "hook",
        "pty"
      ],
      "measurement_source": "transcript",
      "cli_name": "codex",
      "resume_argv": [
        "resume"
      ],
      "reserved_launch_args": [
        "resume",
        "notify=",
        "mcp_servers.mux."
      ],
      "skill_invocation_prefix": "$",
      "composer_clear_keys": "\u0015",
      "composer_newline": "\u001b\r",
      "paste_leading_newline_submits": true,
      "capabilities": {
        "observed": true,
        "transcript": true,
        "measurement": true,
        "lifecycle_hooks": true,
        "mcp": true,
        "pty_delivery": true,
        "provider_accounts": true,
        "repaints_scrollback": true,
        "assigns_conversation_id": false,
        "resolves_transcript_by_cwd": false,
        "branch": true,
        "branch_from_message": false,
        "webgl_unsafe": false,
        "owns_scroll_viewport": false,
        "width_envelope": false,
        "min_desktop_columns": 80,
        "suppresses_late_color_response": true,
        "touch_scroll_rows_per_report": 1
      }
    },
    {
      "name": "omp",
      "tested_cli_version": null,
      "display_name": "oh-my-pi",
      "level": "managed",
      "state_sources": [
        "hook",
        "transcript",
        "pty"
      ],
      "measurement_source": "transcript",
      "cli_name": "omp",
      "resume_argv": [
        "--resume"
      ],
      "reserved_launch_args": [
        "--resume"
      ],
      "skill_invocation_prefix": "/skill:",
      "composer_clear_keys": "\u0015",
      "composer_newline": "\u001b\r",
      "paste_leading_newline_submits": false,
      "capabilities": {
        "observed": true,
        "transcript": true,
        "measurement": true,
        "lifecycle_hooks": true,
        "mcp": true,
        "pty_delivery": true,
        "provider_accounts": false,
        "repaints_scrollback": true,
        "assigns_conversation_id": false,
        "resolves_transcript_by_cwd": true,
        "branch": false,
        "branch_from_message": false,
        "webgl_unsafe": true,
        "owns_scroll_viewport": false,
        "width_envelope": false,
        "min_desktop_columns": null,
        "suppresses_late_color_response": false,
        "touch_scroll_rows_per_report": 1
      }
    },
    {
      "name": "pi",
      "tested_cli_version": null,
      "display_name": "pi",
      "level": "managed",
      "state_sources": [
        "hook",
        "transcript",
        "pty"
      ],
      "measurement_source": "transcript",
      "cli_name": "pi",
      "resume_argv": [
        "--session"
      ],
      "reserved_launch_args": [
        "--session",
        "--resume"
      ],
      "skill_invocation_prefix": "/",
      "composer_clear_keys": "\u0015",
      "composer_newline": "\u001b\r",
      "paste_leading_newline_submits": false,
      "capabilities": {
        "observed": true,
        "transcript": true,
        "measurement": true,
        "lifecycle_hooks": true,
        "mcp": false,
        "pty_delivery": true,
        "provider_accounts": false,
        "repaints_scrollback": true,
        "assigns_conversation_id": false,
        "resolves_transcript_by_cwd": true,
        "branch": false,
        "branch_from_message": false,
        "webgl_unsafe": false,
        "owns_scroll_viewport": false,
        "width_envelope": false,
        "min_desktop_columns": null,
        "suppresses_late_color_response": false,
        "touch_scroll_rows_per_report": 1
      }
    },
    {
      "name": "opencode",
      "tested_cli_version": null,
      "display_name": "opencode",
      "level": "managed",
      "state_sources": [
        "hook"
      ],
      "measurement_source": "database",
      "cli_name": "opencode",
      "resume_argv": [
        "--session"
      ],
      "reserved_launch_args": [
        "--session"
      ],
      "skill_invocation_prefix": "/",
      "composer_clear_keys": "\u0015",
      "composer_newline": "\u001b\r",
      "paste_leading_newline_submits": false,
      "capabilities": {
        "observed": true,
        "transcript": true,
        "measurement": true,
        "lifecycle_hooks": true,
        "mcp": true,
        "pty_delivery": true,
        "provider_accounts": false,
        "repaints_scrollback": false,
        "assigns_conversation_id": false,
        "resolves_transcript_by_cwd": false,
        "branch": false,
        "branch_from_message": false,
        "webgl_unsafe": false,
        "owns_scroll_viewport": false,
        "width_envelope": false,
        "min_desktop_columns": null,
        "suppresses_late_color_response": false,
        "touch_scroll_rows_per_report": 1
      }
    }
  ]
}
