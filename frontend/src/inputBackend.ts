import { isAgentBackend } from './harnessRegistry.ts'

/**
 * Which backend's rules govern *input encoding* for a pane, right now.
 *
 * A session spawned as a shell and promoted around an agent typed into it does
 * not become an agent pane at the moment the agent starts - it becomes one when
 * the daemon can prove which conversation started. Measured 2026-08-27 on the
 * frozen desktop app, that gap is about ten seconds (`session_spawned` 09:10:46,
 * `backend_detected` 09:10:56), and nearly all of it is the launch chain's own
 * boot rather than anything mux waits for.
 *
 * For that whole window the pane reads as `shell`, so every rule keyed on the
 * backend answers for a shell while an agent's composer is on screen: Shift+Enter
 * inserts nothing instead of a newline, a multi-line paste has its newlines
 * turned into carriage returns (which submits each line), and the harness's
 * leading-newline repair is skipped. It is also exactly when the CLI runs its
 * terminal capability probes and when a user, having just typed the agent's
 * name, starts typing their first prompt.
 *
 * `agent_launch_pending` closes that window: the daemon publishes the harness it
 * has seen launching before it can bind the conversation, and this resolves the
 * pane's input rules against it.
 *
 * **Only input encoding uses this.** Everything the promotion actually changes -
 * the transcript, the token accounting, resume, branch, the width envelope - is
 * still keyed on `backend`, because those need a *bound conversation* and this is
 * only evidence that one is coming. Guessing wrong here costs one keystroke's
 * encoding; guessing wrong there would attribute a conversation.
 */
export function resolveInputBackend(session: {
  backend: string
  agent_launch_pending?: string[]
}): string {
  if (isAgentBackend(session.backend)) return session.backend
  // A shell that has been resolved already wins; only an unpromoted one defers.
  if (session.backend !== 'shell') return session.backend
  const pending = session.agent_launch_pending || []
  // Exactly one candidate, or nothing. Two harness names in a shell's output mean
  // the daemon cannot tell which is launching either (its own promotion refuses
  // an ambiguous match for the same reason), and picking one would apply a
  // measured harness's byte sequences to a different harness's composer.
  const candidates = pending.filter(name => isAgentBackend(name))
  return candidates.length === 1 ? candidates[0] : session.backend
}

/** Whether this pane should be treated as an agent for input purposes. */
export function inputBackendIsAgent(session: {
  backend: string
  agent_launch_pending?: string[]
}): boolean {
  return isAgentBackend(resolveInputBackend(session))
}
