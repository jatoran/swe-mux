// The arrange mode's state and its pointer wiring, as one controller.
//
// Its own module rather than JSX inside `TerminalPane` for the reason `RailPad` and
// `RailRepeatKey` are: the gesture *is* this wiring - which press starts a drag, which chip
// it resolves to, what a drop writes - and a gesture is only worth trusting once real touches
// have been driven at it through a real rail
// (`test/renderer/command-rail-arrange.spec.ts`). Mounting a terminal pane is not a way to do
// that, and a harness that re-implemented the wiring would be testing a copy of the thing
// that can break.
//
// The controller owns everything except *where the edit is persisted*. That is the caller's,
// because it is the one part that is not about the gesture: `TerminalPane` routes a save
// through `applyScopedRail`, so a shared row lands in the global scope and a detached
// Project's layout stays with it.

import { useEffect, useRef, useState } from 'preact/hooks'

import type { RailConfig, RailDevice, RailSurface } from './commandRail.ts'
import type { RailRef } from './railLayout.ts'
import {
  RAIL_ARRANGE_UNDO_DEPTH, applyRailArrange,
  type RailArrangeSource, type RailArrangeTarget,
} from './railArrange.ts'
import {
  NO_RAIL_ARRANGE_PREVIEW, beginRailArrangeDrag, railChipAtPoint,
  type RailArrangeDragHost, type RailArrangePreview,
} from './railArrangeDrag.ts'
import { railRowAt } from './railLayout.ts'

export interface RailArrangeController {
  arranging: boolean
  preview: RailArrangePreview
  catalogOpen: boolean
  canUndo: boolean
  enter: () => void
  exit: () => void
  toggleCatalog: () => void
  undo: () => void
  /** Where this row draws its insertion caret right now, or null. */
  caretFor: (rowId: string) => number | null
  /** `pointerdown` on a container that holds rows. Resolves the chip and starts a drag. */
  beginChipDrag: (event: PointerEvent) => void
  beginCatalogDrag: (event: PointerEvent, itemId: string, label: string) => void
}

export interface RailArrangeInput {
  device: RailDevice
  surface: RailSurface
  /** The effective configuration the rail is drawing, read at the moment a drop lands.
   *
   *  A getter rather than a value because the host resolves its configuration further down
   *  its own render than a hook may be called from - so a value would be one render stale
   *  exactly where staleness is a silently wrong write. */
  config: () => RailConfig
  /** Persist an edited effective configuration. Scope routing belongs to the caller. */
  save: (next: RailConfig) => void
  /** The element an outside press ends the mode from. Anything inside it is "in the rail". */
  rootSelector?: string
}

export function useRailArrange({ device, surface, config, save, rootSelector = '.terminal-action-rail' }: RailArrangeInput): RailArrangeController {
  const [arranging, setArranging] = useState(false)
  const [preview, setPreview] = useState<RailArrangePreview>(NO_RAIL_ARRANGE_PREVIEW)
  const [catalogOpen, setCatalogOpen] = useState(false)
  // Every layout this arrange session replaced, newest last. A drag is cheap to make and a
  // drop on the bin is destructive, so the way back has to be one press rather than a trip
  // through Settings. Dropped when the mode closes: this is an undo for the gesture, not a
  // history of the configuration, which is durable and editable elsewhere either way.
  const [undoStack, setUndoStack] = useState<RailConfig[]>([])
  const cancelDrag = useRef<(() => void) | null>(null)

  // Read by listeners that are registered once, so they see the current render's values
  // without being rebound on every frame of a live drag.
  const latest = useRef({ config, device, surface, save, arranging })
  latest.current = { config, device, surface, save, arranging }
  const exit = useRef(() => {})
  exit.current = () => {
    cancelDrag.current?.()
    cancelDrag.current = null
    setArranging(false)
    setCatalogOpen(false)
    setUndoStack([])
    setPreview(NO_RAIL_ARRANGE_PREVIEW)
  }

  useEffect(() => {
    if (!arranging) return
    // Bubble phase, deliberately after the drag driver's own capture-phase Escape: a drag in
    // flight puts the chip back and the mode stays open, so the two escapes are ordered the
    // way a hand expects rather than collapsing into one.
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); exit.current() } }
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Element && event.target.closest(rootSelector)) return
      exit.current()
    }
    window.addEventListener('keydown', key)
    window.addEventListener('pointerdown', outside, true)
    return () => {
      window.removeEventListener('keydown', key)
      window.removeEventListener('pointerdown', outside, true)
    }
  }, [arranging, rootSelector])

  // A drag that outlives its host would keep `claimPointerDrag`'s process-wide counter up,
  // which silently stands every other gesture in the app down.
  useEffect(() => () => { cancelDrag.current?.(); cancelDrag.current = null }, [])

  const commit = (next: RailConfig) => {
    setUndoStack(stack => [...stack, latest.current.config()].slice(-RAIL_ARRANGE_UNDO_DEPTH))
    latest.current.save(next)
  }

  const host: RailArrangeDragHost = {
    rowLength: rowId => railRowAt(latest.current.config(), latest.current.device, latest.current.surface, rowId)?.items.length ?? 0,
    setPreview,
    commit: (source: RailArrangeSource, target: RailArrangeTarget) => {
      const { device: on, surface: at } = latest.current
      const next = applyRailArrange(latest.current.config(), on, at, source, target)
      if (next) commit(next)
    },
    onEnd: () => { cancelDrag.current = null },
  }

  return {
    arranging,
    preview,
    catalogOpen,
    canUndo: undoStack.length > 0,
    enter: () => { setUndoStack([]); setPreview(NO_RAIL_ARRANGE_PREVIEW); setArranging(true) },
    exit: () => exit.current(),
    toggleCatalog: () => setCatalogOpen(open => !open),
    undo: () => {
      const previous = undoStack[undoStack.length - 1]
      if (!previous) return
      latest.current.save(previous)
      setUndoStack(stack => stack.slice(0, -1))
    },
    caretFor: rowId => preview.caret && preview.caret.rowId === rowId ? preview.caret.at : null,
    // The press lands on the row container, never on a chip: chips are `pointer-events:none`
    // while arranging, which is what keeps a pad from opening its fan and an arrow from
    // repeating under a gesture that means to move them. So the chip is found by rectangle.
    beginChipDrag: event => {
      if (!latest.current.arranging || event.button !== 0 || !event.isPrimary) return
      const row = event.target instanceof Element ? event.target.closest<HTMLElement>('[data-rail-row]') : null
      if (!row) return
      const rowId = (row.dataset.railRow || '').split('|')[2]
      const chip = railChipAtPoint(row, event.clientX, event.clientY)
      if (!rowId || !chip) return
      const slot = Number(chip.dataset.railSlot)
      if (!Number.isInteger(slot)) return
      const ref: RailRef = { device: latest.current.device, surface: latest.current.surface, rowId, index: slot }
      const label = chip.getAttribute('aria-label') || chip.textContent?.trim() || 'Action'
      cancelDrag.current?.()
      cancelDrag.current = beginRailArrangeDrag(event, host, { kind: 'chip', ref }, chip.dataset.reorderId || null, label)
    },
    beginCatalogDrag: (event, itemId, label) => {
      if (!latest.current.arranging || event.button !== 0 || !event.isPrimary) return
      cancelDrag.current?.()
      cancelDrag.current = beginRailArrangeDrag(event, host, { kind: 'catalog', itemId }, null, label)
    },
  }
}
