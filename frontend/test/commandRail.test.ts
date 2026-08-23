import assert from 'node:assert/strict'
import test from 'node:test'
import {
  BUILTIN_RAIL, adoptLegacyPlacement, clearProjectRailBlob, defaultRailConfig,
  mergeRailCatalog, migratedRailItemId, migrateLegacyRail, normalizeRailConfig, railConfigFromBlob, railHasProjectOverride,
  railItemDisplayLabel, railItemDisplayMode, railItemVisible, railPadSlotItemIds, railPayload, resolveRailRows, writeRailConfigBlob,
  type LegacyRailItem, type RailBlob, type RailConfig, type RailContext, type RailItem, type RailSurface,
} from '../src/commandRail.ts'
import { composerClearSequence } from '../src/composerText.ts'
import { AGENT_NEWLINE } from '../src/terminalKeys.ts'

const CLAUDE: RailContext = { device: 'desktop', backend: 'claude' }
const ids = (config: RailConfig, surface: RailSurface, ctx: RailContext = CLAUDE): string[] =>
  resolveRailRows(config, surface, ctx).flatMap(row => row.entries.map(entry => entry.item.id))

test('the default layout seeds one rail row, identical on both devices', () => {
  const config = defaultRailConfig()
  for (const device of ['desktop', 'mobile'] as const) {
    assert.equal(config.layouts[device].strip.length, 1)
  }
  assert.deepEqual(config.layouts.desktop.strip[0].items, config.layouts.mobile.strip[0].items)
  // Identical contents, but separate rows with separate ids: the two devices are
  // never the same row, so editing one can never move the other.
  assert.notEqual(config.layouts.desktop.strip[0].id, config.layouts.mobile.strip[0].id)
})

test('default rail places the four pads instead of what they hold, and ends with Attach', () => {
  assert.deepEqual(ids(defaultRailConfig(), 'strip'), [
    'relaunch', 'padCopy', 'branch', 'approveOnce', 'paste', 'padPickers', 'kbdToggle',
    'esc', 'enter', 'tab', 'shiftTab', 'ctrlC', 'padArrows', 'down', 'padJump',
    'modCtrl', 'modAlt', 'modShift',
    'markdownDivider', 'markdownCodeFence', 'ctrlU', 'restoreInput',
    'newline', 'rewind', 'endSession', 'attach',
  ])
})

// The invariant that replaced "one placement per built-in", which the pads deliberately
// broke: a built-in must still be *reachable* on a fresh install, as its own chip or
// inside a pad that is placed. Placing both would spend the space the pad exists to save,
// and placing neither would ship a button nobody can find.
test('every visible built-in is reachable on the default rail, directly or through a pad', () => {
  const config = defaultRailConfig()
  const placed = new Set(ids(config, 'strip'))
  const reachable = new Set(placed)
  for (const id of placed) {
    const item = config.items.find(entry => entry.id === id)
    if (item) for (const slot of railPadSlotItemIds(item)) reachable.add(slot)
  }
  assert.deepEqual(
    [...reachable].sort(),
    BUILTIN_RAIL.filter(item => railItemVisible(item, 'claude')).map(item => item.id).sort(),
  )
})

test('a pad and its contents are never both on the default rail', () => {
  const config = defaultRailConfig()
  const placed = ids(config, 'strip')
  for (const id of placed) {
    const item = config.items.find(entry => entry.id === id)
    for (const slot of item ? railPadSlotItemIds(item) : []) {
      assert.equal(placed.includes(slot), false, `${slot} is both in ${id} and on the rail`)
    }
  }
})

test('desktop and mobile layouts are edited independently', () => {
  const config = defaultRailConfig()
  const desktopCount = ids(config, 'strip').length
  config.layouts.mobile.strip[0].items = ['esc', 'enter']
  assert.deepEqual(ids(config, 'strip', { device: 'mobile', backend: 'claude' }), ['esc', 'enter'])
  // The desktop layout is untouched by the mobile edit.
  assert.equal(ids(config, 'strip').length, desktopCount)
})

test('an item placed in no row is simply absent from that device', () => {
  const config = defaultRailConfig()
  config.layouts.mobile.strip[0].items = []
  assert.deepEqual(resolveRailRows(config, 'strip', { device: 'mobile', backend: 'claude' }), [])
})

