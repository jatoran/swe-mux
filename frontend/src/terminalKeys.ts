import { DEFAULT_COMPOSER_NEWLINE, composerNewline, isAgentBackend } from './harnessRegistry.ts'

export type TerminalKeyDecision =
  | { kind: 'pass' }
  /** swe-mux owns this keystroke: xterm must not write it to the PTY. The command
   *  itself is dispatched by App's window handler, which owns the sequence state -
   *  the pane can only be told whether the key is claimed, never which binding it
   *  belongs to, because a chord halfway through a sequence has no command yet. */
  | { kind: 'claimed' }
  | { kind: 'browserPaste' }
  | { kind: 'copySelection' }
  | { kind: 'sendInput'; data: string }

export type TerminalKey = {
  type: string; key: string; ctrlKey: boolean; shiftKey: boolean; altKey: boolean; metaKey: boolean
}

// xterm.js encodes Enter as CR and ignores Shift and Ctrl, and it speaks neither the kitty
// keyboard protocol nor modifyOtherKeys, so an agent can never see those two chords: both
// arrive as a plain submit. ESC+CR (Alt+Enter) is the one legacy sequence both agents read as
// "insert a newline" - verified against live Claude and Codex panes over the same ConPTY the
// server uses, where Codex reports alt+enter bound to editor.insert_newline and Claude keeps
// the draft on a second line. Shells keep xterm's CR, where Enter variants already mean submit.
//
// The per-harness answer now lives in the registry (`composerNewline`), so this is the default
// a daemon too old to declare one falls back to, and the payload the rail's static Markdown
// helpers are built from. It is re-exported here because that is where its consumers look.
export const AGENT_NEWLINE = DEFAULT_COMPOSER_NEWLINE

export function terminalKeyDecision(
  event: TerminalKey,
  claimed: boolean,
  hasSelection: boolean,
  backend?: string,
): TerminalKeyDecision {
  if (event.type !== 'keydown') return { kind: 'pass' }
  if (claimed) return { kind: 'claimed' }
  const key = event.key.toLowerCase()
  if (key === 'enter' && (event.shiftKey || event.ctrlKey) && !event.altKey && !event.metaKey) {
    if (isAgentBackend(backend)) return { kind: 'sendInput', data: composerNewline(backend) }
  }
  if (event.ctrlKey && !event.altKey && !event.metaKey && key === 'v') return { kind: 'browserPaste' }
  if (event.ctrlKey && !event.altKey && !event.metaKey && key === 'c' && hasSelection) {
    return { kind: 'copySelection' }
  }
  return { kind: 'pass' }
}
