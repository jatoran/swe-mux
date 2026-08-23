import assert from 'node:assert/strict'
import test from 'node:test'

import {
  chatPatienceMs, COMPLETION, DEFAULT_CHAT_PATIENCE_MS, DEFERRAL_COMPLETION_THRESHOLD,
  DEFERRAL_FACTOR_MAX, DEFERRAL_FACTOR_MIN, deferralExtensionMs, deferralFactor,
  endpointPatienceMs, MAX_DEFERRAL_EXTENSION_MS,
  MAX_EXTENDED_PATIENCE_MS, MIN_DEFERRAL_EXTENSION_MS, MIN_WEAK_PREPOSITION_WORDS,
  utteranceCompleteness, utteranceWords,
} from '../src/utteranceCompleteness.ts'

const incomplete = (text: string, trigger: string, kind: string) => {
  const verdict = utteranceCompleteness(text)
  assert.deepEqual(
    { complete: verdict.complete, trigger: verdict.trigger, kind: verdict.kind },
    { complete: false, trigger, kind },
    `expected "${text}" to read as unfinished`,
  )
  // Every rule that fires must claim it, or the extension curve sizes a window
  // for a hold the threshold would not have granted in the first place.
  assert.ok(
    verdict.completion < DEFERRAL_COMPLETION_THRESHOLD,
    `"${text}" deferred on "${trigger}" but scored ${verdict.completion}, at or above the threshold`,
  )
}
const complete = (text: string) => {
  const verdict = utteranceCompleteness(text)
  assert.equal(verdict.complete, true, `expected "${text}" to read as finished, got trigger "${verdict.trigger}"`)
  assert.equal(verdict.trigger, null)
  assert.equal(verdict.kind, null)
}

test('a dangling conjunction is the canonical trail-off', () => {
  incomplete('open the scan timeline and', 'and', 'conjunction')
  incomplete('I want to check the queue but', 'but', 'conjunction')
  incomplete('spawn a session in swe-mux because', 'because', 'conjunction')
  incomplete('hold the deploy until', 'until', 'conjunction')
})

test('"and then" is caught as a phrase where bare "then" is not', () => {
  incomplete('run the tests and then', 'and then', 'conjunction')
  // "see you then" and "back then" end sentences constantly, so bare "then" is
  // deliberately not a trigger: the two-word form is the unambiguous one.
  complete('it was working back then')
})

test('a dangling article or determiner never needs a length or question guard', () => {
  incomplete('put that note in the', 'the', 'article')
  incomplete('a', 'a', 'article')
  incomplete('what is the status of my', 'my', 'article')
  // Determiners that double as pronouns are absent from the list on purpose.
  complete('I did not say that')
  complete('I already have some')
  complete('give me another')
})

test('strong prepositions dangle at any length; weak ones need a clause', () => {
  incomplete('send it to', 'to', 'preposition')
  incomplete('a summary of', 'of', 'preposition')
  incomplete('compare it with', 'with', 'preposition')
  // Weak prepositions are also verb particles, so a short utterance ending on
  // one is an idiom rather than a trail-off.
  complete("I'm in")
  complete('come on')
  complete("it's over")
  const long = 'let me know what the reviewer actually said about'
  assert.ok(utteranceWords(long).length >= MIN_WEAK_PREPOSITION_WORDS)
  incomplete(long, 'about', 'preposition')
})

test('a question strands prepositions legitimately and is left alone', () => {
  complete('what is this for')
  complete('who should I send the diff to?')
  complete('where did that transcript come from')
  complete("what's the queue backed up behind")
  // The question guard covers prepositions only: nothing ends on "the".
  incomplete('what is the status of the', 'the', 'article')
})

test('the two conjunction idioms that really do end sentences are exempt', () => {
  complete('I think so')
  complete('yeah I guess so')
  complete("it's been a while")
  // The exemption is keyed on the preceding word, so the ordinary dangler stands.
  incomplete('the merge was clean so', 'so', 'conjunction')
  incomplete('keep it running while', 'while', 'conjunction')
})

test('trailing punctuation is stripped, because Whisper punctuates fragments too', () => {
  incomplete('open the drawer and.', 'and', 'conjunction')
  incomplete('open the drawer and…', 'and', 'conjunction')
  incomplete('open the drawer, and', 'and', 'conjunction')
})

