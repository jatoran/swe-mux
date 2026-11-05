import { t } from './localization/index.ts'
import type { Session } from './types'

/**
 * Reading a session that no longer has a process behind it.
 *
 * Two situations reach the UI and they want different words. An **ended**
 * session is one the daemon watched exit: the pane holds everything it printed,
 * and the only thing worth saying is that typing will not reach anything. A
 * **recovered** (cold) session is one whose process died with the daemon that
 * owned it, so the pane was rebuilt from disk — its content has an age, or there
 * is none at all, and the empty case reads as a bug unless something explains it.
 *
 * Logic lives here rather than in the component so it is directly testable; the
 * project's node-based test runner strips types from `.ts` but not from `.tsx`.
 */

/** A recovered session: rebuilt from durable recovery data, not observed. */
export function isColdSession(session: Session): boolean {
  return session.cold === true
}

/** An intentionally stopped session whose row and pane are durably retained. */
export function isInactiveSession(session: Session): boolean {
  return session.inactive === true
}

/** One line explaining why the retained row has no process behind it. */
export function inactiveSessionSummary(session: Session): string {
  if (!isInactiveSession(session)) return ''
  return session.backend === 'shell'
    ? t("coldSession.d0d525b59a")
    : t("coldSession.7d751176f9")
}

/** Whether a recovered shell can be restarted from its recorded command. */
export function canRestartCold(session: Session): boolean {
  // Cold *agents* are deliberately excluded. Replaying an agent's argv would
  // start a fresh conversation while re-injecting the old one's `--session-id`,
  // where the operator asked to return to the conversation. That is Resume's job.
  return isColdSession(session) && session.backend === 'shell'
}

/**
 * Why a recovered session has no terminal content, in the operator's terms.
 *
 * `null` means the daemon named no reason, which is the ordinary case for a
 * session that simply has content.
 */
export function missingContentReason(session: Session): string | null {
  switch (session.cold_terminal_skipped) {
    case 'alternate_screen_harness':
    case 'alternate_screen':
      return t("coldSession.66c77ac241")
    case 'repaints_scrollback':
      return t("coldSession.7fc390f10f")
    case null:
    case undefined:
      return null
    default:
      return t("coldSession.259b15983a")
  }
}

/**
 * How old a recovered pane's content is, coarse on purpose.
 *
 * The exact second is never interesting and would invite reading the checkpoint
 * as a precise record of the crash; what matters is whether the pane is showing
 * the last moments or something from hours earlier.
 */
export function checkpointAge(capturedAt: number, now: number): string {
  const seconds = Math.max(0, Math.round(now / 1000 - capturedAt))
  if (seconds < 90) return t("coldSession.07720b3202")
  const minutes = Math.round(seconds / 60)
  if (minutes < 90) return t("coldSession.2c5cff824e", { value1: minutes, value2: (minutes === 1) ? 'yes' : 'no' })
  const hours = Math.round(minutes / 60)
  if (hours < 36) return t("coldSession.e06650a01f", { value1: hours, value2: (hours === 1) ? 'yes' : 'no' })
  return t("coldSession.8ad118cc76", { value1: Math.round(hours / 24), value2: (Math.round(hours / 24) === 1) ? 'yes' : 'no' })
}

/** One line explaining a recovered row, for a tooltip. */
export function coldSessionSummary(session: Session, now: number = Date.now()): string {
  const content = session.cold_terminal_at
    ? t("coldSession.6f834a759f", { value1: checkpointAge(session.cold_terminal_at, now) })
    : missingContentReason(session) ?? t("coldSession.259b15983a")
  return t("coldSession.6f203cf379", { content: content })
}
