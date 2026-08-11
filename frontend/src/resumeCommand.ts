import { assignsConversationId, harnessDescriptor } from './harnessRegistry.ts'
import type { Session } from './types'

type ResumeSource = Pick<Session, 'id' | 'backend' | 'native_session_id'>

/**
 * The clean provider CLI command that resumes this agent conversation in any
 * standalone terminal. Deliberately NOT the mux-instrumented argv (no `--settings`
 * hook injection, no `-c notify=...`), because the copied string is for a human to
 * paste outside mux.
 *
 * Composed from the harness's own declared resume grammar (`cli_name` plus
 * `resume_argv`) rather than from a list of names. The list version knew Claude,
 * Codex, and OMP, so pi and opencode had no copyable resume command at all even
 * though both resume by id through `--session`.
 *
 * Returns null when there is nothing resumable:
 * - a shell has no provider conversation, and neither has an unknown harness;
 * - a harness that mints its own conversation id carries a placeholder equal to the
 *   mux id until the first write is observed, and resuming that would find nothing.
 *   A harness where mux dictated the id (`assigns_conversation_id`) is resumable
 *   from its first moment.
 *
 * A harness that resolves transcripts by working directory only resumes when the
 * command runs from the session's cwd, which callers surface separately.
 */
export function resumeCommand(session: ResumeSource): string | null {
  const id = (session.native_session_id || '').trim()
  if (!id) return null
  const harness = harnessDescriptor(session.backend)
  const cli = harness?.cli_name
  const argv = harness?.resume_argv
  if (!cli || !argv?.length) return null
  if (!assignsConversationId(session.backend) && id === session.id) return null
  return [cli, ...argv, id].join(' ')
}
