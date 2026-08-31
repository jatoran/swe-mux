import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  BUILTIN_RAIL,
  DEFAULT_RAIL_ROWS,
  RAIL_PAD_MAX_RINGS,
  RAIL_PAD_MAX_WEDGES,
  defaultPadTriggerMode,
  defaultRailConfig,
  normalizeRailConfig,
  normalizeRailPad,
  padRingCount,
  padSlotKey,
  padSlotKeys,
  padWedgeCount,
  padWedgeName,
  padWedgeUnit,
  parsePadSlotKey,
  railConfigFromBlob,
  railPadSlotItemIds,
  railPadSlotLabel,
  railPadBanded,
  railPadSlotMode,
  writeRailConfigBlob,
  type RailItem,
} from '../src/commandRail.ts'
import { setRailPadShape, updateRailPadSlot } from '../src/railLayout.ts'
import { isRepeatableRailKey } from '../src/railKeyRepeat.ts'
import { resolveRailVoiceEntries } from '../src/railVoice.ts'

const padOf = (config: { items: RailItem[] }, id: string): RailItem =>
  config.items.find(item => item.id === id)!

test('a slot key is a position, and nothing else is', () => {
  assert.deepEqual(parsePadSlotKey(padSlotKey(1, 2)), { ring: 1, wedge: 2 })
  assert.equal(parsePadSlotKey('center'), null, 'the retired centre is not a position')
  assert.equal(parsePadSlotKey('up'), null, 'the old compass names are not positions')
})

test('a pad offers exactly wedges times rings, and no non-positional slot', () => {
  for (let wedges = 1; wedges <= RAIL_PAD_MAX_WEDGES; wedges += 1) {
    for (let rings = 1; rings <= RAIL_PAD_MAX_RINGS; rings += 1) {
      const keys = padSlotKeys({ wedges, rings, slots: {} })
      assert.equal(keys.length, wedges * rings)
      assert.ok(keys.every(key => !!parsePadSlotKey(key)), 'every slot is a wedge')
      // Wedge-major within a ring, which is the order `railPadResolve` and the dial both
      // index into.
      assert.equal(keys[0], padSlotKey(0, 0))
      if (rings > 1) assert.equal(keys[wedges], padSlotKey(1, 0))
    }
  }
})

test('a stored centre binding is carried onto the first free wedge, not dropped', () => {
  // Dropping is what a slot the operator *shrinks* out of existence gets, and that is right
  // because they asked for it. Nobody asked for the centre to go, so the same silence would
  // just be a binding that vanished between two builds.
  const carried = normalizeRailPad({
    wedges: 3,
    rings: 1,
    slots: { '0:1': { item: 'up' }, center: { item: 'down', mode: 'release' } },
  })
  assert.deepEqual(carried.slots['0:0'], { item: 'down', mode: 'release' }, 'first free wedge, mode intact')
  assert.equal(carried.slots['0:1']?.item, 'up', 'and nothing already bound moved')
  assert.equal(carried.slots.center, undefined)

  // Idempotent, which fork-equality depends on: a second pass has no centre left to carry.
  assert.deepEqual(normalizeRailPad(carried), carried)

  // A pad with every wedge taken has nowhere to put it, which is the honest end of the rule.
  const full = normalizeRailPad({
    wedges: 1,
    rings: 1,
    slots: { '0:0': { item: 'up' }, center: { item: 'down' } },
  })
  assert.deepEqual(railPadSlotItemIds({ id: 'x', type: 'pad', label: 'x', pad: full }), ['up'])
})

test('the counts are clamped, so a stored nonsense shape is still a usable pad', () => {
  assert.equal(padWedgeCount({ wedges: 99, rings: 1, slots: {} }), RAIL_PAD_MAX_WEDGES)
  assert.equal(padWedgeCount({ wedges: 0, rings: 1, slots: {} }), 1)
  assert.equal(padWedgeCount(undefined), 3)
  assert.equal(padRingCount({ wedges: 3, rings: 9, slots: {} }), RAIL_PAD_MAX_RINGS)
  assert.equal(padRingCount(undefined), 1)
})

