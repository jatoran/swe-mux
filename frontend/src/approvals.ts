import { t } from './localization/index.ts'
import type { ApprovalMode, ApprovalPolicy, ApprovalStatus, Session } from './types.ts'

/**
 * Client-side reading of the approval axis.
 *
 * The effective mode is recomputed here rather than taken from the server's
 * `effective_mode` for the *row* badge, because the badge renders from the
 * ordinary session snapshot that every `update` frame carries and must not need
 * a second request per session. The two implementations agree by construction:
 * both apply the expiry and the run-id check, and `approvals.test.ts` pins the
 * cases where they could drift.
 *
 * A grant that has expired is not the same as one that was never made. The strip
 * says so — "expired" rather than "off" — because an operator who set a mode and
 * finds it inactive needs to know whether it lapsed or was refused.
 */

export const APPROVAL_MODES: ApprovalMode[] = ['wait', 'allowlisted', 'allow_all']

export const MODE_LABELS: Record<ApprovalMode, string> = {
  get wait() { return t("approvals.716ecabb45") },
  get allowlisted() { return t("approvals.fdd0fdd0d4") },
  get allow_all() { return t("approvals.e8b2371270") },
}

export const MODE_DESCRIPTIONS: Record<ApprovalMode, string> = {
  get wait() { return t("approvals.a0768be902") },
  get allowlisted() { return t("approvals.52c7890a9e") },
  get allow_all() { return t("approvals.9d09febe87") },
}

export type ApprovalLapse = 'active' | 'expired' | 'superseded' | 'exhausted' | 'off'

/** Why a stored grant is not in force, or 'active'. */
export function approvalLapse(
  policy: ApprovalPolicy | undefined | null,
  runId: string | null | undefined,
  now: number,
): ApprovalLapse {
  if (!policy || policy.mode === 'wait') return 'off'
  if (policy.expires_at !== null && now >= policy.expires_at) return 'expired'
  // A grant with no run id was made against a session with no conversation, and
  // one whose id no longer matches belongs to a conversation that has been
  // replaced by /clear, /resume, Branch, or a rollover.
  if (policy.run_id && policy.run_id !== (runId || null)) return 'superseded'
  if (!policy.run_id !== !runId) return 'superseded'
  if (policy.max_auto > 0 && policy.auto_approved >= policy.max_auto) return 'exhausted'
  return 'active'
}

export function effectiveApprovalMode(
  session: Pick<Session, 'approval_policy'> & { agent_run_id?: string | null },
  now: number,
): ApprovalMode {
  const policy = session.approval_policy
  if (!policy) return 'wait'
  return approvalLapse(policy, session.agent_run_id ?? null, now) === 'active' ? policy.mode : 'wait'
}

/** Whether the sidebar row should carry an approval badge at all. */
export function showsApprovalBadge(
  session: Pick<Session, 'approval_policy'> & { agent_run_id?: string | null },
  now: number,
): boolean {
  return effectiveApprovalMode(session, now) !== 'wait'
}

function remaining(expiresAt: number | null, now: number): string {
  if (expiresAt === null) return ''
  const seconds = Math.max(0, expiresAt - now)
  if (seconds >= 3600) return `${Math.round(seconds / 3600)}h left`
  if (seconds >= 60) return `${Math.round(seconds / 60)}m left`
  return `${Math.round(seconds)}s left`
}

/**
 * The collapsed one-liner, after the `approvals:` prefix the strip draws.
 *
 * Deliberately never just "on": the two facts an operator needs from a glance
 * are how much authority is standing and how much of it has been spent, and a
 * mode that has answered nothing all session reads very differently from one
 * that has answered forty things.
 */
export function approvalSummary(status: ApprovalStatus | null, now: number): string {
  if (!status) return t("approvals.fbc6d752fe")
  if (!status.enabled) return t("approvals.b57f160c0f")
  if (!status.supported) return t("approvals.dd628455fa")
  const { policy } = status
  const lapse = approvalLapse(policy, policy.run_id, now)
  if (policy.mode === t("approvals.716ecabb45")) return status.unavailable ? t("approvals.697ce25c5b", { value1: status.unavailable }) : t("approvals.716ecabb45")
  const label = MODE_LABELS[policy.mode]
  if (lapse === 'expired') return t("approvals.c13fcdb382", { label: label })
  if (lapse === 'superseded') return t("approvals.6d3ce90a1a", { label: label })
  if (lapse === 'exhausted') return t("approvals.181355a6d7", { label: label, auto_approved: policy.auto_approved })
  const parts = [label, `${policy.auto_approved}/${policy.max_auto} approved`]
  const left = remaining(policy.expires_at, now)
  if (left) parts.push(left)
  if (policy.floor_deferred > 0) parts.push(t("approvals.0c45660bbf", { value1: policy.floor_deferred }))
  return parts.join(' · ')
}

/**
 * The pane-bar chip's whole text, after the `appr:` prefix.
 *
 * Four characters at most, because it sits beside `tts:` in a bar that also has
 * to hold the session state, the path, and the tools on a phone. The chip's job
 * on a glance is only "is authority standing here"; every number, reason, and
 * expiry lives in the drop-down. `ALL` is upper-case on purpose — it is the one
 * value that should catch the eye from across a fleet.
 */
export function approvalChipLabel(status: ApprovalStatus | null): string {
  if (!status) return '…'
  if (!status.enabled) return t("approvals.b4dc66dde8")
  if (!status.supported) return t("approvals.a683c5c534")
  switch (status.effective_mode) {
    case 'allow_all':
      return t("approvals.b5c7aed7cd")
    case 'allowlisted':
      return t("approvals.a330395cc0")
    default:
      // A grant that lapsed or was made for a replaced conversation is `wait` in
      // effect, and says so: the chip must never imply authority the daemon has
      // already dropped. The drop-down is where "expired" is spelled out.
      return t("approvals.716ecabb45")
  }
}

/** Why a mode cannot be picked right now, or '' when it can. */
export function modeUnavailableReason(status: ApprovalStatus, mode: ApprovalMode): string {
  if (mode === 'wait') return ''
  if (status.unavailable) return status.unavailable
  if (APPROVAL_MODES.indexOf(mode) > APPROVAL_MODES.indexOf(status.ceiling)) {
    return t("approvals.80cf630c2e", { value1: MODE_LABELS[status.ceiling] })
  }
  if (mode === 'allowlisted' && status.rules.length === 0) {
    return t("approvals.c5b7cee831")
  }
  return ''
}
