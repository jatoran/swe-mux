import assert from 'node:assert/strict'
import test from 'node:test'
import {
  BUILTIN_RAIL, adoptLegacyPlacement, clearProjectRailBlob, defaultRailConfig, itemDefaultSurface,
  mergeRailCatalog, migrateLegacyRail, normalizeRailConfig, railConfigFromBlob, railHasProjectOverride,
  railItemVisible, railPayload, resolveRailRows, writeRailConfigBlob,
  type LegacyRailItem, type RailConfig, type RailContext, type RailItem,
} from '../src/commandRail.ts'
import { AGENT_NEWLINE } from '../src/terminalKeys.ts'

const CLAUDE: RailContext = { device: 'desktop', backend: 'claude' }
const ids = (config: RailConfig, surface: 'strip' | 'panel', ctx: RailContext = CLAUDE): string[] =>
  resolveRailRows(config, surface, ctx).flatMap(row => row.entries.map(entry => entry.item.id))

test('the default layout seeds one row per surface, identical on both devices', () => {
  const config = defaultRailConfig()
  for (const device of ['desktop', 'mobile'] as const) {
    assert.equal(config.layouts[device].strip.length, 1)
    assert.equal(config.layouts[device].panel.length, 1)
  }
  assert.deepEqual(config.layouts.desktop.strip[0].items, config.layouts.mobile.strip[0].items)
  assert.deepEqual(config.layouts.desktop.panel[0].items, config.layouts.mobile.panel[0].items)
  // Identical contents, but separate rows with separate ids: the two devices are
  // never the same row, so editing one can never move the other.
  assert.notEqual(config.layouts.desktop.strip[0].id, config.layouts.mobile.strip[0].id)
})

test('default rail groups editing helpers after Down and ends with Attach', () => {
  assert.deepEqual(ids(defaultRailConfig(), 'strip'), [
    'relaunch', 'copyReply', 'copyResume', 'branch', 'paste', 'clipboardHistory', 'actionsDrawer', 'kbdToggle',
    'esc', 'enter', 'tab', 'ctrlC', 'up', 'down',
    'markdownDivider', 'markdownCodeFence', 'clearInput', 'restoreInput',
    'left', 'right', 'attach',
  ])
})

test('the panel seeds the long tail the strip has no room for', () => {
  const config = defaultRailConfig()
  assert.deepEqual(ids(config, 'panel'), ['home', 'end', 'ctrlHome', 'ctrlEnd', 'newline', 'rewind', 'endSession'])
  // Every built-in lands in exactly one surface.
  assert.deepEqual(
    [...ids(config, 'strip'), ...ids(config, 'panel')].sort(),
    BUILTIN_RAIL.filter(item => railItemVisible(item, 'claude')).map(item => item.id).sort(),
  )
})

test('desktop and mobile layouts are edited independently', () => {
  const config = defaultRailConfig()
  config.layouts.mobile.strip[0].items = ['esc', 'enter']
  assert.deepEqual(ids(config, 'strip', { device: 'mobile', backend: 'claude' }), ['esc', 'enter'])
  // The desktop layout is untouched by the mobile edit.
  assert.equal(ids(config, 'strip').length, 21)
})

