import { t } from './localization/index.ts'
/**
 * What the Windows desktop shell publishes into its own page.
 *
 * The shell hosts this app in WebView2, which has no devtools, no console and
 * no network tab, and whose permission decisions never reach the daemon - so a
 * capture failure inside it is invisible from every log swe-mux writes. The
 * shell answers that by installing a microphone grant scoped to the daemon's
 * origin (`swe_mux/desktop_permissions.py`) and publishing the outcome as a
 * frozen global on the page. This module reads it.
 *
 * A browser tab never has the global, and that absence is meaningful rather
 * than missing: it means "not the desktop shell", so nothing here is appended.
 */

export const DESKTOP_MEDIA_MARKER = '__swemuxDesktopMedia'

export type DesktopMediaState = 'pending' | 'armed' | 'granted' | 'refused' | 'unsupported'

export type DesktopMediaReport = {
  state: DesktopMediaState
  origin: string
  detail: string
  mechanism: string | null
}

const STATES: readonly DesktopMediaState[] = ['pending', 'armed', 'granted', 'refused', 'unsupported']

/** The shell's report, or null in any browser (and in a shell too old to publish one). */
export function desktopMediaReport(scope: Record<string, unknown> = globalThis as unknown as Record<string, unknown>): DesktopMediaReport | null {
  const raw = scope[DESKTOP_MEDIA_MARKER]
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Partial<DesktopMediaReport>
  if (!value.state || !STATES.includes(value.state)) return null
  return {
    state: value.state,
    origin: typeof value.origin === 'string' ? value.origin : '',
    detail: typeof value.detail === 'string' ? value.detail : '',
    mechanism: typeof value.mechanism === 'string' ? value.mechanism : null,
  }
}

/**
 * Add the one sentence a desktop-shell reader needs, and nothing in a browser.
 *
 * Each state sends the reader somewhere different, which is the whole point of
 * publishing it: `granted` and `armed` both mean "stop looking at permissions",
 * but only `armed` means WebView2 never asked, and only `unsupported` means the
 * shell's own grant never installed - which is not the same as a broken
 * microphone, so that state carries the shell's own sentence verbatim rather
 * than a conclusion this module invents.
 */
export function captureFailureNote(message: string, report: DesktopMediaReport | null): string {
  if (!report) return message
  const suffix =
    report.state === 'granted'
      ? t("desktopShell.7da24730ef", { value1: report.origin })
      : report.state === 'armed'
        ? t("desktopShell.ab8a359677", { value1: report.origin })
        : report.state === 'pending'
          ? t("desktopShell.41d7021edc")
          : report.detail
  return suffix ? `${message} ${suffix}` : message
}
