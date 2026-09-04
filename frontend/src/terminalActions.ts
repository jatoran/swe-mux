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

/**
 * The settle a harness that declares nothing keeps.
 *
 * Exactly `composer_input.DEFAULT_PASTE_SUBMIT_SETTLE` on the daemon side, and
 * the flat constant this file sent every CLI before the trait existed.
 */
export const TERMINAL_SUBMIT_SETTLE_MS = 180
export const TERMINAL_SUBMIT_SETTLE_PER_KIB_MS = 80
/** Bounded so a huge insert cannot leave the button spinning. Matches the daemon. */
export const MAX_TERMINAL_SUBMIT_SETTLE_MS = 4000

/**
 * How long to let this CLI commit a paste of this size before sending Enter.
 *
 * The browser's insert-and-submit path had the same defect the daemon queue did,
 * and worse: a flat 180 ms with no scaling and no retry. A CLI applies a paste on
 * its own render loop and turns a large one into a placeholder chip; an Enter
 * that arrives mid-consumption is not merely dropped - on Codex it lands in the
 * composer as a newline, leaving the text visible and unsubmitted. Both halves
 * now read the same per-harness trait off the registry rather than each holding a
 * number (`HarnessDescriptor.paste_submit_settle_seconds`).
 */
export function terminalSubmitSettleMs(
  text: string,
  settle: { baseMs: number; perKibMs: number },
): number {
  const kib = new TextEncoder().encode(text).length / 1024
  return Math.min(MAX_TERMINAL_SUBMIT_SETTLE_MS, settle.baseMs + kib * settle.perKibMs)
}

/**
 * The awaiting sub-reasons that mean a dialog, not a composer, is under the cursor.
 *
 * Exactly `PROTECTED_AWAITING_REASONS` in `prompt_queue.py`, and deliberately the
 * same three: text typed at an approval or a question *answers it*, so inserting
 * there is not a misplaced draft but an unintended decision. `rate_limit` and
 * `authentication` are waits rather than dialogs and keep their composer.
 */
export const DIALOG_AWAITING_REASONS = ['approval', 'question', 'elicitation'] as const

/** What a session is showing, as much of it as an insert has to care about. */
export type InsertTargetState = {
  state?: string
  awaiting_reason?: string | null
  name?: string
}

/**
 * Why an insert into this session must be refused, or `''` when it may proceed.
 *
 * A rail button, a prompt template, a clipboard entry, or a dictated draft all
 * write into whatever the CLI happens to be showing. When that is an approval or
 * a question, the write does not fill a composer - it answers the dialog, and the
 * text is gone. The queue has refused this since it existed
 * (`NON_OVERRIDABLE_REASONS`); nothing stopped the insert paths doing it.
 */
export function insertionRefusal(session: InsertTargetState | null | undefined): string {
  if (!session || session.state !== 'awaiting') return ''
  const reason = session.awaiting_reason || ''
  if (!(DIALOG_AWAITING_REASONS as readonly string[]).includes(reason)) return ''
  const showing = reason === 'approval'
    ? 'is showing an approval request'
    : reason === 'question'
      ? 'is asking a question'
      : 'is waiting for an answer'
  const who = session.name ? `“${session.name}”` : 'This session'
  return `${who} ${showing}, so the text was not inserted — it would answer the dialog instead of filling the composer. Draft kept.`
}

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
  settleMs: number = TERMINAL_SUBMIT_SETTLE_MS,
): Promise<void> {
  append(text)
  if (!submit) return
  await wait(settleMs)
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
