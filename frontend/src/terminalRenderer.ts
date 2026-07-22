export type TerminalRendererPreference = 'auto' | 'dom' | 'webgl'
export type ActiveTerminalRenderer = 'dom' | 'webgl'

export function shouldLoadWebgl(preference: TerminalRendererPreference, mobileViewport: boolean): boolean {
  return !mobileViewport && preference !== 'dom'
}

export function terminalAttachReadyFrame(cols: number, rows: number, renderer: ActiveTerminalRenderer) {
  return { type: 'attach_ready' as const, cols, rows, renderer }
}