test('multiple rows render in order, and empty rows are dropped', () => {
  const config = defaultRailConfig()
  config.layouts.desktop.strip = [
    { id: 'r1', items: ['esc', 'enter'] },
    { id: 'r2', items: ['rewind'] },
    { id: 'r3', items: ['left', 'right'] },
  ]
  const rows = resolveRailRows(config, 'strip', CLAUDE)
  assert.deepEqual(rows.map(row => row.id), ['r1', 'r2', 'r3'])
  assert.deepEqual(rows.map(row => row.entries.map(entry => entry.item.id)), [['esc', 'enter'], ['rewind'], ['left', 'right']])
  // `rewind` is Claude-only, so on Codex its row disappears rather than rendering blank.
  const codex = resolveRailRows(config, 'strip', { device: 'desktop', backend: 'codex' })
  assert.deepEqual(codex.map(row => row.id), ['r1', 'r3'])
})

test('the same item may appear twice, with a distinct key per occurrence', () => {
  const config = defaultRailConfig()
  config.layouts.desktop.strip = [{ id: 'r1', items: ['esc', 'esc'] }, { id: 'r2', items: ['esc'] }]
  const entries = resolveRailRows(config, 'strip', CLAUDE).flatMap(row => row.entries)
  assert.deepEqual(entries.map(entry => entry.item.id), ['esc', 'esc', 'esc'])
  assert.equal(new Set(entries.map(entry => entry.key)).size, 3)
})

test('backend filters gate items wherever they are placed', () => {
  const config = defaultRailConfig()
  assert.equal(ids(config, 'strip', { device: 'desktop', backend: 'shell' }).includes('branch'), false)
  assert.equal(ids(config, 'strip', { device: 'desktop', backend: 'codex' }).includes('branch'), true)
  assert.equal(railItemVisible({ id: 'x', type: 'slash', label: 'x', backends: ['claude'] }, 'codex'), false)
  assert.equal(railItemVisible({ id: 'x', type: 'slash', label: 'x', backends: ['claude'] }, 'claude'), true)
})

test('agent-only built-ins stay out of shell sessions', () => {
  const config = defaultRailConfig()
  const shell = ids(config, 'strip', { device: 'desktop', backend: 'shell' })
  assert.equal(shell.includes('attach'), false)
  assert.equal(shell.includes('markdownDivider'), false)
  assert.equal(ids(config, 'strip', { device: 'mobile', backend: 'codex' }).includes('attach'), true)
})

test('end session ships on the rail for every backend', () => {
  const config = defaultRailConfig()
  for (const backend of ['claude', 'codex', 'shell'] as const) {
    const ctx = { device: 'mobile', backend } as const
    assert.equal(ids(config, 'strip', ctx).includes('endSession'), true)
  }
})

test('the drawer newline and editing helpers use non-submitting agent keys', () => {
  const divider = BUILTIN_RAIL.find(item => item.id === 'markdownDivider')
  const codeFence = BUILTIN_RAIL.find(item => item.id === 'markdownCodeFence')
  assert.equal(BUILTIN_RAIL.find(item => item.id === 'newline')?.bytes, AGENT_NEWLINE)
  assert.equal(divider?.bytes, `${AGENT_NEWLINE}${AGENT_NEWLINE}---${AGENT_NEWLINE}${AGENT_NEWLINE}`)
  assert.equal(codeFence?.bytes, `${AGENT_NEWLINE}${AGENT_NEWLINE}\`\`\`${AGENT_NEWLINE}`)
  assert.equal(BUILTIN_RAIL.find(item => item.id === 'restoreInput')?.bytes, '\x19')
  // The rail's kill-line key is a raw Ctrl+U and nothing else. The Clear button that
  // briefly stood here sent the harness's declared whole-composer discard sequence,
  // which on Claude is a double Esc — an interrupt of whatever turn is running. That
  // is why no rail item may carry `composerClearSequence` again without being aware
  // of the session's state; the sequence itself stays published for the daemon's
  // unsent-input accounting.
  const ctrlU = BUILTIN_RAIL.find(item => item.id === 'ctrlU')
  assert.equal(ctrlU?.type, 'key')
  assert.equal(ctrlU?.bytes, '\x15')
  assert.equal(ctrlU?.className, 'term-key')
  for (const item of BUILTIN_RAIL) {
    assert.notEqual(item.bytes, composerClearSequence('claude'), `${item.id} must not send Claude's composer discard`)
  }
})

