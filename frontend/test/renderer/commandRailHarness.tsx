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
import { normalizeRailPad, type RailItem } from '../../src/commandRail'
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
  }
}
window.railSends = []
window.railClaims = []
window.addEventListener('pointermove', () => { window.railClaims.push(pointerDragOwnsPointer()) })

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

// Two pads, in the same strip as the arrows they exist to replace, because the arbitration
// under test is between the pad and *this* scroller's pointer capture.
//
// The cardinal one deliberately mixes every trigger mode: `up` repeats while held, `right`
// and `down` fire on entry, and `left` waits for the lift so the escape hatch has something
// real to escape from. Its centre is bound too, so a tap has an answer.
const CARDINAL_PAD: RailItem = {
  id: 'padCardinal',
  type: 'pad',
  label: 'Pad',
  className: 'term-key',
  title: 'Pad',
  pad: normalizeRailPad({
    orientation: 'cardinal',
    slots: {
      up: { item: 'up', mode: 'enter-repeat' },
      right: { item: 'right', mode: 'enter' },
      down: { item: 'down', mode: 'enter' },
      left: { item: 'kill', mode: 'release' },
      center: { item: 'centre', mode: 'enter' },
    },
  }),
}
const DIAGONAL_PAD: RailItem = {
  id: 'padDiagonal',
  type: 'pad',
  label: 'Jump',
  className: 'term-key',
  title: 'Jump',
  pad: normalizeRailPad({
    orientation: 'diagonal',
    slots: {
      upLeft: { item: 'home', mode: 'enter' },
      upRight: { item: 'ctrlHome', mode: 'enter' },
      downLeft: { item: 'end', mode: 'enter' },
      downRight: { item: 'ctrlEnd', mode: 'enter' },
    },
  }),
}
const PAD_BYTES: Record<string, string> = {
  up: '\x1b[A', down: '\x1b[B', left: '\x1b[D', right: '\x1b[C',
  home: '\x1b[H', end: '\x1b[F', ctrlHome: '\x1b[1;5H', ctrlEnd: '\x1b[1;5F',
  kill: 'KILL', centre: 'CENTRE', dead: 'DEAD',
}

function padSlots(item: RailItem): RailPadSlotView[] {
  const pad = item.pad!
  return (Object.keys(pad.slots) as (keyof typeof pad.slots)[]).map(key => {
    const slot = pad.slots[key]!
    // One slot is disabled on purpose: a dead direction has to stay where it is and let
    // nothing through, rather than the others sliding over to fill the gap.
    const disabled = slot.item === 'dead'
    return {
      key,
      itemId: slot.item,
      label: slot.item,
      title: slot.item,
      mode: slot.mode || 'enter',
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
        {[CARDINAL_PAD, DIAGONAL_PAD].map(item => (
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
