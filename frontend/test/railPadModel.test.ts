import assert from 'node:assert/strict'
import test from 'node:test'

import {
  BUILTIN_RAIL,
  CARDINAL_PAD_DIRECTIONS,
  DEFAULT_RAIL_ORDER,
  DIAGONAL_PAD_DIRECTIONS,
  defaultPadTriggerMode,
  defaultRailConfig,
  normalizeRailConfig,
  normalizeRailPad,
  padDirectionDescends,
  padDirections,
  padSlotKeys,
  railConfigFromBlob,
  railPadSlotItemIds,
  railPadSlotMode,
  writeRailConfigBlob,
  type RailItem,
} from '../src/commandRail.ts'
import { setRailPadOrientation, updateRailPadSlot } from '../src/railLayout.ts'
import { isRepeatableRailKey } from '../src/railKeyRepeat.ts'
import { resolveRailVoiceEntries } from '../src/railVoice.ts'

const padOf = (config: { items: RailItem[] }, id: string): RailItem =>
  config.items.find(item => item.id === id)!

test('an orientation carves the circle four ways, plus a centre', () => {
  assert.deepEqual(padDirections('cardinal'), CARDINAL_PAD_DIRECTIONS)
  assert.deepEqual(padDirections('diagonal'), DIAGONAL_PAD_DIRECTIONS)
  assert.equal(padSlotKeys('cardinal').length, 5)
  assert.equal(padSlotKeys('diagonal').length, 5)
  assert.equal(padSlotKeys('diagonal').at(-1), 'center')
  // Four, never eight: the two orientations are alternatives, not a union.
  assert.equal(new Set([...CARDINAL_PAD_DIRECTIONS, ...DIAGONAL_PAD_DIRECTIONS]).size, 8)
})

test('the descending directions are exactly the ones that spend room below the finger', () => {
  assert.deepEqual(
    [...CARDINAL_PAD_DIRECTIONS, ...DIAGONAL_PAD_DIRECTIONS].filter(padDirectionDescends),
    ['down', 'downRight', 'downLeft'],
  )
})

test('a slot mode defaults from the action, so a binding arrives already safe', () => {
  const arrow = BUILTIN_RAIL.find(item => item.id === 'up')
  const home = BUILTIN_RAIL.find(item => item.id === 'home')
  const kill = BUILTIN_RAIL.find(item => item.id === 'endSession')
  assert.equal(defaultPadTriggerMode(arrow), 'enter-repeat')
  assert.equal(defaultPadTriggerMode(home), 'enter')
  assert.equal(defaultPadTriggerMode(kill), 'release', 'anything destructive waits for the lift')
  assert.equal(defaultPadTriggerMode(undefined), 'enter')
  // An explicit mode on the binding always wins, and a nonsense one falls back.
  assert.equal(railPadSlotMode({ item: 'up', mode: 'release' }, arrow), 'release')
  assert.equal(railPadSlotMode({ item: 'up', mode: 'nope' as never }, arrow), 'enter-repeat')
})

test('the repeatable flag is one fact: the standalone chip and the pad slot read it together', () => {
  for (const id of ['up', 'down', 'left', 'right']) {
    assert.equal(isRepeatableRailKey(id), true)
    assert.equal(defaultPadTriggerMode(BUILTIN_RAIL.find(item => item.id === id)), 'enter-repeat')
  }
  for (const id of ['enter', 'tab', 'ctrlC', 'home', 'padArrows']) {
    assert.equal(isRepeatableRailKey(id), false)
    assert.notEqual(defaultPadTriggerMode(BUILTIN_RAIL.find(item => item.id === id)), 'enter-repeat')
  }
})

test('the four shipped pads hold what they say they hold', () => {
  const config = defaultRailConfig()
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'padArrows')), ['up', 'right', 'down', 'left'])
  // NW Home, NE Ctrl+Home, SE Ctrl+End, SW End: left/right is which end of the line,
  // up/down is line or document. Reported in `padDirections` order, not reading order.
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'padJump')), ['home', 'ctrlHome', 'ctrlEnd', 'end'])
  assert.equal(padOf(config, 'padJump').pad?.orientation, 'diagonal')
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'padCopy')), ['copyInput', 'copyResume', 'copyReply'])
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'padPickers')), ['skills', 'prompts', 'actionsDrawer', 'clipboardHistory'])
  assert.deepEqual(railPadSlotItemIds(BUILTIN_RAIL.find(item => item.id === 'esc')!), [])
})

