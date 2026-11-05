import { t } from './localization/index.ts'
import { serverNow } from './serverClock.ts'
import type { DeliveryReadiness, DeliveryState, Session } from './types.ts'

/**
 * Turning the daemon's readiness verdict into something a person can act on.
 *
 * Until this existed, every surface printed the raw reason code - the queue's
 * refusal read `Not safe right now: terminal_input_after_completion`, which names
 * the check that fired and answers none of the three questions actually being
 * asked: what does that mean, can I override it, and what would clear it. The
 * third is the one that matters most, because several of these blocks cannot be
 * cleared by doing the obvious thing (see `terminal_input_after_completion`).
 *
 * Two rules hold this file honest:
 *
 * - **The vocabulary is the daemon's, and it grows.** An unmapped reason renders
 *   as its own code rather than being hidden or guessed at - a reader seeing
 *   `some_new_reason` is mildly unhelped; a reader seeing nothing is misled.
 * - **This never predicts safety.** It explains a verdict the daemon reached and
 *   is not itself an input to any decision. The daemon re-evaluates at send.
 */

type Explanation = {
  /** One clause, lowercase, completes "not deliverable — …". */
  summary: string
  /** What would clear it. Omitted when the reason speaks for itself. */
  clears?: string
}

const EXPLANATIONS: Record<string, Explanation> = {
  // --- blocked: the agent is busy or being asked something -------------------
  root_agent_working: {
    get summary() { return t("deliveryReadiness.eef46d4d1d") },
    get clears() { return t("deliveryReadiness.d0f9a0252e") },
  },
  approval_required: {
    get summary() { return t("deliveryReadiness.f5a1aabb1e") },
    get clears() { return t("deliveryReadiness.4380b02b1c") },
  },
  awaiting_user_input: {
    get summary() { return t("deliveryReadiness.f1ede2a0d0") },
    get clears() { return t("deliveryReadiness.550511d99f") },
  },
  awaiting_approval: {
    get summary() { return t("deliveryReadiness.f5a1aabb1e") },
    get clears() { return t("deliveryReadiness.255022c879") },
  },
  awaiting_question: {
    get summary() { return t("deliveryReadiness.f1ede2a0d0") },
    get clears() { return t("deliveryReadiness.bd2ce1826e") },
  },
  awaiting_elicitation: {
    get summary() { return t("deliveryReadiness.6a7fa6d678") },
    get clears() { return t("deliveryReadiness.93e9a6cb7d") },
  },
  provider_rate_limit: {
    get summary() { return t("deliveryReadiness.217bcc1fc0") },
    get clears() { return t("deliveryReadiness.9d12746ff5") },
  },

  // --- blocked: the composer / the screen -----------------------------------
  composer_holds_draft: {
    get summary() { return t("deliveryReadiness.4be0be851e") },
    get clears() { return t("deliveryReadiness.65f54d2497") },
  },
  terminal_input_after_completion: {
    get summary() { return t("deliveryReadiness.11dadd68fa") },
    get clears() { return t("deliveryReadiness.3f7445b649") },
  },
  operator_recently_typed: {
    get summary() { return t("deliveryReadiness.a7c5b374b2") },
    get clears() { return t("deliveryReadiness.4615818780") },
  },
  unsubmitted_delivery_in_composer: {
    get summary() { return t("deliveryReadiness.587d03cacb") },
    get clears() { return t("deliveryReadiness.5a86827dd6") },
  },
  screen_not_at_agent_prompt: {
    get summary() { return t("deliveryReadiness.2db6c91aa2") },
    get clears() { return t("deliveryReadiness.267d45c635") },
  },

  // --- blocked: identity, liveness, boundary --------------------------------
  session_ended: {
    get summary() { return t("deliveryReadiness.fd1bc81b47") },
    get clears() { return t("deliveryReadiness.7782dd9ead") },
  },
  not_live_agent_run: {
    get summary() { return t("deliveryReadiness.d4f479d1e7") },
    get clears() { return t("deliveryReadiness.f8d7c36e0c") },
  },
  transcript_stale: {
    get summary() { return t("deliveryReadiness.8236821327") },
    get clears() { return t("deliveryReadiness.858662ca54") },
  },
  remote_terminal_boundary: {
    get summary() { return t("deliveryReadiness.6092603cf1") },
    get clears() { return t("deliveryReadiness.07a52e7832") },
  },
  terminal_boundary_unknown: {
    get summary() { return t("deliveryReadiness.cd047876f0") },
    get clears() { return t("deliveryReadiness.aec66ea8e3") },
  },
  turn_interrupted: { get summary() { return t("deliveryReadiness.b39ca97ac5") } },
  turn_aborted: { get summary() { return t("deliveryReadiness.f6ce899bfc") } },

  // --- unknown: evidence is missing rather than damning ---------------------
  no_root_lifecycle_evidence: {
    get summary() { return t("deliveryReadiness.c330fb2892") },
    get clears() { return t("deliveryReadiness.b976e6d9bb") },
  },
  observation_capability_unknown: {
    get summary() { return t("deliveryReadiness.418c04500d") },
    get clears() { return t("deliveryReadiness.0a95ae8726") },
  },
  completion_input_boundary_unknown: {
    get summary() { return t("deliveryReadiness.a70cc872f2") },
    get clears() { return t("deliveryReadiness.dd11308fd3") },
  },
  lifecycle_evidence_stale: {
    get summary() { return t("deliveryReadiness.4241cbb709") },
    get clears() { return t("deliveryReadiness.9b76dc90a2") },
  },
  readiness_debounce_pending: {
    get summary() { return t("deliveryReadiness.7c1fc3da1b") },
    get clears() { return t("deliveryReadiness.f5bd9dc8b7") },
  },
  incomplete_readiness_evidence: {
    get summary() { return t("deliveryReadiness.d42d99fd68") },
    get clears() { return t("deliveryReadiness.43e8ac298b") },
  },
  all_required_evidence_positive: { get summary() { return t("deliveryReadiness.d0ec4ac094") } },
}

