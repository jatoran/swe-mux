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

export const terminalRenderDiagnosticsEnabled = import.meta.env.DEV

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
