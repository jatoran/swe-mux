export type TerminalKeyDecision =
  | { kind: 'pass' }
  | { kind: 'command'; command: string }
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
export const AGENT_NEWLINE = '\x1b\r'

export function terminalKeyDecision(
  event: TerminalKey,
  command: string | undefined,
  hasSelection: boolean,
  backend?: string,
): TerminalKeyDecision {
  if (event.type !== 'keydown') return { kind: 'pass' }
  if (command) {
    if (command === 'terminal.copy' && !hasSelection) return { kind: 'pass' }
    return { kind: 'command', command }
  }
  const key = event.key.toLowerCase()
  if (key === 'enter' && (event.shiftKey || event.ctrlKey) && !event.altKey && !event.metaKey) {
    if (backend === 'claude' || backend === 'codex') return { kind: 'sendInput', data: AGENT_NEWLINE }
  }
  if (event.ctrlKey && !event.altKey && !event.metaKey && key === 'v') return { kind: 'browserPaste' }
  if (event.ctrlKey && !event.altKey && !event.metaKey && key === 'c' && hasSelection) {
    return { kind: 'copySelection' }
  }
  return { kind: 'pass' }
}
