export type TerminalKeyDecision =
  | { kind: 'pass' }
  | { kind: 'command'; command: string }
  | { kind: 'pasteText' }
  | { kind: 'copySelection' }

export type TerminalKey = {
  type: string; key: string; ctrlKey: boolean; shiftKey: boolean; altKey: boolean; metaKey: boolean
}

export function terminalKeyDecision(
  event: TerminalKey,
  command: string | undefined,
  hasSelection: boolean,
): TerminalKeyDecision {
  if (event.type !== 'keydown') return { kind: 'pass' }
  if (command) {
    if (command === 'terminal.copy' && !hasSelection) return { kind: 'pass' }
    return { kind: 'command', command }
  }
  const key = event.key.toLowerCase()
  if (event.ctrlKey && !event.altKey && !event.metaKey && key === 'v') return { kind: 'pasteText' }
  if (event.ctrlKey && !event.altKey && !event.metaKey && key === 'c' && hasSelection) {
    return { kind: 'copySelection' }
  }
  return { kind: 'pass' }
}
