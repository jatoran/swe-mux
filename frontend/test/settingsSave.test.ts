import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { ApiError } from '../src/api.ts'
import { committedSections, saveFailureStatus } from '../src/settingsSave.ts'

/**
 * What a failed Settings save is allowed to say about the daemon's disk.
 *
 * The old panel said "invalid · nothing was changed" for every failure of a two-request
 * save, including the one where the sibling request had already committed. The status is
 * now derived from the `committed` array the daemon puts on every answer, so each of the
 * three genuinely different outcomes reads differently.
 */

/** Source with `//` comment lines dropped — the comments here describe the shape that was
 *  removed, so a naive substring search finds the very thing it is asserting is gone. */
const code = (source: string) =>
  source.split('\n').filter(line => !line.trim().startsWith('//')).join('\n')

const apiError = (message: string, status?: number, detail?: Record<string, unknown>): ApiError => {
  const error = new Error(message) as ApiError
  if (status !== undefined) error.status = status
  if (detail) error.detail = detail
  return error
}

test('a rejected save that committed nothing may say so', () => {
  const status = saveFailureStatus(apiError('invalid configuration', 422, { committed: [] }))
  assert.equal(status, 'invalid · nothing was changed')
})

test('a revision conflict from another device committed nothing', () => {
  // The exact case the old message was wrong about: the config PATCH 409d while the
  // sibling keybindings PUT had already rewritten the file. One request cannot do that,
  // and the daemon says `committed: []` to prove it.
  const status = saveFailureStatus(
    apiError('configuration changed externally', 409, { revision: 12, committed: [] }),
  )
  assert.equal(status, 'invalid · nothing was changed')
})

test('a half-commit names the half that landed instead of denying both', () => {
  const status = saveFailureStatus(apiError(
    'settings saved, but the shortcuts could not be written: locked',
    500,
    { committed: ['config'], failed: ['keybindings'] },
  ))
  assert.match(status, /^partly saved · settings committed · shortcuts did not · /)
  assert.doesNotMatch(status, /nothing was changed/)
})

test('an unanswered request does not claim anything about the disk', () => {
  // Offline, timed out, aborted: no status, so no body, so no `committed` to read. The
  // reassuring answer here would be a guess, and it is the guess that was the bug.
  const status = saveFailureStatus(apiError('The daemon did not respond in time.'))
  assert.match(status, /did not answer/)
  assert.doesNotMatch(status, /nothing was changed/)
})

test('a committed list is only trusted when it is a list of strings', () => {
  assert.deepEqual(committedSections(apiError('x', 500, { committed: 'config' })), [])
  assert.deepEqual(committedSections(apiError('x', 500, { committed: ['config', 7] })), ['config'])
  assert.deepEqual(committedSections(apiError('x', 500)), [])
})

test('Settings sends one request for the whole save', () => {
  // The two-request shape is what made a half-commit possible at all, so its absence is
  // the invariant rather than an implementation detail. Source-level because the panel is
  // far too large to mount here; the renderer suite covers the behaviour.
  const panel = readFileSync(join(import.meta.dirname, '..', 'src', 'Settings.tsx'), 'utf8')
  const save = code(panel.slice(panel.indexOf('const save = async'), panel.indexOf('const reset = async')))
  // The host descriptor rides the query since 2026-08-30, so the daemon resolves the
  // keymap it hands back for the keyboard that is actually saving.
  assert.ok(save.includes("api<SettingsApplyResult>('POST',`/api/settings/apply?${hostQuery()}`"))
  assert.doesNotMatch(save, /Promise\.all/)
  assert.doesNotMatch(save, /\/api\/keybindings/)
  assert.doesNotMatch(save, /'PATCH','\/api\/config'/)
})

test('Restore defaults is confirmed and its failure is visible', () => {
  const panel = readFileSync(join(import.meta.dirname, '..', 'src', 'Settings.tsx'), 'utf8')
  // The button raises the decision; it does not call the endpoint.
  assert.ok(panel.includes('onClick={()=>setResetIntent(true)}>Restore defaults<'))
  const reset = panel.slice(panel.indexOf('const reset = async'), panel.indexOf('const exportConfig'))
  assert.ok(reset.includes("api<Config>('POST','/api/config/reset'"))
  // A rejected POST used to be an unhandled rejection with no visible trace.
  assert.ok(reset.includes('catch'))
  assert.ok(reset.includes('restore failed'))
})
