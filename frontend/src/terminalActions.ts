export type TerminalActionRequest = {
  sessionId: string
  action: string
  requestId: string
  text?: string
  submit?: boolean
}

export type TerminalInsertRequest = TerminalActionRequest & {
  action: 'insertText'
  text: string
  submit: boolean
}

export type TerminalActionResult = { requestId: string; ok: boolean; error?: string }
type TerminalWait = (delayMs: number) => Promise<void>

export const TERMINAL_SUBMIT_SETTLE_MS = 180

const waitForTerminal: TerminalWait = delayMs => new Promise(resolve => window.setTimeout(resolve, delayMs))

/**
 * Let an interactive TUI commit bracketed paste before sending Enter.
 *
 * Codex applies pasted text on its render/input loop. Sending carriage return in
 * the same browser turn can reach the TUI before that text becomes its composer,
 * leaving the text visible but unsubmitted. The daemon queue uses this same delay.
 */
export async function settleTerminalInsertion(
  text: string,
  submit: boolean,
  append: (value: string) => void,
  send: () => void,
  wait: TerminalWait = waitForTerminal,
): Promise<void> {
  append(text)
  if (!submit) return
  await wait(TERMINAL_SUBMIT_SETTLE_MS)
  send()
}

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
  return requestTerminalAction(sessionId, { action: 'insertText', text, submit })
}

/** Run an action through the mounted terminal owner and wait for its result. */
export function requestTerminalAction(
  sessionId: string,
  request: { action: string; text?: string; submit?: boolean },
): Promise<void> {
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
      reject(new Error(request.action === 'insertText'
        ? 'The target terminal is not mounted. Draft kept.'
        : 'The target terminal is not mounted.'))
    }, 1_500)
    window.addEventListener('mux:terminal-action-result', finish)
    const detail: TerminalActionRequest = {
      sessionId,
      ...request,
      requestId: id,
    }
    window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail }))
  })
}
