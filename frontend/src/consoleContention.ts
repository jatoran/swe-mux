import type { ConsoleContention, Session } from './types'

export type ConsoleContentionNotice = {
  /** One line, stating the state rather than naming its cause. */
  text: string
  /** What the operator can do, as a title/tooltip on the row. */
  hint: string
}

/**
 * What to say when a pane's console has more than one reader.
 *
 * This is the notice for the failure that looks most like the app being broken
 * and says the least about itself: the agent is on screen and painting, and none
 * of what the user types appears in its composer, because the shell that launched
 * it has the terminal back and is eating half the keystrokes. Measured
 * 2026-08-27, it also renders the shell's own history prediction over the agent's
 * UI and echoes the agent's mouse reports as literal text, so from the user's
 * side it reads as a corrupted terminal rather than as two programs.
 *
 * The wording deliberately names the *shell*, not the shim or the launch chain.
 * The operator's repair is the same in every case - end this pane's shell, or the
 * pane - and a message about wrapper processes would be describing mux's
 * internals to someone whose terminal has stopped working.
 */
export function consoleContentionNotice(
  session: Pick<Session, 'console_contention'>,
): ConsoleContentionNotice | null {
  const contention: ConsoleContention | null | undefined = session.console_contention
  if (!contention) return null
  const orphaned = contention.reason === 'agent_orphaned'
    || contention.reason === 'shim_exited_first'
  return {
    text: 'This pane’s shell is reading the terminal too — typing here is being split',
    hint: orphaned
      // The agent is no longer inside this pane's process tree, so ending the
      // pane will not stop it. Saying so is the whole point: the alternative is
      // an operator who closes the tab and leaves a CLI running on their machine.
      ? 'The agent outlived the process that launched it. Ending this session will not stop the agent; quit it from its own UI first.'
      : 'Quit the agent (Ctrl+D, or /exit) and start it again from the Run menu, which spawns it directly into this terminal.',
  }
}

/**
 * Whether the daemon believes the agent survives this pane ending.
 *
 * Separate from the notice because it changes what an *action* does, not what a
 * message says: a stop that cannot reach its target should not report success.
 */
export function agentOutlivesPane(session: Pick<Session, 'console_contention'>): boolean {
  const contention = session.console_contention
  if (!contention) return false
  return contention.reason === 'agent_orphaned'
    || contention.reason === 'shim_exited_first'
    || contention.census?.agent_in_pty_tree === false
}
