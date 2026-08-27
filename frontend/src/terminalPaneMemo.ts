import type { Session } from './types'

/**
 * Which prop changes a terminal pane must re-render for.
 *
 * Its own module because it is the half of `TerminalPane` that decides whether the
 * *other* half ever sees an update, and because importing `TerminalPane` itself into a
 * test pulls in xterm's stylesheet, which the node runner cannot load. Every other
 * assertion about that file is therefore made against its source text; this one is
 * made against the function.
 *
 * The rule it enforces: the terminal's construction effect is keyed on `session.id`
 * and deliberately does not re-run when the backend changes - rebuilding xterm on a
 * promotion would drop the socket and replay the whole buffer - so every live value it
 * needs is read from a ref assigned during *render*. A prop change the memo swallows is
 * therefore a ref that never updates, and the symptom is a pane that keeps behaving
 * like whatever it was when it mounted.
 */
export interface TerminalPaneMemoProps {
  session: Session
  broadcast: boolean
  keybindings: Record<string, string>
  scrollback: number
  rendererPreference: string
  windowsPty?: unknown
  mobileInput: unknown
  uiScale: number
  visible: boolean
  claudeMaxColumns: number
}

/** Value equality for a list the daemon rebuilds on every publish. */
const sameList = (a: readonly string[] | undefined, b: readonly string[] | undefined): boolean => {
  if (a === b) return true
  const left = a || []
  const right = b || []
  return left.length === right.length && left.every((item, index) => item === right[index])
}

export function terminalPanePropsEqual(
  a: TerminalPaneMemoProps,
  b: TerminalPaneMemoProps,
): boolean {
  return a.session.id === b.session.id &&
    a.session.backend === b.session.backend &&
    a.session.state === b.session.state &&
    // The daemon publishes this on its own: a launch has been seen in a pane that is
    // still `shell`, still `running`, and has no conversation bound yet, so nothing
    // else in this comparison moves. Without it the memo swallows the only signal the
    // launch window has, `inputBackendRef` never updates, and the pane applies shell
    // input encoding to an agent composer for the whole ~10 s the promotion takes.
    sameList(a.session.agent_launch_pending, b.session.agent_launch_pending) &&
    // The same shape of problem at the other end of the session's life: the contention
    // notice is drawn from the record, so a memo that cannot see the verdict arrive
    // stays silent through exactly the fault the notice exists to state. Compared by
    // cause rather than by object, because the census inside it is refreshed on every
    // shell prompt while the cause - and therefore the notice - stays the same.
    (a.session.console_contention?.reason ?? null) ===
      (b.session.console_contention?.reason ?? null) &&
    // Warm panes remain mounted. Swallowing this prop transition leaves the hidden
    // pane registered with the daemon and prevents the shown pane's redraw.
    a.visible === b.visible &&
    // Changes once per agent lifecycle (codex: placeholder → detected rollout id);
    // the resume Action rail button must pick up the flip.
    a.session.native_session_id === b.session.native_session_id &&
    // Task shells set this once at spawn; comparing it keeps the leaner rail authoritative.
    a.session.relaunchable === b.session.relaunchable &&
    a.broadcast === b.broadcast &&
    a.scrollback === b.scrollback &&
    a.keybindings === b.keybindings &&
    // Omitting this blocked a renderer change from ever reaching an existing pane;
    // it only appeared to work because unrelated prop churn re-rendered anyway.
    a.rendererPreference === b.rendererPreference &&
    // Identity-stable per machine (App value-compares it), so this only differs on
    // the single boot transition from "config not loaded yet" to the real value.
    a.windowsPty === b.windowsPty &&
    a.mobileInput === b.mobileInput &&
    // Without this a width envelope edited in Settings reaches no live pane, and the
    // setting appears to do nothing until every terminal is rebuilt.
    a.claudeMaxColumns === b.claudeMaxColumns &&
    // Without this the memo swallows the change and the pane keeps the old font.
    a.uiScale === b.uiScale
}
