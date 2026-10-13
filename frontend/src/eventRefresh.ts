import type { SessionRowPatch } from './sessionSnapshots.ts'

/**
 * What an `/events` frame asks of the fleet the composition root holds.
 *
 * - `none`: the payload or a dedicated consumer already carries the complete update.
 * - `patch`: the payload carries the complete new values of the one row it changed
 *   (`rowPatchOf`), which is written in place, ordered by session revision.
 * - `session`: one session's row changed and the event does not say how, so that
 *   row alone is re-read (`GET /api/sessions/{id}`).
 * - `fleet`: membership, a Project, a Preview, a Group or the harness registry may
 *   have changed, or the event is not understood, so all five slices are re-read.
 *
 * Measured 2026-10-12 over 24 hours of the operator's fleet: 645 refresh-triggering
 * events an hour, and a desktop window re-reading all five fleet endpoints about 9
 * times a minute. Two thirds of those events changed exactly one row - the status
 * line alone (`harness_status`) was 205 an hour.
 */
export type FleetEventAction = 'none' | 'patch' | 'session' | 'fleet'

export type FleetEventFrame = { type?: unknown; session_id?: unknown; payload?: unknown }

/**
 * Event types whose payload or dedicated consumer already carries the complete
 * update. Refetching sessions, projects, previews, groups, and harnesses for these
 * events adds no state and makes transcript/tool traffic contend with terminal input.
 */
const EVENT_HANDLED_WITHOUT_FLEET_REFRESH = new Set([
  'PreToolUse',
  'PostToolUse',
  'agent_context_changed',
  'clipboard_changed',
  'configuration_changed',
  // Carries the complete new readiness for one session and is patched straight
  // onto that row. Its whole reason to exist is that a fleet refetch is the wrong
  // response to it: the daemon emits this at up to once a second, and the reasons
  // it reports (typing, screen changes, debounce thresholds) are exactly the ones
  // whose own events are excluded here for the same cost reason.
  'delivery_readiness_changed',
  'note_changed',
  'notification',
  'notification_created',
  'project_files_changed',
  'project_used',
  // The account views poll their own endpoint; no fleet slice carries quota.
  'provider_quota_refreshed',
  'queue_delivery',
  'queue_updated',
  'settings_changed',
  'spawn_request_decided',
  'spawn_request_drafted',
  'terminal_attached',
  'terminal_client_repair',
  'terminal_detached',
  'terminal_input',
  'terminal_input_diagnostic',
  'terminal_mode_changed',
  // A paste's timing trace, for diagnostics. It changes nothing a row shows.
  'terminal_paste_trace',
  'terminal_repaint_requested',
  'tool_result',
  'tool_use',
  // Both are cues for the drawer's transcript reader, which reads its own endpoint.
  // `transcript_appended` arrives every two seconds per working agent; routed to a
  // fleet refresh it would re-read all five slices that often for nothing.
  'transcript_appended',
  'transcript_message',
  'assistant_turn_queued',
  'voice_clip_failed',
  'voice_clip_joined',
  'voice_clip_ready',
  'voice_stream_closed',
])

/**
 * Event types that carry a row patch for their session (`session_patch.py` on the
 * daemon). Without one - an older daemon - they fall back to re-reading the row.
 */
const EVENT_PATCHES_ONE_ROW = new Set([
  'git_changed',
  'harness_status',
])

/**
 * Event types that change their own session's row and nothing else the fleet
 * slices hold. Each names its session, and the row is re-read alone.
 *
 * Membership (`session_spawned`, `session_exited`, `session_removed`,
 * `session_branched`), Previews (`listener_detected`, `listener_closed`,
 * `preview_registered`) and everything not listed here stay `fleet`: an event this
 * list does not know about cannot silently leave a slice stale.
 */
const EVENT_CHANGES_ONE_ROW = new Set([
  'PermissionRequest',
  'PostToolUseFailure',
  'SessionEnd',
  'SessionStart',
  'Stop',
  'SubagentStart',
  'SubagentStop',
  'UserPromptSubmit',
  'agent-turn-complete',
  'agent_native_id_bound',
  'agent_shim_report',
  'annotation_created',
  'approval_detected',
  'approval_needed',
  'claim_unverified',
  'composer_input_changed',
  'context_compacted',
  'git_provenance_changed',
  'observation_stale',
  'observation_stale_cleared',
  'queue_auto_policy',
  'reply_copy',
  'root_turn_settled',
  'runtime_cwd_changed',
  'session_attachment_uploaded',
  'session_read',
  'session_startup_client_measured',
  'session_startup_measured',
  'skill_invoked',
  'stalled',
  'state_changed',
  'subagent_activity',
  'terminal_input_owner',
  'title_regenerate_requested',
  'transcript_process_binding',
  'transcript_provisionally_bound',
  'turn_aborted',
  'turn_ended',
  'turn_started',
  'unattended_attention',
  'unwitnessed_turn_armed',
])

/** The row patch an event carries, or null when it carries none this client can order. */
export function rowPatchOf(payload: unknown): SessionRowPatch | null {
  if (!payload || typeof payload !== 'object') return null
  const { patch, session_revision: revision } = payload as Record<string, unknown>
  if (!patch || typeof patch !== 'object' || Array.isArray(patch)) return null
  if (typeof revision !== 'number' || !Number.isSafeInteger(revision) || revision < 0) return null
  return { fields: patch as Record<string, unknown>, revision }
}

export function fleetEventAction(event: FleetEventFrame): FleetEventAction {
  const type = event.type
  if (typeof type !== 'string') return 'fleet'
  if (EVENT_HANDLED_WITHOUT_FLEET_REFRESH.has(type)) return 'none'
  // A row event with no session to name is a Project-level change (`git_changed`
  // after a Git drawer action, an annotation with no run), which is a fleet one.
  const sessionId = typeof event.session_id === 'string' ? event.session_id : ''
  if (!sessionId) return 'fleet'
  if (EVENT_PATCHES_ONE_ROW.has(type)) return rowPatchOf(event.payload) ? 'patch' : 'session'
  if (EVENT_CHANGES_ONE_ROW.has(type)) return 'session'
  return 'fleet'
}
