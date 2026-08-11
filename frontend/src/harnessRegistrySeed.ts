// GENERATED FILE - do not edit by hand.
// Regenerate: uv run python packaging/generate_frontend_registry.py
// Source of truth: src/swe_mux/harness.py (public_harness_registry).
// tests/test_harness_registry.py fails when this file drifts from it.
import type { HarnessRegistryPayload } from './harnessRegistry.ts'

export const HARNESS_REGISTRY_SEED: HarnessRegistryPayload = {
  "version": 1,
  "harnesses": [
    {
      "name": "claude",
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
      "skill_invocation_prefix": "/",
      "capabilities": {
        "observed": true,
        "transcript": true,
        "measurement": true,
        "lifecycle_hooks": true,
        "pty_delivery": true,
        "external_usage": true,
        "provider_accounts": true,
        "repaints_scrollback": false,
        "assigns_conversation_id": true,
        "resolves_transcript_by_cwd": true,
        "branch": true,
        "webgl_unsafe": true,
        "owns_scroll_viewport": true,
        "width_envelope": true,
        "min_desktop_columns": null,
        "suppresses_late_color_response": false
      }
    },
    {
      "name": "codex",
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
      "skill_invocation_prefix": "$",
      "capabilities": {
        "observed": true,
        "transcript": true,
        "measurement": true,
        "lifecycle_hooks": true,
        "pty_delivery": true,
        "external_usage": true,
        "provider_accounts": true,
        "repaints_scrollback": true,
        "assigns_conversation_id": false,
        "resolves_transcript_by_cwd": false,
        "branch": true,
        "webgl_unsafe": false,
        "owns_scroll_viewport": false,
        "width_envelope": false,
        "min_desktop_columns": 80,
        "suppresses_late_color_response": true
      }
    },
    {
      "name": "omp",
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
      "skill_invocation_prefix": "/skill:",
      "capabilities": {
        "observed": true,
        "transcript": true,
        "measurement": true,
        "lifecycle_hooks": true,
        "pty_delivery": true,
        "external_usage": false,
        "provider_accounts": false,
        "repaints_scrollback": true,
        "assigns_conversation_id": false,
        "resolves_transcript_by_cwd": true,
        "branch": false,
        "webgl_unsafe": true,
        "owns_scroll_viewport": false,
        "width_envelope": false,
        "min_desktop_columns": null,
        "suppresses_late_color_response": false
      }
    },
    {
      "name": "pi",
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
      "skill_invocation_prefix": "/",
      "capabilities": {
        "observed": true,
        "transcript": true,
        "measurement": true,
        "lifecycle_hooks": true,
        "pty_delivery": true,
        "external_usage": false,
        "provider_accounts": false,
        "repaints_scrollback": true,
        "assigns_conversation_id": false,
        "resolves_transcript_by_cwd": true,
        "branch": false,
        "webgl_unsafe": false,
        "owns_scroll_viewport": false,
        "width_envelope": false,
        "min_desktop_columns": null,
        "suppresses_late_color_response": false
      }
    },
    {
      "name": "opencode",
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
      "skill_invocation_prefix": "/",
      "capabilities": {
        "observed": true,
        "transcript": true,
        "measurement": true,
        "lifecycle_hooks": true,
        "pty_delivery": true,
        "external_usage": false,
        "provider_accounts": false,
        "repaints_scrollback": false,
        "assigns_conversation_id": false,
        "resolves_transcript_by_cwd": false,
        "branch": false,
        "webgl_unsafe": false,
        "owns_scroll_viewport": false,
        "width_envelope": false,
        "min_desktop_columns": null,
        "suppresses_late_color_response": false
      }
    }
  ]
}
