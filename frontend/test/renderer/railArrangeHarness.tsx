// A real Action rail being rearranged, over a terminal-shaped panel.
//
// Mounting the production pieces is the point. Everything worth doubting about this feature
// is an interaction no unit test can hold at once: the chips are `pointer-events:none` while
// arranging, so a press lands on the row container and the chip under it is found by
// rectangle against a real `getBoundingClientRect`; `OverflowRail` takes pointer capture on
// the same touch, so every later move is retargeted away from what was pressed; and Chrome's
// own touch-to-click synthesis is the third party to both arguments.
//
// So the harness supplies exactly one thing production supplies - a `RailConfig` and
// somewhere to save it - and everything else is the shipped code: `useRailArrange` (the
// wiring), `beginRailArrangeDrag` (the gesture), `RailStrip` (the row), `RailArrangePanel`
// (the panel, its bin and its tray), and the real stylesheet.
//
// The chips are plain buttons rather than `TerminalPane.renderRailItem`'s three shapes,
// because mounting a pane means an xterm, a socket and a session. What that simplification
// could hide - a chip shape that stopped publishing `data-rail-slot` - is held by a
// source-string contract test instead (`test/railArrange.test.ts`).
import { render } from 'preact'
import { useState } from 'preact/hooks'

import { RailArrangePanel, type RailArrangeCatalogEntry, type RailArrangeRow } from '../../src/RailArrangePanel'
import { RailStrip } from '../../src/RailStrip'
import { railArrangeScopeDetail, railArrangeScopeLabel } from '../../src/railArrange'
import { defaultRailConfig, type RailConfig, type RailItem } from '../../src/commandRail'
import { useRailArrange } from '../../src/useRailArrange'
import '../../src/style.css'

declare global {
  interface Window {
    /** The layout as it stands, newest write last. The spec reads order out of this. */
    railArrangeRows: () => string[][]
    /** Every chip activation the harness has seen. Must stay empty while arranging. */
    railArrangeFires: string[]
  }
}
window.railArrangeFires = []

const key = (id: string): RailItem => ({ id, type: 'key', label: id, bytes: id })

/**
 * Two rows, and one item in the first that this "session" filters out.
 *
 * `hidden` sits at stored index 1 and is never drawn, so the row renders `a c d` over stored
 * slots 0, 2, 3. That gap is the whole reason the translation exists: a drop measured against
 * what is on screen has to come back as an index into what is stored.
 */
const HIDDEN = 'hidden'
function seed(): RailConfig {
  const config = defaultRailConfig()
  config.items = ['a', 'b', 'c', 'd', HIDDEN, 'spare', 'extra'].map(key)
  config.layouts.desktop.strip = [
    { id: 'r1', items: ['a', HIDDEN, 'c', 'd'] },
    { id: 'r2', items: ['b'] },
  ]
  config.layouts.mobile.strip = [{ id: 'm1', items: [] }]
  return config
}

function Harness() {
  const [config, setConfig] = useState<RailConfig>(seed)
  const arrange = useRailArrange({
    device: 'desktop',
    surface: 'strip',
    config: () => config,
    save: setConfig,
  })
  window.railArrangeRows = () => config.layouts.desktop.strip.map(row => [...row.items])

  const chip = (id: string, slot: number, rowId: string) => <button
    key={`${rowId}:${slot}:${id}`}
    type="button"
    class="rail-text"
    data-rail-item={id}
    data-rail-slot={slot}
    data-reorder-id={`${rowId}:${slot}:${id}`}
    data-key={id}
    title={id}
    onClick={() => window.railArrangeFires.push(id)}
  >{id}</button>

  const rows = config.layouts.desktop.strip.map(row => ({
    id: row.id,
    // Backend filtering, expressed the way `resolveRailRows` expresses it: the entry is not
    // drawn, and every chip beside it keeps its *stored* slot.
    chips: row.items.flatMap((id, index) => id === HIDDEN ? [] : [chip(id, index, row.id)]),
  }))
  // Rows are dropped when empty and kept while arranging, exactly as the pane decides it.
  const drawn = rows.filter(row => arrange.arranging || row.chips.length)

  const placed = new Set(config.layouts.desktop.strip.flatMap(row => row.items))
  const catalog: RailArrangeCatalogEntry[] = config.items
    .filter(item => !placed.has(item.id))
    .map(item => ({
      id: item.id,
      label: item.label,
      chip: <button type="button" class="rail-text" data-key={`catalog-${item.id}`}>{item.label}</button>,
    }))
  const panelRows: RailArrangeRow[] = drawn.map(row => ({ id: row.id, chips: row.chips }))

  return <div class="terminal-surface" style="position:fixed;inset:0">
    <div class="terminal-host" style="background:#0b0e14" />
    <div
      class={`terminal-action-rail${arrange.arranging ? ' rail-arranging' : ''}`}
      role="toolbar"
      aria-label="Terminal keys and clipboard actions"
    >
      {arrange.arranging && <RailArrangePanel
        device="desktop"
        rows={panelRows}
        catalog={catalog}
        catalogOpen={arrange.catalogOpen}
        onToggleCatalog={arrange.toggleCatalog}
        scopeLabel={railArrangeScopeLabel('global')}
        scopeDetail={railArrangeScopeDetail('global')}
        preview={arrange.preview}
        canUndo={arrange.canUndo}
        onUndo={arrange.undo}
        onDone={arrange.exit}
        onChipPointerDown={arrange.beginChipDrag}
        onCatalogPointerDown={arrange.beginCatalogDrag}
      />}
      <div
        class="terminal-action-rows"
        data-rail-arrange-surface={arrange.arranging ? 'rail' : undefined}
        onPointerDown={event => arrange.beginChipDrag(event as unknown as PointerEvent)}
      >
        {drawn.map((row, index) => <RailStrip
          key={row.id}
          chips={row.chips}
          label={`Actions, row ${index + 1}`}
          onConfigure={() => window.railArrangeFires.push('configure')}
          device="desktop"
          rowId={row.id}
          arranging={arrange.arranging}
          caretAt={arrange.caretFor(row.id)}
          onArrange={arrange.enter}
        />)}
      </div>
    </div>
  </div>
}

render(<Harness />, document.getElementById('root')!)
