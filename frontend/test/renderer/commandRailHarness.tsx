// A real Action rail: the production `RailScroller` holding production `RailRepeatKey`
// arrows alongside ordinary click-to-send buttons, in the markup and CSS TerminalPane
// draws them in.
//
// The point of mounting the real components is that the bug this exists for is not
// visible to any of them alone. `RailScroller` takes pointer capture on its wrapper the
// moment a touch lands on an overflowing strip, which retargets every later pointer event
// away from the button that was pressed — so what an arrow key does with a swipe is
// decided by two modules, real capture, and real click suppression. Only trusted input
// reproduces that; the spec drives touches through CDP.
//
// The four arrows sit in the middle of the strip, with ordinary keys either side. That is
// what gives a spec somewhere to swipe *to*: an arrow at the strip's leading edge has no
// room to the left to drag towards and no scroll left to consume.
import { render } from 'preact'
import { RailScroller } from '../../src/RailScroller'
import { RailRepeatKey, useRailKeyRepeat } from '../../src/RailRepeatKey'
import { RailPad, useRailPad, type RailPadSlotView } from '../../src/RailPad'
import { pointerDragOwnsPointer } from '../../src/pointerDragClaim'
import { dismissSoftKeyboard } from '../../src/mobileKeyboard'
import { normalizeRailPad, padRingCount, padSlotKeys, railPadSlotMode, type RailItem } from '../../src/commandRail'
import '../../src/style.css'

declare global {
  interface Window {
    /** Every sequence the rail has written, oldest first. */
    railSends: string[]
    /** Whether a drag owned the pointer, sampled on every window pointer move.
     *
     *  This is the arbitration under test and it cannot be observed after the fact: the
     *  claim is released on `pointerup`, which lands *before* the `touchend` where the
     *  mobile recognizer would otherwise classify the same motion as the rail's
     *  swipe-up-opens-the-menu gesture. Sampling mid-drag is the only way to see it. */
    railClaims: boolean[]
    /**
     * How many sequences had been written at the instant the finger came up.
     *
     * Sampled here for the same reason `railClaims` is: a spec cannot take it. Reading the
     * count over CDP is a round trip, and on a loaded runner that round trip is longer than
     * one repeat interval - so a hold's own next repetition lands between the read and the
     * lift and reads as "lifting added a tap" (seen on CI: 6 sampled, 7 after). A tap
     * synthesised *by* the lift is dispatched after `pointerup`, so it lands after this
     * snapshot and is still caught, which is the thing the assertion is actually about.
     */
    railSendsAtLift: number
    /** The production dismissal call, so a spec can put the keyboard away *deliberately*
     *  mid-gesture. That is the one thing `restoreSoftKeyboard` must not undo, and it is a
     *  counter rather than an event, so there is no other way to reach it from a page. */
    railDismissKeyboard: () => void
  }
}
window.railSends = []
window.railClaims = []
window.railSendsAtLift = 0
window.railDismissKeyboard = dismissSoftKeyboard
window.addEventListener('pointermove', () => { window.railClaims.push(pointerDragOwnsPointer()) })
// Capture phase, so the snapshot precedes anything a component does on the same lift.
window.addEventListener(
  'pointerup',
  () => { window.railSendsAtLift = window.railSends.length },
  true,
)

const ARROWS = [
  { id: 'up', label: '↑', title: 'Up', bytes: '\x1b[A' },
  { id: 'down', label: '↓', title: 'Down', bytes: '\x1b[B' },
  { id: 'left', label: '←', title: 'Left', bytes: '\x1b[D' },
  { id: 'right', label: '→', title: 'Right', bytes: '\x1b[C' },
]

// Ordinary rail keys: the neighbours the arrows are supposed to behave like. Enough of
// them to overflow any width the spec sets, split either side of the arrows.
const LEADING = [
  { id: 'esc', label: 'Esc', bytes: '\x1b' },
  { id: 'enter', label: '⏎', bytes: '\r' },
  { id: 'tab', label: 'Tab', bytes: '\t' },
  { id: 'ctrlC', label: '^C', bytes: '\x03' },
  { id: 'ctrlU', label: '^U', bytes: '\x15' },
  { id: 'restoreInput', label: '^Y', bytes: '\x19' },
]
const TRAILING = [
  { id: 'home', label: 'Home', bytes: '\x1b[H' },
  { id: 'end', label: 'End', bytes: '\x1b[F' },
  { id: 'ctrlHome', label: '^Home', bytes: '\x1b[1;5H' },
  { id: 'ctrlEnd', label: '^End', bytes: '\x1b[1;5F' },
  { id: 'newline', label: '↵ nl', bytes: '\x1b\r' },
  { id: 'divider', label: '---', bytes: '---' },
]

const PlainKey = ({ id, label, bytes }: { id: string; label: string; bytes: string }) => (
  <button class="term-key" data-key={id} title={label} onClick={() => window.railSends.push(bytes)}>{label}</button>
)