test('no wedge points downward, at any count', () => {
  // The whole reason the fan gave up its lower half: the rail sits on the bottom edge of a
  // phone, so a downward wedge is drawn off the glass and dragged where a thumb cannot go.
  for (let wedges = 1; wedges <= RAIL_PAD_MAX_WEDGES; wedges += 1) {
    for (let wedge = 0; wedge < wedges; wedge += 1) {
      assert.ok(padWedgeUnit(wedge, wedges).y <= 0.001, `${wedge}/${wedges} points down`)
    }
  }
})

test('a wedge is named for where it points, not for its index', () => {
  // Three wedges and five wedges do not have the same "up-left", which is exactly why the
  // name is derived rather than tabulated.
  assert.equal(padWedgeName(1, 3), 'Up')
  assert.equal(padWedgeName(0, 3), 'Right')
  assert.equal(padWedgeName(2, 3), 'Left')
  assert.equal(padWedgeName(0, 1), 'Up', 'a single wedge is the whole fan, so it is Up')
  assert.equal(padWedgeName(1, 2, 1), 'Up-left, far')
})

test('a slot mode defaults from the action, so a binding arrives already safe', () => {
  const arrow = BUILTIN_RAIL.find(item => item.id === 'up')
  const home = BUILTIN_RAIL.find(item => item.id === 'home')
  const kill = BUILTIN_RAIL.find(item => item.id === 'endSession')
  // Repeat on *push-out* rather than on dwell: `enter-repeat` starts after 350ms anywhere in
  // the wedge, so a thumb that hesitates begins spamming without being asked. Distance says
  // it deliberately, which is the rule the rest of the gesture already runs on.
  assert.equal(defaultPadTriggerMode(arrow), 'enter-repeat-far')
  assert.equal(defaultPadTriggerMode(home), 'enter')
  assert.equal(defaultPadTriggerMode(kill), 'release', 'anything destructive waits for the lift')
  assert.equal(defaultPadTriggerMode(undefined), 'enter')
  assert.equal(railPadSlotMode({ item: 'up', mode: 'release' }, arrow), 'release')
  assert.equal(railPadSlotMode({ item: 'up', mode: 'nope' as never }, arrow), 'enter-repeat-far')
  // Hold-anywhere is still there for anyone who prefers it.
  assert.equal(railPadSlotMode({ item: 'up', mode: 'enter-repeat' }, arrow), 'enter-repeat')
})

test('repeat-on-push-out is refused where the band already means something else', () => {
  // On a two-ring pad the outer band is a different slot, so there is no room for it to also
  // mean "repeat this one". It resolves to a plain `enter` rather than silently repeating on
  // a boundary that belongs to a neighbour.
  const arrow = BUILTIN_RAIL.find(item => item.id === 'up')
  assert.equal(railPadSlotMode({ item: 'up', mode: 'enter-repeat-far' }, arrow, 1), 'enter-repeat-far')
  assert.equal(railPadSlotMode({ item: 'up', mode: 'enter-repeat-far' }, arrow, 2), 'enter')
  assert.equal(defaultPadTriggerMode(arrow, 2), 'release')
})

test('a pad is banded when a ring needs it, or when a slot streams beyond it', () => {
  assert.equal(railPadBanded(1, ['enter', 'release']), false)
  assert.equal(railPadBanded(1, ['enter', 'enter-repeat-far']), true)
  assert.equal(railPadBanded(2, ['release']), true)
  assert.equal(railPadBanded(1, []), false)
})

test('a ringed pad commits on release, because its near ring is transit', () => {
  // Geometry, not taste: reaching the far ring means crossing the near one, so a near slot
  // firing on entry would fire every time somebody went past it - and again on the way back
  // in. A ring you must pass through cannot be a fire-on-entry target.
  const arrow = BUILTIN_RAIL.find(item => item.id === 'up')
  assert.equal(defaultPadTriggerMode(arrow, 2), 'release')
  assert.equal(defaultPadTriggerMode(undefined, 2), 'release')
  // A one-ring pad has no transit and keeps entry-firing, which is what makes it fast - and
  // is why the shipped pads grew a fourth *wedge* rather than a second ring.
  assert.equal(defaultPadTriggerMode(arrow, 1), 'enter-repeat-far')
  assert.equal(railPadSlotMode({ item: 'up', mode: 'enter' }, arrow, 2), 'enter')
  assert.equal(railPadSlotMode({ item: 'up' }, arrow, 2), 'release')
})

