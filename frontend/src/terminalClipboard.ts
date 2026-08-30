import type { ClipboardSelectionType, IClipboardProvider } from '@xterm/addon-clipboard'

export const MAX_TERMINAL_CLIPBOARD_CHARS = 1_000_000
export const TERMINAL_IMAGE_TYPES = new Set([
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/gif',
])

type ClipboardAccess = {
  readText: () => Promise<string>
  writeText: (text: string) => Promise<void>
}

export type ClipboardFileItem = {
  kind: string
  type: string
  getAsFile: () => Blob | null
}

export function isTerminalImage(blob: Pick<Blob, 'type'>): boolean {
  return TERMINAL_IMAGE_TYPES.has(blob.type.toLowerCase())
}

export function hasTerminalImage(items: Iterable<Pick<ClipboardFileItem, 'kind' | 'type'>>): boolean {
  for (const item of items) {
    if (item.kind === 'file' && TERMINAL_IMAGE_TYPES.has(item.type.toLowerCase())) return true
  }
  return false
}

export function clipboardImage(items: Iterable<ClipboardFileItem>): Blob | null {
  for (const item of items) {
    if (item.kind !== 'file' || !TERMINAL_IMAGE_TYPES.has(item.type.toLowerCase())) continue
    const file = item.getAsFile()
    if (file && isTerminalImage(file)) return file
  }
  return null
}

type TerminalTextPasteEvent = Pick<ClipboardEvent, 'clipboardData' | 'preventDefault' | 'stopPropagation'>

/**
 * Claim a native text paste before xterm's DOM listener handles it.
 *
 * Every terminal paste surface must converge on the pane's paste function, because that
 * function - not xterm - decides the bracketing and lifts a leading newline run out of a
 * Codex paste. Letting xterm own Ctrl+V while the command rail uses the pane path makes
 * the two controls observably different, and the difference only shows up on the pastes
 * that matter: multi-line ones, into an agent, submitted a line at a time.
 *
 * A paste that reaches xterm's own handler anyway is reported (`PASTE_UNCLAIMED_PHASE`),
 * because nothing downstream of here can tell that it did.
 */
export function claimTerminalTextPaste(
  event: TerminalTextPasteEvent,
  paste: (text: string) => void,
): boolean {
  const text = event.clipboardData?.getData('text/plain') || ''
  if (!text) return false
  event.preventDefault()
  event.stopPropagation()
  paste(text)
  return true
}

/** What a pane knows about a paste that was dispatched outside its terminal host. */
export type StrayPasteContext = {
  /** This pane is not on screen, so it is not what the operator was pasting into. */
  paneHidden: boolean
  /** The event landed inside some terminal host - that host's own capture listener
   *  owns it, and the document listener runs first, so this must not double-handle it. */
  targetInTerminalHost: boolean
  /** The event, or the keyboard, is inside an open dialog, which owns the interaction. */
  inDialog: boolean
  /** A real text field elsewhere in the app holds the keyboard and will receive the
   *  paste natively - including the mobile input bridge, which sits beside the host. */
  focusHeldByOtherField: boolean
  /** The last-focused insertable surface, when it is a terminal (`insertTarget.ts`). */
  focusedTerminalSessionId: string | null
  sessionId: string
}

/**
 * Whether this pane should adopt a paste that was dispatched somewhere else.
 *
 * Ctrl+V pressed while the keyboard sits on something focusable but not editable - a rail
 * button just clicked, a session tab - is dispatched to that element. The pane's own
 * listener is on its host and never hears it, xterm never hears it, and the paste goes
 * silently nowhere, which reads to the operator as the same defect as a paste that went
 * out wrong.
 *
 * Routing on the last-focused terminal rather than on visibility is what keeps this
 * single-valued: several panes can be on screen at once, and every one of them runs this,
 * so a rule any two of them could answer yes to would paste twice.
 */
export function strayPasteBelongsToPane(context: StrayPasteContext): boolean {
  if (context.paneHidden || context.targetInTerminalHost || context.inDialog) return false
  if (context.focusHeldByOtherField) return false
  return context.focusedTerminalSessionId === context.sessionId
}

function browserClipboard(): ClipboardAccess {
  return {
    readText: () => navigator.clipboard.readText(),
    writeText: text => navigator.clipboard.writeText(text),
  }
}

