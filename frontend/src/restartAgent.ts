import { isInactiveSession } from './coldSession.ts'
import { isAgentBackend } from './harnessRegistry.ts'
import type { Session } from './types'

/**
 * Restarting a live agent into a fresh process on the same conversation.
 *
 * The daemon owns every decision (`session_restart.py`); this module only turns its
 * answer into what the session menu draws, so the menu never second-guesses the
 * refusal it is about to receive. The probe and the restart are the same function on
 * the daemon, which is why a row reading "Restart agent" is a promise the press keeps.
 *
 * Kept out of the component so the mapping is directly testable; the project's
 * node-based test runner strips types from `.ts` but not from `.tsx`.
 */

export type RestartBlocker = { code: string; message: string; forceable: boolean }

/** `GET /api/sessions/{id}/restart`. */
export type RestartEligibility = {
  session_id?: string
  eligible: boolean
  code: string | null
  reason: string | null
  force_allowed: boolean
  blockers: RestartBlocker[]
  would_lose: string[]
}

/** Whether a session is the kind the menu offers a restart for at all. */
export function canOfferRestart(session: Session): boolean {
  if (!isAgentBackend(session.backend)) return false
  if (isInactiveSession(session) || session.cold === true) return false
  return !['exited', 'crashed'].includes(session.state)
}

export type RestartMenuAction = 'restart' | 'arm' | 'force' | 'explain' | 'none'

export type RestartMenuView = {
  label: string
  /** Full explanation, for the tooltip and the screen-reader name. */
  title: string
  /** A second line drawn under the row: the reason, or what a forced restart loses. */
  detail: string
  /** Drawn muted and announced as disabled, but still pressable so a touch screen,
   *  which has no tooltip, can be told why by pressing it. */
  unavailable: boolean
  danger: boolean
  action: RestartMenuAction
}

const LABEL = 'Restart agent (fresh process)'

/** What the menu row says and does, from the probe's answer and the row's own state. */
export function restartMenuView(
  probe: RestartEligibility | null,
  options: { armed: boolean; running: boolean },
): RestartMenuView {
  if (options.running) {
    return {
      label: 'Restarting agent…', title: 'A restart is already running for this session.',
      detail: '', unavailable: true, danger: false, action: 'none',
    }
  }
  if (!probe) {
    return {
      label: LABEL, title: 'Checking whether this agent can be restarted…',
      detail: '', unavailable: true, danger: false, action: 'none',
    }
  }
  if (probe.eligible) {
    return {
      label: LABEL,
      title: 'End this agent process and resume the same conversation in a fresh one, in this pane.',
      detail: '', unavailable: false, danger: false, action: 'restart',
    }
  }
  if (!probe.force_allowed) {
    const reason = probe.reason || 'This agent cannot be restarted right now.'
    return {
      label: LABEL, title: reason, detail: reason,
      unavailable: true, danger: false, action: 'explain',
    }
  }
  const losses = restartLossSummary(probe)
  if (!options.armed) {
    return {
      label: 'Restart agent anyway…',
      title: `A restart now will lose: ${losses}. Press to review, then confirm.`,
      detail: '', unavailable: false, danger: true, action: 'arm',
    }
  }
  return {
    label: 'Confirm restart',
    title: `Restart now and lose: ${losses}.`,
    detail: `Will lose: ${losses}.`,
    unavailable: false, danger: true, action: 'force',
  }
}

/** One sentence listing what a forced restart loses, in the daemon's own words. */
export function restartLossSummary(probe: RestartEligibility): string {
  const losses = probe.would_lose.filter(Boolean)
  return losses.length ? losses.join('; ') : 'nothing the daemon could name'
}

/** The toast for a refused restart, pointing at the override when there is one. */
export function restartRefusalMessage(detail: { error?: string; force_allowed?: boolean } | undefined, fallback: string): string {
  const reason = detail?.error || fallback
  return detail?.force_allowed
    ? `Restart refused: ${reason}. Open the session menu and choose "Restart agent anyway" to override.`
    : `Restart refused: ${reason}.`
}