test('every shipped pad slot names an action that exists, and no pad holds a pad', () => {
  const known = new Map(BUILTIN_RAIL.map(item => [item.id, item]))
  for (const item of BUILTIN_RAIL) {
    for (const id of railPadSlotItemIds(item)) {
      const target = known.get(id)
      assert.ok(target, `${item.id} names a missing action ${id}`)
      assert.notEqual(target.type, 'pad', `${item.id} holds a pad`)
    }
  }
})

test('the default order names only real built-ins, and every entry is placed', () => {
  const known = new Set(BUILTIN_RAIL.map(item => item.id))
  for (const id of DEFAULT_RAIL_ORDER) assert.ok(known.has(id), `${id} is not a built-in`)
  assert.equal(new Set(DEFAULT_RAIL_ORDER).size, DEFAULT_RAIL_ORDER.length, 'no duplicates')
  assert.deepEqual(defaultRailConfig().layouts.desktop.strip[0].items, [...DEFAULT_RAIL_ORDER])
})

test('a stored pad is canonicalized, so an untouched copy still equals the shipped one', () => {
  // The failure this prevents: a fork stores a copy of a shipped pad, reattach asks whether
  // the copy still equals the definition, and a slot map written in reading order compares
  // unequal to one rebuilt in canonical order - reporting an edit nobody made.
  const written = normalizeRailPad({
    orientation: 'cardinal',
    slots: { left: { item: 'left' }, up: { item: 'up' }, right: { item: 'right' }, down: { item: 'down' } },
  })
  assert.deepEqual(Object.keys(written.slots), ['up', 'right', 'down', 'left'])
  assert.deepEqual(written, normalizeRailPad(written))
  for (const item of BUILTIN_RAIL) {
    if (item.type !== 'pad') continue
    assert.deepEqual(item.pad, normalizeRailPad(item.pad), `${item.id} is not stored canonically`)
  }
})

test('normalization keeps an unresolvable slot and drops an unreachable one', () => {
  const pad = normalizeRailPad({
    orientation: 'cardinal',
    // Kept: the action may simply be missing from *this* resolution.
    slots: {
      up: { item: 'custom:skill:ship' },
      // Dropped: `upLeft` cannot be reached at all on a cardinal pad.
      upLeft: { item: 'esc' },
      // Dropped: not a slot at all.
      sideways: { item: 'esc' },
      right: { item: 'nope', mode: 'garbage' },
      down: 'not an object',
      left: { item: '' },
    },
  })
  assert.deepEqual(Object.keys(pad.slots), ['up', 'right'])
  assert.deepEqual(pad.slots.up, { item: 'custom:skill:ship' })
  assert.deepEqual(pad.slots.right, { item: 'nope' }, 'a bad mode falls back to the default rather than sticking')
  // A retired id inside a slot migrates like any other stored id.
  const migrated = normalizeRailPad({ orientation: 'cardinal', slots: { up: { item: 'clearInput' } } })
  assert.equal(migrated.slots.up?.item, 'ctrlU')
  // An unrecognised orientation is cardinal, never a crash.
  assert.equal(normalizeRailPad({ orientation: 'sideways' }).orientation, 'cardinal')
  assert.equal(normalizeRailPad(undefined).orientation, 'cardinal')
})

test('a saved pad override survives a round trip through the blob', () => {
  const config = defaultRailConfig()
  const next = updateRailPadSlot(config, 'padArrows', 'up', { item: 'esc', mode: 'release' })
  const blob = writeRailConfigBlob(undefined, next)
  const reloaded = railConfigFromBlob(blob)
  assert.deepEqual(padOf(reloaded, 'padArrows').pad?.slots.up, { item: 'esc', mode: 'release' })
  // The rest of the pad is untouched, and the shipped definition still supplies it.
  assert.equal(padOf(reloaded, 'padArrows').pad?.slots.down?.item, 'down')
})

