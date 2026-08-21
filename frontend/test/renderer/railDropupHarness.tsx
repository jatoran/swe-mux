// The production `RailDropup` hanging off a production rail button, in a pane-shaped
// box with the rail pinned to its bottom edge.
//
// Mounting the real component matters for the same reason the command-rail harness
// mounts the real `RailScroller`: the behaviours under test are geometric and belong
// to no single module. Where the panel lands is decided by `anchoredPopoverStyle`
// against a real `getBoundingClientRect`; whether five rows are visible is decided by
// real CSS over real row heights; and whether the sticky link survives a scroll is
// decided by the browser's own sticky positioning. A unit test can assert none of it.
//
// The list is longer than the cap on purpose — a drop-up that fits is the case that
// proves nothing.
import { render } from 'preact'
import { useRef, useState } from 'preact/hooks'
import { RailDropup } from '../../src/RailDropup'
import { RailScroller } from '../../src/RailScroller'
import '../../src/style.css'

declare global {
  interface Window {
    /** Rows the harness's drop-up has reported as picked, oldest first. */
    dropupPicks: string[]
    /** Times the sticky link has been used. */
    dropupSticky: number
  }
}
window.dropupPicks = []
window.dropupSticky = 0

// Enough keys either side of the trigger to make the strip overflow at any width the spec
// sets, which is what gives the scroller something to pan. That now includes widths *above*
// the 760px device-class breakpoint, since the drop-up's placement rule differs either side
// of it and the pan has to be exercised on the desktop side.
const PADDING = [
  'esc', 'enter', 'tab', 'shiftTab', 'ctrlC', 'up', 'down', 'left',
  'right', 'home', 'end', 'ctrlHome', 'ctrlEnd', 'ctrlU', 'ctrlY', 'newline',
]

const ROWS = Array.from({ length: 12 }, (_, index) => ({
  id: `row-${index}`,
  title: `entry ${index}`,
  detail: `detail line for entry ${index}`,
}))

function Harness() {
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  return <div class="terminal-surface" style="position:fixed;inset:0;display:grid;grid-template-rows:minmax(0,1fr) auto">
    <div class="terminal-host" style="background:#111" />
    <div class="terminal-action-rail" role="toolbar" aria-label="Terminal keys and clipboard actions">
      <div class="terminal-action-rows">
        {/* The real scroller, so the trigger really can pan out from under a fixed panel. */}
        <RailScroller>
          {PADDING.map(id => <button key={id} class="term-key" data-key={id}>{id}</button>)}
          <button
            ref={trigger}
            data-key="clip"
            class={open ? 'rail-dropup-open-trigger' : undefined}
            aria-expanded={open}
            onClick={() => setOpen(value => !value)}
          >Clip</button>
          {PADDING.map(id => <button key={`${id}-tail`} class="term-key" data-key={`${id}-tail`}>{id}</button>)}
        </RailScroller>
      </div>
    </div>
    {open && <RailDropup
      label="Recent clipboard"
      anchor={trigger.current}
      onClose={() => setOpen(false)}
      sticky={{ label: 'All clipboard history…', title: 'Open the full section', run: () => { window.dropupSticky += 1 } }}
    >
      {ROWS.map(row => <button
        key={row.id}
        type="button"
        class="rail-dropup-row"
        data-row={row.id}
        onClick={() => { window.dropupPicks.push(row.id); setOpen(false) }}
      >
        <span>{row.title}</span>
        <small>{row.detail}</small>
      </button>)}
    </RailDropup>}
  </div>
}

render(<Harness />, document.getElementById('root')!)
