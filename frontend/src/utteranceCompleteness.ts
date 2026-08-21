/**
 * Did the operator finish the thought, or trail off mid-clause?
 *
 * The defining voice-agent complaint is that a pause becomes a reply, so the
 * operator rushes to beat the endpoint. The fix is deterministic and it runs
 * BEFORE a chat turn is dispatched, for two reasons. A model-arbitrated "are you
 * done?" loop is exactly the round-trip spam this feature exists to remove; and
 * a model instructed to sometimes return nothing will return nothing when it
 * should have answered, which is a worse failure than answering early.
 *
 * Pure, so the whole rule set is unit-testable without a microphone, a decoder,
 * or a dialog. It answers one narrow question - does the transcript END
 * mid-clause? - and deliberately does not attempt to parse English. A dangling
 * conjunction, preposition, or article is the shape a trailed-off thought
 * actually has; everything else reads as complete, because the cost of a miss is
 * the ordinary behaviour (the utterance is answered, and the daemon's queue-merge
 * still coalesces the next breath into the pending turn) while the cost of a
 * false positive is the operator waiting on a surface that hesitates over normal
 * speech.
 *
 * Two structural guards keep the false-positive rate low without a parser:
 *
 * - **Questions strand prepositions legitimately.** "What is this for", "where
 *   are you from", "who should I send it to" are finished turns. A trailing `?`
 *   or an interrogative opener therefore disqualifies the preposition rule
 *   (never the article or conjunction rules - nothing ends "the").
 * - **Short utterances end on particles legitimately.** "I'm in.", "it's on.",
 *   "come on." are complete; "let me know what you think about" is not. The
 *   prepositions that double as adverbs or verb particles count only once the
 *   utterance is long enough to be a clause rather than an idiom.
 *
 * Every deferral is reported with the trigger token that caused it (see
 * `/api/voice/deferral-diagnostic`), so this list is tuned from measured false
 * positives rather than from intuition.
 */

export type CompletenessKind = 'conjunction' | 'preposition' | 'article'

export type CompletenessVerdict = {
  /** False only when the transcript ends on a dangler this rule set recognizes. */
  complete: boolean
  /** The dangling token, lowercased and punctuation-free; null when complete. */
  trigger: string | null
  kind: CompletenessKind | null
}

const COMPLETE: CompletenessVerdict = { complete: true, trigger: null, kind: null }

/**
 * Conjunctions that essentially never end an English utterance.
 *
 * Deliberate omissions, each because it IS commonly sentence-final: "yet" ("I
 * haven't done it yet"), "though" ("I like it though"), "then" ("see you then" -
 * but see AND_THEN below, where the two-word form is unambiguous).
 */
const CONJUNCTIONS = new Set([
  'and', 'or', 'but', 'nor', 'plus', 'so', 'because', 'cause', 'since', 'although',
  'unless', 'until', 'till', 'while', 'whilst', 'whereas', 'if', 'whether', 'versus', 'vs',
])

/**
 * Tokens that make an otherwise-dangling conjunction sentence-final.
 *
 * "I think so." and "it's been a while." are finished turns whose last word is on
 * the list above. This is the narrowest possible fix: two idioms, matched on the
 * preceding token, rather than loosening the rule for every use of the word.
 */
const CONJUNCTION_EXEMPTIONS: Record<string, string[]> = {
  so: ['think', 'thought', 'guess', 'hope', 'believe', 'suppose', 'said', 'say', 'says', 'seems', 'seem', 'sure', 'even', 'or'],
  while: ['a'],
}

/** "and then…" is the canonical thinking-aloud trail-off; bare "then" is not. */
const AND_THEN = ['and', 'then']

/**
 * Prepositions that are not also adverbs or verb particles. Outside a question
 * these do not end an utterance at any length.
 */
const STRONG_PREPOSITIONS = new Set([
  'of', 'to', 'for', 'with', 'into', 'onto', 'upon', 'toward', 'towards', 'within',
  'without', 'during', 'throughout', 'among', 'amongst', 'amid', 'between', 'than',
  'per', 'via', 'unto', 'atop', 'regarding', 'concerning', 'despite',
])

/**
 * Prepositions that double as adverbs or verb particles ("I'm in", "it's over",
 * "come on"), so they count only in an utterance long enough to be a clause.
 */
const WEAK_PREPOSITIONS = new Set([
  'in', 'on', 'at', 'from', 'by', 'about', 'over', 'under', 'through', 'across',
  'against', 'before', 'after', 'around', 'along', 'behind', 'below', 'above',
  'inside', 'outside', 'past', 'near', 'beside', 'besides', 'beyond', 'like',
])

/**
 * A weak preposition needs at least this many words before it reads as a dangler
 * rather than as an idiom. "I'm in." is four tokens short of a trailed-off clause.
 */
export const MIN_WEAK_PREPOSITION_WORDS = 5

/**
 * Articles and determiners. Nothing in English ends on one, which is why this
 * list needs no length or question guard - and why it is kept to the words that
 * are unambiguously determiners. "that", "this", "some", "any", "both", and
 * "another" are all commonly sentence-final pronouns and are absent on purpose.
 */
const ARTICLES = new Set(['a', 'an', 'the', 'my', 'your', 'our', 'their', 'its', 'every'])

