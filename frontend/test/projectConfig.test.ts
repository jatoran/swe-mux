import assert from 'node:assert/strict'
import test from 'node:test'
import {
  PANEL_CONFIG_FIELDS,
  conflictNotice,
  nextWorktreeTable,
  projectConfigBase,
  projectConfigDelta,
  revisionConflict,
  type ProjectConfigValues,
} from '../src/projectConfig.ts'

const saved = (values: Record<string, unknown>) => values as ProjectConfigValues

test('a write carries only the fields the operator changed', () => {
  const file = saved({
    preferred_backend: 'claude',
    automations: { raw_store: true },
    session_control_grant: 'draft',
  })
  // The whole reason for the delta: this draft names one field, so a stale copy of the
  // other two cannot revert them and cannot be reported as a conflict either.
  const changes = projectConfigDelta({ preferred_backend: 'codex' }, file)
  assert.deepEqual(changes, { preferred_backend: 'codex' })
  assert.deepEqual(projectConfigBase(changes, file), { preferred_backend: 'claude' })
})

test('a draft that matches the file is not a change', () => {
  const file = saved({ prompt_library_scope: 'project' })
  assert.deepEqual(projectConfigDelta({ prompt_library_scope: 'project' }, file), {})
  // Two edits back to where it started is also not a change, so Save stays disabled
  // rather than writing a no-op that bumps the file for every other reader.
  assert.deepEqual(projectConfigDelta({ prompt_library_scope: 'project' }, file), {})
})

test('clearing a field is sent as null, because undefined is not a JSON value', () => {
  const file = saved({ preferred_backend: 'claude' })
  assert.deepEqual(projectConfigDelta({ preferred_backend: undefined }, file), {
    preferred_backend: null,
  })
  assert.deepEqual(projectConfigBase({ preferred_backend: null }, saved({})), {
    preferred_backend: null,
  })
})

test('the ignore-pattern textarea is compared the way the file stores it', () => {
  const file = saved({})
  // Blank lines are how the textarea behaves while someone is typing; the file never
  // stores them, so an all-blank draft against an unset field is not an edit.
  assert.deepEqual(projectConfigDelta({ ignore_patterns: ['', '  '] }, file), {})
  assert.deepEqual(projectConfigDelta({ ignore_patterns: ['', ' .cache '] }, file), {
    ignore_patterns: ['.cache'],
  })
  // And emptying a list that had entries is a removal, not an empty list.
  assert.deepEqual(projectConfigDelta({ ignore_patterns: [''] }, saved({ ignore_patterns: ['x'] })), {
    ignore_patterns: null,
  })
})

test('editing the worktree setup command leaves the land queue’s verify command alone', () => {
  const table = { setup_command: 'uv sync', verify_command: './.worktree-verify' }
  assert.deepEqual(nextWorktreeTable(table, 'setup_command', 'npm ci'), {
    setup_command: 'npm ci',
    verify_command: './.worktree-verify',
  })
  // Clearing the setup command used to replace the whole table with `undefined`, which
  // silently deleted the approved verification command a landing runs.
  assert.deepEqual(nextWorktreeTable(table, 'setup_command', ''), {
    verify_command: './.worktree-verify',
  })
  // With nothing left the table goes away, rather than being written empty.
  assert.equal(nextWorktreeTable({ setup_command: 'uv sync' }, 'setup_command', ''), undefined)
  assert.equal(nextWorktreeTable(undefined, 'setup_command', ''), undefined)
})

test('"reset repo options" reaches only the fields that form draws', () => {
  // It once wrote an empty document. Every id below has a control somewhere else in the
  // same panel and belongs to a different owner; a reset that could clear them is the
  // difference between "inherit the defaults" and "revoke this Project's permissions".
  for (const field of [
    'automations',
    'scan_timeline_auto_enable',
    'session_control_grant',
    'spawn_grant',
    'land_grant',
    'interject_grant',
    'approval_allow',
    'approval_ceiling',
  ]) {
    assert.ok(
      !(PANEL_CONFIG_FIELDS as readonly string[]).includes(field),
      `${field} is not drawn by the repo options form and must not be reset by it`,
    )
  }
})

test('a field-scoped conflict is recognised, and nothing else is', () => {
  const conflict = revisionConflict({
    detail: {
      code: 'revision_conflict',
      conflicts: ['automations', 7],
      current: { revision: 'abc', values: {} },
    },
  })
  assert.deepEqual(conflict?.fields, ['automations'])
  assert.equal(conflict?.current?.revision, 'abc')
  assert.equal(revisionConflict({ detail: { code: 'automation_not_implemented' } }), null)
  assert.equal(revisionConflict(new Error('offline')), null)
  assert.equal(revisionConflict(null), null)
})

test('the conflict notice names what moved and does not say "reload"', () => {
  const notice = conflictNotice(['automations', 'approval_ceiling'])
  assert.match(notice, /the automation opt-ins and the approval ceiling/)
  // The panel has already resynced by the time this is shown, so telling the operator
  // to reload would be describing the old failure rather than the current state.
  assert.doesNotMatch(notice, /reload/i)
  // An unlabelled field still reads as a sentence rather than dropping out of it.
  assert.match(conflictNotice(['mystery_field']), /mystery_field/)
  assert.match(conflictNotice([]), /configuration/)
})
