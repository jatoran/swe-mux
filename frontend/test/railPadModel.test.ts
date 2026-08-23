import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
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
  padDirectionUnit,
  padDirections,
  padRingOf,
  padSectorCount,
  padSlotKeys,
  railConfigFromBlob,
  railPadSlotItemIds,
  railPadSlotLabel,
  railPadSlotMode,
  writeRailConfigBlob,
  type RailItem,
} from '../src/commandRail.ts'
import { setRailPadOrientation, updateRailPadSlot } from '../src/railLayout.ts'
import { isRepeatableRailKey } from '../src/railKeyRepeat.ts'
import { resolveRailVoiceEntries } from '../src/railVoice.ts'

const padOf = (config: { items: RailItem[] }, id: string): RailItem =>
  config.items.find(item => item.id === id)!

test('an orientation is three wedges of one ring, or two of two', () => {
  assert.deepEqual(padDirections('cardinal'), CARDINAL_PAD_DIRECTIONS)
  assert.deepEqual(padDirections('diagonal'), DIAGONAL_PAD_DIRECTIONS)
  assert.equal(padSectorCount('cardinal'), 3)
  assert.equal(padSectorCount('diagonal'), 2)
  // Both offer four addressable slots counting the centre; the diagonal one gets a fifth
  // because its second ring buys a direction without buying an angle.
  assert.equal(padSlotKeys('cardinal').length, 4)
  assert.equal(padSlotKeys('diagonal').length, 5)
  assert.equal(padSlotKeys('diagonal').at(-1), 'center')
})

test('no direction points downward, in either orientation', () => {
  // The whole reason the fan gave up its lower half: the rail sits on the bottom edge of a
  // phone, so a downward wedge is drawn off the glass and dragged where a thumb cannot go.
  for (const direction of [...CARDINAL_PAD_DIRECTIONS, ...DIAGONAL_PAD_DIRECTIONS]) {
    assert.ok(padDirectionUnit(direction).y <= 0.001, `${direction} points down`)
  }
})

test('the far ring is exactly the two Far directions', () => {
  assert.deepEqual(CARDINAL_PAD_DIRECTIONS.map(padRingOf), ['near', 'near', 'near'])
  assert.deepEqual(DIAGONAL_PAD_DIRECTIONS.map(padRingOf), ['near', 'near', 'far', 'far'])
  // A far direction shares its wedge's angle and differs only in distance, which is what
  // makes the ring a second division rather than four cramped wedges.
  assert.deepEqual(padDirectionUnit('upLeftFar'), padDirectionUnit('upLeft'))
  assert.deepEqual(padDirectionUnit('upRightFar'), padDirectionUnit('upRight'))
})

test('a slot mode defaults from the action, so a binding arrives already safe', () => {
  const arrow = BUILTIN_RAIL.find(item => item.id === 'up')
  const home = BUILTIN_RAIL.find(item => item.id === 'home')
  const kill = BUILTIN_RAIL.find(item => item.id === 'endSession')
  assert.equal(defaultPadTriggerMode(arrow), 'enter-repeat')
  assert.equal(defaultPadTriggerMode(home), 'enter')
  assert.equal(defaultPadTriggerMode(kill), 'release', 'anything destructive waits for the lift')
  assert.equal(defaultPadTriggerMode(undefined), 'enter')
  assert.equal(railPadSlotMode({ item: 'up', mode: 'release' }, arrow), 'release')
  assert.equal(railPadSlotMode({ item: 'up', mode: 'nope' as never }, arrow), 'enter-repeat')
})

test('a ringed pad commits on release, because its near ring is transit', () => {
  // Geometry, not taste: reaching the far ring means crossing the near one, so a near slot
  // firing on entry would fire every time somebody went past it - and again on the way back
  // in. A ring you must pass through cannot be a fire-on-entry target.
  const arrow = BUILTIN_RAIL.find(item => item.id === 'up')
  assert.equal(defaultPadTriggerMode(arrow, 'diagonal'), 'release')
  assert.equal(defaultPadTriggerMode(undefined, 'diagonal'), 'release')
  // The one-ring pad has no transit and keeps entry-firing, which is what makes it fast.
  assert.equal(defaultPadTriggerMode(arrow, 'cardinal'), 'enter-repeat')
  // An explicit mode still wins, so the trade stays available to anyone who wants it.
  assert.equal(railPadSlotMode({ item: 'up', mode: 'enter' }, arrow, 'diagonal'), 'enter')
  assert.equal(railPadSlotMode({ item: 'up' }, arrow, 'diagonal'), 'release')
})

