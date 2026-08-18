import assert from 'node:assert/strict'
import test from 'node:test'
import {
  defaultRailConfig, isProjectRailDelta, railConfigFromBlob, railProjectScopeKind,
  resolveDeltaScope, writeRailConfigBlob,
  type RailBlob, type RailItem, type RailProjectDelta,
} from '../src/commandRail.ts'
import { removeRailEntry, moveRailEntry, railPlacementCounts } from '../src/railLayout.ts'
import {
  addProjectRailRow, addScopedRailItem, applyScopedRail, detachProjectRail,
  pinPrompt, pinSkill, pinnedPromptItem, pinnedSkillItem, removeScopedRailItem,
  resolveRail, toggleScopedPlacement,
} from '../src/railScope.ts'

const P = 'proj-a'
const OTHER = 'proj-b'

const skillItem = (name: string): RailItem =>
  ({ id: `custom:skill:${name}`, type: 'skill', label: name, text: name, defaultSurface: 'panel' })

/** A blob whose project P carries one project-scoped action, via the public op. */
const deltaBlob = (): RailBlob =>
  addScopedRailItem(undefined, P, skillItem('ship'), 'project')

// --- resolution --------------------------------------------------------------

test('scope kinds: none, delta, and fork are told apart by shape', () => {
  assert.equal(railProjectScopeKind(undefined, P), 'global')
  assert.equal(railProjectScopeKind(deltaBlob(), P), 'delta')
  const fork = writeRailConfigBlob(undefined, defaultRailConfig(), P)
  assert.equal(railProjectScopeKind(fork, P), 'fork')
  assert.equal(isProjectRailDelta(deltaBlob().projects?.[P]), true)
})

test('a project delta overlays the live global layout instead of replacing it', () => {
  const blob = deltaBlob()
  const resolved = resolveRail(blob, P)
  assert.equal(resolved.kind, 'delta')
  // The addition is there, placed in a trailing project row on both devices' Drawer.
  const item = resolved.config.items.find(entry => entry.id === 'custom:skill:ship')
  assert.ok(item)
  for (const device of ['desktop', 'mobile'] as const) {
    const rows = resolved.config.layouts[device].panel
    const last = rows[rows.length - 1]
    assert.equal(last.label, 'Project')
    assert.deepEqual(last.items, ['custom:skill:ship'])
    assert.equal(resolved.projectRowIds.has(last.id), true)
  }
  assert.equal(resolved.projectItemIds.has('custom:skill:ship'), true)
  // Every global row is still exactly the global layout's.
  const global = railConfigFromBlob(blob)
  assert.deepEqual(resolved.config.layouts.desktop.strip, global.layouts.desktop.strip)
  // Other scopes see none of it.
  assert.equal(railConfigFromBlob(blob).items.some(entry => entry.id === 'custom:skill:ship'), false)
  assert.equal(railConfigFromBlob(blob, OTHER).items.some(entry => entry.id === 'custom:skill:ship'), false)
})

test('global edits keep flowing into a delta project', () => {
  let blob = deltaBlob()
  // Remove Esc from the global desktop strip after the delta exists.
  const global = railConfigFromBlob(blob)
  const stripRow = global.layouts.desktop.strip[0]
  const escIndex = stripRow.items.indexOf('esc')
  blob = writeRailConfigBlob(blob, removeRailEntry(global, { device: 'desktop', surface: 'strip', rowId: stripRow.id, index: escIndex }))
  const effective = railConfigFromBlob(blob, P)
  assert.equal(effective.layouts.desktop.strip[0].items.includes('esc'), false)
  // The addition is still there: the delta survived the global write.
  assert.equal(effective.items.some(entry => entry.id === 'custom:skill:ship'), true)
})

