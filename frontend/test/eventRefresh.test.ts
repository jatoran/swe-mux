import assert from 'node:assert/strict'
import test from 'node:test'
import { eventRequiresFleetRefresh } from '../src/eventRefresh.ts'

test('high-volume observation events do not refetch the whole fleet', () => {
  assert.equal(eventRequiresFleetRefresh('tool_use'), false)
  assert.equal(eventRequiresFleetRefresh('tool_result'), false)
  assert.equal(eventRequiresFleetRefresh('PreToolUse'), false)
  assert.equal(eventRequiresFleetRefresh('PostToolUse'), false)
  assert.equal(eventRequiresFleetRefresh('project_files_changed'), false)
})

test('state-changing and unknown events retain the refresh safety net', () => {
  assert.equal(eventRequiresFleetRefresh('turn_started'), true)
  assert.equal(eventRequiresFleetRefresh('turn_ended'), true)
  assert.equal(eventRequiresFleetRefresh('git_changed'), true)
  assert.equal(eventRequiresFleetRefresh('future_state_change'), true)
  assert.equal(eventRequiresFleetRefresh(null), true)
})
