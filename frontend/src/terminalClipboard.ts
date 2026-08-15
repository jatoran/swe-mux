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
 * Every terminal paste surface must converge on the pane's paste function because that
 * function repairs stale bracketed-paste mode. Letting xterm own Ctrl+V while the command
 * rail uses the pane path makes the two controls observably different after a reconnect.
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
 * xterm wraps a paste only once it has seen the child enable bracketed paste, and an
 * agent CLI enables it exactly once at startup. A pane that reset its terminal on
 * reconnect can therefore believe the mode is off while the CLI still has it on.
 * Unwrapped, xterm rewrites every newline to a carriage return, so the CLI submits the
 * paste a line at a time and keeps only the text after the final newline.
 *
 * Deliberately narrow. Single-line text is unaffected by the bug, and a child that
 * genuinely has no bracketed paste (a plain shell) must never be sent the wrapper
 * bytes, so it only fires for multi-line text going into an agent session.
 */
export function pasteNeedsManualBracketing(input: {
  text: string
  agentBackend: boolean
  bracketedPasteMode: boolean
}): boolean {
  if (!input.agentBackend || input.bracketedPasteMode) return false
  return /[\r\n]/.test(input.text)
}