test('a newly shipped built-in is placed, not merely catalogued', () => {
  // Placement is the only thing that makes a button visible, so a catalog-only
  // append would leave every existing user unable to find a new command.
  const saved = defaultRailConfig()
  saved.items = saved.items.filter(item => item.id !== 'rewind')
  saved.layouts.desktop.strip[0].items = saved.layouts.desktop.strip[0].items.filter(id => id !== 'rewind')
  saved.layouts.mobile.strip[0].items = saved.layouts.mobile.strip[0].items.filter(id => id !== 'rewind')
  const config = normalizeRailConfig(saved)
  assert.equal(config.items.some(item => item.id === 'rewind'), true)
  for (const device of ['desktop', 'mobile'] as const) {
    assert.equal(config.layouts[device].strip.some(row => row.items.includes('rewind')), true)
  }
})

test('normalization removes the retired separate Draft action from saved rails', () => {
  const saved = defaultRailConfig()
  saved.items.push({ id: 'draftToggle', type: 'action', action: 'toggleDraft', label: 'Draft' })
  for (const device of ['desktop', 'mobile'] as const) {
    saved.layouts[device].strip[0].items.splice(7, 0, 'draftToggle')
  }
  const config = normalizeRailConfig(saved)
  assert.equal(config.items.some(item => item.id === 'draftToggle'), false)
  for (const device of ['desktop', 'mobile'] as const) {
    const strip = ids(config, 'strip', { device, backend: 'claude' })
    assert.equal(strip.includes('draftToggle'), false)
  }
})

test('a retired built-in id keeps its exact slot under its replacement', () => {
  // `clearInput` (the Clear button) was retired in favour of a raw Ctrl+U key. A
  // layout is per-device and per-Project and can be arbitrarily old, so the stored id
  // has to resolve rather than be dropped — and it has to resolve *in place*, or the
  // operator finds a hole where the button was.
  const saved = defaultRailConfig()
  const withOldId = (ids: string[]) => ids.map(id => (id === 'ctrlU' ? 'clearInput' : id))
  for (const device of ['desktop', 'mobile'] as const) {
    saved.layouts[device].strip[0].items = withOldId(saved.layouts[device].strip[0].items)
  }
  saved.items = saved.items.map(item =>
    item.id === 'ctrlU' ? { id: 'clearInput', type: 'action', action: 'clearInput', label: 'Clear' } : item)
  const config = normalizeRailConfig(saved)
  assert.equal(migratedRailItemId('clearInput'), 'ctrlU')
  assert.equal(config.items.some(item => item.id === 'clearInput'), false)
  const ctrlU = config.items.find(item => item.id === 'ctrlU')
  assert.equal(ctrlU?.type, 'key')
  assert.equal(ctrlU?.bytes, '\x15')
  for (const device of ['desktop', 'mobile'] as const) {
    // Same position as the shipped default, and appearing exactly once: the migration
    // must not also append the "newly shipped built-in" copy on top of the migrated one.
    const strip = ids(config, 'strip', { device, backend: 'claude' })
    assert.deepEqual(strip, ids(defaultRailConfig(), 'strip', { device, backend: 'claude' }))
    assert.equal(strip.filter(id => id === 'ctrlU').length, 1)
  }
})

test('a pre-layout save holding the retired id migrates it too', () => {
  const migrated = migrateLegacyRail([
    { id: 'clearInput', type: 'key', label: '^U', bytes: '\x15', placement: 'strip' },
    { id: 'restoreInput', type: 'key', label: '^Y', bytes: '\x19', placement: 'strip' },
    { id: 'markdownDivider', type: 'key', label: '---', placement: 'strip' },
    { id: 'markdownCodeFence', type: 'key', label: '```', placement: 'strip' },
  ] as LegacyRailItem[])
  const strip = ids(migrated, 'strip')
  assert.equal(strip.includes('clearInput'), false)
  assert.equal(strip.filter(id => id === 'ctrlU').length, 1)
  assert.equal(migrated.items.filter(item => item.id === 'ctrlU').length, 1)
})

test('normalization drops dangling references and unknown custom types', () => {
  const config = normalizeRailConfig({
    items: [
      { id: 'esc', type: 'key', label: 'Esc' },
      { id: 'custom:skill:commit', type: 'skill', label: 'commit', text: 'commit' },
      { id: 'custom:evil', type: 'action', action: 'endSession', label: 'boom' },
    ],
    layouts: {
      desktop: { strip: [{ id: 'r1', items: ['esc', 'ghost', 'custom:evil', 'custom:skill:commit'] }] },
      mobile: {},
    },
  })
  assert.equal(config.items.some(item => item.id === 'custom:evil'), false)
  assert.deepEqual(config.layouts.desktop.strip[0].items.slice(0, 2), ['esc', 'custom:skill:commit'])
  // Every device keeps a row so there is always somewhere to drop.
  assert.equal(config.layouts.mobile.strip.length, 1)
})