test('every shipped pad is one ring, so every shipped wedge fires as you cross in', () => {
  const config = defaultRailConfig()
  for (const item of config.items) {
    if (item.type !== 'pad') continue
    assert.equal(padRingCount(item.pad), 1, `${item.id} would make its own near ring transit`)
    for (const key of padSlotKeys(item.pad)) {
      const binding = item.pad?.slots[key]
      if (!binding) continue
      const target = config.items.find(entry => entry.id === binding.item)
      assert.notEqual(railPadSlotMode(binding, target, 1), 'release', `${item.id}.${key}`)
    }
  }
})

test('the repeatable flag is one fact: the standalone chip and the pad slot read it together', () => {
  const repeats = (mode: string) => mode === 'enter-repeat' || mode === 'enter-repeat-far'
  for (const id of ['up', 'down', 'left', 'right']) {
    assert.equal(isRepeatableRailKey(id), true)
    assert.ok(repeats(defaultPadTriggerMode(BUILTIN_RAIL.find(item => item.id === id))), id)
  }
  for (const id of ['enter', 'tab', 'ctrlC', 'home', 'padArrows']) {
    assert.equal(isRepeatableRailKey(id), false)
    assert.equal(repeats(defaultPadTriggerMode(BUILTIN_RAIL.find(item => item.id === id))), false, id)
  }
})

test('the four shipped pads hold what they say they hold', () => {
  const config = defaultRailConfig()
  // Four wedges, reading left to right as left, up, down, right: the ends are the horizontal
  // pair and the vertical pair sits between them, with Down in the upper-right wedge beside
  // Up. Down held the centre before the centre was retired.
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'padArrows')), ['right', 'down', 'up', 'left'])
  assert.equal(padWedgeCount(padOf(config, 'padArrows').pad), 4)
  assert.equal(padOf(config, 'padArrows').pad?.slots.center, undefined)
  // Four wedges, reading left to right as document-start, line-start, line-end, document-end.
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'padJump')), ['ctrlEnd', 'end', 'home', 'ctrlHome'])
  assert.equal(padWedgeCount(padOf(config, 'padJump').pad), 4)
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'padCopy')), ['copyResume', 'copyInput', 'copyReply'])
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'padPickers')), ['actionsDrawer', 'prompts', 'skills', 'clipboardHistory'])
  assert.deepEqual(railPadSlotItemIds(BUILTIN_RAIL.find(item => item.id === 'esc')!), [])
})

test('a wedge label is a label, never a title', () => {
  // Shipped live once: the fallback chain reached for `title` when an item had no short
  // override, so the Pick dial drew "Insert one of this session's skills", "Insert one of
  // your prompt templates" and "Open Actions temporarily" across each other.
  const config = defaultRailConfig()
  const byId = new Map(config.items.map(item => [item.id, item]))
  for (const item of config.items) {
    for (const id of railPadSlotItemIds(item)) {
      const target = byId.get(id)
      const label = railPadSlotLabel(undefined, target, id)
      assert.ok(label.length <= 14, `${item.id} → ${id} draws a ${label.length}-char label: "${label}"`)
      assert.notEqual(label, target?.title, `${item.id} → ${id} fell through to its tooltip`)
    }
  }
})

test('the pane resolves a wedge label through the helper rather than its own chain', () => {
  const pane = readFileSync(new URL('../src/TerminalPane.tsx', import.meta.url), 'utf8')
  const slots = pane.slice(pane.indexOf('const railPadSlots='), pane.indexOf('const renderRailItem='))
  assert.ok(slots.includes('railPadSlotLabel('), 'the pad slot label must come from the helper')
  assert.equal(/label\s*[:=][^\n]*view\?\.title/.test(slots), false, 'a wedge label must never fall through to a tooltip')
})

