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
    ? 'Inactive - no process is running. Restart the terminal to use it again.'
    : 'Inactive - no process is running. Resume to continue this conversation.'
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
      return 'Full-screen interfaces cannot be replayed without the process that drew them.'
    case 'repaints_scrollback':
      return 'This harness repaints its transcript rather than writing scrollback, so there was nothing to keep.'
    case null:
    case undefined:
      return null
    default:
      return 'No terminal content was captured.'
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
  if (seconds < 90) return 'seconds before the shutdown'
  const minutes = Math.round(seconds / 60)
  if (minutes < 90) return `${minutes} minute${minutes === 1 ? '' : 's'} before this restart`
  const hours = Math.round(minutes / 60)
  if (hours < 36) return `${hours} hour${hours === 1 ? '' : 's'} before this restart`
  return `${Math.round(hours / 24)} day${Math.round(hours / 24) === 1 ? '' : 's'} before this restart`
}

/** One line explaining a recovered row, for a tooltip. */
export function coldSessionSummary(session: Session, now: number = Date.now()): string {
  const content = session.cold_terminal_at
    ? `Terminal content is from ${checkpointAge(session.cold_terminal_at, now)}.`
    : missingContentReason(session) ?? 'No terminal content was captured.'
  return `Recovered after an unexpected shutdown — the process is gone. ${content}`
}
