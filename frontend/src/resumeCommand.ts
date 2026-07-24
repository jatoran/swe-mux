import type { Session } from './types'

type ResumeSource = Pick<Session, 'id' | 'backend' | 'native_session_id'>

/**
 * The clean provider CLI command that resumes this agent conversation in any
 * standalone terminal — deliberately NOT the mux-instrumented argv (no
 * `--settings` hook injection, no `-c notify=…`), because the copied string is
 * for a human to paste outside mux.
 *
 * Returns null when no resumable id exists yet:
 * - shell sessions have no provider conversation;
 * - Claude sessions are resumable immediately (mux assigns the native id via
 *   `--session-id` at spawn, so it always equals the mux id);
 * - Codex assigns its own session id, which mux only discovers after the first
 *   rollout file is written. Until then `native_session_id` is a placeholder
 *   equal to the mux id, which `codex resume` would not recognize.
 *
 * Claude resolves transcripts by working directory, so its command only
 * resumes when run from the session's cwd — callers should surface that.
 */
export function resumeCommand(session: ResumeSource): string | null {
  const id = (session.native_session_id || '').trim()
  if (!id) return null
  if (session.backend === 'claude') return `claude --resume ${id}`
  if (session.backend === 'codex') return id === session.id ? null : `codex resume ${id}`
  return null
}
