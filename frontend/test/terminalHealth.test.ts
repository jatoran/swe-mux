import assert from 'node:assert/strict'
import test from 'node:test'
import {
  REMOUNT_ATTEMPT_LIMIT,
  remountDecision,
  surfaceDrifted,
  WRITE_PIPELINE_STALL_MS,
  writePipelineStalled,
} from '../src/terminalHealth.ts'

test('a live write pipeline keeps byte arrival and parse progress together', () => {
  // Parsed milliseconds after arrival, checked seconds later: healthy.
  assert.equal(writePipelineStalled(10_000, 10_005, 20_000), false)
  // Nothing ever arrived: nothing to judge.
  assert.equal(writePipelineStalled(null, null, 20_000), false)
  // Bytes landed this instant; the parser has not had a turn yet.
  assert.equal(writePipelineStalled(19_900, 10_000, 20_000), false)
})

test('bytes arriving without parse progress declare the pipeline dead', () => {
  // The DECRQM crash shape (measured 2026-08-06): the parser died early, output
  // kept arriving, and nothing ever rendered again.
  const parsedAt = 10_000
  const bytesAt = parsedAt + WRITE_PIPELINE_STALL_MS + 1
  assert.equal(writePipelineStalled(bytesAt, parsedAt, bytesAt + 1500), true)
  // A pane that never parsed anything at all counts from zero.
  assert.equal(writePipelineStalled(WRITE_PIPELINE_STALL_MS + 1, null, WRITE_PIPELINE_STALL_MS + 2000), true)
})

test('surface drift is only judged on a settled, visible pane', () => {
  const confirmed = { cols: 120, rows: 30, width: 800, height: 600 }
  const moved = { cols: 120, rows: 30, width: 640, height: 600 }
  assert.equal(surfaceDrifted(confirmed, moved, false, false), true)
  assert.equal(surfaceDrifted(confirmed, { ...confirmed }, false, false), false)
  // A replaying or hidden pane's surface is legitimately in flux.
  assert.equal(surfaceDrifted(confirmed, moved, true, false), false)
  assert.equal(surfaceDrifted(confirmed, moved, false, true), false)
  // No measurable host: nothing to compare.
  assert.equal(surfaceDrifted(confirmed, null, false, false), false)
})

test('remount attempts are budgeted so a poison replay cannot loop the pane', () => {
  const first = remountDecision([], 1_000)
  assert.equal(first.allow, true)
  const second = remountDecision(first.attempts, 2_000)
  assert.equal(second.allow, true)
  assert.equal(second.attempts.length, REMOUNT_ATTEMPT_LIMIT)
  const third = remountDecision(second.attempts, 3_000)
  assert.equal(third.allow, false)
  // Outside the window the budget resets: an old failure is not this failure.
  const resetAt = 2_000 + 6 * 60_000
  const later = remountDecision(second.attempts, resetAt)
  assert.equal(later.allow, true)
  assert.deepEqual(later.attempts, [resetAt])
})
