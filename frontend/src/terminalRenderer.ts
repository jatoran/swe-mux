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
 * Claude and OMP are always excluded. Their repainting surfaces can leave a live
 * WebGL context intermittently mangled after a retained pane returns or a deep
 * session is reconstructed from bounded replay. No context-loss event fires, and
 * invalidating xterm's model does not reliably recover it; a real resize does. The
 * DOM renderer has no corresponding failure.
 *
 * The `auto` preference also excludes any harness whose TUI rewrites content already
 * in scrollback (`repaints_scrollback` on its descriptor: Codex reflows its
 * normal-screen transcript on resize, OMP repaints its tail continuously),
 * because those full-screen redraws could corrupt WebGL scrollback while the
 * viewport was off-tail. A trait rather than a name check, so a new harness
 * defaults to the safe DOM renderer until its descriptor declares otherwise —
 * shells remain WebGL-eligible.
 *
 * An *explicit* `webgl` preference still reaches Codex and shells. OMP's continuous
 * tail repaint makes its failure indistinguishable from incomplete replay, so it
 * does not expose that unsafe override.
 */
export function shouldLoadWebgl(
  preference: TerminalRendererPreference,
  mobileViewport: boolean,
  backend: TerminalRendererBackend,
): boolean {
  if (mobileViewport || preference === 'dom' || backend === 'claude' || backend === 'omp') return false
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