export class ResilientClipboardProvider implements IClipboardProvider {
  private readonly onPending: (text: string) => void
  private readonly onRejected: (message: string) => void
  private readonly access: ClipboardAccess
  private readonly suppressWrite: () => boolean

  constructor(
    onPending: (text: string) => void,
    onRejected: (message: string) => void,
    access: ClipboardAccess = browserClipboard(),
    // OSC 52 arrives via replayed scrollback on every re-attach and while the
    // browser tab is hidden. Honoring those writes silently overwrites the system
    // clipboard with a stale payload, so the terminal supplies a predicate that
    // reports when a write does not reflect a live, visible copy.
    suppressWrite: () => boolean = () => false,
  ) {
    this.onPending = onPending
    this.onRejected = onRejected
    this.access = access
    this.suppressWrite = suppressWrite
  }

  async readText(selection: ClipboardSelectionType): Promise<string> {
    if (selection !== 'c') return ''
    try { return await this.access.readText() } catch { return '' }
  }

  async writeText(selection: ClipboardSelectionType, text: string): Promise<void> {
    if (selection !== 'c') return
    if (this.suppressWrite()) return
    if (text.length > MAX_TERMINAL_CLIPBOARD_CHARS) {
      this.onRejected('Terminal clipboard content exceeded the 1,000,000 character safety limit.')
      return
    }
    try {
      await this.access.writeText(text)
    } catch {
      this.onPending(text)
    }
  }
}

type ClipboardWriter = { writeText: (text: string) => Promise<void> }
type LegacyCopy = () => boolean

export async function copyPreparedText(
  text: string,
  textarea?: HTMLTextAreaElement | null,
  clipboard: ClipboardWriter | null | undefined = navigator.clipboard,
  legacyCopy: LegacyCopy = () => document.execCommand('copy'),
): Promise<boolean> {
  let modernWrite: Promise<void> | null = null
  try {
    modernWrite = clipboard?.writeText(text) ?? null
  } catch {
    modernWrite = null
  }

  // Keep this synchronous with the button gesture. Mobile browsers commonly
  // expire activation before a rejected Clipboard promise settles.
  if (textarea) {
    textarea.focus()
    textarea.select()
    textarea.setSelectionRange?.(0, text.length)
    try {
      if (legacyCopy()) {
        // The modern write was intentionally started in the same user gesture;
        // consume a later rejection even though the synchronous fallback won.
        void modernWrite?.catch(() => {})
        return true
      }
    } catch { /* use the modern result */ }
  }

  if (!modernWrite) return false
  try { await modernWrite; return true } catch { return false }
}

/**
 * Whether a paste must be bracketed by hand instead of trusting xterm to do it.
 *
 * xterm wraps a paste only once it has *seen* the child enable bracketed paste. That
 * mirror is a guess about the child, and it is wrong in both directions:
 *
 *  * stale **off** - an agent CLI enables the mode exactly once at startup, so a pane
 *    that reset its terminal on reconnect believes it is off while the CLI still has
 *    it on. Unwrapped, xterm rewrites every newline to a carriage return, the CLI
 *    submits the paste a line at a time, and only the text after the final newline
 *    survives.
 *  * stale **on** - the mirror was set by a child that has since been replaced (a
 *    shell promoted into an agent, a resumed pane), or by a CLI that has dropped out
 *    of its TUI. xterm wraps, the child ignores the wrapper, and the CRs submit line
 *    by line exactly as above.
 *
 * The mirror is therefore not consulted at all: an agent CLI holds bracketed paste on
 * for its whole life, so the wrapper is always the right bytes for one, and the two
 * branches produce *identical* bytes when the mirror happens to be right - `term.paste`
 * and `bracketedPaste` do the same `\n`→`\r` rewrite inside the same wrapper. Removing
 * the condition can only change what happens when the mirror was lying.
 *
 * Deliberately still narrow on the other two axes. Single-line text is unaffected by
 * the bug and wrapping it would turn a short paste into a `[Pasted text]` placeholder
 * in Codex, and a child that genuinely has no bracketed paste (a plain shell) must
 * never be sent the wrapper bytes it would print literally.
 */
export function pasteNeedsManualBracketing(input: {
  text: string
  agentBackend: boolean
}): boolean {
  if (!input.agentBackend) return false
  return /[\r\n]/.test(input.text)
}
