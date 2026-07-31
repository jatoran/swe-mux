type TerminalRenderDiagnostic = {
  at: number
  sessionId: string
  phase: string
  detail?: Record<string, unknown>
}

declare global {
  interface Window {
    __muxTerminalRenderDiagnostics?: TerminalRenderDiagnostic[]
  }
}

/** Turns render diagnostics on in a build where `DEV` is false. */
export const TERMINAL_RENDER_DIAGNOSTICS_KEY = 'mux:terminal-render-diagnostics'

/** Whether the flag above opts this client in. Separated for testing. */
export function diagnosticsOptIn(read: (key: string) => string | null): boolean {
  const value = read(TERMINAL_RENDER_DIAGNOSTICS_KEY)
  return value === '1' || value === 'true'
}

/**
 * Render faults are a production phenomenon, so this cannot be `DEV` alone.
 *
 * Every bug this instrumentation exists for — a repaint that silently drew nothing, a lost
 * WebGL context, a pane that came back from `display:none` with half a screen — happens on
 * the frozen desktop app, which is a production build. Gating the only evidence on a flag
 * that is false exactly there left the choice between reasoning from the symptom and asking
 * the user to run a dev server against their live sessions.
 *
 * Read once at import, so a client that has not opted in pays a boolean and nothing else.
 * Enable with `localStorage.setItem('mux:terminal-render-diagnostics', '1')` and reload;
 * read the ring buffer from `window.__muxTerminalRenderDiagnostics`.
 */
export const terminalRenderDiagnosticsEnabled = (() => {
  try {
    // Vite substitutes this at build time. Under the node test runner there is no
    // `import.meta.env` at all, and reading `.DEV` off it throws.
    if (import.meta.env.DEV) return true
  } catch { /* not a Vite build */ }
  try {
    return diagnosticsOptIn(key => window.localStorage.getItem(key))
  } catch {
    // Storage can throw outright under a blocked-cookies policy or in a sandboxed frame,
    // and `window` does not exist at all outside a browser.
    return false
  }
})()

export function recordTerminalRenderDiagnostic(
  sessionId: string,
  phase: string,
  detail?: Record<string, unknown>,
): void {
  if (!terminalRenderDiagnosticsEnabled) return
  const entries = window.__muxTerminalRenderDiagnostics ?? []
  entries.push({ at: performance.now(), sessionId, phase, detail })
  if (entries.length > 100) entries.splice(0, entries.length - 100)
  window.__muxTerminalRenderDiagnostics = entries
  window.dispatchEvent(new CustomEvent('mux:terminal-render-diagnostic', { detail: entries.at(-1) }))
}

export function isWebglRenderError(event: ErrorEvent): boolean {
  const evidence = `${event.message}\n${event.error instanceof Error ? event.error.stack ?? '' : ''}`
  return /WebglRenderer|_updateModel|@xterm\/addon-webgl|addon-webgl\.(?:js|mjs)/i.test(evidence)
}
