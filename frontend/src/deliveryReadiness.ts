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
    summary: 'this agent is mid-turn',
    clears: 'It clears when the turn ends. Mark a message “interrupt” to have it written into the running turn instead.',
  },
  approval_required: {
    summary: 'this agent is waiting on an approval prompt',
    clears: 'Answer the prompt in the terminal. Typing here cannot be allowed through, because text sent at an approval dialog would answer it.',
  },
  awaiting_user_input: {
    summary: 'this agent asked you a question',
    clears: 'Answer it in the terminal. The queue will not write into a question prompt, because the text would be taken as the answer.',
  },
  awaiting_approval: {
    summary: 'this agent is waiting on an approval prompt',
    clears: 'Answer the prompt in the terminal; this protection cannot be overridden.',
  },
  awaiting_question: {
    summary: 'this agent asked you a question',
    clears: 'Answer it in the terminal; this protection cannot be overridden.',
  },
  awaiting_elicitation: {
    summary: 'this agent is collecting input through a tool prompt',
    clears: 'Complete the prompt in the terminal; this protection cannot be overridden.',
  },
  provider_rate_limit: {
    summary: 'the provider is rate-limiting this agent',
    clears: 'It clears when the limit lifts.',
  },

  // --- blocked: the composer / the screen -----------------------------------
  terminal_input_after_completion: {
    summary: 'you typed in this terminal after its last turn ended',
    clears:
      'Clearing the line does not clear this: the queue counts keystrokes, not what is in the composer, because an estimate that said “empty” could authorize a send on top of text nothing can see. It resets when the next turn ends.',
  },
  operator_recently_typed: {
    summary: 'someone is typing in this terminal right now',
    clears: 'It clears a moment after the typing stops.',
  },
  unsubmitted_delivery_in_composer: {
    summary: 'the last message mux sent is still sitting in this composer, unsubmitted',
    clears:
      'The CLI took the text but not the Enter, so sending again would paste the next message on top of it and the agent would receive both as one. Press Enter in the pane to send what is there, or clear the composer — either one releases the queue.',
  },
  screen_not_at_agent_prompt: {
    summary: 'this terminal is not showing the agent’s prompt',
    clears: 'Leave whatever is on top — a pager, a picker, a viewer — and get back to the prompt.',
  },

  // --- blocked: identity, liveness, boundary --------------------------------
  session_ended: {
    summary: 'this session has ended',
    clears: 'Nothing can be delivered to it. Retarget the message at a live session.',
  },
  not_live_agent_run: {
    summary: 'this is not a live agent run',
    clears: 'Only a registered prompt-delivery harness can be queued to; a plain shell would execute the paste.',
  },
  transcript_stale: {
    summary: 'the followed transcript is no longer this session’s conversation',
    clears:
      'An in-CLI /clear or /new retires the conversation being read, so every other reading here is about a conversation that no longer exists. It clears when the successor conversation proves itself.',
  },
  remote_terminal_boundary: {
    summary: 'this terminal is a remote shell',
    clears: 'Delivery is restricted to local terminals, so SSH sessions and remote shells are never written into.',
  },
  terminal_boundary_unknown: {
    summary: 'the daemon cannot tell whether this terminal is local',
    clears: 'Only an explicit local boundary satisfies this check.',
  },
  turn_interrupted: { summary: 'the last turn was interrupted rather than completed' },
  turn_aborted: { summary: 'the last turn was aborted rather than completed' },

  // --- unknown: evidence is missing rather than damning ---------------------
  no_root_lifecycle_evidence: {
    summary: 'nothing has yet proved this agent finished a turn',
    clears: 'It resolves on its own once the agent starts or completes a turn.',
  },
  observation_capability_unknown: {
    summary: 'the daemon has not yet confirmed it can read this session',
    clears: 'It resolves once a hook or a transcript record arrives.',
  },
  completion_input_boundary_unknown: {
    summary: 'the daemon does not know what was in the composer when the last turn ended',
    clears: 'Without that boundary it cannot tell a clean prompt from a half-typed one, so it declines to guess.',
  },
  lifecycle_evidence_stale: {
    summary: 'the evidence about this agent has gone stale',
    clears: 'It refreshes on the next turn, hook, or transcript record.',
  },
  readiness_debounce_pending: {
    summary: 'the agent just reached its prompt',
    clears: 'It settles in a moment.',
  },
  incomplete_readiness_evidence: {
    summary: 'some required evidence is missing',
    clears: 'Unknown is never treated as safe, so the send needs your explicit confirmation.',
  },
  all_required_evidence_positive: { summary: 'every required check is positive' },
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
      state === 'safe' ? 'deliverable' : state === 'blocked' ? 'not deliverable' : 'readiness unknown',
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
  return age < 90 ? `${Math.round(age)}s ago` : `${Math.round(age / 60)} min ago`
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
