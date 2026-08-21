import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import {
  GRANTS,
  automationClosure,
  automationsSpend,
  grantKey,
  grantWrittenTo,
  isGrantable,
  type GrantId,
} from '../src/grants.ts'
import { settingTarget, type SettingTargetId } from '../src/settingTargets.ts'

const root = join(import.meta.dirname, '..')
const source = (name: string) => readFileSync(join(root, 'src', name), 'utf8')
const daemon = (name: string) => readFileSync(join(root, '..', 'src', 'swe_mux', name), 'utf8')

const grantsModule = daemon('grants.py')
const registry = daemon('automation_registry.py')
const ids = Object.keys(GRANTS) as GrantId[]

/** The daemon's allowlist, read out of its source so the two ends cannot drift. */
function daemonInstallKeys(): string[] {
  const block = grantsModule.split('GRANTABLE_INSTALL_KEYS')[1].split(')')[0]
  return [...block.matchAll(/"([a-z_]+)"/g)].map(match => match[1])
}

function daemonProjectValues(): Map<string, string[]> {
  const block = grantsModule.split('GRANTABLE_PROJECT_VALUES: Mapping')[1].split('\n}')[0]
  const rows = new Map<string, string[]>()
  for (const line of block.split('\n')) {
    const match = /"([a-z_]+)":\s*\(([^)]*)\)/.exec(line)
    if (!match) continue
    rows.set(match[1], [...match[2].matchAll(/"([^"]+)"|(True|False)/g)]
      .map(item => item[1] ?? item[2]))
  }
  return rows
}

// The whole safety argument for a write reachable from a drawer pane rests on the
// allowlist being closed. A gate offering a switch the daemon will refuse is the
// stranded-link failure this feature exists to remove, moved one layer down — so the
// browser's catalogue is held against the daemon's rather than trusted to match.
test('every install grant is one the daemon will accept', () => {
  const allowed = new Set(daemonInstallKeys())
  assert.ok(allowed.size >= 10, 'expected to have parsed the daemon allowlist')
  for (const id of ids) {
    if (GRANTS[id].scope !== 'install') continue
    assert.ok(allowed.has(grantKey(id)), `${id} grants ${grantKey(id)}, which the daemon refuses`)
  }
})

test('every Project value grant sets a value the daemon allows', () => {
  const allowed = daemonProjectValues()
  assert.ok(allowed.size >= 5, 'expected to have parsed the daemon Project allowlist')
  for (const id of ids) {
    const grant = GRANTS[id]
    if (grant.scope !== 'project' || grant.kind !== 'value') continue
    const values = allowed.get(grantKey(id))
    assert.ok(values !== undefined, `${id} sets ${grantKey(id)}, which is not grantable`)
    // `True` in the Python tuple is the same value as `true` here; everything else is a
    // string literal on both sides.
    const rendered = grant.value === true ? 'True' : String(grant.value)
    assert.ok(values!.includes(rendered), `${id} sets ${grantKey(id)} to a value the daemon refuses`)
  }
})

test('every automation grant names an automation the registry defines', () => {
  for (const id of ids) {
    const grant = GRANTS[id]
    if (grant.scope !== 'project' || grant.kind !== 'automation') continue
    assert.ok(
      new RegExp(`Automation\\(\\s*"${grantKey(id)}"`).test(registry),
      `${id} grants ${grantKey(id)}, which the registry does not define`,
    )
  }
})

// A grant id *is* a setting target id, which is what lets one gate render both the
// button and the owner's link. A grant pointing at a target with no control would render
// a button with nowhere to fall back to.
test('every grant is a setting target that names a control', () => {
  for (const id of ids) {
    const target = settingTarget(id as SettingTargetId)
    assert.ok(target, `${id} is not a setting target`)
    assert.ok('setting' in target && target.setting, `${id} points at an area, not a switch`)
  }
  assert.ok(isGrantable('project.codeGraph'))
  assert.ok(!isGrantable('project.automations'), 'an area must not be grantable')
  assert.ok(!isGrantable('terminals.claudeWidth'), 'a value setting must not be grantable')
})

// Scope is the first thing read on a gate and the thing most costly to get wrong: a
// Project opt-in is committed repository content and reaches every clone.
test('a Project grant says its change travels with the checkout', () => {
  assert.match(grantWrittenTo('project.codeGraph'), /\.swe-mux\/config\.toml/)
  assert.match(grantWrittenTo('project.codeGraph'), /travels with the checkout/)
  assert.match(grantWrittenTo('automation.scanTimeline'), /machine/)
  assert.match(grantWrittenTo('alerts.master'), /device/)
})