test('every slot of every shipped ringed pad waits for the lift', () => {
  const config = defaultRailConfig()
  for (const item of config.items) {
    if (item.type !== 'pad' || item.pad?.orientation !== 'diagonal') continue
    for (const key of padSlotKeys('diagonal')) {
      const binding = item.pad.slots[key]
      if (!binding) continue
      const target = config.items.find(entry => entry.id === binding.item)
      assert.equal(railPadSlotMode(binding, target, 'diagonal'), 'release', `${item.id}.${key}`)
    }
  }
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
  // Three arrows, not four: Down has no wedge in an upward fan and keeps a chip of its own.
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'padArrows')), ['right', 'up', 'left'])
  assert.equal(padOf(config, 'padArrows').pad?.orientation, 'cardinal')
  // Two wedges over two rings: left/right is the scope, near/far is which end.
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'padJump')), ['ctrlHome', 'home', 'ctrlEnd', 'end'])
  assert.equal(padOf(config, 'padJump').pad?.orientation, 'diagonal')
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'padCopy')), ['copyResume', 'copyInput', 'copyReply'])
  // Diagonal purely to keep all four pickers in one chip.
  assert.deepEqual(railPadSlotItemIds(padOf(config, 'padPickers')), ['prompts', 'clipboardHistory', 'actionsDrawer', 'skills'])
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
  // The helper can only hold the rule for callers that use it, and the bug was at the call
  // site: `view?.padLabel || view?.title || …`. So the call site is pinned too, the same way
  // `railDensity.test.ts` pins where `rail-text` is applied.
  const pane = readFileSync(new URL('../src/TerminalPane.tsx', import.meta.url), 'utf8')
  const slots = pane.slice(pane.indexOf('const railPadSlots='), pane.indexOf('const renderRailItem='))
  assert.ok(slots.includes('railPadSlotLabel('), 'the pad slot label must come from the helper')
  assert.equal(
    /label\s*[:=][^\n]*view\?\.title/.test(slots),
    false,
    'a wedge label must never fall through to a tooltip',
  )
})

