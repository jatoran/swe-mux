import assert from 'node:assert/strict'
import test from 'node:test'
import {
  defaultRailConfig, isProjectRailDelta, railConfigFromBlob, railProjectScopeKind,
  resolveDeltaScope, writeRailConfigBlob,
  type RailBlob, type RailItem, type RailProjectDelta,
} from '../src/commandRail.ts'
import { insertRailItem, removeRailEntry, moveRailEntry, railPlacementCounts } from '../src/railLayout.ts'
import {
  addProjectRailRow, addScopedRailItem, applyScopedRail, detachProjectRail,
  hideScopedRailEntry, isProjectRailPlacement, removeScopedRailItem, resolveRail, toggleScopedPlacement,
  unhideScopedRailEntry,
} from '../src/railScope.ts'
import { applyForkReattach, planForkReattach } from '../src/railReattach.ts'

const P = 'proj-a'
const OTHER = 'proj-b'

const skillItem = (name: string): RailItem =>
  ({ id: `custom:skill:${name}`, type: 'skill', label: name, text: name })

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
  // The addition is there, placed in a trailing project row on both devices.
  const item = resolved.config.items.find(entry => entry.id === 'custom:skill:ship')
  assert.ok(item)
  for (const device of ['desktop', 'mobile'] as const) {
    const rows = resolved.config.layouts[device].strip
    const last = rows[rows.length - 1]
    assert.equal(last.label, 'Project')
    assert.deepEqual(last.items, ['custom:skill:ship'])
    assert.equal(resolved.projectRowIds.has(last.id), true)
  }
  assert.equal(resolved.projectItemIds.has('custom:skill:ship'), true)
  // Every global row is still exactly the global layout's.
  const global = railConfigFromBlob(blob)
  assert.deepEqual(resolved.config.layouts.desktop.strip.slice(0, global.layouts.desktop.strip.length), global.layouts.desktop.strip)
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
    layouts: { desktop: { strip: [{ id: 'pr', items: ['esc', 'custom:skill:ok', 'ghost'] }] } },
  }
  const { config, projectItemIds } = resolveDeltaScope(defaultRailConfig(), delta)
  assert.equal(config.items.filter(entry => entry.id === 'esc').length, 1)
  assert.equal(config.items.find(entry => entry.id === 'esc')?.type, 'key')
  assert.equal(config.items.some(entry => entry.id === 'custom:evil'), false)
  assert.deepEqual([...projectItemIds], ['custom:skill:ok'])
  // The delta row keeps known ids (global ones included) and drops the dangling one.
  const projectRow = config.layouts.desktop.strip.at(-1)
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
  const projectRow = resolved.config.layouts.desktop.strip.at(-1)!
  // Drag a *global* item into the project row: legal, and project-local.
  const globalRow = resolved.config.layouts.desktop.strip[0]
  const homeIndex = globalRow.items.indexOf('home')
  const next = moveRailEntry(resolved.config,
    { device: 'desktop', surface: 'strip', rowId: globalRow.id, index: homeIndex },
    { device: 'desktop', surface: 'strip', rowId: projectRow.id, index: 0 })
  const written = applyScopedRail(blob, P, resolved, next)
  assert.equal(railConfigFromBlob(written).layouts.desktop.strip[0].items.includes('home'), false)
  const effective = railConfigFromBlob(written, P)
  assert.deepEqual(effective.layouts.desktop.strip.at(-1)?.items, ['home', 'custom:skill:ship'])
  // The other project never sees the project row.
  assert.equal(railConfigFromBlob(written, OTHER).layouts.desktop.strip.some(row => row.items.includes('custom:skill:ship')), false)
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
  // The other device is untouched.
  assert.equal(resolved.config.layouts.mobile.strip.length, railConfigFromBlob(blob).layouts.mobile.strip.length)
})

test('toggleScopedPlacement removes and restores a project item inside project rows', () => {
  let blob = deltaBlob()
  let resolved = resolveRail(blob, P)
  // Disable the existing desktop placement without touching mobile.
  blob = toggleScopedPlacement(blob, P, resolved, 'custom:skill:ship', 'desktop', 'strip')
  resolved = resolveRail(blob, P)
  assert.equal(railPlacementCounts(resolved.config, 'custom:skill:ship').desktop.strip, 0)
  assert.equal(railPlacementCounts(resolved.config, 'custom:skill:ship').mobile.strip, 1)
  // Restore it into a project-owned desktop row.
  blob = toggleScopedPlacement(blob, P, resolved, 'custom:skill:ship', 'desktop', 'strip')
  resolved = resolveRail(blob, P)
  const stripLast = resolved.config.layouts.desktop.strip.at(-1)!
  assert.equal(resolved.projectRowIds.has(stripLast.id), true)
  assert.deepEqual(stripLast.items, ['custom:skill:ship'])
})