test('an item placed in no row is simply absent from that device', () => {
  const config = defaultRailConfig()
  config.layouts.mobile.strip[0].items = []
  config.layouts.mobile.panel[0].items = []
  assert.deepEqual(resolveRailRows(config, 'strip', { device: 'mobile', backend: 'claude' }), [])
  assert.deepEqual(resolveRailRows(config, 'panel', { device: 'mobile', backend: 'claude' }), [])
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

test('end session ships in the panel for every backend, never on the strip', () => {
  // It is the one destructive built-in, so it must not default next to the arrow keys.
  const config = defaultRailConfig()
  for (const backend of ['claude', 'codex', 'shell'] as const) {
    const ctx = { device: 'mobile', backend } as const
    assert.equal(ids(config, 'strip', ctx).includes('endSession'), false)
    assert.equal(ids(config, 'panel', ctx).includes('endSession'), true)
  }
})

test('the drawer newline and editing helpers use non-submitting agent keys', () => {
  const divider = BUILTIN_RAIL.find(item => item.id === 'markdownDivider')
  const codeFence = BUILTIN_RAIL.find(item => item.id === 'markdownCodeFence')
  assert.equal(BUILTIN_RAIL.find(item => item.id === 'newline')?.bytes, AGENT_NEWLINE)
  assert.equal(divider?.bytes, `${AGENT_NEWLINE}${AGENT_NEWLINE}---${AGENT_NEWLINE}${AGENT_NEWLINE}`)
  assert.equal(codeFence?.bytes, `${AGENT_NEWLINE}${AGENT_NEWLINE}\`\`\`${AGENT_NEWLINE}`)
  assert.equal(BUILTIN_RAIL.find(item => item.id === 'clearInput')?.bytes, '\x15')
  assert.equal(BUILTIN_RAIL.find(item => item.id === 'restoreInput')?.bytes, '\x19')
})

test('a newly shipped built-in is placed, not merely catalogued', () => {
  // Placement is the only thing that makes a button visible, so a catalog-only
  // append would leave every existing user unable to find a new command.
  const saved = defaultRailConfig()
  saved.items = saved.items.filter(item => item.id !== 'rewind')
  saved.layouts.desktop.panel[0].items = saved.layouts.desktop.panel[0].items.filter(id => id !== 'rewind')
  saved.layouts.mobile.panel[0].items = saved.layouts.mobile.panel[0].items.filter(id => id !== 'rewind')
  const config = normalizeRailConfig(saved)
  assert.equal(config.items.some(item => item.id === 'rewind'), true)
  for (const device of ['desktop', 'mobile'] as const) {
    assert.equal(config.layouts[device].panel.some(row => row.items.includes('rewind')), true)
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

test('normalization drops dangling references and unknown custom types', () => {
  const config = normalizeRailConfig({
    items: [
      { id: 'esc', type: 'key', label: 'Esc' },
      { id: 'custom:skill:commit', type: 'skill', label: 'commit', text: 'commit' },
      { id: 'custom:evil', type: 'action', action: 'endSession', label: 'boom' },
    ],
    layouts: {
      desktop: { strip: [{ id: 'r1', items: ['esc', 'ghost', 'custom:evil', 'custom:skill:commit'] }], panel: [] },
      mobile: {},
    },
  })
  assert.equal(config.items.some(item => item.id === 'custom:evil'), false)
  assert.deepEqual(config.layouts.desktop.strip[0].items.slice(0, 2), ['esc', 'custom:skill:commit'])
  // Every surface keeps a row so there is always somewhere to drop.
  assert.equal(config.layouts.desktop.panel.length, 1)
  assert.equal(config.layouts.mobile.strip.length, 1)
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

test('mergeRailCatalog reports which built-ins it had to add', () => {
  const { items, addedBuiltins } = mergeRailCatalog([{ id: 'esc', type: 'key', label: 'Esc' }])
  assert.equal(items.length, BUILTIN_RAIL.length)
  assert.equal(addedBuiltins.some(item => item.id === 'esc'), false)
  assert.equal(addedBuiltins.length, BUILTIN_RAIL.length - 1)
})

// --- migration from the pre-layout format ----------------------------------

test('a pre-layout save becomes four rows that render exactly as it did', () => {
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
  // 'both' lands in each surface rather than needing a placement concept.
  assert.equal(desktopStrip.includes('rewind'), true)
  assert.equal(ids(config, 'panel').includes('rewind'), true)
  // An entry that predates `placement` and says "off" meant "not on the strip", so
  // it keeps rendering in the panel exactly as it did before the upgrade.
  assert.equal(ids(config, 'panel').includes('custom:skill:commit:0'), true)
  assert.equal(desktopStrip.includes('custom:skill:commit:0'), false)
  // An explicit placement plus "off" was a genuine hide, and stays placed nowhere —
  // which is the only way to be hidden now.
  assert.equal(config.items.some(item => item.id === 'custom:text:hidden:0'), true)
  for (const device of ['desktop', 'mobile'] as const) {
    for (const surface of ['strip', 'panel'] as const) {
      assert.equal(config.layouts[device][surface][0].items.includes('custom:text:hidden:0'), false)
    }
  }
  // No item carries a legacy position field into the new catalog.
  for (const item of config.items) {
    assert.equal('placement' in item, false)
    assert.equal('platforms' in item, false)
    assert.equal('enabled' in item, false)
  }
})

test('an untouched pre-layout save migrates to the shipped default', () => {
  const migrated = migrateLegacyRail(BUILTIN_RAIL.map(item => ({
    ...item,
    placement: itemDefaultSurface(item) === 'panel' ? 'drawer' : 'strip',
  })) as LegacyRailItem[])
  const fresh = defaultRailConfig()
  for (const device of ['desktop', 'mobile'] as const) {
    assert.deepEqual(migrated.layouts[device].strip[0].items, fresh.layouts[device].strip[0].items)
    assert.deepEqual(migrated.layouts[device].panel[0].items, fresh.layouts[device].panel[0].items)
  }
})

test('a save that predates placement reads "off" as "not on the strip"', () => {
  assert.deepEqual(adoptLegacyPlacement({ enabled: false }), { enabled: undefined, placement: 'drawer' })
  assert.deepEqual(adoptLegacyPlacement({ enabled: false, placement: 'strip' }), { enabled: false, placement: 'strip' })
  assert.deepEqual(adoptLegacyPlacement({ enabled: undefined }), { enabled: undefined, placement: 'strip' })
  assert.deepEqual(adoptLegacyPlacement({ enabled: undefined }, 'drawer'), { enabled: undefined, placement: 'drawer' })
  const migrated = migrateLegacyRail([{ id: 'home', type: 'key', label: 'Home', enabled: false }])
  assert.equal(ids(migrated, 'panel').includes('home'), true)
  assert.equal(ids(migrated, 'strip').includes('home'), false)
})

test('the editing-helper catalog migration still reorders old saved rails once', () => {
  const migrated = migrateLegacyRail([{ id: 'paste', type: 'action', action: 'paste', label: 'Paste' }])
  const strip = ids(migrated, 'strip')
  const down = strip.indexOf('down')
  assert.deepEqual(strip.slice(down + 1, down + 5), ['markdownDivider', 'markdownCodeFence', 'clearInput', 'restoreInput'])
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
  assert.equal(upgraded.version, 2)
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