test('a delta item colliding with a base id is dropped: the base wins', () => {
  const delta: RailProjectDelta = {
    mode: 'delta',
    items: [
      { id: 'esc', type: 'text', label: 'hijack', text: 'rm -rf /' },
      { id: 'custom:evil', type: 'action', action: 'endSession', label: 'boom' } as unknown as RailItem,
      skillItem('ok'),
    ],
    layouts: { desktop: { panel: [{ id: 'pr', items: ['esc', 'custom:skill:ok', 'ghost'] }] } },
  }
  const { config, projectItemIds } = resolveDeltaScope(defaultRailConfig(), delta)
  assert.equal(config.items.filter(entry => entry.id === 'esc').length, 1)
  assert.equal(config.items.find(entry => entry.id === 'esc')?.type, 'key')
  assert.equal(config.items.some(entry => entry.id === 'custom:evil'), false)
  assert.deepEqual([...projectItemIds], ['custom:skill:ok'])
  // The delta row keeps known ids (global ones included) and drops the dangling one.
  const projectRow = config.layouts.desktop.panel.at(-1)
  assert.deepEqual(projectRow?.items, ['esc', 'custom:skill:ok'])
})

// --- routing edits by ownership ------------------------------------------------

test('an edit to a shared row in project scope writes the global layout', () => {
  const blob = deltaBlob()
  const resolved = resolveRail(blob, P)
  const stripRow = resolved.config.layouts.desktop.strip[0]
  const escIndex = stripRow.items.indexOf('esc')
  const next = removeRailEntry(resolved.config, { device: 'desktop', surface: 'strip', rowId: stripRow.id, index: escIndex })
  const written = applyScopedRail(blob, P, resolved, next)
  // Global lost it, so every project lost it.
  assert.equal(railConfigFromBlob(written).layouts.desktop.strip[0].items.includes('esc'), false)
  assert.equal(railConfigFromBlob(written, OTHER).layouts.desktop.strip[0].items.includes('esc'), false)
  // The delta was not touched.
  assert.equal(railConfigFromBlob(written, P).items.some(entry => entry.id === 'custom:skill:ship'), true)
})

test('an edit to a project row stays project state', () => {
  const blob = deltaBlob()
  const resolved = resolveRail(blob, P)
  const projectRow = resolved.config.layouts.desktop.panel.at(-1)!
  // Drag a *global* item into the project row: legal, and project-local.
  const globalRow = resolved.config.layouts.desktop.panel[0]
  const homeIndex = globalRow.items.indexOf('home')
  const next = moveRailEntry(resolved.config,
    { device: 'desktop', surface: 'panel', rowId: globalRow.id, index: homeIndex },
    { device: 'desktop', surface: 'panel', rowId: projectRow.id, index: 0 })
  const written = applyScopedRail(blob, P, resolved, next)
  assert.equal(railConfigFromBlob(written).layouts.desktop.panel[0].items.includes('home'), false)
  const effective = railConfigFromBlob(written, P)
  assert.deepEqual(effective.layouts.desktop.panel.at(-1)?.items, ['home', 'custom:skill:ship'])
  // The other project never sees the project row.
  assert.equal(railConfigFromBlob(written, OTHER).layouts.desktop.panel.some(row => row.items.includes('custom:skill:ship')), false)
})

test('fork scope routes every edit to the fork', () => {
  const fork = writeRailConfigBlob(undefined, defaultRailConfig(), P)
  const resolved = resolveRail(fork, P)
  assert.equal(resolved.kind, 'fork')
  const stripRow = resolved.config.layouts.desktop.strip[0]
  const next = removeRailEntry(resolved.config, { device: 'desktop', surface: 'strip', rowId: stripRow.id, index: 0 })
  const written = applyScopedRail(fork, P, resolved, next)
  assert.notDeepEqual(railConfigFromBlob(written, P).layouts.desktop.strip[0].items, railConfigFromBlob(written).layouts.desktop.strip[0].items)
})

test('detach freezes the effective layout as a fork that stops tracking', () => {
  let blob = deltaBlob()
  blob = detachProjectRail(blob, P)
  assert.equal(railProjectScopeKind(blob, P), 'fork')
  // The addition travelled into the fork.
  assert.equal(railConfigFromBlob(blob, P).items.some(entry => entry.id === 'custom:skill:ship'), true)
  // A later global edit no longer reaches the fork.
  const global = railConfigFromBlob(blob)
  const stripRow = global.layouts.desktop.strip[0]
  blob = writeRailConfigBlob(blob, removeRailEntry(global, { device: 'desktop', surface: 'strip', rowId: stripRow.id, index: 0 }))
  assert.notEqual(
    railConfigFromBlob(blob, P).layouts.desktop.strip[0].items.length,
    railConfigFromBlob(blob).layouts.desktop.strip[0].items.length,
  )
})