test('the wedge label chain cannot land on a sentence', () => {
  const skills = BUILTIN_RAIL.find(item => item.id === 'skills')!
  assert.equal(railPadSlotLabel(undefined, skills, 'skills'), 'Skills')
  assert.equal(railPadSlotLabel('Pick', skills, 'skills'), 'Pick', 'an explicit short override wins')
  assert.equal(railPadSlotLabel('   ', skills, 'skills'), 'Skills', 'and a blank one does not')
  assert.equal(railPadSlotLabel(undefined, undefined, 'custom:skill:ship'), 'custom:skill:ship')
})

test('every shipped pad slot names an action that exists, and no pad holds a pad', () => {
  const known = new Map(BUILTIN_RAIL.map(item => [item.id, item]))
  for (const item of BUILTIN_RAIL) {
    for (const id of railPadSlotItemIds(item)) {
      const target = known.get(id)
      assert.ok(target, `${item.id} names a missing action ${id}`)
      assert.notEqual(target?.type, 'pad', `${item.id} holds a pad`)
    }
  }
})

test('the default rows name only real built-ins, and every entry is placed', () => {
  const known = new Set(BUILTIN_RAIL.map(item => item.id))
  for (const device of ['desktop', 'mobile'] as const) {
    const flat = DEFAULT_RAIL_ROWS[device].flat()
    for (const id of flat) assert.ok(known.has(id), `${device}: ${id} is not a built-in`)
    assert.equal(new Set(flat).size, flat.length, `${device}: no duplicates across rows`)
    // Down is inside the arrows pad now, so it is not also a chip of its own.
    assert.equal(flat.includes('down'), false, device)
  }
  const layouts = defaultRailConfig().layouts
  assert.deepEqual(layouts.desktop.strip.map(row => row.items), DEFAULT_RAIL_ROWS.desktop.map(row => [...row]))
  assert.deepEqual(layouts.mobile.strip.map(row => row.items), DEFAULT_RAIL_ROWS.mobile.map(row => [...row]))
  // Row ids are fixed so an untouched layout round-trips byte-identically.
  assert.deepEqual(layouts.desktop.strip.map(row => row.id), ['desktop-strip'])
  assert.deepEqual(layouts.mobile.strip.map(row => row.id), ['mobile-strip', 'mobile-strip-2'])
  // The desktop keeps the approval verb a new user needs most; the mobile rows keep
  // the keyboard the device does not have.
  assert.ok(DEFAULT_RAIL_ROWS.desktop.flat().includes('approveOnce'))
  assert.ok(DEFAULT_RAIL_ROWS.mobile.flat().includes('kbdToggle'))
})

test('a stored pad is canonicalized, so an untouched copy still equals the shipped one', () => {
  const written = normalizeRailPad({
    wedges: 3,
    rings: 1,
    slots: { '0:2': { item: 'left' }, '0:0': { item: 'right' }, '0:1': { item: 'up' } },
  })
  assert.deepEqual(Object.keys(written.slots), ['0:0', '0:1', '0:2'])
  assert.deepEqual(written, normalizeRailPad(written))
  for (const item of BUILTIN_RAIL) {
    if (item.type !== 'pad') continue
    assert.deepEqual(item.pad, normalizeRailPad(item.pad), `${item.id} is not stored canonically`)
  }
})