test('the wedge label chain cannot land on a sentence', () => {
  const skills = BUILTIN_RAIL.find(item => item.id === 'skills')!
  // The item whose title is longest, resolved by every route into this helper.
  assert.equal(railPadSlotLabel(undefined, skills, 'skills'), 'Skills')
  assert.equal(railPadSlotLabel('Pick', skills, 'skills'), 'Pick', 'an explicit short override wins')
  assert.equal(railPadSlotLabel('   ', skills, 'skills'), 'Skills', 'and a blank one does not')
  // A slot naming something this resolution cannot see shows the id rather than nothing:
  // a wedge with no face reads as a bug, and the id says which binding to go and fix.
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

test('the default order names only real built-ins, and every entry is placed', () => {
  const known = new Set(BUILTIN_RAIL.map(item => item.id))
  for (const id of DEFAULT_RAIL_ORDER) assert.ok(known.has(id), `${id} is not a built-in`)
  assert.equal(new Set(DEFAULT_RAIL_ORDER).size, DEFAULT_RAIL_ORDER.length, 'no duplicates')
  assert.deepEqual(defaultRailConfig().layouts.desktop.strip[0].items, [...DEFAULT_RAIL_ORDER])
  // Down is on the rail as its own chip, next to the pad holding the other three arrows.
  assert.equal(DEFAULT_RAIL_ORDER.indexOf('down'), DEFAULT_RAIL_ORDER.indexOf('padArrows') + 1)
})

test('a stored pad is canonicalized, so an untouched copy still equals the shipped one', () => {
  // The failure this prevents: a fork stores a copy of a shipped pad, reattach asks whether
  // the copy still equals the definition, and a slot map written in reading order compares
  // unequal to one rebuilt in canonical order - reporting an edit nobody made.
  const written = normalizeRailPad({
    orientation: 'cardinal',
    slots: { left: { item: 'left' }, up: { item: 'up' }, right: { item: 'right' } },
  })
  assert.deepEqual(Object.keys(written.slots), ['right', 'up', 'left'])
  assert.deepEqual(written, normalizeRailPad(written))
  for (const item of BUILTIN_RAIL) {
    if (item.type !== 'pad') continue
    assert.deepEqual(item.pad, normalizeRailPad(item.pad), `${item.id} is not stored canonically`)
  }
})

test('normalization keeps an unresolvable slot and drops an unreachable one', () => {
  const pad = normalizeRailPad({
    orientation: 'cardinal',
    slots: {
      // Kept: the action may simply be missing from *this* resolution.
      up: { item: 'custom:skill:ship' },
      // Dropped: `upLeft` and `down` cannot be reached at all on a three-wedge cardinal pad.
      // `down` in particular is how a config written before the fan lost its lower half
      // loses that binding, which is correct - there is nowhere to put it.
      upLeft: { item: 'esc' },
      down: { item: 'down' },
      sideways: { item: 'esc' },
      right: { item: 'nope', mode: 'garbage' },
      left: 'not an object',
    },
  })
  assert.deepEqual(Object.keys(pad.slots), ['right', 'up'])
  assert.deepEqual(pad.slots.up, { item: 'custom:skill:ship' })
  assert.deepEqual(pad.slots.right, { item: 'nope' }, 'a bad mode falls back to the default rather than sticking')
  const migrated = normalizeRailPad({ orientation: 'cardinal', slots: { up: { item: 'clearInput' } } })
  assert.equal(migrated.slots.up?.item, 'ctrlU')
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
  assert.equal(padOf(reloaded, 'padArrows').pad?.slots.left?.item, 'left')
})

test('clearing a slot leaves a dead wedge rather than shuffling the others', () => {
  const cleared = updateRailPadSlot(defaultRailConfig(), 'padArrows', 'left', { item: null })
  const pad = padOf(cleared, 'padArrows').pad!
  assert.equal(pad.slots.left, undefined)
  assert.equal(pad.slots.right?.item, 'right', 'right did not slide into the gap')
  assert.deepEqual(railPadSlotItemIds(padOf(cleared, 'padArrows')), ['right', 'up'])
})

test('a pad slot edit refuses anything that is not a pad', () => {
  const config = defaultRailConfig()
  assert.equal(updateRailPadSlot(config, 'esc', 'up', { item: 'enter' }), config)
  assert.equal(updateRailPadSlot(config, 'nope', 'up', { item: 'enter' }), config)
  assert.equal(setRailPadOrientation(config, 'esc', 'diagonal'), config)
  assert.equal(setRailPadOrientation(config, 'padArrows', 'cardinal'), config, 'already there')
})

test('turning a pad carries what fits and drops what does not, position for position', () => {
  const turned = setRailPadOrientation(defaultRailConfig(), 'padArrows', 'diagonal')
  const pad = padOf(turned, 'padArrows').pad!
  assert.equal(pad.orientation, 'diagonal')
  // Three positions into four: all of them fit, and the third lands on the far ring.
  assert.equal(pad.slots.upRight?.item, 'right')
  assert.equal(pad.slots.upLeft?.item, 'up')
  assert.equal(pad.slots.upRightFar?.item, 'left')
  assert.deepEqual(railPadSlotItemIds(padOf(turned, 'padArrows')), ['right', 'up', 'left'])

  // The other way round, three of the four fit and the fourth is the one left behind.
  const flattened = setRailPadOrientation(defaultRailConfig(), 'padJump', 'cardinal')
  assert.deepEqual(railPadSlotItemIds(padOf(flattened, 'padJump')), ['ctrlHome', 'home', 'ctrlEnd'])
})

test('a custom pad survives normalization; a saved pad naming no slots is inert, not dropped', () => {
  const saved = {
    version: 3,
    items: [
      ...defaultRailConfig().items,
      { id: 'custom:pad:mine', type: 'pad', label: 'Mine', pad: { orientation: 'diagonal', slots: { upLeftFar: { item: 'esc' } } } },
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
  // Down is its own chip again, and is voiced from there.
  assert.ok(phrases.includes('press down'))
  // The pad chip itself is a container with no behaviour, so it is never voiced.
  const voicedIds = resolveRailVoiceEntries(config, { device: 'desktop', backend: 'claude' }).map(entry => entry.item.id)
  assert.equal(voicedIds.includes('padArrows'), false)
  assert.equal(voicedIds.includes('padJump'), false)
})

test('an unplaced pad does not voice what it holds', () => {
  const config = defaultRailConfig()
  config.layouts.desktop.strip[0].items = config.layouts.desktop.strip[0].items
    .filter(id => id !== 'padJump')
  const phrases = resolveRailVoiceEntries(config, { device: 'desktop', backend: 'claude' })
    .flatMap(entry => entry.phrases)
  assert.equal(phrases.includes('home key'), false)
})