test('an empty transcript is complete, because there is nothing to hold', () => {
  complete('')
  complete('   ')
  complete('\n\t')
})

test('ordinary finished requests are not held', () => {
  complete('open the terminal for the alpha session')
  complete('how many sessions are working right now')
  complete('spawn a claude session in swe-mux and run the tests')
  complete('stop')
  complete('yes')
})

test('words are tokenized on letters, digits, apostrophes and hyphens', () => {
  assert.deepEqual(utteranceWords("Mux, open swe-mux's queue!"), ['mux', 'open', "swe-mux's", 'queue'])
  // Leading and trailing apostrophes and hyphens are trimmed, so a quoted word
  // and an em-dash break do not become tokens the rule set cannot match.
  assert.deepEqual(utteranceWords("- 'and' -"), ['and'])
  assert.deepEqual(utteranceWords('one   two\nthree'), ['one', 'two', 'three'])
})

test('the extension is the operator\'s own patience, floored and capped', () => {
  assert.equal(chatPatienceMs(undefined), DEFAULT_CHAT_PATIENCE_MS)
  assert.equal(chatPatienceMs(null), DEFAULT_CHAT_PATIENCE_MS)
  assert.equal(chatPatienceMs(Number.NaN), DEFAULT_CHAT_PATIENCE_MS)
  assert.equal(chatPatienceMs(-500), 0)
  assert.equal(chatPatienceMs(99_000), 5_000)
  // A patience of 0 still buys a usable pause: the floor is what makes the
  // deferral a real extension rather than a no-op re-dispatch.
  assert.equal(deferralExtensionMs(0, COMPLETION.article), MIN_DEFERRAL_EXTENSION_MS)
  assert.equal(deferralExtensionMs(99_000, COMPLETION.article), MAX_DEFERRAL_EXTENSION_MS)
})

test('a weaker rule buys a shorter window than a stronger one', () => {
  // The whole point of the score. Before this, a rule that is right about a
  // third of the time cost the operator exactly as much silence as one that is
  // right essentially always.
  const patience = DEFAULT_CHAT_PATIENCE_MS
  const article = deferralExtensionMs(patience, COMPLETION.article)
  const conjunction = deferralExtensionMs(patience, COMPLETION.conjunction)
  const weak = deferralExtensionMs(patience, COMPLETION.weakPreposition)
  assert.ok(article > conjunction, `article ${article} should outwait conjunction ${conjunction}`)
  assert.ok(conjunction > weak, `conjunction ${conjunction} should outwait weak preposition ${weak}`)
  // And the spread is worth having, not a rounding difference.
  assert.ok(article >= weak * 1.5, `spread too narrow: ${weak} to ${article}`)
})

test('the factor curve spans the deferral region and stops at the threshold', () => {
  assert.equal(deferralFactor(0), DEFERRAL_FACTOR_MAX)
  assert.equal(deferralFactor(DEFERRAL_COMPLETION_THRESHOLD), 0)
  assert.equal(deferralFactor(1), 0)
  // Monotonic: more confidence that the turn is finished never buys more silence.
  let previous = Infinity
  for (let score = 0; score < DEFERRAL_COMPLETION_THRESHOLD; score += 0.02) {
    const factor = deferralFactor(score)
    assert.ok(factor <= previous, `factor rose at ${score}`)
    assert.ok(factor >= DEFERRAL_FACTOR_MIN, `factor fell below the floor at ${score}`)
    previous = factor
  }
  // Garbage in is a dispatch, never an unbounded wait.
  assert.equal(deferralFactor(Number.NaN), 0)
  assert.equal(deferralFactor(-5), DEFERRAL_FACTOR_MAX)
  assert.equal(deferralExtensionMs(1_200, 0.9), 0)
})

test('the patience extension applies only while a fragment is held, and is capped', () => {
  assert.equal(endpointPatienceMs(1_200, null), 1_200)
  assert.equal(endpointPatienceMs(1_200, 0), 1_200)
  assert.equal(endpointPatienceMs(1_200, 1_200), 2_400)
  assert.equal(endpointPatienceMs(0, null), 0)
  assert.equal(endpointPatienceMs(0, MIN_DEFERRAL_EXTENSION_MS), MIN_DEFERRAL_EXTENSION_MS)
  assert.ok(endpointPatienceMs(5_000, MAX_DEFERRAL_EXTENSION_MS) <= MAX_EXTENDED_PATIENCE_MS)
})