/** The one place a raw reason code becomes a sentence. Unmapped codes pass through. */
export function explainReason(reason: string): Explanation {
  return EXPLANATIONS[reason] ?? { summary: reason.replace(/_/g, ' ') }
}

/**
 * A list of reason codes as one sentence fragment.
 *
 * Every surface that prints daemon refusal reasons goes through here — the queue's
 * refusal line, the send-to-agent dialog, the prompt library's staging note, the
 * per-message blocked marks. They used to `join(', ')` the raw codes independently,
 * which is four places for the vocabulary to drift and four places a reader met
 * `terminal_input_after_completion` with no way to find out what it meant.
 */
export function wordReasons(reasons: readonly string[]): string {
  return reasons.map(reason => explainReason(reason).summary).join('; ')
}

/** Whether any of these reasons is one no confirmation can override. */
export function isProtected(readiness: DeliveryReadiness | undefined): boolean {
  return !!readiness?.protected?.length
}

/** Every reason, tolerating the pre-`reasons` payload shape. */
export function readinessReasons(readiness: DeliveryReadiness | undefined): string[] {
  if (!readiness) return []
  if (readiness.reasons?.length) return readiness.reasons
  return readiness.reason ? [readiness.reason] : []
}

export type ReadinessVerdict = {
  state: DeliveryState
  /** "deliverable" / "not deliverable" / "readiness unknown". */
  headline: string
  /** The leading reason as a sentence clause, empty when safe. */
  summary: string
  /** What would clear it, when that is not obvious from the summary. */
  clears: string
  /** True when no “Send anyway” exists, because the daemon will refuse it. */
  protected: boolean
  /** Reasons past the first, already worded. Usually empty. */
  also: string[]
  /** Seconds since the daemon took this reading; null when it did not say. */
  ageSeconds: number | null
}

/**
 * The whole verdict, worded once, for every surface that shows it.
 *
 * Returns null for an absent reading rather than inventing `unknown`: "the
 * daemon has not told us" and "the daemon evaluated this as unknown" are
 * different facts, and only the second is a readiness verdict.
 */
export function describeReadiness(
  readiness: DeliveryReadiness | undefined | null,
  now = serverNow(),
): ReadinessVerdict | null {
  if (!readiness) return null
  const reasons = readinessReasons(readiness)
  const [first, ...rest] = reasons
  const leading = first ? explainReason(first) : { summary: '' }
  const state = readiness.state
  return {
    state,
    headline:
      state === 'safe' ? 'deliverable' : state === 'blocked' ? t("deliveryReadiness.38e847b485") : t("deliveryReadiness.766f768353"),
    summary: state === 'safe' ? '' : leading.summary,
    clears: state === 'safe' ? '' : leading.clears ?? '',
    protected: isProtected(readiness),
    also: rest.map(reason => explainReason(reason).summary),
    ageSeconds:
      typeof readiness.observed_at === 'number' ? Math.max(0, now - readiness.observed_at) : null,
  }
}

/**
 * How old a reading may be before its age is worth showing.
 *
 * The live stream keeps this at zero for the sessions it follows, so a visible
 * age is itself the signal that this one is not being followed - which is a
 * true and useful thing to say, and much better than silently presenting a
 * minute-old verdict as current.
 */
export const READINESS_AGE_VISIBLE_SECONDS = 5

export function readinessAgeLabel(verdict: ReadinessVerdict): string {
  const age = verdict.ageSeconds
  if (age === null || age < READINESS_AGE_VISIBLE_SECONDS) return ''
  return age < 90 ? t("deliveryReadiness.54cc4999d6", { value1: Math.round(age) }) : t("deliveryReadiness.1de67215cf", { value1: Math.round(age / 60) })
}

/** One compact line for a row or a chip: "not deliverable — this agent is mid-turn". */
export function readinessLine(readiness: DeliveryReadiness | undefined | null): string {
  const verdict = describeReadiness(readiness)
  if (!verdict) return ''
  return verdict.summary ? `${verdict.headline} — ${verdict.summary}` : verdict.headline
}

/** The readiness a surface should show for one session, or undefined. */
export function sessionReadiness(session: Session | null | undefined): DeliveryReadiness | undefined {
  return session?.delivery_readiness
}

/**
 * The newest of several readings of the same session.
 *
 * A surface can hold two: the one riding the session row (REST load, then kept
 * current by the readiness stream) and the one the queue's own fetch returned.
 * Neither is reliably newer - the stream only follows sessions some surface can
 * be reading, and the queue fetch only happens on open and on mutation - so
 * picking by `observed_at` is the only ordering that is right in both directions.
 * A reading with no stamp loses to one that has it: an unstamped payload predates
 * the stamp, so it is older by construction.
 */
export function freshestReadiness(
  ...candidates: (DeliveryReadiness | null | undefined)[]
): DeliveryReadiness | undefined {
  let best: DeliveryReadiness | undefined
  for (const candidate of candidates) {
    if (!candidate) continue
    if (!best || (candidate.observed_at ?? 0) > (best.observed_at ?? 0)) best = candidate
  }
  return best
}