// --- rows, placement, removal ----------------------------------------------------

test('addProjectRailRow creates the delta and a labelled project row', () => {
  const blob = addProjectRailRow(undefined, P, 'desktop', 'strip')
  assert.equal(railProjectScopeKind(blob, P), 'delta')
  const resolved = resolveRail(blob, P)
  const last = resolved.config.layouts.desktop.strip.at(-1)!
  assert.equal(last.label, 'Project')
  assert.equal(resolved.projectRowIds.has(last.id), true)
  // The other device and surface are untouched.
  assert.equal(resolved.config.layouts.mobile.strip.length, railConfigFromBlob(blob).layouts.mobile.strip.length)
})

test('toggleScopedPlacement keeps a project item inside project rows', () => {
  let blob = deltaBlob()
  let resolved = resolveRail(blob, P)
  // Enable on the desktop strip, where no project row exists yet: one is created.
  blob = toggleScopedPlacement(blob, P, resolved, 'custom:skill:ship', 'desktop', 'strip')
  resolved = resolveRail(blob, P)
  const stripLast = resolved.config.layouts.desktop.strip.at(-1)!
  assert.equal(resolved.projectRowIds.has(stripLast.id), true)
  assert.deepEqual(stripLast.items, ['custom:skill:ship'])
  // Disable on the desktop Drawer: only the project rows lose it.
  blob = toggleScopedPlacement(blob, P, resolved, 'custom:skill:ship', 'desktop', 'panel')
  resolved = resolveRail(blob, P)
  assert.equal(railPlacementCounts(resolved.config, 'custom:skill:ship').desktop.panel, 0)
  assert.equal(railPlacementCounts(resolved.config, 'custom:skill:ship').mobile.panel, 1)
})

test('unpinning the only project addition returns the project to plain inheritance', () => {
  const blob = deltaBlob()
  const cleaned = removeScopedRailItem(blob, P, 'custom:skill:ship')
  assert.equal(railProjectScopeKind(cleaned, P), 'global')
  assert.deepEqual(railConfigFromBlob(cleaned, P), railConfigFromBlob(cleaned))
})

test('removing a project item spares rows it did not empty', () => {
  let blob = deltaBlob()
  const resolved = resolveRail(blob, P)
  blob = addScopedRailItem(blob, P, skillItem('deploy'), 'project')
  blob = removeScopedRailItem(blob, P, 'custom:skill:ship')
  const after = resolveRail(blob, P)
  assert.equal(after.kind, 'delta')
  const row = after.config.layouts.desktop.panel.at(-1)!
  assert.deepEqual(row.items, ['custom:skill:deploy'])
  assert.equal(after.projectRowIds.size, resolved.projectRowIds.size)
})

test('a global-target add reaches every non-forked project', () => {
  let blob = deltaBlob()
  blob = addScopedRailItem(blob, P, skillItem('review'), 'global')
  assert.equal(railConfigFromBlob(blob).items.some(entry => entry.id === 'custom:skill:review'), true)
  assert.equal(railConfigFromBlob(blob, OTHER).items.some(entry => entry.id === 'custom:skill:review'), true)
  assert.equal(railConfigFromBlob(blob, P).items.some(entry => entry.id === 'custom:skill:review'), true)
})

test('an add whose id the effective catalog already has is refused', () => {
  const blob = deltaBlob()
  const again = addScopedRailItem(blob, P, skillItem('ship'), 'project')
  assert.deepEqual(again, blob)
})

// --- pinning -----------------------------------------------------------------