// Three pads, in the same strip as the arrows they exist to replace, because the arbitration
// under test is between the pad and *this* scroller's pointer capture.
//
// The three-wedge one deliberately mixes every trigger mode: `up` repeats while held, `right`
// fires on entry, and `left` waits for the lift so the escape hatch has something real to
// escape from. Every slot is a wedge - there is no centre - so a tap on it opens the dial
// rather than running anything, which is what the standing-dial specs drive.
const WEDGE_PAD: RailItem = {
  id: 'padWedges',
  type: 'pad',
  label: 'Pad',
  className: 'term-key',
  title: 'Pad',
  pad: normalizeRailPad({
    wedges: 3,
    rings: 1,
    slots: {
      '0:0': { item: 'right', mode: 'enter' },
      '0:1': { item: 'up', mode: 'enter-repeat' },
      '0:2': { item: 'kill', mode: 'release' },
      // Deliberately not `enter-repeat-far`: this pad covers hold-anywhere, and `STREAM_PAD`
      // covers push-out, so one spec never has to reason about both at once.
    },
  }),
}
// Four wedges of one ring, which is what the shipped Jump and Pick pads are: every wedge
// fires the instant you cross in, because a single ring has no transit to pay for.
const FOUR_PAD: RailItem = {
  id: 'padFour',
  type: 'pad',
  label: 'Jump',
  className: 'term-key',
  title: 'Jump',
  pad: normalizeRailPad({
    wedges: 4,
    rings: 1,
    slots: {
      '0:0': { item: 'ctrlEnd' },
      '0:1': { item: 'end' },
      '0:2': { item: 'home' },
      '0:3': { item: 'ctrlHome' },
    },
  }),
}
// The two-ring shape, kept so the transit rule stays covered by a real gesture: no explicit
// modes, so every slot defaults to release.
const RING_PAD: RailItem = {
  id: 'padRings',
  type: 'pad',
  label: 'Ring',
  className: 'term-key',
  title: 'Ring',
  pad: normalizeRailPad({
    wedges: 2,
    rings: 2,
    slots: {
      '0:0': { item: 'nearRight' },
      '0:1': { item: 'nearLeft' },
      '1:0': { item: 'farRight' },
      '1:1': { item: 'farLeft' },
    },
  }),
}
// Push-out repeat, which is what the shipped arrows pad defaults to: one send anywhere in
// the wedge however long you rest there, a stream only once you push past the band.
const STREAM_PAD: RailItem = {
  id: 'padStream',
  type: 'pad',
  label: 'Flow',
  className: 'term-key',
  title: 'Flow',
  pad: normalizeRailPad({
    wedges: 2,
    rings: 1,
    slots: {
      '0:0': { item: 'right', mode: 'enter-repeat-far' },
      '0:1': { item: 'up', mode: 'enter-repeat-far' },
    },
  }),
}
const PAD_BYTES: Record<string, string> = {
  up: '\x1b[A', down: '\x1b[B', left: '\x1b[D', right: '\x1b[C',
  home: '\x1b[H', end: '\x1b[F', ctrlHome: '\x1b[1;5H', ctrlEnd: '\x1b[1;5F',
  kill: 'KILL', dead: 'DEAD',
  nearLeft: 'NEAR-L', nearRight: 'NEAR-R', farLeft: 'FAR-L', farRight: 'FAR-R',
}

function padSlots(item: RailItem): RailPadSlotView[] {
  const pad = item.pad!
  return padSlotKeys(pad).filter(key => pad.slots[key]).map(key => {
    const slot = pad.slots[key]
    // One slot is disabled on purpose: a dead wedge has to stay where it is and let nothing
    // through, rather than the others sliding over to fill the gap.
    const disabled = slot.item === 'dead'
    return {
      key,
      itemId: slot.item,
      label: slot.item,
      title: slot.item,
      // Through the production resolver, so the harness inherits the ringed-pad rule rather
      // than restating it and drifting from what a real pane would do.
      mode: railPadSlotMode(slot, undefined, padRingCount(pad)),
      disabled,
      run: () => { window.railSends.push(PAD_BYTES[slot.item] || slot.item) },
    }
  })
}

function Rail() {
  const repeat = useRailKeyRepeat(sequence => { window.railSends.push(sequence) }, 'harness')
  const padControl = useRailPad('harness')
  return <div class="terminal-action-rail" role="toolbar" aria-label="Terminal keys and clipboard actions">
    <div class="terminal-action-rows">
      <RailScroller>
        {LEADING.map(key => <PlainKey key={key.id} {...key} />)}
        {ARROWS.map(key => (
          <RailRepeatKey
            key={key.id}
            repeat={repeat}
            sequence={key.bytes}
            label={key.label}
            title={key.title}
            className="term-key"
          />
        ))}
        {[WEDGE_PAD, FOUR_PAD, RING_PAD, STREAM_PAD].map(item => (
          <RailPad
            key={item.id}
            controller={padControl}
            item={item}
            slots={padSlots(item)}
            className="term-key"
            content={item.label}
          />
        ))}
        {TRAILING.map(key => <PlainKey key={key.id} {...key} />)}
      </RailScroller>
    </div>
  </div>
}

// A width the rail must overflow at, inside the terminal surface it normally sits in so
// the production selectors that scope the rail's rules all match.
const root = document.querySelector<HTMLElement>('#root')!
root.classList.add('terminal-surface')
root.style.width = '300px'
render(<Rail />, root)
