import { COMPLETION, deferralExtensionMs, utteranceCompleteness, utteranceWords } from './utteranceCompleteness.ts'
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

/**
 * Who decided this fragment was unfinished, which decides how it ends.
 *
 * `heuristic` is the pre-model word rule: it may be wrong, so its window expires
 * into an ordinary turn - exactly what would have happened without the feature.
 * `assistant` is the model itself reporting that the turn contained nothing to
 * answer, which is a much stronger claim and has a very different consequence:
 * re-sending that text alone would produce the same verdict again, so it is
 * parked for the next breath instead of ever being submitted by a timer. That
 * asymmetry is what makes a hold loop impossible rather than merely unlikely.
 */
export type DeferralSource = 'heuristic' | 'assistant'

export type DeferredUtterance = {
  /** Everything held so far - a single fragment, never an accumulated chain. */
  text: string
  /** The dangling token that caused the deferral. Reported for every outcome. */
  trigger: string
  kind: CompletenessKind
  words: number
  /** Clock reading at the deferral, for the hold ceiling and the held-ms report. */
  at: number
  source: DeferralSource
  /** P(finished) that produced this hold; reported so the priors can be tuned. */
  completion: number
  /** The window this score bought. 0 for a park, which has no release timer. */
  extensionMs: number
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

/**
 * How long a parked fragment waits for the breath that completes it.
 *
 * A park has no release timer by design, so this is the only thing standing
 * between "the operator paused to think" and "the operator walked away and comes
 * back after lunch to find their half-sentence glued to a new question". Long
 * enough to cover real thinking, short enough that the merge is still obviously
 * the same thought.
 */
export const DEFERRAL_PARK_MAX_MS = 120_000

/**
 * Word ceiling on a parked fragment.
 *
 * Each park needs new speech from the operator, so parks cannot loop on their own
 * - but a long enough dictation of pure context would keep parking forever and
 * never reach the model. At this many words the accumulated text is a turn in its
 * own right and dispatches whatever it looks like.
 */
export const DEFERRAL_PARK_MAX_WORDS = 400

/** The score attributed to a hold the model asked for. See `DeferralSource`. */
export const ASSISTANT_HOLD_COMPLETION = 0.12

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
  offer(text: string, patienceMs: number): OfferResult {
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
    const extensionMs = deferralExtensionMs(patienceMs, verdict.completion)
    // A score that buys no window is not a deferral. Belt-and-braces against a
    // future scorer whose threshold and factor curve disagree: without this the
    // caller would arm a zero-length timer and answer mid-clause anyway.
    if (extensionMs <= 0) return { kind: 'dispatch', text: body, merged: null }
    const deferred: DeferredUtterance = {
      text: body,
      trigger: verdict.trigger,
      kind: verdict.kind,
      words: utteranceWords(body).length,
      at: this.now(),
      source: 'heuristic',
      completion: verdict.completion,
      extensionMs,
    }
    this.held = deferred
    return { kind: 'defer', deferred }
  }

  /**
   * The model answered a dispatched turn with "there is nothing here to answer".
   *
   * Parked rather than deferred: no timer is armed, because there is no useful
   * thing for a timer to do. Submitting the text again would produce the same
   * verdict, and saying anything at all is the interruption the whole feature
   * exists to prevent, so it waits silently for the breath that finishes it.
   *
   * Refuses once the accumulated text is long enough to be a turn in its own
   * right, so an operator who never stops trailing off still eventually gets an
   * answer. Also refuses when something is already held, which cannot happen from
   * the dispatch path but keeps the one-fragment invariant true by construction.
   */
  park(text: string, patienceMs: number): DeferredUtterance | null {
    const body = text.trim()
    if (this.held || !body) return null
    const words = utteranceWords(body).length
    if (words >= DEFERRAL_PARK_MAX_WORDS) return null
    const parked: DeferredUtterance = {
      text: body,
      trigger: 'assistant',
      kind: 'conjunction',
      words,
      at: this.now(),
      source: 'assistant',
      completion: ASSISTANT_HOLD_COMPLETION,
      // Sized like any other hold even though nothing will fire on it: the
      // window is what the *gate* reads, and an operator the model just judged
      // mid-thought needs the roomier tail more than anyone, not less.
      extensionMs: deferralExtensionMs(patienceMs, ASSISTANT_HOLD_COMPLETION),
    }
    this.held = parked
    return parked
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
   * Drop a parked fragment that waited too long for its second half.
   *
   * Returns it so the caller can report the outcome; returns null while the park
   * is still fresh, so this is safe to call on any convenient tick.
   */
  expireStalePark(): DeferredUtterance | null {
    const held = this.held
    if (!held || held.source !== 'assistant') return null
    if (this.now() - held.at < DEFERRAL_PARK_MAX_MS) return null
    this.held = null
    return held
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

/** Re-exported so callers scoring a fragment do not import two modules. */
export { COMPLETION }

/**
 * What the conversation panel should show as the operator's in-flight words.
 *
 * Three things can be true at once - a brainstorm buffer is accumulating, the
 * pen is holding a fragment, and a speculative decode has a provisional reading
 * of the breath happening right now - and all three are the same sentence from
 * the operator's point of view. Composed here, pure, because the ordering rule
 * ("what is already held, then what is being said") is the whole correctness
 * claim and it belongs somewhere a test can reach without a microphone.
 *
 * The row this feeds is deliberately NOT a dialog message. A held fragment that
 * became a real message would have to be deleted again the moment the merged
 * turn re-sent it, which is exactly the disappearing-text behaviour this
 * replaces; a client-local row simply clears when the real turn arrives.
 */
export type PendingUtterance = {
  text: string
  /** Header line for the row, naming which of the three states it is in. */
  note: string
}

export type PendingInputs = {
  /** Brainstorm hold is engaged, so plain speech is buffering rather than asking. */
  hold: boolean
  holdBuffer: string
  /** The pen's fragment, if one is held or parked. */
  held: DeferredUtterance | null
  /**
   * The speculative decode's reading of the utterance in progress.
   *
   * Lower fidelity than the real one - it runs on the command profile - and it
   * is a PREFIX, taken at the first short pause. It exists because the real
   * transcript does not arrive until the endpoint has proved the turn is over,
   * which is seconds later and is the wait that makes the surface feel deaf.
   */
  provisional: string
}

export function pendingUtterance(inputs: PendingInputs): PendingUtterance {
  const provisional = inputs.provisional.trim()
  const join = (...parts: string[]) => parts.map(part => part.trim()).filter(Boolean).join(' ')
  if (inputs.hold) {
    return {
      text: join(inputs.holdBuffer, provisional),
      note: 'holding — say “go ahead” to send',
    }
  }
  if (inputs.held) {
    return {
      text: join(inputs.held.text, provisional),
      // The two holds end differently and the operator has to be able to tell
      // which one they are in: one expires into a turn, the other never does.
      note: inputs.held.source === 'assistant'
        ? 'waiting for the rest of your thought'
        : `unfinished after “${inputs.held.trigger}” — keep going, or pause to send`,
    }
  }
  if (provisional) return { text: provisional, note: 'hearing you…' }
  return { text: '', note: '' }
}
