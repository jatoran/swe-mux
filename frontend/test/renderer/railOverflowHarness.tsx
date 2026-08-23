// A real Action rail row, narrow enough that most of it is in the overflow popover, over a
// terminal-shaped panel whose colour the spec can drive between white and black.
//
// Mounting the production `RailStrip` is the point. Everything the spec asserts is decided
// by something no unit test can reach: a real `getBoundingClientRect` on every chip (the
// split), real CSS variables (the fixed `+N` width and the density group), the browser's own
// `backdrop-filter` compositing (the contrast), and the interaction between two independent
// `window` listeners (a drop-up opened from inside the popover, which must render over it
// and must not dismiss it).
//
// The buffer behind the rail is a plain div rather than a terminal because what matters to
// the glass is the *colour under it*, and pure white and pure black are the two extremes any
// real buffer sits between. Driving those directly is the honest worst case; screenshotting
// an xterm would measure whatever grey its theme happened to average to.
import { render } from 'preact'
import { useRef, useState } from 'preact/hooks'
import { RailDropup } from '../../src/RailDropup'
import { RailStrip } from '../../src/RailStrip'
import { AttachIcon, BranchIcon, CopyIcon, PasteIcon } from '../../src/railIcons'
import '../../src/style.css'

declare global {
  interface Window {
    /** Every chip activation the harness has seen, oldest first. */
    railOverflowFires: string[]
    /** Paint the terminal behind the rail, so the glass has something to be glass over. */
    setBuffer: (colour: string) => void
    /** Fire the command bus the way `TerminalPane.runCommand` does. */
    fireCommand: (command: string) => void
    /** Stand a soft keyboard up exactly the way production does: the layout viewport keeps
     *  its height, `--keyboard-inset` is published, and `.soft-keyboard-open` slides the
     *  terminal surface up with a transform - which is what silently makes that surface the
     *  containing block for every `position:fixed` overlay inside it. That half of the
     *  keyboard bug is reproducible here; the visual-viewport half is pure arithmetic and is
     *  covered in `test/railOverflow.test.ts`, since `visualViewport` cannot be resized from
     *  script. */
    openKeyboard: (inset: number) => void
  }
}
window.railOverflowFires = []

// Deliberately more than fits: eight labelled chips, four icon chips, a drop-up trigger, and
// a two-click destructive confirm, which is the chip whose behaviour the panel exists to
// preserve.
const TEXT = ['learn', 'commit-and-push', 'Copy resume', 'review', 'plan', 'Actions', 'test', 'lint']
const ICONS: [string, () => preact.VNode][] = [
  ['copyReply', CopyIcon], ['paste', PasteIcon], ['attach', AttachIcon], ['branch', BranchIcon],
]

function Harness() {
  const [buffer, setBufferColour] = useState('#0b0e14')
  const [dropup, setDropup] = useState<HTMLElement | null>(null)
  const [armed, setArmed] = useState(false)
  const clip = useRef<HTMLButtonElement>(null)
  const query = new URLSearchParams(location.search)
  const multipleRows = query.has('rows')
  // The second row's readout, so a spec can drive the two cases that decide where the right
  // wedge lands: real text (a trailing cluster wider than the row above it) and the empty
  // string production passes most of the time (a cluster that must match it exactly).
  const secondStatus = query.get('status') ?? 'Copied'
  window.setBuffer = setBufferColour
  window.fireCommand = command => window.dispatchEvent(new CustomEvent('mux:command', { detail: command }))
  window.openKeyboard = inset => {
    document.documentElement.style.setProperty('--keyboard-inset', `${inset}px`)
    document.documentElement.classList.toggle('soft-keyboard-open', inset > 0)
    window.dispatchEvent(new Event('resize'))
  }

  const fire = (id: string) => { window.railOverflowFires.push(id) }

  const chips = [
    ...TEXT.map(label => <button
      key={label}
      class="rail-text"
      data-key={label}
      title={label}
      onClick={() => fire(label)}
    >{label}</button>),
    ...ICONS.map(([id, Icon]) => <button key={id} class="rail-icon" data-key={id} aria-label={id} onClick={() => fire(id)}>
      <Icon />
    </button>),
    <button
      key="clip"
      ref={clip}
      data-key="clip"
      class={dropup ? 'rail-dropup-open-trigger' : undefined}
      aria-expanded={!!dropup}
      onClick={event => setDropup(current => current ? null : event.currentTarget as HTMLElement)}
    >Clip</button>,
    <button
      key="drawer"
      data-key="drawer"
      onClick={() => window.fireCommand('drawer.peekActions')}
    >Drawer</button>,
    <button
      key="endSession"
      data-key="endSession"
      class={`rail-danger ${armed ? 'confirming' : ''}`}
      onClick={() => { fire(armed ? 'endSession:confirm' : 'endSession:arm'); setArmed(value => !value) }}
    >{armed ? 'Confirm ✓' : 'End session'}</button>,
  ]
  const secondRow = TEXT.concat(['inspect', 'deploy', 'history']).map(label => <button
    key={`second-${label}`}
    class="rail-text"
    data-key={`second-${label}`}
    onClick={() => fire(`second-${label}`)}
  >{label}</button>)

  return <div class="terminal-surface" style="position:fixed;inset:0;display:grid;grid-template-rows:minmax(0,1fr) auto">
    <div class="terminal-host" id="buffer" style={`background:${buffer}`} />
    <div class="terminal-action-rail" role="toolbar" aria-label="Terminal keys and clipboard actions">
      <div class="terminal-action-rows">
        <RailStrip
          chips={chips}
          label="Actions"
          status={multipleRows ? undefined : ''}
          onConfigure={() => { window.railOverflowFires.push('configure') }}
        />
        {multipleRows && <RailStrip
          chips={secondRow}
          label="Actions, row 2"
          status={secondStatus}
          onConfigure={() => { window.railOverflowFires.push('configure') }}
        />}
      </div>
    </div>
    {dropup && <RailDropup
      label="Recent clipboard"
      anchor={dropup}
      onClose={() => setDropup(null)}
      sticky={{ label: 'All clipboard history…', title: 'Open the full section', run: () => window.fireCommand('clipboard.open') }}
    >
      {Array.from({ length: 8 }, (_, index) => <button
        key={index}
        type="button"
        class="rail-dropup-row"
        data-row={`row-${index}`}
        onClick={() => { fire(`clip:${index}`); setDropup(null) }}
      ><span>entry {index}</span></button>)}
    </RailDropup>}
  </div>
}

render(<Harness />, document.getElementById('root')!)
