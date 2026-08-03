export type TerminalRendererPreference = 'auto' | 'dom' | 'webgl'
export type ActiveTerminalRenderer = 'dom' | 'webgl'
export type TerminalRendererBackend = 'shell' | 'claude' | 'codex'

export function terminalCursorOptions(mobileInput: boolean) {
  return mobileInput
    ? { cursorInactiveStyle: 'bar' as const, cursorWidth: 2 }
    : { cursorInactiveStyle: 'outline' as const, cursorWidth: 1 }
}

export function shouldLoadWebgl(
  preference: TerminalRendererPreference,
  mobileViewport: boolean,
  backend: TerminalRendererBackend,
): boolean {
  return backend !== 'codex' && !mobileViewport && preference !== 'dom'
}

/**
 * xterm's `windowsPty` compatibility descriptor, as reported by the daemon.
 * Only the daemon knows what the PTY actually is, and xterm cannot infer it:
 * without this it rewraps ConPTY scrollback on every pane resize even though
 * ConPTY hard-wraps and never reports the wrap flag. `undefined` means the
 * host is not Windows, and xterm keeps its normal reflow behaviour.
 */
export type WindowsPtyCompatibility = { backend: 'conpty'; buildNumber: number }

export function windowsPtyCompatibility(value: unknown): WindowsPtyCompatibility | undefined {
  if (!value || typeof value !== 'object') return undefined
  const { backend, build_number: buildNumber } = value as { backend?: unknown; build_number?: unknown }
  if (backend !== 'conpty' || typeof buildNumber !== 'number' || !Number.isFinite(buildNumber) || buildNumber <= 0) {
    return undefined
  }
  return { backend: 'conpty', buildNumber }
}

/** `hidden` reports that this client is not on screen, which deregisters its viewport
 *  from the daemon's geometry arbitration instead of registering these dimensions. */
export function terminalAttachReadyFrame(
  cols: number,
  rows: number,
  renderer: ActiveTerminalRenderer,
  hidden = false,
) {
  return { type: 'attach_ready' as const, cols, rows, renderer, hidden }
}