test('v2 panel entries become ordinary rail entries without duplicates', () => {
  const config = normalizeRailConfig({
    items: [
      { id: 'esc', type: 'key', label: 'Esc' },
      { id: 'pin:skill:commit', type: 'skill', label: 'commit', text: 'commit' },
    ],
    layouts: {
      desktop: {
        strip: [{ id: 'main', items: ['esc'] }],
        panel: [{ id: 'old-panel', items: ['esc', 'pin:skill:commit'] }],
      },
      mobile: {
        strip: [{ id: 'main-mobile', items: [] }],
        panel: [{ id: 'old-panel-mobile', items: ['pin:skill:commit'] }],
      },
    },
  })
  assert.deepEqual(config.layouts.desktop.strip[0].items.slice(0, 2), ['esc', 'pin:skill:commit'])
  assert.equal(config.layouts.desktop.strip[0].items.filter(id => id === 'esc').length, 1)
  assert.equal(config.layouts.mobile.strip[0].items.includes('pin:skill:commit'), true)
  assert.equal(config.items.find(item => item.id === 'pin:skill:commit')?.type, 'skill')
})

test('built-in behaviour is re-adopted over a saved catalog entry', () => {
  const config = normalizeRailConfig({
    items: [{ id: 'ctrlC', type: 'text', label: 'hijacked', text: 'rm -rf /' }],
    layouts: { desktop: { strip: [{ id: 'r1', items: ['ctrlC'] }] } },
  })
  const ctrlC = config.items.find(item => item.id === 'ctrlC')
  assert.equal(ctrlC?.type, 'key')
  assert.equal(ctrlC?.bytes, '\x03')
  assert.equal(ctrlC?.label, '^C')
})

test('built-in presentation and session visibility survive behavior re-adoption', () => {
  const config = normalizeRailConfig({
    items: [{
      id: 'copyInput', type: 'text', label: 'hijacked', text: 'unsafe',
      display: 'icon-label', displayLabel: 'Composer', backends: ['codex'],
    }],
    layouts: { desktop: { strip: [{ id: 'r1', items: ['copyInput'] }] } },
  })
  const item = config.items.find(entry => entry.id === 'copyInput')!
  assert.equal(item.type, 'action')
  assert.equal(item.action, 'copyInput')
  assert.equal('text' in item, false)
  assert.equal(item.display, 'icon-label')
  assert.equal(item.displayLabel, 'Composer')
  assert.deepEqual(item.backends, ['codex'])
})

test('display helpers fall back safely when an item has no registered icon', () => {
  const item: RailItem = { id: 'custom:text:x', type: 'text', label: 'Original', display: 'icon', displayLabel: 'Visible' }
  assert.equal(railItemDisplayLabel(item), 'Visible')
  assert.equal(railItemDisplayMode(item, false), 'label')
  assert.equal(railItemDisplayMode({ ...item, display: 'icon-label' }, true), 'icon-label')
})

test('mergeRailCatalog reports which built-ins it had to add', () => {
  const { items, addedBuiltins } = mergeRailCatalog([{ id: 'esc', type: 'key', label: 'Esc' }])
  assert.equal(items.length, BUILTIN_RAIL.length)
  assert.equal(addedBuiltins.some(item => item.id === 'esc'), false)
  assert.equal(addedBuiltins.length, BUILTIN_RAIL.length - 1)
})

// --- migration from the pre-layout format ----------------------------------

