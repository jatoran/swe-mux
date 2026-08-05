export type TerminalRendererPreference = 'auto' | 'dom' | 'webgl'
export type ActiveTerminalRenderer = 'dom' | 'webgl'
export type TerminalRendererBackend = 'shell' | 'claude' | 'codex'

export function terminalCursorOptions(mobileInput: boolean) {
  return mobileInput
    ? { cursorInactiveStyle: 'bar' as const, cursorWidth: 2 }
    : { cursorInactiveStyle: 'outline' as const, cursorWidth: 1 }
}

/**
 * Whether this pane may run the WebGL renderer.
 *
 * Codex is excluded under `auto` because its full-screen redraws could corrupt WebGL
 * scrollback while the viewport was off-tail. That hazard is narrower than it was —
 * sessions now keep their transcript in scrollback (`tui.alternate_screen="never"`)
 * rather than repainting it — but it is not gone: Codex's rich renderer reflows the
 * transcript on resize, which is the same shape of redraw. So `auto` stays
 * conservative and keeps Codex on the DOM renderer.
 *
 * An *explicit* `webgl` preference now reaches Codex, which it previously did not.
 * That was a silent no-op: the setting existed, the user could select it, and the one
 * backend whose repaint cost makes the renderer worth choosing ignored it. Opting in
 * is a decision with a visible failure mode (blank or torn scrollback, repaired by
 * scrolling or switching back to `dom`), and it is the only way to measure whether
 * the exclusion is still earning its place.
 */
export function shouldLoadWebgl(
  preference: TerminalRendererPreference,
  mobileViewport: boolean,
  backend: TerminalRendererBackend,
): boolean {
  if (mobileViewport || preference === 'dom') return false
  return backend !== 'codex' || preference === 'webgl'
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
