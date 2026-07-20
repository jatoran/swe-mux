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

  constructor(
    onPending: (text: string) => void,
    onRejected: (message: string) => void,
    access: ClipboardAccess = browserClipboard(),
  ) {
    this.onPending = onPending
    this.onRejected = onRejected
    this.access = access
  }

  async readText(selection: ClipboardSelectionType): Promise<string> {
    if (selection !== 'c') return ''
    try { return await this.access.readText() } catch { return '' }
  }

  async writeText(selection: ClipboardSelectionType, text: string): Promise<void> {
    if (selection !== 'c') return
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
