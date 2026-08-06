// Clipboard history capture and client.
//
// Capture works through two hooks installed once at boot instead of at each copy
// site, because copies happen in code this app does not own (the Continuity WASM
// note editor, the xterm clipboard addon's OSC 52 path) and in ~30 scattered
// `navigator.clipboard.writeText` calls:
//
//  1. `Clipboard.prototype.writeText` is wrapped, which covers every programmatic
//     copy in the app *and* in the vendored editor (it calls the same global).
//  2. A capture-phase `copy`/`cut` listener on the document covers the paths that
//     never touch `writeText`: plain DOM selections, and the editor's
//     `execCommand("copy")` fallback on insecure contexts / mobile.
//
// One gesture can legitimately fire both hooks, so `CaptureDedupe` collapses
// identical text seen inside a short window before anything is posted.
//
// Nothing here reads the OS clipboard, and nothing polls it: only copies made
// inside swe-mux are recorded. That is a deliberate scope limit, not an omission.

// Explicit extension: this module is unit-tested under node's type-stripping
// runner, which does not resolve extensionless specifiers.
import { api } from './api.ts'

export type ClipboardEntry = {
  id: string
  preview: string
  char_count: number
  line_count: number
  source: string
  session_id: string | null
  project_id: string | null
  device: string
  pinned: boolean
  created_at: number
  updated_at: number
}

export type ClipboardHistory = {
  enabled: boolean
  persist: boolean
  limit: number
  entry_max_chars: number
  retention_hours: number
  redact_secrets: boolean
  count: number
  entries: ClipboardEntry[]
}

export type ClipboardCaptureResult = {
  stored: boolean
  reason: string
  entry: ClipboardEntry | null
}

/** Window event fired after a local capture or mutation so open pickers refresh. */
export const CLIPBOARD_CHANGED_EVENT = 'mux:clipboard-changed'

/** Two hooks can report the same copy; collapse repeats inside this window. */
export const CAPTURE_DEDUPE_MS = 1500
/** Ignore trivia (a single character, stray whitespace) — it only crowds the ring. */
export const MIN_CAPTURE_CHARS = 2

export function shouldCapture(text: unknown): text is string {
  return typeof text === 'string' && text.trim().length >= MIN_CAPTURE_CHARS
}

/**
 * Suppresses a repeat of the same text within `windowMs`.
 *
 * Pure and clock-injectable so the double-report collapse is unit-testable
 * without a DOM or timers.
 */
export class CaptureDedupe {
  private last = ''
  private at = -Infinity
  private readonly windowMs: number
  private readonly now: () => number

  // Explicit fields, not constructor parameter properties: the test runner strips
  // types only and rejects that syntax.
  constructor(windowMs: number = CAPTURE_DEDUPE_MS, now: () => number = () => Date.now()) {
    this.windowMs = windowMs
    this.now = now
  }

  /** True when this text should be reported; false when it is a repeat. */
  accept(text: string): boolean {
    const moment = this.now()
    if (text === this.last && moment - this.at < this.windowMs) {
      // Keep the window sliding so a burst of identical reports stays collapsed.
      this.at = moment
      return false
    }
    this.last = text
    this.at = moment
    return true
  }

  forget(): void {
    this.last = ''
    this.at = -Infinity
  }
}

/** Text of the current selection, preferring the focused input's own range. */
export function selectionText(
  active: Element | null,
  windowSelection: { toString: () => string } | null,
): string {
  const field = active as HTMLInputElement | HTMLTextAreaElement | null
  if (field && (field.tagName === 'TEXTAREA' || field.tagName === 'INPUT')) {
    const start = field.selectionStart
    const end = field.selectionEnd
    if (typeof start === 'number' && typeof end === 'number' && end > start) {
      return String(field.value ?? '').slice(start, end)
    }
  }
  return windowSelection ? windowSelection.toString() : ''
}

type CaptureContext = {
  /** Device class, so the picker can show where a copy came from. */
  device: () => string
  /** Focused session id, when a terminal owns the copy. */
  sessionId: () => string | null
  projectId: () => string | null
  enabled: () => boolean
}

let context: CaptureContext = {
  device: () => 'desktop',
  sessionId: () => null,
  projectId: () => null,
  enabled: () => true,
}
const dedupe = new CaptureDedupe()
let installed = false
let suppressDepth = 0

/**
 * Run a copy that must not enter the ring, and return whatever it returns.
 *
 * For surfaces whose copies are documents rather than snippets — the transcript
 * reader, above all, where copying is the primary act and one agent reply can be
 * kilobytes. Capturing those would evict the short things the ring exists to hand
 * back, which is the opposite of helping.
 *
 * The guard only needs to span the *synchronous* part of a copy: both hooks (the
 * patched `writeText` and the document-level `copy` listener) fire before
 * `copyPreparedText` reaches its first `await`, so wrapping the call that starts
 * it is enough even though the promise settles later. Depth-counted so nesting
 * cannot leave capture switched off.
 */
