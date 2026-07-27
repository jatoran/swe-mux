export type TerminalRendererPreference = 'auto' | 'dom' | 'webgl'
export type ActiveTerminalRenderer = 'dom' | 'webgl'
export type TerminalRendererBackend = 'shell' | 'claude' | 'codex'

export function shouldLoadWebgl(
  preference: TerminalRendererPreference,
  mobileViewport: boolean,
  backend: TerminalRendererBackend,
): boolean {
  return backend !== 'codex' && !mobileViewport && preference !== 'dom'
}

export function terminalAttachReadyFrame(cols: number, rows: number, renderer: ActiveTerminalRenderer) {
  return { type: 'attach_ready' as const, cols, rows, renderer }
}
