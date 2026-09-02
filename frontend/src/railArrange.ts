// In-place Action rail arrangement: the pure rules a drop obeys.
//
// This is not the Actions editor's drag (`railDrag.ts`), and the difference is the whole
// reason the module exists. The editor draws every configured chip, so an index into what
// it drew *is* the index into the stored row. A live rail draws a filtered projection -
// backend gating (`railItemVisible`), the mutually exclusive built-ins that resolve to
// nothing at render time, and the mobile Enter end-cap each remove a chip the row still
// holds - so an index measured against pixels has to be translated back before it may
// touch a config. Getting that wrong is silent: the layout saves, the drop looks right,
// and an action nobody could see has moved.
//
// Everything here is a total function over data. The DOM half is `railArrangeDrag.ts`.

import {
  RAIL_DEVICES,
  type RailConfig, type RailDevice, type RailRow, type RailScopeKind, type RailSurface,
} from './commandRail.ts'
import {
  addRailRow, insertRailItem, moveRailEntry, railEntryId, railRowAt, removeRailEntry,
  type RailRef,
} from './railLayout.ts'

/** How many arrangement steps the panel's Undo can walk back.
 *
 *  Bounded, and dropped when the mode closes, because this is an undo for the *gesture* and
 *  not a history of the configuration - the layout itself is already durable and already
 *  editable in Settings. Deep enough that a run of drags is recoverable, shallow enough that
 *  the stack cannot become the thing an operator reasons about. */
export const RAIL_ARRANGE_UNDO_DEPTH = 20

/** What is being dragged: a chip already placed, or an entry from the catalog tray. */
export type RailArrangeSource =
  | { kind: 'chip'; ref: RailRef }
  | { kind: 'catalog'; itemId: string }

/**
 * Where it would land. `index` on a row is a **stored** insertion point, already
 * translated out of rendered coordinates by `storedInsertIndex` - a target that still
 * carried a rendered index would be one more thing that reads correctly and saves wrong.
 */
export type RailArrangeTarget =
  | { kind: 'row'; rowId: string; index: number }
  | { kind: 'remove' }
  | { kind: 'new-row' }

/**
 * The stored insertion index a drop at rendered position `renderedIndex` implies.
 *
 * `slots` is the stored position of each *rendered* chip of the target row, ascending, in
 * a config the dragged chip has already been taken out of (`slotsWithoutDragged`).
 * Dropping before rendered chip k means dropping before whatever stored slot k occupies,
 * which leaves any filtered-out neighbour ahead of it still ahead of it.
 *
 * Past the last rendered chip the answer is the row's end rather than one past the last
 * rendered slot. The two differ only when a row ends with chips this session filters out,
 * and "I dropped it at the end of the row" is the reading that survives changing session.
 */
export function storedInsertIndex(slots: readonly number[], renderedIndex: number, rowLength: number): number {
  if (!Number.isFinite(renderedIndex)) return rowLength
  const at = Math.max(0, Math.floor(renderedIndex))
  return at < slots.length ? slots[at] : rowLength
}

/**
 * The same stored positions, renumbered for a config the dragged chip has left.
 *
 * `removed` is the dragged chip's stored index when it came out of *this* row, and null
 * when it came from another row or from the catalog - which is also why this cannot be
 * folded into the caller: a cross-row drop must not renumber anything.
 */
export function slotsWithoutDragged(slots: readonly number[], removed: number | null): number[] {
  if (removed === null) return [...slots]
  return slots.filter(slot => slot !== removed).map(slot => slot > removed ? slot - 1 : slot)
}

/**
 * Where the insertion caret is drawn, counted over *every* rendered chip of the row.
 *
 * The hit test ignores the chip being dragged, so its answer is an index among peers; the
 * row still draws the dragged chip (dimmed rather than lifted out, so nothing reflows under
 * the finger mid-drag), so the caret has to step over it. `draggedRenderedIndex` is -1 when
 * the dragged chip is not in this row at all.
 */
export function caretPosition(renderedIndex: number, draggedRenderedIndex: number): number {
  if (draggedRenderedIndex < 0) return renderedIndex
  return renderedIndex >= draggedRenderedIndex ? renderedIndex + 1 : renderedIndex
}

/**
 * Hold a measuring point inside the band the row's chips actually occupy.
 *
 * `dropIndexForPoint` is written for the editor's wrapped rows, where a point below every
 * chip legitimately means "past the last line". On a single-line strip every chip shares one
 * line, so the same rule reads a point a few pixels above or below the chips - the strip's
 * own vertical padding, or the forgiveness margin outside the row - as one end of the row or
 * the other, and a drop aimed squarely between two chips lands at an end nobody chose.
 *
 * Only the vertical axis is clamped. Horizontally, past the first or last chip is exactly
 * what it looks like.
 */
