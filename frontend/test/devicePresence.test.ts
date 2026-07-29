import assert from 'node:assert/strict'
import test from 'node:test'
import { PRESENCE_MIN_REPORT_MS, presenceFrame, shouldReportInteraction } from '../src/devicePresence.ts'

test('interaction is reported as an age, never a timestamp', () => {
  // A phone whose clock is minutes off would otherwise look permanently active
  // to the daemon, and permanently silence its own notifications.
  const frame = presenceFrame({
    profile: 'desktop', visible: true, focused: true,
    now: 1_000_000, lastInteractionAt: 1_000_000 - 4_500,
  })
  assert.deepEqual(frame, {
    type: 'presence', profile: 'desktop', visible: true, focused: true, interaction_age: 4.5,
  })
})

test('a device that has never been touched says so', () => {
  const frame = presenceFrame({
    profile: 'mobile', visible: true, focused: false, now: 5_000, lastInteractionAt: null,
  })
  assert.equal(frame.interaction_age, null)
  assert.equal(frame.focused, false)
})

test('a clock that jumped backwards cannot report a negative age', () => {
  const frame = presenceFrame({
    profile: 'desktop', visible: true, focused: true, now: 1_000, lastInteractionAt: 2_000,
  })
  assert.equal(frame.interaction_age, 0)
})

test('the first interaction after a quiet stretch reports immediately', () => {
  // The edge is what matters: sitting down at a quiet desktop must reach the daemon
  // now, or a notification meant for the phone fires while the user is right there.
  assert.equal(shouldReportInteraction(null, 10_000), true)
  assert.equal(shouldReportInteraction(10_000, 10_000 + PRESENCE_MIN_REPORT_MS), true)
})

test('continuous typing does not report on every key', () => {
  assert.equal(shouldReportInteraction(10_000, 10_500), false)
  assert.equal(shouldReportInteraction(10_000, 10_000 + PRESENCE_MIN_REPORT_MS - 1), false)
})
