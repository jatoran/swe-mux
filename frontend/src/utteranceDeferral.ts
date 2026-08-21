import { utteranceCompleteness, utteranceWords } from './utteranceCompleteness.ts'
import type { CompletenessKind } from './utteranceCompleteness.ts'

/**
 * The holding pen for one unfinished utterance.
 *
 * The feature's safety claim is structural - "at most one deferral per
 * utterance, so unbounded round-trips are impossible" - and a claim like that
 * belongs somewhere it can be tested rather than inside a component that needs a
 * microphone, a decoder, and a dialog to exercise. So the decisions live here and
 * the side effects stay with the caller: this class never dispatches a turn,
 * never posts a diagnostic, and never owns a timer. It answers three questions -
 * what should happen to this utterance, is the release due, and who is holding
 * the fragment now - and the caller does the rest.
 *
 * The invariant it enforces: a fragment that has already been deferred once
 * merges into the next utterance and dispatches, whatever the merged text looks
 * like. The heuristic is deliberately NOT re-run on a merge, which is what makes
 * a chain of fragments unable to compound into an unbounded wait.
 *
 * Clock-injected, like `CaptureFrameWatchdog`, so the hold ceiling is testable
 * without waiting fifteen real seconds.
 */

export type DeferredUtterance = {
  /** Everything held so far - a single fragment, never an accumulated chain. */
  text: string
  /** The dangling token that caused the deferral. Reported for every outcome. */
  trigger: string
  kind: CompletenessKind
  words: number
  /** Clock reading at the deferral, for the hold ceiling and the held-ms report. */
  at: number
}

export type OfferResult =
  /** Send this text as one assistant turn. `merged` is the fragment folded in, if any. */
  | { kind: 'dispatch'; text: string; merged: DeferredUtterance | null }
  /** Hold it: arm a release timer for the extension and show the operator why. */
  | { kind: 'defer'; deferred: DeferredUtterance }

export type ReleaseResult =
  /** Not due: speech is still arriving or a decode is in flight. Re-arm the timer. */
  | { kind: 'wait' }
  /** Due. Submit it alone, exactly as it would have gone without the feature. */
  | { kind: 'submit'; deferred: DeferredUtterance }
  /** Someone else already claimed this fragment. The timer is stale; do nothing. */
  | { kind: 'gone' }

/**
 * Hard ceiling on how long a fragment may be held, whatever the operator is
 * doing. The release re-arms while speech is still arriving - answering half a
 * sentence because the other half is mid-flight is the exact failure being fixed
 * - and this is what stops a detector wedged in "speaking" from holding a turn
 * forever.
 */
export const DEFERRAL_MAX_HOLD_MS = 15_000

export class DeferralPen {
  // A plain assigned field, not a constructor parameter property: the frontend
  // unit tests run under node's type stripping, which refuses parameter properties.
  private readonly now: () => number
  private held: DeferredUtterance | null = null

  constructor(now: () => number = () => performance.now()) { this.now = now }

  /** The fragment currently held back, or null. Read by the patience provider. */
  get pending(): DeferredUtterance | null { return this.held }

  /**
   * Route one plain chat utterance.
   *
   * With a fragment held this always dispatches the merged text - the second
   * breath completes the thought, and re-judging the merge is what a bounded
   * wait cannot afford. With nothing held it dispatches a complete utterance and
   * holds an unfinished one.
   */
  offer(text: string): OfferResult {
    const body = text.trim()
    const merged = this.held
    if (merged) {
      this.held = null
      return { kind: 'dispatch', text: `${merged.text} ${body}`.trim(), merged }
    }
    const verdict = utteranceCompleteness(body)
    if (verdict.complete || !verdict.trigger || !verdict.kind) {
      return { kind: 'dispatch', text: body, merged: null }
    }
    const deferred: DeferredUtterance = {
      text: body,
      trigger: verdict.trigger,
      kind: verdict.kind,
      words: utteranceWords(body).length,
      at: this.now(),
    }
    this.held = deferred
    return { kind: 'defer', deferred }
  }

  /**
   * The release timer fired for `deferred`.
   *
   * `busy` is the caller's answer to "is the operator still mid-thought" -
   * speech arriving or an utterance mid-decode. A stale timer for a fragment
   * something else already claimed reports `gone` rather than resurrecting it.
   */
  release(deferred: DeferredUtterance, busy: boolean): ReleaseResult {
    if (this.held !== deferred) return { kind: 'gone' }
    if (busy && this.now() - deferred.at < DEFERRAL_MAX_HOLD_MS) return { kind: 'wait' }
    this.held = null
    return { kind: 'submit', deferred }
  }

  /**
   * Claim the held fragment for any non-dispatch reason: folded into a
   * brainstorm hold, or dropped by cancel, standby, or Talk stopping. Returns
   * null when nothing is held, so the caller can report only real deferrals.
   */
  take(): DeferredUtterance | null {
    const held = this.held
    this.held = null
    return held
  }
}
