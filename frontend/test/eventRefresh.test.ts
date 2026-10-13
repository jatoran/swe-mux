import assert from 'node:assert/strict'
import test from 'node:test'
import { fleetEventAction, rowPatchOf } from '../src/eventRefresh.ts'

const frame = (type: unknown, session_id?: unknown, payload?: unknown) => ({ type, session_id, payload })

test('high-volume observation events do not refetch the whole fleet', () => {
  assert.equal(fleetEventAction(frame('tool_use', 's1')), 'none')
  assert.equal(fleetEventAction(frame('tool_result', 's1')), 'none')
  assert.equal(fleetEventAction(frame('PreToolUse', 's1')), 'none')
  assert.equal(fleetEventAction(frame('PostToolUse', 's1')), 'none')
  assert.equal(fleetEventAction(frame('project_files_changed')), 'none')
  assert.equal(fleetEventAction(frame('project_used')), 'none')
  // No fleet slice carries quota; the account views poll their own endpoint.
  assert.equal(fleetEventAction(frame('provider_quota_refreshed')), 'none')
  assert.equal(fleetEventAction(frame('terminal_paste_trace', 's1')), 'none')
})

test('the readiness stream patches one row instead of refetching the fleet', () => {
  // It exists precisely because a fleet refetch is the wrong response to it: the
  // daemon emits it at up to once a second, and the reasons it reports are the ones
  // whose own events (`terminal_input`, `terminal_mode_changed`) are excluded here
  // for exactly the same cost reason.
  assert.equal(fleetEventAction(frame('delivery_readiness_changed', 's1')), 'none')
  assert.equal(fleetEventAction(frame('terminal_input', 's1')), 'none')
  assert.equal(fleetEventAction(frame('terminal_mode_changed', 's1')), 'none')
})

test('an event carrying a row patch is applied to that row in place', () => {
  // The two most frequent refresh causes measured on 2026-10-12: the status line at
  // 205 an hour and a checkout's Git state at 53 an hour, each one row's handful of fields.
  const patch = { patch: { cost_usd: 2 }, session_revision: 7 }
  assert.equal(fleetEventAction(frame('harness_status', 's1', patch)), 'patch')
  assert.equal(fleetEventAction(frame('git_changed', 's1', { git: {}, ...patch })), 'patch')
  // An older daemon sends neither field: the row is re-read rather than guessed at.
  assert.equal(fleetEventAction(frame('harness_status', 's1', { effort: 'high' })), 'session')
  assert.equal(fleetEventAction(frame('git_changed', 's1', { git: {} })), 'session')
})

test('a row change the event does not describe re-reads that one row', () => {
  for (const type of ['turn_started', 'turn_ended', 'state_changed', 'subagent_activity', 'composer_input_changed']) {
    assert.equal(fleetEventAction(frame(type, 's1')), 'session', type)
  }
})

test('membership, previews, Project-level and unknown events retain the fleet refresh', () => {
  assert.equal(fleetEventAction(frame('session_spawned', 's1')), 'fleet')
  assert.equal(fleetEventAction(frame('session_exited', 's1')), 'fleet')
  assert.equal(fleetEventAction(frame('session_removed', 's1')), 'fleet')
  assert.equal(fleetEventAction(frame('listener_detected', 's1')), 'fleet')
  assert.equal(fleetEventAction(frame('listener_closed', 's1')), 'fleet')
  // A Git drawer action names a Project, not a session.
  assert.equal(fleetEventAction(frame('git_changed', undefined, { project_id: 'p1' })), 'fleet')
  // A row event that names no session cannot be routed to a row.
  assert.equal(fleetEventAction(frame('turn_started')), 'fleet')
  assert.equal(fleetEventAction(frame('turn_ended', '')), 'fleet')
  assert.equal(fleetEventAction(frame('future_state_change', 's1')), 'fleet')
  assert.equal(fleetEventAction(frame(null)), 'fleet')
  assert.equal(fleetEventAction({}), 'fleet')
})

test('a row patch is only read when it is complete enough to order', () => {
  assert.deepEqual(rowPatchOf({ patch: { git: { branch: 'main' } }, session_revision: 3 }), {
    fields: { git: { branch: 'main' } }, revision: 3,
  })
  assert.equal(rowPatchOf(null), null)
  assert.equal(rowPatchOf({ patch: { git: {} } }), null)
  assert.equal(rowPatchOf({ patch: { git: {} }, session_revision: '3' }), null)
  assert.equal(rowPatchOf({ patch: { git: {} }, session_revision: -1 }), null)
  assert.equal(rowPatchOf({ patch: { git: {} }, session_revision: 1.5 }), null)
  assert.equal(rowPatchOf({ patch: ['git'], session_revision: 3 }), null)
  assert.equal(rowPatchOf({ patch: null, session_revision: 3 }), null)
})