export function withoutClipboardCapture<T>(run: () => T): T {
  suppressDepth += 1
  try { return run() } finally { suppressDepth -= 1 }
}

/** Point capture at live app state (focused session, device class, on/off). */
export function configureClipboardCapture(next: Partial<CaptureContext>): void {
  context = { ...context, ...next }
}

/**
 * Report one copy. Fire-and-forget: a failed capture must never surface as an
 * error over the copy the user actually asked for.
 */
export function captureCopy(text: unknown, source: string): void {
  if (suppressDepth > 0) return
  if (!context.enabled() || !shouldCapture(text)) return
  if (!dedupe.accept(text)) return
  void api<ClipboardCaptureResult>('POST', '/api/clipboard', {
    text,
    source,
    session_id: context.sessionId(),
    project_id: context.projectId(),
    device: context.device(),
  })
    .then(result => {
      if (result.stored || result.reason === 'promoted') {
        window.dispatchEvent(new CustomEvent(CLIPBOARD_CHANGED_EVENT, { detail: result }))
      }
    })
    .catch(() => {
      // An older daemon (or a disabled ring) simply means no history.
    })
}

/**
 * Install the two capture hooks. Idempotent, and safe to call before the app
 * knows whether history is enabled — `configureClipboardCapture` gates it later.
 */
export function installClipboardCapture(): void {
  if (installed || typeof window === 'undefined') return
  installed = true
  const clipboard = navigator.clipboard as Clipboard | undefined
  const prototype = clipboard ? Object.getPrototypeOf(clipboard) : null
  const original = prototype?.writeText as ((text: string) => Promise<void>) | undefined
  if (original && prototype) {
    prototype.writeText = function patchedWriteText(this: Clipboard, text: string) {
      // Record first, then delegate untouched: the wrapper must never change the
      // promise the caller sees, and a rejected write is still a copy the user
      // asked for (the app's manual-copy fallbacks paste from history too).
      captureCopy(text, 'app')
      return original.call(this, text)
    }
  }
  // Capture phase: xterm and the note editor both stop propagation of their own
  // copy events in some paths, and this must see them regardless.
  const onNativeCopy = (event: Event) => {
    // A manual fallback can outlive the synchronous suppression scope. Its
    // source surface therefore carries an explicit capture opt-out.
    if (event.target instanceof Element && event.target.closest('[data-clipboard-capture="ignore"]')) return
    const clipboardEvent = event as ClipboardEvent
    const text =
      clipboardEvent.clipboardData?.getData('text/plain') ||
      selectionText(document.activeElement, window.getSelection())
    captureCopy(text, event.type === 'cut' ? 'cut' : 'selection')
  }
  document.addEventListener('copy', onNativeCopy, true)
  document.addEventListener('cut', onNativeCopy, true)
}

/** Reset the dedupe window (used when a test or a re-copy must not be collapsed). */
export function resetCaptureDedupe(): void {
  dedupe.forget()
}

export async function loadClipboardHistory(): Promise<ClipboardHistory> {
  return api<ClipboardHistory>('GET', '/api/clipboard')
}

/** Full text for one entry. The list payload deliberately carries previews only. */
export async function clipboardEntryText(entryId: string): Promise<string> {
  const entry = await api<ClipboardEntry & { text: string }>(
    'GET',
    `/api/clipboard/${encodeURIComponent(entryId)}`,
  )
  return entry.text
}

export async function setClipboardEntryPinned(
  entryId: string,
  pinned: boolean,
): Promise<ClipboardEntry> {
  return api<ClipboardEntry>('PATCH', `/api/clipboard/${encodeURIComponent(entryId)}`, { pinned })
}

export async function deleteClipboardEntry(entryId: string): Promise<void> {
  await api('DELETE', `/api/clipboard/${encodeURIComponent(entryId)}`)
}

export async function clearClipboardHistory(includePinned = false): Promise<number> {
  const result = await api<{ removed: number }>(
    'DELETE',
    `/api/clipboard${includePinned ? '?include_pinned=1' : ''}`,
  )
  return result.removed
}

/** Compact "3m ago" style stamp for the picker rows. */
export function relativeAge(seconds: number, now: number = Date.now() / 1000): string {
  const delta = Math.max(0, now - seconds)
  if (delta < 45) return 'now'
  if (delta < 3600) return `${Math.round(delta / 60)}m`
  if (delta < 86400) return `${Math.round(delta / 3600)}h`
  return `${Math.round(delta / 86400)}d`
}

const SOURCE_LABELS: Record<string, string> = {
  app: 'copy',
  selection: 'selection',
  cut: 'cut',
  terminal: 'terminal',
  reply: 'agent reply',
  resume: 'resume cmd',
  note: 'note',
  history: 'history',
}

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] || source || 'copy'
}
