import { t } from './localization/index.ts'
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

const restartLabel = () => (t("restartAgent.2b29a981b4"))

/** What the menu row says and does, from the probe's answer and the row's own state. */
export function restartMenuView(
  probe: RestartEligibility | null,
  options: { armed: boolean; running: boolean },
): RestartMenuView {
  if (options.running) {
    return {
      get label() { return t("restartAgent.b56e18c8bc") }, get title() { return t("restartAgent.2532f751e5") },
      detail: '', unavailable: true, danger: false, action: 'none',
    }
  }
  if (!probe) {
    return {
      label: restartLabel(), get title() { return t("restartAgent.d38b670068") },
      detail: '', unavailable: true, danger: false, action: 'none',
    }
  }
  if (probe.eligible) {
    return {
      label: restartLabel(),
      get title() { return t("restartAgent.4eadfba958") },
      detail: '', unavailable: false, danger: false, action: 'restart',
    }
  }
  if (!probe.force_allowed) {
    const reason = probe.reason || t("restartAgent.98847f2f54")
    return {
      label: restartLabel(), title: reason, detail: reason,
      unavailable: true, danger: false, action: 'explain',
    }
  }
  const losses = restartLossSummary(probe)
  if (!options.armed) {
    return {
      get label() { return t("restartAgent.92282d08bb") },
      get title() { return t("restartAgent.f7f7cd922f", { losses: losses }) },
      detail: '', unavailable: false, danger: true, action: 'arm',
    }
  }
  return {
    get label() { return t("restartAgent.25b0910de6") },
    get title() { return t("restartAgent.75414d84f5", { losses: losses }) },
    get detail() { return t("restartAgent.cb7a864581", { losses: losses }) },
    unavailable: false, danger: true, action: 'force',
  }
}

/** One sentence listing what a forced restart loses, in the daemon's own words. */
export function restartLossSummary(probe: RestartEligibility): string {
  const losses = probe.would_lose.filter(Boolean)
  return losses.length ? losses.join('; ') : t("restartAgent.500b50d32e")
}

/** The toast for a refused restart, pointing at the override when there is one. */
export function restartRefusalMessage(detail: { error?: string; force_allowed?: boolean } | undefined, fallback: string): string {
  const reason = detail?.error || fallback
  return detail?.force_allowed
    ? t("restartAgent.25e7dfa14e", { value1: reason })
    : t("restartAgent.7bc329640d", { value1: reason })
}