/**
 * Openers that make a stranded trailing preposition ordinary rather than
 * unfinished. Compared with apostrophes removed, so "what's" and "isn't" match
 * without the set carrying every contraction twice.
 */
const QUESTION_OPENERS = new Set([
  'what', 'whats', 'when', 'whens', 'where', 'wheres', 'who', 'whos', 'whom', 'whose',
  'which', 'why', 'whys', 'how', 'hows',
  'is', 'isnt', 'are', 'arent', 'was', 'wasnt', 'were', 'werent', 'am',
  'do', 'dont', 'does', 'doesnt', 'did', 'didnt', 'can', 'cant', 'could', 'couldnt',
  'will', 'wont', 'would', 'wouldnt', 'should', 'shouldnt', 'shall', 'may', 'might',
  'have', 'havent', 'has', 'hasnt', 'had', 'hadnt',
])

/**
 * Split a transcript into comparable word tokens.
 *
 * Punctuation is dropped rather than trusted: Whisper punctuates fragments too,
 * writing "…and." or "…and…" for a trail-off, so a trailing period is no evidence
 * of completeness. A trailing question mark IS evidence, and is read from the raw
 * text before this runs.
 */
export function utteranceWords(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[’']/g, "'")
    .replace(/[^a-z0-9'\s-]+/g, ' ')
    .split(/\s+/)
    .map(word => word.replace(/^['-]+|['-]+$/g, ''))
    .filter(Boolean)
}

const interrogative = (text: string, words: string[]): boolean =>
  /\?\s*$/.test(text.trim()) || (words.length > 0 && QUESTION_OPENERS.has(words[0].replace(/'/g, '')))

/**
 * Judge one transcript. Complete unless it ends on a recognized dangler.
 *
 * Never throws and never needs context: an empty or whitespace transcript is
 * "complete" because there is nothing to hold, and the caller has nothing to
 * dispatch either.
 */
export function utteranceCompleteness(text: string): CompletenessVerdict {
  const words = utteranceWords(text)
  if (!words.length) return COMPLETE
  const last = words[words.length - 1]
  const previous = words.length > 1 ? words[words.length - 2] : ''

  if (words.length >= 2 && previous === AND_THEN[0] && last === AND_THEN[1]) {
    return { complete: false, trigger: 'and then', kind: 'conjunction' }
  }
  if (ARTICLES.has(last)) return { complete: false, trigger: last, kind: 'article' }
  if (CONJUNCTIONS.has(last) && !(CONJUNCTION_EXEMPTIONS[last] || []).includes(previous)) {
    return { complete: false, trigger: last, kind: 'conjunction' }
  }
  if (interrogative(text, words)) return COMPLETE
  if (STRONG_PREPOSITIONS.has(last)) return { complete: false, trigger: last, kind: 'preposition' }
  if (WEAK_PREPOSITIONS.has(last) && words.length >= MIN_WEAK_PREPOSITION_WORDS) {
    return { complete: false, trigger: last, kind: 'preposition' }
  }
  return COMPLETE
}

/** Bounds on the configured chat patience, matching the daemon's own clamp. */
export const MIN_CHAT_PATIENCE_MS = 0
export const MAX_CHAT_PATIENCE_MS = 5_000
export const DEFAULT_CHAT_PATIENCE_MS = 1_200

/** Bounds on the single extension an unfinished utterance earns. */
export const MIN_DEFERRAL_EXTENSION_MS = 600
export const MAX_DEFERRAL_EXTENSION_MS = 5_000

/**
 * Ceiling on the endpoint patience once the extension is applied, so the two
 * knobs together can never leave the microphone waiting on a dead room.
 */
export const MAX_EXTENDED_PATIENCE_MS = 10_000

const clamp = (value: number, low: number, high: number): number =>
  Math.max(low, Math.min(high, value))

/** The configured chat patience, clamped; the fallback matches the daemon default. */
export function chatPatienceMs(configured: number | null | undefined): number {
  return clamp(
    typeof configured === 'number' && Number.isFinite(configured) ? configured : DEFAULT_CHAT_PATIENCE_MS,
    MIN_CHAT_PATIENCE_MS,
    MAX_CHAT_PATIENCE_MS,
  )
}

/**
 * The one extension an unfinished utterance earns, derived from the operator's
 * own patience setting rather than from a second knob: an unfinished thought
 * waits its normal patience at the gate and then one more patience-length window
 * at the dispatch layer, so "how long before Mux answers" stays one number to
 * turn. Floored, because a patience of 0 must still buy a usable pause.
 */
export function deferralExtensionMs(patienceMs: number): number {
  return clamp(patienceMs, MIN_DEFERRAL_EXTENSION_MS, MAX_DEFERRAL_EXTENSION_MS)
}

/**
 * The trailing-silence patience the gate should use right now.
 *
 * `deferred` is the whole adaptive part: while a fragment is held, the
 * continuation gets a roomier tail so the second breath is not itself chopped in
 * half. It lasts only as long as the deferral, and a deferral resolves exactly
 * once, so the extension cannot compound.
 */
export function endpointPatienceMs(patienceMs: number, deferred: boolean): number {
  if (!deferred) return patienceMs
  return Math.min(MAX_EXTENDED_PATIENCE_MS, patienceMs + deferralExtensionMs(patienceMs))
}
