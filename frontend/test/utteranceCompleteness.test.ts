import assert from 'node:assert/strict'
import test from 'node:test'

import {
  chatPatienceMs, DEFAULT_CHAT_PATIENCE_MS, deferralExtensionMs, endpointPatienceMs,
  MAX_EXTENDED_PATIENCE_MS, MIN_DEFERRAL_EXTENSION_MS, MIN_WEAK_PREPOSITION_WORDS,
  utteranceCompleteness, utteranceWords,
} from '../src/utteranceCompleteness.ts'

const incomplete = (text: string, trigger: string, kind: string) => {
  const verdict = utteranceCompleteness(text)
  assert.deepEqual(verdict, { complete: false, trigger, kind }, `expected "${text}" to read as unfinished`)
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
  assert.equal(deferralExtensionMs(0), MIN_DEFERRAL_EXTENSION_MS)
  assert.equal(deferralExtensionMs(1_200), 1_200)
  assert.equal(deferralExtensionMs(99_000), 5_000)
})

test('the patience extension applies only while a fragment is held, and is capped', () => {
  assert.equal(endpointPatienceMs(1_200, false), 1_200)
  assert.equal(endpointPatienceMs(1_200, true), 2_400)
  assert.equal(endpointPatienceMs(0, false), 0)
  assert.equal(endpointPatienceMs(0, true), MIN_DEFERRAL_EXTENSION_MS)
  assert.ok(endpointPatienceMs(5_000, true) <= MAX_EXTENDED_PATIENCE_MS)
})