test('clearing a slot leaves a dead direction rather than shuffling the others', () => {
  const cleared = updateRailPadSlot(defaultRailConfig(), 'padArrows', 'left', { item: null })
  const pad = padOf(cleared, 'padArrows').pad!
  assert.equal(pad.slots.left, undefined)
  assert.equal(pad.slots.right?.item, 'right', 'right did not slide into the gap')
  assert.deepEqual(railPadSlotItemIds(padOf(cleared, 'padArrows')), ['up', 'right', 'down'])
})

test('a pad slot edit refuses anything that is not a pad', () => {
  const config = defaultRailConfig()
  assert.equal(updateRailPadSlot(config, 'esc', 'up', { item: 'enter' }), config)
  assert.equal(updateRailPadSlot(config, 'nope', 'up', { item: 'enter' }), config)
  assert.equal(setRailPadOrientation(config, 'esc', 'diagonal'), config)
  assert.equal(setRailPadOrientation(config, 'padArrows', 'cardinal'), config, 'already there')
})

test('turning a pad carries its four bindings across rather than dropping them', () => {
  const turned = setRailPadOrientation(defaultRailConfig(), 'padArrows', 'diagonal')
  const pad = padOf(turned, 'padArrows').pad!
  assert.equal(pad.orientation, 'diagonal')
  // Position for position, in each orientation's own order.
  assert.deepEqual(railPadSlotItemIds(padOf(turned, 'padArrows')), ['up', 'right', 'down', 'left'])
  assert.equal(pad.slots.upLeft?.item, 'up')
  assert.equal(pad.slots.upRight?.item, 'right')
  // And back again, unchanged.
  const back = setRailPadOrientation(turned, 'padArrows', 'cardinal')
  assert.deepEqual(padOf(back, 'padArrows').pad, padOf(defaultRailConfig(), 'padArrows').pad)
})

test('a custom pad survives normalization; a saved pad naming no slots is inert, not dropped', () => {
  const saved = {
    version: 3,
    items: [
      ...defaultRailConfig().items,
      { id: 'custom:pad:mine', type: 'pad', label: 'Mine', pad: { orientation: 'diagonal', slots: { upLeft: { item: 'esc' } } } },
      { id: 'custom:pad:empty', type: 'pad', label: 'Empty' },
    ],
    layouts: defaultRailConfig().layouts,
  }
  const config = normalizeRailConfig(saved)
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'custom:pad:mine')), ['esc'])
  assert.equal(padOf(config, 'custom:pad:mine').pad?.orientation, 'diagonal')
  // A half-built pad is what the editor produces the moment it is created, so it has to
  // load rather than vanish on the next read.
  assert.deepEqual(padOf(config, 'custom:pad:empty').pad, { orientation: 'cardinal', slots: {} })
})

test('a padded key keeps its spoken alias, because a pad is a placement', () => {
  const config = defaultRailConfig()
  const phrases = resolveRailVoiceEntries(config, { device: 'desktop', backend: 'claude' })
    .flatMap(entry => entry.phrases)
  // `up` is only reachable through `padArrows` on the default rail. Folding it into a pad
  // must not silently retire "press up" as a spoken command.
  assert.ok(phrases.includes('press up'), 'the arrow inside the pad is still voiced')
  assert.ok(phrases.includes('home key'))
  // The pad chip itself is a container with no behaviour, so it is never voiced.
  const voicedIds = resolveRailVoiceEntries(config, { device: 'desktop', backend: 'claude' }).map(entry => entry.item.id)
  assert.equal(voicedIds.includes('padArrows'), false)
  assert.equal(voicedIds.includes('padJump'), false)
})

test('an unplaced pad does not voice what it holds', () => {
  const config = defaultRailConfig()
  config.layouts.desktop.strip[0].items = config.layouts.desktop.strip[0].items.filter(id => id !== 'padArrows')
  const phrases = resolveRailVoiceEntries(config, { device: 'desktop', backend: 'claude' })
    .flatMap(entry => entry.phrases)
  assert.equal(phrases.includes('press up'), false)
})
