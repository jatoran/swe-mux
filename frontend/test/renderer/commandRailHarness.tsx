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
import '../../src/style.css'

declare global {
  interface Window {
    /** Every sequence the rail has written, oldest first. */
    railSends: string[]
  }
}
window.railSends = []

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

function Rail() {
  const repeat = useRailKeyRepeat(sequence => { window.railSends.push(sequence) }, 'harness')
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