// The closure is what the button promises. Naming only the requested id while writing
// three would be misleading even though nothing went wrong.
test('the closure names every automation a grant would switch on', () => {
  const registryPayload = [
    { id: 'raw_store', kind: 'substrate', label: 'Raw transcript store', requires: [], implemented: true, spends: false },
    { id: 'tier0', kind: 'substrate', label: 'Deterministic fact capture', requires: ['raw_store'], implemented: true, spends: false },
    { id: 'scan_timeline', kind: 'substrate', label: 'Scan timeline', requires: ['tier0', 'raw_store'], implemented: true, spends: true },
    { id: 'code_graph', kind: 'consumer', label: 'Code-structure graph', requires: ['tier0'], implemented: true, spends: false },
    { id: 'catch_me_up', kind: 'consumer', label: 'Catch-me-up digest', requires: ['scan_timeline'], implemented: true, spends: false },
  ]
  assert.deepEqual(automationClosure(['code_graph'], registryPayload), ['code_graph', 'raw_store', 'tier0'])
  assert.equal(automationsSpend(['code_graph'], registryPayload), false)
  // The point of asking the closure rather than the named id: catch-me-up costs nothing
  // and cannot be switched on without the timeline, which does.
  assert.equal(automationsSpend(['catch_me_up'], registryPayload), true)
})

test('the closure walk terminates on an unknown id rather than looping', () => {
  assert.deepEqual(automationClosure(['nope'], []), ['nope'])
})

// The Findings gate offers exactly the four detectors the pane reads. Adding a fifth
// detector to one list and not the other would leave the gate quietly incomplete.
test('the findings gate offers every detector the findings pane reads', () => {
  const pane = source('FindingsPane.tsx')
  const automations = /DETECTOR_AUTOMATIONS = \[([^\]]+)\]/.exec(pane)?.[1] || ''
  const grants = /DETECTOR_GRANTS: GrantId\[\] = \[([^\]]+)\]/.exec(pane)?.[1] || ''
  const named = [...automations.matchAll(/'([a-z_]+)'/g)].map(match => match[1]).sort()
  const granted = [...grants.matchAll(/'(project\.[A-Za-z]+)'/g)]
    .map(match => grantKey(match[1] as GrantId)).sort()
  assert.ok(named.length === 4, 'expected four detectors')
  assert.deepEqual(granted, named)
})

// A gate turns things on and can never turn anything off. That is what lets a write live
// inside a drawer pane while "one owner per switch" stays true: many granters, one place
// that can withdraw. A `false` or a `draft` reaching the applier would break it.
test('the browser never asks the daemon to withdraw a permission', () => {
  const module = source('grants.ts')
  assert.ok(module.includes('install[grantKey(id)] = true'), 'install grants must be additive')
  for (const id of ids) {
    const grant = GRANTS[id]
    if (grant.scope !== 'project' || grant.kind !== 'value') continue
    assert.notEqual(grant.value, false, `${id} would withdraw rather than grant`)
    assert.notEqual(grant.value, 'draft', `${id} would withdraw rather than grant`)
    assert.notEqual(grant.value, 'off', `${id} would withdraw rather than grant`)
  }
})

// One daemon call, whatever the mix of scopes. Sequencing an install write and a Project
// write from the browser would mean two revisions, two failure modes, and a half-granted
// state whenever the second lost — which is the thing a gate exists to spare someone.
test('a mixed-scope grant is one request', () => {
  const module = source('grants.ts')
  const calls = [...module.matchAll(/api<[^>]*>\('POST'/g)]
  assert.equal(calls.length, 1, 'applyGrants must make exactly one daemon call')
  assert.ok(module.includes("'/api/grants'"))
})

// The one device grant. It never reaches the daemon, so the gate has to be handed the
// local write; a device grant with no `applyDevice` would silently do nothing.
test('the device grant is wired to a local write', () => {
  const device = ids.filter(id => GRANTS[id].scope === 'device')
  assert.deepEqual(device, ['alerts.master'])
  const notifications = source('Notifications.tsx')
  assert.ok(notifications.includes('applyDevice={unmuteAlerts}'))
  assert.ok(notifications.includes('setAlertPreferencesFor'))
})