test('pinning a project skill creates a project-scoped drawer button', () => {
  const blob = pinSkill(undefined, P, { name: 'commit', label: 'commit', backend: 'claude', projectScoped: true })
  assert.equal(railProjectScopeKind(blob, P), 'delta')
  const resolved = resolveRail(blob, P)
  const pinned = pinnedSkillItem(resolved.config, 'commit')
  assert.ok(pinned)
  assert.equal(pinned.type, 'skill')
  assert.deepEqual(pinned.backends, ['claude'])
  assert.equal(pinned.submit, false)
  // Placed on both devices' Drawer, invisible to the global scope.
  assert.equal(railPlacementCounts(resolved.config, pinned.id).desktop.panel, 1)
  assert.equal(railPlacementCounts(resolved.config, pinned.id).mobile.panel, 1)
  assert.equal(pinnedSkillItem(railConfigFromBlob(blob), 'commit'), null)
})

test('pinning a user skill lands globally; a slash-command kind pins as slash', () => {
  const blob = pinSkill(undefined, P, { name: 'review', label: 'review', kind: 'command', backend: 'codex', projectScoped: false })
  assert.equal(railProjectScopeKind(blob, P), 'global')
  const pinned = pinnedSkillItem(railConfigFromBlob(blob), 'review')
  assert.equal(pinned?.type, 'slash')
})

test('pinning is idempotent and matches through a leading sigil', () => {
  const blob = pinSkill(undefined, undefined, { name: 'commit', label: 'commit', backend: 'claude', projectScoped: false })
  assert.deepEqual(pinSkill(blob, undefined, { name: '/commit', label: 'commit', backend: 'claude', projectScoped: false }), blob)
  assert.ok(pinnedSkillItem(railConfigFromBlob(blob), '$commit'))
})

test('pinned state is per harness: another CLI pins its own restricted button', () => {
  let blob = pinSkill(undefined, undefined, { name: 'commit', label: 'commit', backend: 'claude', projectScoped: false })
  const config = railConfigFromBlob(blob)
  assert.ok(pinnedSkillItem(config, 'commit', 'claude'))
  assert.equal(pinnedSkillItem(config, 'commit', 'codex'), null)
  blob = pinSkill(blob, undefined, { name: 'commit', label: 'commit', backend: 'codex', projectScoped: false })
  const both = railConfigFromBlob(blob)
  const claudePin = pinnedSkillItem(both, 'commit', 'claude')
  const codexPin = pinnedSkillItem(both, 'commit', 'codex')
  assert.ok(claudePin && codexPin)
  assert.notEqual(claudePin.id, codexPin.id)
  // An unrestricted hand-authored button reads as pinned everywhere.
  const open = addScopedRailItem(undefined, undefined, skillItem('deploy'), 'global')
  assert.ok(pinnedSkillItem(railConfigFromBlob(open), 'deploy', 'claude'))
  assert.ok(pinnedSkillItem(railConfigFromBlob(open), 'deploy', 'codex'))
})

test('a built-in slash command reads as already pinned', () => {
  // `rewind` ships as a built-in slash item; a discovered /rewind must not create
  // a second button, and unpin surfaces refuse to delete built-ins.
  const config = defaultRailConfig()
  assert.equal(pinnedSkillItem(config, 'rewind')?.id, 'rewind')
})

test('pinning a prompt template stores the key and carries its backend restriction', () => {
  const all = ['shell', 'claude', 'codex']
  const blob = pinPrompt(undefined, P, {
    key: 'project:tpl-1', id: 'tpl-1', title: 'Ship checklist',
    backends: ['claude'], allBackends: all, projectScoped: true,
  })
  const resolved = resolveRail(blob, P)
  const pinned = pinnedPromptItem(resolved.config, 'project:tpl-1')
  assert.ok(pinned)
  assert.equal(pinned.promptKey, 'project:tpl-1')
  assert.deepEqual(pinned.backends, ['claude'])
  // An unrestricted template carries no restriction at all.
  const open = pinPrompt(undefined, undefined, {
    key: 'global:tpl-2', id: 'tpl-2', title: 'Open', backends: all, allBackends: all, projectScoped: false,
  })
  assert.equal(pinnedPromptItem(railConfigFromBlob(open), 'global:tpl-2')?.backends, undefined)
})