test('a pre-layout save becomes one rail row per device without losing visible entries', () => {
  const legacy: LegacyRailItem[] = [
    { id: 'esc', type: 'key', label: 'Esc', placement: 'strip' },
    { id: 'enter', type: 'key', label: '⏎', placement: 'strip', platforms: ['mobile'] },
    { id: 'rewind', type: 'slash', label: 'Rewind…', text: 'rewind', placement: 'both' },
    { id: 'endSession', type: 'action', label: 'End', placement: 'drawer' },
    { id: 'custom:skill:commit:0', type: 'skill', label: 'commit', text: 'commit', enabled: false },
    { id: 'custom:text:hidden:0', type: 'text', label: 'hidden', text: 'x', enabled: false, placement: 'strip' },
  ]
  const config = migrateLegacyRail(legacy)
  const desktopStrip = ids(config, 'strip')
  const mobileStrip = ids(config, 'strip', { device: 'mobile', backend: 'claude' })
  // The desktop/mobile split expressed by `platforms` becomes row membership.
  assert.equal(desktopStrip.includes('enter'), false)
  assert.equal(mobileStrip.includes('enter'), true)
  // Every formerly visible placement becomes one rail occurrence.
  assert.equal(desktopStrip.includes('rewind'), true)
  assert.equal(desktopStrip.filter(id => id === 'rewind').length, 1)
  assert.equal(desktopStrip.includes('endSession'), true)
  // An entry that predates `placement` and says "off" meant panel-only, so it
  // remains visible as a normal rail entry.
  assert.equal(desktopStrip.includes('custom:skill:commit:0'), true)
  // An explicit placement plus "off" was a genuine hide, and stays placed nowhere,
  // which is the only way to be hidden now.
  assert.equal(config.items.some(item => item.id === 'custom:text:hidden:0'), true)
  for (const device of ['desktop', 'mobile'] as const) {
    assert.equal(config.layouts[device].strip[0].items.includes('custom:text:hidden:0'), false)
  }
  // No item carries a legacy position field into the new catalog.
  for (const item of config.items) {
    assert.equal('placement' in item, false)
    assert.equal('platforms' in item, false)
    assert.equal('enabled' in item, false)
  }
})

// A pre-layout save keeps everything it had, which is no longer the fresh default: the
// pads changed where a *new* rail starts, and must not reach back and unplace buttons an
// existing operator has been using. So the migration is asserted against the catalog it
// came from, and the two are asserted to differ - the pads are the difference.
test('an untouched pre-layout save keeps every button it had placed', () => {
  const migrated = migrateLegacyRail(BUILTIN_RAIL.map(item => ({
    ...item,
    placement: 'strip',
  })) as LegacyRailItem[])
  const everything = BUILTIN_RAIL.map(item => item.id)
  const fresh = defaultRailConfig()
  for (const device of ['desktop', 'mobile'] as const) {
    assert.deepEqual(migrated.layouts[device].strip[0].items, everything)
    assert.notDeepEqual(migrated.layouts[device].strip[0].items, fresh.layouts[device].strip[0].items)
  }
})

test('a save that predates placement reads "off" as "not on the strip"', () => {
  assert.deepEqual(adoptLegacyPlacement({ enabled: false }), { enabled: undefined, placement: 'drawer' })
  assert.deepEqual(adoptLegacyPlacement({ enabled: false, placement: 'strip' }), { enabled: false, placement: 'strip' })
  assert.deepEqual(adoptLegacyPlacement({ enabled: undefined }), { enabled: undefined, placement: 'strip' })
  assert.deepEqual(adoptLegacyPlacement({ enabled: undefined }, 'drawer'), { enabled: undefined, placement: 'drawer' })
  const migrated = migrateLegacyRail([{ id: 'home', type: 'key', label: 'Home', enabled: false }])
  assert.equal(ids(migrated, 'strip').includes('home'), true)
})

test('the editing-helper catalog migration still reorders old saved rails once', () => {
  const migrated = migrateLegacyRail([{ id: 'paste', type: 'action', action: 'paste', label: 'Paste' }])
  const strip = ids(migrated, 'strip')
  const down = strip.indexOf('down')
  assert.deepEqual(strip.slice(down + 1, down + 5), ['markdownDivider', 'markdownCodeFence', 'ctrlU', 'restoreInput'])
  assert.equal(strip.at(-1), 'attach')
})

// --- storage ---------------------------------------------------------------

test('a project override fully replaces the global config; other projects keep global', () => {
  const custom = defaultRailConfig()
  custom.layouts.desktop.strip = [{ id: 'r1', items: ['paste'] }]
  const blob = writeRailConfigBlob(undefined, custom, 'proj-a')
  assert.deepEqual(ids(railConfigFromBlob(blob, 'proj-a'), 'strip'), ['paste'])
  assert.deepEqual(ids(railConfigFromBlob(blob, 'proj-b'), 'strip'), ids(defaultRailConfig(), 'strip'))
  assert.equal(railHasProjectOverride(blob, 'proj-a'), true)
  assert.equal(railHasProjectOverride(blob, 'proj-b'), false)
})

test('writing the global config leaves project overrides intact', () => {
  const project = defaultRailConfig()
  project.layouts.desktop.strip = [{ id: 'r1', items: ['esc'] }]
  const global = defaultRailConfig()
  global.layouts.desktop.strip = [{ id: 'g1', items: ['paste'] }]
  const blob = writeRailConfigBlob(writeRailConfigBlob(undefined, project, 'proj-a'), global)
  assert.deepEqual(ids(railConfigFromBlob(blob, 'proj-a'), 'strip'), ['esc'])
  assert.deepEqual(ids(railConfigFromBlob(blob), 'strip'), ['paste'])
})

