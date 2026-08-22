// How a session's read-aloud participation resolves, in one place.
//
// Three surfaces need the same answer and must never disagree: the voice panel's
// `tts` tab (which edits it for the focused session), the session context menu
// (which edits it for any session), and the sidebar row / tab-strip mark (which
// reports it for every session at once). A session stores `voice_mode` only once
// somebody has chosen one; until then it inherits the global default, and the
// master switch overrides both — with read aloud off, nothing generates and
// nothing speaks, whatever a session row says.
//
// Pure and dependency-free on purpose: the row-token engine imports it, and that
// engine must not pull in the playback module's audio element or its
// module-level device state.

import type { Session, VoiceMode } from './types'

/** The two facts about the global switch that a per-session mode resolves against. */
export interface VoiceModeDefaults {
  enabled: boolean
  default_mode: VoiceMode
}

export const VOICE_MODE_OFF: VoiceModeDefaults = { enabled: false, default_mode: 'off' }

/**
 * What this session actually does with completed replies.
 *
 * `off` whenever the master switch is off, because a stored `auto` on a session
 * is a preference the daemon is currently ignoring, and reporting it as live
 * would put a speaker mark on a fleet that cannot make a sound.
 */
export function resolveVoiceMode(
  session: Pick<Session, 'voice_mode'>,
  defaults: VoiceModeDefaults | null,
): VoiceMode {
  if (!defaults?.enabled) return 'off'
  const mode = session.voice_mode
  if (mode === 'off' || mode === 'on_demand' || mode === 'auto') return mode
  return defaults.default_mode
}

export const voiceModeLabel = (mode: VoiceMode): string =>
  mode === 'on_demand' ? 'on demand' : mode === 'auto' ? 'auto on reply' : 'off'