test('a pre-positional save is read through its compass names, forever', () => {
  // The first shipped pads addressed slots by name. A layout is device-local, per-Project and
  // arbitrarily old, so an unrecognised key is silently a binding the operator loses - the
  // same durability rule `RETIRED_RAIL_IDS` exists for.
  const cardinal = normalizeRailPad({
    orientation: 'cardinal',
    slots: { right: { item: 'right' }, up: { item: 'up' }, left: { item: 'left' } },
  })
  assert.equal(cardinal.wedges, 3)
  assert.equal(cardinal.rings, 1)
  assert.deepEqual(cardinal.slots, { '0:0': { item: 'right' }, '0:1': { item: 'up' }, '0:2': { item: 'left' } })

  const diagonal = normalizeRailPad({
    orientation: 'diagonal',
    slots: {
      upRight: { item: 'ctrlHome' },
      upLeft: { item: 'home' },
      upRightFar: { item: 'ctrlEnd', mode: 'release' },
      upLeftFar: { item: 'end' },
    },
  })
  assert.equal(diagonal.wedges, 2)
  assert.equal(diagonal.rings, 2)
  assert.equal(diagonal.slots['1:0']?.item, 'ctrlEnd')
  assert.equal(diagonal.slots['1:0']?.mode, 'release', 'an explicit mode survives the move')
  assert.equal(diagonal.slots['0:1']?.item, 'home')
  // A stored `down` from before the fan lost its lower half has nowhere to go, which is
  // correct - there is no south to put it in.
  const dropped = normalizeRailPad({ orientation: 'cardinal', slots: { down: { item: 'down' } } })
  assert.deepEqual(dropped.slots, {})
})

test('normalization keeps an unresolvable slot and drops an unreachable one', () => {
  const pad = normalizeRailPad({
    wedges: 2,
    rings: 1,
    slots: {
      // Kept: the action may simply be missing from *this* resolution.
      '0:0': { item: 'custom:skill:ship' },
      // Dropped: no such position on a two-wedge, one-ring pad.
      '0:4': { item: 'esc' },
      '1:0': { item: 'esc' },
      sideways: { item: 'esc' },
      '0:1': { item: 'nope', mode: 'garbage' },
    },
  })
  assert.deepEqual(Object.keys(pad.slots), ['0:0', '0:1'])
  assert.deepEqual(pad.slots['0:0'], { item: 'custom:skill:ship' })
  assert.deepEqual(pad.slots['0:1'], { item: 'nope' }, 'a bad mode falls back to the default rather than sticking')
  const migrated = normalizeRailPad({ wedges: 1, rings: 1, slots: { '0:0': { item: 'clearInput' } } })
  assert.equal(migrated.slots['0:0']?.item, 'ctrlU')
  assert.equal(normalizeRailPad(undefined).wedges, 3)
})

test('a saved pad override survives a round trip through the blob', () => {
  const config = defaultRailConfig()
  const next = updateRailPadSlot(config, 'padArrows', padSlotKey(0, 1), { item: 'esc', mode: 'release' })
  const blob = writeRailConfigBlob(undefined, next)
  const reloaded = railConfigFromBlob(blob)
  assert.deepEqual(padOf(reloaded, 'padArrows').pad?.slots['0:1'], { item: 'esc', mode: 'release' })
  // The rest of the pad is untouched, and the shipped definition still supplies it.
  assert.equal(padOf(reloaded, 'padArrows').pad?.slots['0:3']?.item, 'left')
})

test('clearing a slot leaves a dead wedge rather than shuffling the others', () => {
  const cleared = updateRailPadSlot(defaultRailConfig(), 'padArrows', padSlotKey(0, 2), { item: null })
  const pad = padOf(cleared, 'padArrows').pad!
  assert.equal(pad.slots['0:2'], undefined)
  assert.equal(pad.slots['0:3']?.item, 'left', 'and left did not slide into it either')
  assert.equal(pad.slots['0:0']?.item, 'right', 'right did not slide into the gap')
})

test('a pad slot edit refuses anything that is not a pad', () => {
  const config = defaultRailConfig()
  assert.equal(updateRailPadSlot(config, 'esc', padSlotKey(0, 0), { item: 'enter' }), config)
  assert.equal(updateRailPadSlot(config, 'nope', padSlotKey(0, 0), { item: 'enter' }), config)
  assert.equal(setRailPadShape(config, 'esc', { wedges: 4 }), config)
  assert.equal(setRailPadShape(config, 'padArrows', { wedges: 4 }), config, 'already there')
})