test('clearing a project override reverts it to the global config', () => {
  const custom = defaultRailConfig()
  custom.layouts.desktop.strip = [{ id: 'r1', items: ['esc'] }]
  const cleared = clearProjectRailBlob(writeRailConfigBlob(undefined, custom, 'proj-a'), 'proj-a')
  assert.equal(railHasProjectOverride(cleared, 'proj-a'), false)
  assert.deepEqual(ids(railConfigFromBlob(cleared, 'proj-a'), 'strip'), ids(defaultRailConfig(), 'strip'))
})

test('a v2 project panel delta migrates into the project rail', () => {
  // The overlay semantics live in railScope.test.ts; this fixes the storage
  // shape: `mode: 'delta'` in a project slot resolves as global-plus-additions
  // wherever `railConfigFromBlob` is the reader (panes, drawer, voice).
  const blob = {
    projects: {
      'proj-a': {
        mode: 'delta',
        items: [{ id: 'proj:skill:ship', type: 'skill', label: 'ship', text: 'ship' }],
        layouts: { desktop: { panel: [{ id: 'proj-row', label: 'Project', items: ['proj:skill:ship'] }] } },
      },
    },
  } as unknown as RailBlob
  const effective = railConfigFromBlob(blob, 'proj-a')
  assert.deepEqual(ids(effective, 'strip'), [...ids(defaultRailConfig(), 'strip'), 'proj:skill:ship'])
  // Global and other projects never see the addition.
  assert.equal(ids(railConfigFromBlob(blob), 'strip').includes('proj:skill:ship'), false)
  assert.equal(ids(railConfigFromBlob(blob, 'proj-b'), 'strip').includes('proj:skill:ship'), false)
})

test('a pre-layout blob is migrated per scope, by shape rather than by version', () => {
  // A global save can be rewritten in the new shape while a project override is
  // still the old array, so each scope has to be detected on its own.
  const blob = {
    items: [{ id: 'esc', type: 'key', label: 'Esc', placement: 'strip' }] as LegacyRailItem[],
    projects: { 'proj-a': [{ id: 'paste', type: 'action', action: 'paste', label: 'Paste', placement: 'strip' }] as LegacyRailItem[] },
  }
  assert.equal(ids(railConfigFromBlob(blob), 'strip')[0], 'esc')
  assert.equal(ids(railConfigFromBlob(blob, 'proj-a'), 'strip')[0], 'paste')
  const upgraded = writeRailConfigBlob(blob, defaultRailConfig())
  assert.equal(upgraded.version, 3)
  assert.equal(ids(railConfigFromBlob(upgraded, 'proj-a'), 'strip')[0], 'paste')
})

test('an absent or junk blob resolves to the shipped default', () => {
  assert.deepEqual(ids(railConfigFromBlob(undefined), 'strip'), ids(defaultRailConfig(), 'strip'))
  assert.deepEqual(ids(normalizeRailConfig(null), 'strip'), ids(defaultRailConfig(), 'strip'))
  assert.deepEqual(ids(normalizeRailConfig('nonsense'), 'strip'), ids(defaultRailConfig(), 'strip'))
})

// --- payloads --------------------------------------------------------------

test('skill payload is backend-aware; slash is literal both ways', () => {
  const skill: RailItem = { id: 's', type: 'skill', label: 's', text: 'commit' }
  assert.equal(railPayload(skill, 'claude'), '/commit')
  assert.equal(railPayload(skill, 'codex'), '$commit')
  const slash: RailItem = { id: 'n', type: 'slash', label: 'n', text: 'new' }
  assert.equal(railPayload(slash, 'claude'), '/new')
  assert.equal(railPayload(slash, 'codex'), '/new')
})

test('skill/slash payload tolerates a leading sigil in the stored name', () => {
  assert.equal(railPayload({ id: 's', type: 'skill', label: 's', text: '/commit' }, 'codex'), '$commit')
  assert.equal(railPayload({ id: 'n', type: 'slash', label: 'n', text: '/new' }, 'claude'), '/new')
})

test('text payload is passed through verbatim', () => {
  assert.equal(railPayload({ id: 't', type: 'text', label: 't', text: 'hello world' }, 'claude'), 'hello world')
})
