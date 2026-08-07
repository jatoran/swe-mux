import { repaintsScrollback } from './harnessRegistry.ts'

export type TerminalRendererPreference = 'auto' | 'dom' | 'webgl'
export type ActiveTerminalRenderer = 'dom' | 'webgl'
export type TerminalRendererBackend = string

export function terminalCursorOptions(mobileInput: boolean) {
  return mobileInput
    ? { cursorInactiveStyle: 'bar' as const, cursorWidth: 2 }
    : { cursorInactiveStyle: 'outline' as const, cursorWidth: 1 }
}

/**
 * Whether this pane may run the WebGL renderer.
 *
 * The `auto` preference excludes any harness whose TUI rewrites content already
 * in scrollback (`repaints_scrollback` on its descriptor: Codex reflows its
 * normal-screen transcript on resize, OMP repaints its tail continuously),
 * because those full-screen redraws could corrupt WebGL scrollback while the
 * viewport was off-tail. A trait rather than a name check, so a new harness
 * defaults to the safe DOM renderer until its descriptor declares otherwise —
 * alternate-screen TUIs (Claude) and shells never rewrite scrollback and stay
 * WebGL-eligible.
 *
 * An *explicit* `webgl` preference reaches every backend. Opting in is a
 * decision with a visible failure mode (blank or torn scrollback, repaired by
 * scrolling or switching back to `dom`), and it is the only way to measure
 * whether the exclusion is still earning its place.
 */
export function shouldLoadWebgl(
  preference: TerminalRendererPreference,
  mobileViewport: boolean,
  backend: TerminalRendererBackend,
): boolean {
  if (mobileViewport || preference === 'dom') return false
  return !repaintsScrollback(backend) || preference === 'webgl'
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
