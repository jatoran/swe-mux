/**
 * Event types whose payload or dedicated consumer already carries the complete
 * update. Refetching sessions, projects, previews, groups, and harnesses for these
 * events adds no state and makes transcript/tool traffic contend with terminal input.
 *
 * Unknown event types still refresh. That is the compatibility-safe direction:
 * adding a new state-changing daemon event cannot silently leave the UI stale.
 */
const EVENT_HANDLED_WITHOUT_FLEET_REFRESH = new Set([
  'PreToolUse',
  'PostToolUse',
  'agent_context_changed',
  'clipboard_changed',
  'configuration_changed',
  'note_changed',
  'notification',
  'notification_created',
  'project_files_changed',
  'project_used',
  'queue_delivery',
  'queue_updated',
  'settings_changed',
  'terminal_attached',
  'terminal_client_repair',
  'terminal_detached',
  'terminal_input',
  'terminal_input_diagnostic',
  'terminal_mode_changed',
  'terminal_repaint_requested',
  'tool_result',
  'tool_use',
  'transcript_message',
  'voice_clip_failed',
  'voice_clip_ready',
])

export function eventRequiresFleetRefresh(type: unknown): boolean {
  return typeof type !== 'string' || !EVENT_HANDLED_WITHOUT_FLEET_REFRESH.has(type)
}
