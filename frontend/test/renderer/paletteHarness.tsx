// The command palette's gate, mounted against the production `paletteResults`.
//
// What this exists to catch is work that is *not* done: while the palette is closed,
// no command in the registry may be scored, however often the shell around it
// re-renders. That is invisible to a pure test of the return value - an empty list
// looks the same whether the scorer ran or not - so every command here carries a
// `label` getter that counts the reads `searchCommands` makes when it builds its
// haystack. The counter is the observation; the list is only the control.
//
// The shell's own re-renders are simulated by a counter bumped from a button, which
// is what the five-second sidebar clock tick used to be before it moved into the
// rows: a state change above the palette that had nothing to do with it.

import { render } from 'preact'
import { useState } from 'preact/hooks'
import { paletteResults, type Command } from '../../src/commands.ts'
import '../../src/style.css'

declare global {
  interface Window {
    /** How many command labels the scorer has read since the page loaded. */
    paletteLabelReads: number
  }
}
window.paletteLabelReads = 0

const LABELS = [
  'Open command palette',
  'Reload daemon (keep sessions)',
  'Focus next workspace tab',
  'Focus previous workspace tab',
  'Toggle navigation sidebar',
  'Zoom focused pane',
  'Start broadcasting input',
  'New Claude in swe-mux',
  'New Codex in swe-mux',
  'Focus session: builder',
]

const slug = (label: string) => label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')

/**
 * A registry whose labels report being read, and nothing else out of the ordinary.
 *
 * The rows below render the *id*, never the label, so that every counted read comes
 * from the scorer rather than from drawing the result it produced.
 */
const commands: Command[] = LABELS.map(label => {
  const command = {
    id: slug(label),
    category: 'view' as const,
    available: true,
    run: () => {},
  }
  Object.defineProperty(command, 'label', {
    enumerable: true,
    get() { window.paletteLabelReads += 1; return label },
  })
  return command as Command
})

function PaletteHarness() {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [renders, setRenders] = useState(0)
  const shown = paletteResults(open, commands, query)
  return <div class="palette-harness">
    {/* Above the palette layer, which is a full-viewport overlay in production CSS and
        would otherwise swallow the clicks that close it again. */}
    <div style={{ position: 'relative', zIndex: 1000 }}>
      <button id="toggle" onClick={() => setOpen(value => !value)}>{open ? 'Close palette' : 'Open palette'}</button>
      <button id="rerender" onClick={() => setRenders(value => value + 1)}>Re-render shell</button>
      <span id="renders">{renders}</span>
    </div>
    {open && <div class="palette-layer">
      <div class="palette" role="dialog" aria-modal="true" aria-label="Command palette">
        <input id="palette-input" role="combobox" aria-controls="command-results" aria-expanded="true"
          value={query} onInput={event => setQuery(event.currentTarget.value)} placeholder="Type a command…" />
        <div id="command-results" role="listbox">
          {shown.map(command => <button key={command.id} role="option" aria-selected={false}>
            <span><small>{command.category}</small>{command.id}</span>
          </button>)}
        </div>
      </div>
    </div>}
  </div>
}

render(<PaletteHarness />, document.getElementById('palette')!)
