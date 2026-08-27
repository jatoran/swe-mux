import assert from 'node:assert/strict'
import test from 'node:test'
import { eventRequiresFleetRefresh } from '../src/eventRefresh.ts'

test('high-volume observation events do not refetch the whole fleet', () => {
  assert.equal(eventRequiresFleetRefresh('tool_use'), false)
  assert.equal(eventRequiresFleetRefresh('tool_result'), false)
  assert.equal(eventRequiresFleetRefresh('PreToolUse'), false)
  assert.equal(eventRequiresFleetRefresh('PostToolUse'), false)
  assert.equal(eventRequiresFleetRefresh('project_files_changed'), false)
  assert.equal(eventRequiresFleetRefresh('project_used'), false)
})

test('the readiness stream patches one row instead of refetching the fleet', () => {
  // It exists precisely because a fleet refetch is the wrong response to it: the
  // daemon emits it at up to once a second, and the reasons it reports are the ones
  // whose own events (`terminal_input`, `terminal_mode_changed`) are excluded here
  // for exactly the same cost reason.
  assert.equal(eventRequiresFleetRefresh('delivery_readiness_changed'), false)
  assert.equal(eventRequiresFleetRefresh('terminal_input'), false)
  assert.equal(eventRequiresFleetRefresh('terminal_mode_changed'), false)
})

test('state-changing and unknown events retain the refresh safety net', () => {
  assert.equal(eventRequiresFleetRefresh('turn_started'), true)
  assert.equal(eventRequiresFleetRefresh('turn_ended'), true)
  assert.equal(eventRequiresFleetRefresh('git_changed'), true)
  assert.equal(eventRequiresFleetRefresh('future_state_change'), true)
  assert.equal(eventRequiresFleetRefresh(null), true)
})