test('removing the only project addition returns the project to plain inheritance', () => {
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
  const row = after.config.layouts.desktop.strip.at(-1)!
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

// --- splices and hides: project placement inside a shared row -------------------

/** The shared desktop strip row every splice/hide test works against. */
const sharedStrip = (blob: RailBlob | undefined) => railConfigFromBlob(blob).layouts.desktop.strip[0]

test('a project action dropped into a shared row becomes a splice, not a global write', () => {
  let blob = deltaBlob()
  const resolved = resolveRail(blob, P)
  const row = resolved.config.layouts.desktop.strip[0]
  const after = row.items.indexOf('esc')
  const next = insertRailItem(resolved.config, 'custom:skill:ship', { device: 'desktop', surface: 'strip', rowId: row.id, index: after + 1 })
  blob = applyScopedRail(blob, P, resolved, next)
  // The project sees it, anchored where it was dropped.
  const mine = railConfigFromBlob(blob, P).layouts.desktop.strip[0].items
  assert.equal(mine[mine.indexOf('esc') + 1], 'custom:skill:ship')
  // The shared row's *definition* never contains a project item — that is the
  // invariant the whole splice model exists to preserve.
  assert.equal(sharedStrip(blob).items.includes('custom:skill:ship'), false)
  assert.equal(railConfigFromBlob(blob, OTHER).layouts.desktop.strip[0].items.includes('custom:skill:ship'), false)
})

test('a splice re-anchors when global moves around it, and falls back to the end when its anchor goes', () => {
  let blob = deltaBlob()
  let resolved = resolveRail(blob, P)
  const row = resolved.config.layouts.desktop.strip[0]
  const next = insertRailItem(resolved.config, 'custom:skill:ship',
    { device: 'desktop', surface: 'strip', rowId: row.id, index: row.items.indexOf('esc') + 1 })
  blob = applyScopedRail(blob, P, resolved, next)
  // Global gains a button before the anchor: the splice follows the anchor rather
  // than staying at a stored index.
  const global = railConfigFromBlob(blob)
  blob = writeRailConfigBlob(blob, insertRailItem(global, 'home', { device: 'desktop', surface: 'strip', rowId: row.id, index: 0 }))
  let mine = railConfigFromBlob(blob, P).layouts.desktop.strip[0].items
  assert.equal(mine[mine.indexOf('esc') + 1], 'custom:skill:ship')
  // Global drops the anchor entirely: the splice survives at the end of the row.
  const withAnchor = railConfigFromBlob(blob)
  const escAt = withAnchor.layouts.desktop.strip[0].items.indexOf('esc')
  blob = writeRailConfigBlob(blob, removeRailEntry(withAnchor, { device: 'desktop', surface: 'strip', rowId: row.id, index: escAt }))
  mine = railConfigFromBlob(blob, P).layouts.desktop.strip[0].items
  assert.equal(mine.includes('esc'), false)
  assert.equal(mine[mine.length - 1], 'custom:skill:ship')
  resolved = resolveRail(blob, P)
  assert.equal(isProjectRailPlacement(resolved, 'desktop', 'strip', row.id, 'custom:skill:ship'), true)
  assert.equal(isProjectRailPlacement(resolved, 'desktop', 'strip', row.id, 'tab'), false)
})

test('hiding a shared button removes it here and nowhere else, and unhiding restores it', () => {
  const rowId = railConfigFromBlob(undefined).layouts.desktop.strip[0].id
  let blob = hideScopedRailEntry(undefined, P, 'esc', 'desktop', 'strip', rowId)
  assert.equal(railProjectScopeKind(blob, P), 'delta')
  assert.equal(railConfigFromBlob(blob, P).layouts.desktop.strip[0].items.includes('esc'), false)
  // The shared row is untouched: every other project still has it.
  assert.equal(sharedStrip(blob).items.includes('esc'), true)
  assert.equal(railConfigFromBlob(blob, OTHER).layouts.desktop.strip[0].items.includes('esc'), true)
  // The editors need it back: the resolution reports what was hidden and where.
  const hidden = resolveRail(blob, P).hiddenEntries.get(`desktop|strip|${rowId}`)
  assert.deepEqual(hidden?.map(entry => entry.item), ['esc'])
  // Unhiding drops the record and the project returns to plain inheritance.
  blob = unhideScopedRailEntry(blob, P, 'esc', 'desktop', 'strip', rowId)
  assert.equal(railProjectScopeKind(blob, P), 'global')
  assert.deepEqual(railConfigFromBlob(blob, P), railConfigFromBlob(blob))
})

test('a hidden button survives an unrelated edit to the same shared row', () => {
  const rowId = railConfigFromBlob(undefined).layouts.desktop.strip[0].id
  const blob = hideScopedRailEntry(undefined, P, 'esc', 'desktop', 'strip', rowId)
  const resolved = resolveRail(blob, P)
  // Reorder two *other* chips from the project's view. The hidden entry is in no
  // row, so it cannot be moved — and it must not be deleted for everybody either.
  const items = resolved.config.layouts.desktop.strip[0].items
  const next = moveRailEntry(resolved.config,
    { device: 'desktop', surface: 'strip', rowId, index: items.indexOf('tab') },
    { device: 'desktop', surface: 'strip', rowId, index: 0 })
  const written = applyScopedRail(blob, P, resolved, next)
  assert.equal(sharedStrip(written).items.includes('esc'), true)
  assert.equal(railConfigFromBlob(written, P).layouts.desktop.strip[0].items.includes('esc'), false)
  assert.equal(railConfigFromBlob(written, P).layouts.desktop.strip[0].items[0], 'tab')
})

test('editing a row that holds a splice round-trips: what you see is what is stored', () => {
  // The property the write-back turns on. Whatever the operator drags a shared row
  // into, resolving what was stored must give back exactly that row — otherwise a
  // chip moves by itself after the save.
  let blob = deltaBlob()
  let resolved = resolveRail(blob, P)
  const rowId = resolved.config.layouts.desktop.strip[0].id
  const at = (index: number) => ({ device: 'desktop' as const, surface: 'strip' as const, rowId, index })
  const edits: Array<(items: readonly string[]) => { from: number; to: number }> = [
    items => ({ from: items.indexOf('custom:skill:ship'), to: 0 }),
    items => ({ from: items.indexOf('custom:skill:ship'), to: items.length - 1 }),
    items => ({ from: items.indexOf('esc'), to: items.indexOf('custom:skill:ship') }),
    items => ({ from: items.indexOf('custom:skill:ship'), to: 3 }),
  ]
  blob = applyScopedRail(blob, P, resolved,
    insertRailItem(resolved.config, 'custom:skill:ship', at(2)))
  for (const edit of edits) {
    resolved = resolveRail(blob, P)
    const items = resolved.config.layouts.desktop.strip[0].items
    const { from, to } = edit(items)
    const next = moveRailEntry(resolved.config, at(from), at(to))
    const wanted = next.layouts.desktop.strip[0].items
    blob = applyScopedRail(blob, P, resolved, next)
    assert.deepEqual(railConfigFromBlob(blob, P).layouts.desktop.strip[0].items, wanted)
    // And every time, the shared definition stays free of the project's action.
    assert.equal(sharedStrip(blob).items.includes('custom:skill:ship'), false)
  }
})

test('a splice naming a deleted project action leaves no dead record behind', () => {
  let blob = deltaBlob()
  const resolved = resolveRail(blob, P)
  const row = resolved.config.layouts.desktop.strip[0]
  blob = applyScopedRail(blob, P, resolved,
    insertRailItem(resolved.config, 'custom:skill:ship', { device: 'desktop', surface: 'strip', rowId: row.id, index: 0 }))
  blob = removeScopedRailItem(blob, P, 'custom:skill:ship')
  assert.equal(railProjectScopeKind(blob, P), 'global')
  assert.deepEqual(railConfigFromBlob(blob, P), railConfigFromBlob(blob))
})

test('a hand-written splice that would duplicate a shared entry is refused', () => {
  // The one-owner-per-id rule: a global action may only be spliced into a row that
  // does not already define it (or that this project hides it from). Without the
  // rule, the row shows two copies of `esc` that nothing can tell apart.
  const rowId = railConfigFromBlob(undefined).layouts.desktop.strip[0].id
  const delta: RailProjectDelta = { mode: 'delta', splices: { desktop: { strip: [{ row: rowId, item: 'esc', after: null }] } } }
  const items = resolveDeltaScope(defaultRailConfig(), delta).config.layouts.desktop.strip[0].items
  assert.equal(items.filter(id => id === 'esc').length, 1)
  // Paired with a hide of the same id, it *is* the project-local move it claims to be.
  const moved: RailProjectDelta = { ...delta, hides: { desktop: { strip: [{ row: rowId, item: 'esc' }] } } }
  const movedItems = resolveDeltaScope(defaultRailConfig(), moved).config.layouts.desktop.strip[0].items
  assert.equal(movedItems[0], 'esc')
  assert.equal(movedItems.filter(id => id === 'esc').length, 1)
})

// --- fork -> delta reattach ------------------------------------------------------

test('reattach turns a fork back into a delta that reproduces it', () => {
  // The shape the tool exists for: a fork that is "the shared rail, plus one
  // project action, minus one button".
  let blob = addScopedRailItem(undefined, P, skillItem('ship'), 'project')
  blob = detachProjectRail(blob, P)
  const fork = railConfigFromBlob(blob, P)
  const rowId = fork.layouts.desktop.strip[0].id
  const forkStrip = fork.layouts.desktop.strip[0].items
  const edited = { ...fork, layouts: { ...fork.layouts, desktop: { ...fork.layouts.desktop, strip: [{
    ...fork.layouts.desktop.strip[0],
    items: [...forkStrip.filter(id => id !== 'esc'), 'custom:skill:ship'],
  }] } } }
  blob = writeRailConfigBlob(blob, edited, P)

  const plan = planForkReattach(blob, P)
  assert.ok(plan)
  assert.equal(plan.exact, true)
  assert.deepEqual(plan.issues, [])
  assert.equal(plan.counts.items, 1)
  assert.equal(plan.counts.hides, 1)
  assert.ok(plan.counts.splices >= 1)

  const after = applyForkReattach(blob, P, plan)
  assert.equal(railProjectScopeKind(after, P), 'delta')
  // Same rail as the fork rendered...
  assert.deepEqual(railConfigFromBlob(after, P).layouts.desktop.strip[0].items, edited.layouts.desktop.strip[0].items)
  // ...and the shared row is global again, so later global edits arrive.
  assert.equal(sharedStrip(after).items.includes('esc'), true)
  assert.equal(sharedStrip(after).items.includes('custom:skill:ship'), false)
  const grown = railConfigFromBlob(after)
  const withNew = writeRailConfigBlob(after, insertRailItem(grown, 'home', { device: 'desktop', surface: 'strip', rowId, index: 0 }))
  assert.equal(railConfigFromBlob(withNew, P).layouts.desktop.strip[0].items[0], 'home')
})

test('reattach expresses a reorder of shared buttons, and names what it cannot express', () => {
  let blob = detachProjectRail(undefined, P)
  const fork = railConfigFromBlob(blob, P)
  const strip = fork.layouts.desktop.strip[0]
  // Move `esc` to the head — a project-local reorder of a *global* button, which
  // is a hide plus a splice — and rename the shared row, which a delta cannot do.
  const reordered = ['esc', ...strip.items.filter(id => id !== 'esc')]
  blob = writeRailConfigBlob(blob, {
    ...fork,
    layouts: { ...fork.layouts, desktop: { ...fork.layouts.desktop, strip: [{ ...strip, label: 'Mine', items: reordered }] } },
  }, P)

  const plan = planForkReattach(blob, P)
  assert.ok(plan)
  assert.deepEqual(plan.issues.map(issue => issue.kind), ['row-label'])
  const after = applyForkReattach(blob, P, plan)
  assert.deepEqual(railConfigFromBlob(after, P).layouts.desktop.strip[0].items, reordered)
  // The shared definition kept its own order and its own name.
  assert.equal(sharedStrip(after).items[0], 'relaunch')
  assert.equal(sharedStrip(after).label, undefined)
})

test('reattach is a no-op on a project that is not forked', () => {
  assert.equal(planForkReattach(undefined, P), null)
  assert.equal(planForkReattach(deltaBlob(), P), null)
})
