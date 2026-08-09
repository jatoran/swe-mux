export type TerminalInsertRequest = {
  sessionId: string
  text: string
  submit: boolean
  requestId: string
}

type TerminalActionResult = { requestId: string; ok: boolean; error?: string }

const requestId = (): string => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`

/**
 * Append text through the mounted xterm pane, then optionally use its real Send path.
 *
 * This deliberately does not call the daemon's old voice-submit shortcut. The pane
 * owns bracketed paste, input replay, ownership claims, and the carriage return used
 * by the mobile Send button. The acknowledgement also prevents Talk from clearing a
 * draft when its target pane is no longer mounted.
 */
export function insertIntoTerminal(sessionId: string, text: string, submit: boolean): Promise<void> {
  const id = requestId()
  return new Promise((resolve, reject) => {
    const finish = (event: Event) => {
      const detail = (event as CustomEvent<TerminalActionResult>).detail
      if (detail.requestId !== id) return
      window.removeEventListener('mux:terminal-action-result', finish)
      window.clearTimeout(timer)
      if (detail.ok) resolve()
      else reject(new Error(detail.error || 'The terminal rejected the voice input.'))
    }
    const timer = window.setTimeout(() => {
      window.removeEventListener('mux:terminal-action-result', finish)
      reject(new Error('The target terminal is not mounted. Draft kept.'))
    }, 1_500)
    window.addEventListener('mux:terminal-action-result', finish)
    const detail: TerminalInsertRequest & { action: 'insertText' } = {
      sessionId,
      action: 'insertText',
      text,
      submit,
      requestId: id,
    }
    window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail }))
  })
}
