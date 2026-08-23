import assert from 'node:assert/strict'
import test from 'node:test'
import {
  launchBody, launchState, opensChooser, type ConfiguratorOptions,
} from '../src/configurator.ts'

const options = (overrides: Partial<ConfiguratorOptions> = {}): ConfiguratorOptions => ({
  harnesses: ['claude'],
  default_harness: 'claude',
  configured_default: '',
  install_mode: 'source',
  source_checkout: 'D:/PROJECTS/swe-mux',
  projects: 2,
  ...overrides,
})

test('a ready install offers a press that names the harness it will use', () => {
  const state = launchState(options({ default_harness: 'codex', harnesses: ['claude', 'codex'] }))
  assert.equal(state.enabled, true)
  assert.equal(state.blocker, null)
  assert.equal(state.harness, 'codex')
  assert.match(state.reason, /codex/)
})

test('no agent CLI and no Project are different blockers with different sentences', () => {
  const noHarness = launchState(options({ harnesses: [], default_harness: null }))
  assert.equal(noHarness.enabled, false)
  assert.equal(noHarness.blocker, 'no-harness')
  assert.match(noHarness.reason, /Settings → Harnesses/)

  const noProject = launchState(options({ projects: 0 }))
  assert.equal(noProject.enabled, false)
  assert.equal(noProject.blocker, 'no-project')
  assert.match(noProject.reason, /Project/)
  // The two must never collapse into one message: one is a CLI to install, the
  // other is a step to take in the app, and telling the wrong one sends the
  // operator somewhere that cannot help.
  assert.notEqual(noHarness.reason, noProject.reason)
})

test('a harness list with no resolvable default is still blocked', () => {
  // The daemon resolves the default against live detection, so "some harnesses
  // are registered" and "one of them can actually be launched" are different
  // answers. Trusting the list alone would produce a press that always refuses.
  const state = launchState(options({ harnesses: ['claude'], default_harness: null }))
  assert.equal(state.enabled, false)
  assert.equal(state.blocker, 'no-harness')
})

test('an unanswered options request leaves the button pressable', () => {
  const state = launchState(null)
  assert.equal(state.enabled, true)
  assert.equal(state.blocker, null)
  assert.equal(state.harness, '')
})

test('the chooser opens only on a modifier press, and only with a real choice', () => {
  const many = options({ harnesses: ['claude', 'codex'] })
  assert.equal(opensChooser({ button: 0, shiftKey: false, altKey: false }, many), false)
  assert.equal(opensChooser({ button: 2, shiftKey: false, altKey: false }, many), true)
  assert.equal(opensChooser({ button: 0, shiftKey: true, altKey: false }, many), true)
  assert.equal(opensChooser({ button: 0, shiftKey: false, altKey: true }, many), true)
  // One candidate means the menu would offer exactly what a plain press does.
  assert.equal(opensChooser({ button: 2, shiftKey: false, altKey: false }, options()), false)
  assert.equal(opensChooser({ button: 2, shiftKey: false, altKey: false }, null), false)
})

test('an unnamed harness is omitted rather than sent empty', () => {
  // The daemon reads a present-but-empty `harness` as an explicit ask it cannot
  // satisfy; omitting the key is what asks it to resolve one.
  assert.deepEqual(launchBody('p1'), { project_id: 'p1' })
  assert.deepEqual(launchBody('p1', ''), { project_id: 'p1' })
  assert.deepEqual(launchBody('p1', 'codex'), { project_id: 'p1', harness: 'codex' })
})
