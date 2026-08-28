import assert from 'node:assert/strict'
import test from 'node:test'
import {
  lastCheckedLabel, shouldShowUpdateBanner, updateBannerText, updateStatusSummary,
  UPDATE_CHECK_GESTURE, type UpdateStatus,
} from '../src/updateCheck.ts'

/**
 * The browser half of the release update check.
 *
 * The comparison itself is the daemon's and is tested in `tests/test_update_check.py`;
 * what is pinned here is the narrow thing the browser decides - whether there is
 * anything to draw. Every clause of that guard is a state a real daemon returns:
 * an install that has never checked, one that could not reach the site, one whose
 * operator declined the version, and an older daemon that has no such endpoint at
 * all and answers without a `banner` field.
 */

const status = (extra: Partial<UpdateStatus> = {}): UpdateStatus => ({
  enabled: true,
  status: 'ok',
  current_version: '0.1.0',
  update_available: true,
  latest: {
    version: '0.2.0',
    tag: 'v0.2.0',
    published: '2026-08-27T00:00:00Z',
    changelog: 'https://example.invalid/releases/tag/v0.2.0',
    source: 'manifest',
  },
  banner: true,
  ...extra,
})

test('the banner follows the daemon verdict rather than re-deriving it', () => {
  // Two comparisons would eventually disagree, and the phone and the desktop
  // would then differ about whether an update exists. `banner` is the one answer.
  assert.equal(shouldShowUpdateBanner(status()), true)
  assert.equal(shouldShowUpdateBanner(status({ banner: false })), false)
  // `update_available` alone is not the trigger: a declined version keeps this
  // true and must still draw nothing.
  assert.equal(
    shouldShowUpdateBanner(status({ banner: false, update_available: true })),
    false,
  )
})

test('nothing is drawn without a payload to draw it from', () => {
  // A fetch that failed or has not returned yet.
  assert.equal(shouldShowUpdateBanner(null), false)
  // An older daemon with no update endpoint: the field is simply absent, and a
  // truthiness check on it would be the same bug as trusting it.
  assert.equal(shouldShowUpdateBanner({ update_available: true } as unknown as UpdateStatus), false)
  // A verdict with nothing to name or link to is not a banner.
  assert.equal(shouldShowUpdateBanner(status({ latest: null })), false)
  assert.equal(
    shouldShowUpdateBanner(status({ latest: { ...status().latest!, version: '' } })),
    false,
  )
})

test('the banner names both versions and claims nothing else', () => {
  // Deliberately no severity word: the manifest carries none, so "critical" or
  // "recommended" here would be the banner inventing a fact.
  const text = updateBannerText(status())
  assert.match(text, /0\.2\.0/)
  assert.match(text, /0\.1\.0/)
  assert.doesNotMatch(text, /critical|security|urgent|recommended/i)
  // A daemon that did not report its own version still gets a usable sentence.
  assert.equal(
    updateBannerText(status({ current_version: undefined })),
    'swe-mux 0.2.0 is available.',
  )
})

test('every check outcome has its own sentence, and none of them read as an error', () => {
  // The point of keeping the daemon's word rather than a boolean: "we have not
  // looked yet" and "we looked and could not tell" are different facts, and a
  // single "update status unknown" would collapse them.
  const summary = (state: string, extra: Partial<UpdateStatus> = {}) =>
    updateStatusSummary(status({ status: state, ...extra }))
  assert.equal(summary('never_checked'), 'Not checked yet.')
  assert.equal(summary('ok', { update_available: false }), 'This is the latest release.')
  assert.match(String(summary('ok')), /0\.2\.0/)
  assert.match(String(summary('unreachable')), /could not reach/)
  assert.match(String(summary('malformed')), /did not answer/)
  assert.match(String(summary('unsupported_schema')), /newer format/)
  assert.match(String(summary('incomparable')), /could not compare/)
  assert.match(String(summary('disabled')), /Nothing is requested/)
  // Two silences, both deliberate: no payload, and a daemon that has no checker.
  assert.equal(updateStatusSummary(null), null)
  assert.equal(summary('unavailable'), null)
  assert.equal(summary('something-a-later-build-invented'), null)
})

test('a last-checked line appears only when there is a real timestamp', () => {
  assert.equal(lastCheckedLabel(null), null)
  assert.equal(lastCheckedLabel(status({ checked_at: null })), null)
  assert.equal(lastCheckedLabel(status({ checked_at: undefined })), null)
  // Not a number is not a time; rendering `Invalid Date` under a control is worse
  // than rendering no line.
  assert.equal(lastCheckedLabel(status({ checked_at: Number.NaN })), null)
  assert.equal(
    lastCheckedLabel(status({ checked_at: 1_756_000_000 })),
    new Date(1_756_000_000_000).toLocaleString(),
  )
})

test('the gesture header the daemon requires is spelled the same on both sides', () => {
  // The daemon refuses `POST /api/update/check` without exactly this value, and a
  // drift here would turn the button into a silent 400.
  assert.equal(UPDATE_CHECK_GESTURE, 'update-check')
})