export function chipBandY(rects: readonly { top: number; bottom: number }[], y: number): number {
  if (!rects.length) return y
  let top = Infinity
  let bottom = -Infinity
  for (const rect of rects) {
    top = Math.min(top, rect.top)
    bottom = Math.max(bottom, rect.bottom)
  }
  if (!Number.isFinite(top) || !Number.isFinite(bottom) || bottom < top) return y
  return Math.min(Math.max(y, top), bottom)
}

/**
 * Drop empty rows, so a chip dragged out of the last slot of a row takes the row with it.
 *
 * Only a row that is *stored* empty: a row emptied by backend filtering still holds items
 * for another session and is not this session's to delete. A labelled row is kept as well -
 * the label is authored state and pruning it would lose something nobody dropped - and the
 * surface always keeps one row, so there is always somewhere to drop.
 */
export function pruneEmptyRailRows(config: RailConfig, device: RailDevice, surface: RailSurface): RailConfig {
  const rows = config.layouts[device]?.[surface] || []
  const keep = rows.filter(row => row.items.length || row.label)
  if (keep.length === rows.length) return config
  const next: RailRow[] = keep.length ? keep : [{ ...rows[0], items: [] }]
  const layouts = {} as RailConfig['layouts']
  for (const each of RAIL_DEVICES) {
    layouts[each] = { strip: (config.layouts[each]?.strip || []).map(row => ({ ...row, items: [...row.items] })) }
  }
  layouts[device][surface] = next.map(row => ({ ...row, items: [...row.items] }))
  return { items: config.items.map(item => ({ ...item })), layouts }
}

/**
 * The config a drop would commit, or null when the drop means nothing.
 *
 * Null rather than the unchanged config on purpose: the caller uses it to decide whether
 * anything happened at all, and an "edit" that saves an identical layout still costs a
 * round trip and a settings broadcast to every other device.
 */
export function applyRailArrange(
  config: RailConfig,
  device: RailDevice,
  surface: RailSurface,
  source: RailArrangeSource,
  target: RailArrangeTarget,
): RailConfig | null {
  const itemId = source.kind === 'chip' ? railEntryId(config, source.ref) : source.itemId
  if (itemId === null || !config.items.some(item => item.id === itemId)) return null

  if (target.kind === 'remove') {
    // A catalog entry is not placed anywhere, so there is nothing for the bin to take. It
    // reads as an abort rather than as an error, which is what a drag that changed its mind
    // over the bin actually was.
    if (source.kind !== 'chip') return null
    return pruneEmptyRailRows(removeRailEntry(config, source.ref), device, surface)
  }

  if (target.kind === 'new-row') {
    const base = source.kind === 'chip' ? removeRailEntry(config, source.ref) : config
    const withRow = addRailRow(base, device, surface)
    const rows = withRow.layouts[device][surface]
    const created = rows[rows.length - 1]
    // Pruned *before* the new row exists, so emptying the row a chip came from does not
    // leave a hole behind the one just created.
    const placed = insertRailItem(withRow, itemId, { device, surface, rowId: created.id, index: 0 })
    return pruneEmptyRailRows(placed, device, surface)
  }

  if (!railRowAt(config, device, surface, target.rowId)) return null
  const at = { device, surface, rowId: target.rowId, index: target.index }
  const next = source.kind === 'chip'
    ? moveRailEntry(config, source.ref, at)
    : insertRailItem(config, itemId, at)
  return pruneEmptyRailRows(next, device, surface)
}

/**
 * What the strip above an arranging rail says it is editing.
 *
 * A detached Project owns its whole layout, so its edits stay with it; every other scope
 * writes the shared rail, which is the answer that has to be visible before a drag rather
 * than discovered after one. A delta Project is *mostly* the shared rail - its own rows and
 * actions stay project state (`applyScopedRail`) - and the sentence says so rather than
 * offering a second scope to choose, because a scope control is not something a hand already
 * holding a chip can reach.
 */
export function railArrangeScopeLabel(kind: RailScopeKind): string {
  return kind === 'fork' ? 'Editing this Project’s rail' : 'Editing the global rail'
}

export function railArrangeScopeDetail(kind: RailScopeKind): string {
  switch (kind) {
    case 'fork': return 'This Project has a detached layout. Nothing here reaches any other Project.'
    case 'delta': return 'Shared rows change everywhere. This Project’s own rows and actions stay here.'
    default: return 'Every Project that has not detached its layout sees these changes.'
  }
}
