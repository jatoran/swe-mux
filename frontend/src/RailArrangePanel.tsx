import type { VNode } from 'preact'
import { cloneElement } from 'preact'
import { useEffect, useRef } from 'preact/hooks'

import { arrangeChildren } from './railArrangeChips.tsx'
import { holdSoftKeyboard } from './mobileKeyboard.ts'
import type { RailArrangePreview } from './railArrangeDrag.ts'
import type { RailDevice } from './commandRail.ts'

// The panel that stands above an arranging Action rail.
//
// It carries the three things the rail itself cannot: the scope this session's edits write
// to, the two drop targets that are not rows, and the catalog of actions that are not on the
// rail yet. It also restates every row as a wrap grid, which is the surface a phone actually
// needs - the rail's rows scroll horizontally, so moving the eighteenth chip to the second
// slot on a strip means dragging into an auto-scrolling edge and waiting, while a grid shows
// the whole row at once and both ends of the move are on screen together.
//
// The grids and the rail are droppable at the same time and that is deliberate: dragging an
// action out of the catalog straight onto the live rail is the shortest version of the whole
// gesture. It is also why every row container is scoped by `[data-rail-arrange-surface]` -
// the same row is drawn twice, and a drop has to resolve against the copy under the pointer
// rather than the one behind it.
//
// Nothing here fires an Action. While `.rail-arranging` is set every chip is
// `pointer-events:none`, so a pad cannot open its fan and an arrow cannot repeat; the press
// lands on the row container and the chip under it is found by rectangle.

export interface RailArrangeRow {
  id: string
  label?: string
  chips: VNode[]
}

export interface RailArrangeCatalogEntry {
  id: string
  label: string
  /** The chip as the rail would draw it, so the tray is a preview rather than a word list. */
  chip: VNode
}

interface Props {
  device: RailDevice
  rows: readonly RailArrangeRow[]
  catalog: readonly RailArrangeCatalogEntry[]
  catalogOpen: boolean
  onToggleCatalog: () => void
  scopeLabel: string
  scopeDetail: string
  preview: RailArrangePreview
  canUndo: boolean
  onUndo: () => void
  onDone: () => void
  /** A press inside a row grid. The host resolves which chip by rectangle and begins a drag. */
  onChipPointerDown: (event: PointerEvent) => void
  onCatalogPointerDown: (event: PointerEvent, itemId: string, label: string) => void
}

export function RailArrangePanel({
  device, rows, catalog, catalogOpen, onToggleCatalog, scopeLabel, scopeDetail,
  preview, canUndo, onUndo, onDone, onChipPointerDown, onCatalogPointerDown,
}: Props) {
  const rowsRef = useRef<HTMLDivElement>(null)
  // Through a ref because the caller passes an inline arrow: listing the handler in the
  // effect's dependencies would rebind the listener on every render of a live drag.
  const beginChip = useRef(onChipPointerDown)
  beginChip.current = onChipPointerDown

  // A chip pressed in a grid never reaches an element listener, because chips are
  // `pointer-events:none` while arranging. The press is taken on the scroller instead, which
  // is also the only element that can see a press landing in the gap between two chips.
  useEffect(() => {
    const host = rowsRef.current
    if (!host) return
    const down = (event: PointerEvent) => beginChip.current(event)
    host.addEventListener('pointerdown', down)
    return () => host.removeEventListener('pointerdown', down)
  }, [])

  const caretFor = (rowId: string): number | null =>
    preview.caret && preview.caret.rowId === rowId ? preview.caret.at : null

  return <div class="rail-arrange" role="group" aria-label="Arrange actions" onMouseDown={holdSoftKeyboard}>
    <div class="rail-arrange-head">
      <span class="rail-arrange-scope" title={scopeDetail}>{scopeLabel}</span>
      <div class="rail-arrange-head-actions">
        <button
          type="button"
          class={catalogOpen ? 'rail-arrange-on' : undefined}
          aria-expanded={catalogOpen}
          title="Show every action, to drag one onto a row"
          onClick={onToggleCatalog}
        >Add</button>
        <button type="button" disabled={!canUndo} title="Undo the last arrangement change" onClick={onUndo}>Undo</button>
        <button type="button" class="rail-arrange-done" title="Stop arranging (Escape)" onClick={onDone}>Done</button>
      </div>
    </div>

    {catalogOpen && <div class="rail-arrange-catalog" role="group" aria-label="Actions available to add">
      {catalog.length
        ? catalog.map(entry => <span
          key={entry.id}
          class="rail-arrange-catalog-chip"
          data-rail-catalog-item={entry.id}
          title={`Drag ${entry.label} onto a row`}
          onPointerDown={event => onCatalogPointerDown(event as unknown as PointerEvent, entry.id, entry.label)}
        >{cloneElement(entry.chip)}</span>)
        : <span class="rail-arrange-empty">Every action this session offers is already on the rail.</span>}
    </div>}

    <div class="rail-arrange-rows" data-rail-arrange-surface="panel" ref={rowsRef}>
      {rows.map((row, index) => <div class="rail-arrange-row" key={row.id}>
        <span class="rail-arrange-row-label">{row.label || `Row ${index + 1}`}</span>
        <div
          class="rail-arrange-grid"
          data-rail-row={`${device}|strip|${row.id}`}
          role="group"
          aria-label={row.label || `Row ${index + 1}`}
        >{arrangeChildren(row.chips.map(chip => cloneElement(chip)), caretFor(row.id))}</div>
      </div>)}
    </div>

    {/* Drawn only while a chip is in the air. A bin standing over the terminal at all times
        is a control nobody asked for; one that appears under the thing being dragged is the
        gesture stating its own options. */}
    <div class={`rail-arrange-zones${preview.active ? ' rail-arrange-zones-live' : ''}`} aria-hidden={!preview.active}>
      <div
        class={`rail-arrange-zone rail-arrange-remove${preview.zone === 'remove' ? ' rail-arrange-zone-over' : ''}`}
        data-rail-arrange-zone="remove"
      >✕<span class="rail-arrange-zone-text">Remove</span></div>
      <div
        class={`rail-arrange-zone rail-arrange-new-row${preview.zone === 'new-row' ? ' rail-arrange-zone-over' : ''}`}
        data-rail-arrange-zone="new-row"
      >+<span class="rail-arrange-zone-text">New row</span></div>
    </div>
  </div>
}