test('growing a pad keeps every binding; shrinking drops what no longer has a position', () => {
  const grown = setRailPadShape(defaultRailConfig(), 'padArrows', { wedges: 5 })
  assert.equal(padWedgeCount(padOf(grown, 'padArrows').pad), 5)
  // Position for position, and the new wedge is empty.
  assert.deepEqual(railPadSlotItemIds(padOf(grown, 'padArrows')), ['right', 'down', 'up', 'left'])
  assert.equal(padOf(grown, 'padArrows').pad?.slots['0:3']?.item, 'left')

  const shrunk = setRailPadShape(defaultRailConfig(), 'padJump', { wedges: 2 })
  // Four wedges into two: the outer two have nowhere to go, and dropping them beats
  // stacking two actions on one wedge.
  assert.deepEqual(railPadSlotItemIds(padOf(shrunk, 'padJump')), ['ctrlEnd', 'end'])

  // Nothing survives a reshape by having no position: every slot has one now, so shrinking
  // to a single wedge keeps exactly that wedge and releases the rest.
  const reshaped = setRailPadShape(defaultRailConfig(), 'padArrows', { wedges: 1 })
  assert.deepEqual(railPadSlotItemIds(padOf(reshaped, 'padArrows')), ['right'])
})

test('adding a ring keeps the near ring and offers an empty far one', () => {
  const ringed = setRailPadShape(defaultRailConfig(), 'padCopy', { rings: 2 })
  assert.equal(padRingCount(padOf(ringed, 'padCopy').pad), 2)
  assert.deepEqual(railPadSlotItemIds(padOf(ringed, 'padCopy')), ['copyResume', 'copyInput', 'copyReply'])
  assert.equal(padOf(ringed, 'padCopy').pad?.slots['1:0'], undefined)
  // And everything on it now waits for the lift, because the near ring became transit.
  const target = ringed.items.find(entry => entry.id === 'copyReply')
  assert.equal(railPadSlotMode(padOf(ringed, 'padCopy').pad?.slots['0:2'], target, 2), 'release')
})

test('a custom pad survives normalization; a saved pad naming no slots is inert, not dropped', () => {
  const saved = {
    version: 3,
    items: [
      ...defaultRailConfig().items,
      { id: 'custom:pad:mine', type: 'pad', label: 'Mine', pad: { wedges: 5, rings: 2, slots: { '1:4': { item: 'esc' } } } },
      { id: 'custom:pad:empty', type: 'pad', label: 'Empty' },
    ],
    layouts: defaultRailConfig().layouts,
  }
  const config = normalizeRailConfig(saved)
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'custom:pad:mine')), ['esc'])
  assert.equal(padWedgeCount(padOf(config, 'custom:pad:mine').pad), 5)
  // A half-built pad is what the editor produces the moment it is created, so it has to
  // load rather than vanish on the next read.
  assert.deepEqual(padOf(config, 'custom:pad:empty').pad, { wedges: 3, rings: 1, slots: {} })
})

test('a padded key keeps its spoken alias, because a pad is a placement', () => {
  // The arrows ship only inside `padArrows`, which the *mobile* default places.
  // Folding them into a pad must not retire "press up" as a spoken command.
  const config = defaultRailConfig()
  const phrases = resolveRailVoiceEntries(config, { device: 'mobile', backend: 'claude' })
    .flatMap(entry => entry.phrases)
  assert.ok(phrases.includes('press up'), 'the arrow inside the pad is still voiced')
  assert.ok(phrases.includes('press down'), 'including the one in the centre')
  assert.ok(phrases.includes('home key'))
  // The pad chip itself is a container with no behaviour, so it is never voiced.
  const voicedIds = resolveRailVoiceEntries(config, { device: 'mobile', backend: 'claude' }).map(entry => entry.item.id)
  assert.equal(voicedIds.includes('padArrows'), false)
  assert.equal(voicedIds.includes('padJump'), false)
})

test('an unplaced pad does not voice what it holds', () => {
  const config = defaultRailConfig()
  config.layouts.desktop.strip[0].items = config.layouts.desktop.strip[0].items
    .filter(id => id !== 'padArrows')
  const phrases = resolveRailVoiceEntries(config, { device: 'desktop', backend: 'claude' })
    .flatMap(entry => entry.phrases)
  assert.equal(phrases.includes('press up'), false)
  assert.equal(phrases.includes('press down'), false)
})
