// Safe mode after the desktop page crashed or hung (`swe_mux/desktop_renderer.py`).
//
// The desktop shell reloads a crashed or hung page at `/?mux_recovered=<reason>`. The
// Project's tab layout lives on the daemon and is shared by every device, so without
// this the reload would restore the very Preview tab that froze the page and freeze
// it again - which is how the desktop app stayed stuck on 2026-09-24 while the phone
// kept working. For the rest of this page's life no Preview document is mounted until
// the operator asks for that one; everything else, including the Preview tab itself,
// is exactly where it was.
//
// A browser tab can opt in by hand with the same parameter (`?mux_recovered=manual`),
// which is the escape hatch for a Preview that hangs a browser.

export const RECOVERY_PARAM = 'mux_recovered'

export type RecoveryNotice = { reason: string; message: string }

const REASONS: Record<string, string> = {
  renderer_hung: 'The swe-mux page stopped responding and was reloaded.',
  renderer_unresponsive: 'The swe-mux page stopped responding and was reloaded.',
  renderer_exited: 'The swe-mux page crashed and was reloaded.',
  operator_reload: 'The swe-mux window was reloaded from the tray.',
}

export function recoveryMessage(reason: string): string {
  return REASONS[reason] ?? 'swe-mux was opened in safe mode.'
}

/** Only `[a-z_]` survives, so the value is never rendered as anything but a word. */
export function normalizeReason(raw: string): string {
  const cleaned = raw.toLowerCase().replace(/[^a-z_]/g, '').slice(0, 40)
  return cleaned || 'manual'
}

export class RecoveryStore {
  private notice: RecoveryNotice | null = null
  private dismissed = false
  private readonly resumed = new Set<string>()
  private readonly listeners = new Set<() => void>()

  /** Read the flag from `href`; returns the URL to show without it, or null if absent. */
  consume(href: string): string | null {
    const url = new URL(href)
    const raw = url.searchParams.get(RECOVERY_PARAM)
    if (raw === null) return null
    const reason = normalizeReason(raw)
    this.notice = { reason, message: recoveryMessage(reason) }
    url.searchParams.delete(RECOVERY_PARAM)
    return `${url.pathname}${url.search}${url.hash}`
  }

  get active(): boolean { return this.notice !== null }
  current(): RecoveryNotice | null { return this.dismissed ? null : this.notice }
  previewPaused(id: string): boolean { return this.notice !== null && !this.resumed.has(id) }

  resume(id: string): void {
    this.resumed.add(id)
    this.emit()
  }

  resumeAll(ids: Iterable<string>): void {
    for (const id of ids) this.resumed.add(id)
    this.dismissed = true
    this.emit()
  }

  dismiss(): void {
    this.dismissed = true
    this.emit()
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  private emit(): void {
    for (const listener of this.listeners) listener()
  }
}

export const rendererRecovery = new RecoveryStore()

/** Once, before the first render: record the flag and take it out of the address bar,
 *  so an ordinary reload afterwards is an ordinary reload. */
export function consumeRendererRecovery(): void {
  if (typeof location === 'undefined') return
  const clean = rendererRecovery.consume(location.href)
  if (clean !== null) history.replaceState(history.state, '', clean)
}
