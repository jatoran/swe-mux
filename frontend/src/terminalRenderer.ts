import { repaintsScrollback, webglUnsafe } from './harnessRegistry.ts'

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
 * Two independent exclusions, both declared on the harness descriptor rather than
 * decided by name here.
 *
 * `webgl_unsafe` is absolute. Such a harness's repainting surface can leave a live
 * WebGL context intermittently mangled after a retained pane returns or a deep
 * session is reconstructed from bounded replay. No context-loss event fires, and
 * invalidating xterm's model does not reliably recover it; a real resize does. The
 * DOM renderer has no corresponding failure, so there is nothing an override could
 * usefully mean and none is offered. Claude and OMP declare it today.
 *
 * `repaints_scrollback` excludes the `auto` preference only. Those full-screen
 * redraws could corrupt WebGL scrollback while the viewport was off-tail, but the
 * failure is recoverable, so an explicit `webgl` preference still reaches such a
 * harness (Codex) and shells.
 *
 * A new harness therefore defaults to the safe DOM renderer until its descriptor
 * says otherwise, and shells remain WebGL-eligible.
 */
export function shouldLoadWebgl(
  preference: TerminalRendererPreference,
  mobileViewport: boolean,
  backend: TerminalRendererBackend,
): boolean {
  if (mobileViewport || preference === 'dom' || webglUnsafe(backend)) return false
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
  return {
    type: 'attach_ready' as const,
    cols,
    rows,
    renderer,
    hidden,
    // The daemon enables output credit only after the client advertises parser
    // acknowledgements. Older clients therefore keep the pre-credit protocol.
    output_flow_control: true,
  }
}
