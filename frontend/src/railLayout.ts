// Editing operations over a `RailConfig`.
//
// Every function here is a pure transform: it takes a config and returns a new
// one, sharing nothing mutable with the input. The editor drives a live drag
// preview by applying the same transform it will commit on drop, so what you see
// while dragging is literally the result, not an approximation of it.
//
// Two invariants the operations preserve:
//
//  * a surface always keeps at least one row, so there is always somewhere to
//    drop and somewhere for a newly shipped built-in to land;
//  * rows only ever reference catalog ids, so deleting a command cannot leave a
//    dangling button behind.
//
// Item ids may repeat inside a row and across rows, so a position is addressed
// by (device, surface, rowId, index) rather than by id.

import {
  defaultRowId, isBuiltinRailId, newRailRowId, normalizeRailPad,
  padRingCount, padSlotKey, padSlotKeys, padWedgeCount, parsePadSlotKey,
  RAIL_DEVICES, RAIL_SURFACES,
  type RailConfig, type RailDevice, type RailItem, type RailPadConfig,
  type RailPadSlotKey, type RailPadTriggerMode, type RailRow, type RailSurface,
} from './commandRail.ts'

/** A stored position: which chip, on which device's which surface. */
export interface RailRef {
  device: RailDevice
  surface: RailSurface
  rowId: string
  index: number
}

/** Where a drag would land. Same shape as a ref; named apart because its index
 *  is an insertion point rather than an occupied slot. */
export type RailDropTarget = RailRef

export const sameRailRef = (a: RailRef | null, b: RailRef | null): boolean =>
  !!a && !!b && a.device === b.device && a.surface === b.surface && a.rowId === b.rowId && a.index === b.index

function cloneRows(rows: readonly RailRow[]): RailRow[] {
  return rows.map(row => ({ ...row, items: [...row.items] }))
}

function cloneConfig(config: RailConfig): RailConfig {
  const layouts = {} as RailConfig['layouts']
  for (const device of RAIL_DEVICES) {
    layouts[device] = {
      strip: cloneRows(config.layouts[device]?.strip || []),
    }
  }
  return { items: config.items.map(item => ({ ...item })), layouts }
}

const surfaceRows = (config: RailConfig, device: RailDevice, surface: RailSurface): RailRow[] =>
  config.layouts[device]?.[surface] || []

/** The item id stored at a ref, or null when the ref no longer points anywhere. */
export function railEntryId(config: RailConfig, ref: RailRef): string | null {
  const row = surfaceRows(config, ref.device, ref.surface).find(entry => entry.id === ref.rowId)
  if (!row) return null
  return row.items[ref.index] ?? null
}

export function railRowAt(config: RailConfig, device: RailDevice, surface: RailSurface, rowId: string): RailRow | null {
  return surfaceRows(config, device, surface).find(row => row.id === rowId) || null
}

// ---------------------------------------------------------------------------
// Item placement
// ---------------------------------------------------------------------------

/** Insert an item at a position. The index is clamped, so an over-long index
 *  from a stale measurement appends rather than dropping the item on the floor. */
export function insertRailItem(config: RailConfig, itemId: string, at: RailDropTarget): RailConfig {
  if (!config.items.some(item => item.id === itemId)) return config
  const next = cloneConfig(config)
  const row = railRowAt(next, at.device, at.surface, at.rowId)
  if (!row) return config
  row.items.splice(Math.max(0, Math.min(at.index, row.items.length)), 0, itemId)
  return next
}

export function removeRailEntry(config: RailConfig, ref: RailRef): RailConfig {
  const next = cloneConfig(config)
  const row = railRowAt(next, ref.device, ref.surface, ref.rowId)
  if (!row || ref.index < 0 || ref.index >= row.items.length) return config
  row.items.splice(ref.index, 1)
  return next
}

/**
 * Move a chip. `to.index` is an insertion point into the target row *as it looks
 * with the source already removed*, which is what a hit test that ignores the
 * dragged element naturally produces — and what keeps an in-row nudge from
 * off-by-one'ing itself.
 */
export function moveRailEntry(config: RailConfig, from: RailRef, to: RailDropTarget): RailConfig {
  const itemId = railEntryId(config, from)
  if (itemId === null) return config
  return insertRailItem(removeRailEntry(config, from), itemId, to)
}

/** Copy a chip in place, so the same command can sit in two rows. */
export function duplicateRailEntry(config: RailConfig, ref: RailRef): RailConfig {
  const itemId = railEntryId(config, ref)
  if (itemId === null) return config
  return insertRailItem(config, itemId, { ...ref, index: ref.index + 1 })
}

/** How many times an item appears on each device/surface. The editor renders
 *  this as the catalog's placement summary and checkboxes — the one view that
 *  says at a glance that a command exists on desktop and was never put on the
 *  phone. */
export function railPlacementCounts(config: RailConfig, itemId: string): Record<RailDevice, Record<RailSurface, number>> {
  const counts = {} as Record<RailDevice, Record<RailSurface, number>>
  for (const device of RAIL_DEVICES) {
    counts[device] = { strip: 0 }
    for (const surface of RAIL_SURFACES) {
      for (const row of surfaceRows(config, device, surface)) {
        counts[device][surface] += row.items.filter(id => id === itemId).length
      }
    }
  }
  return counts
}

/** Add an item to a device/surface (appending to its last row) or, if it is
 *  already there, take every copy of it off that surface. Drives the badges. */
export function toggleRailPlacement(config: RailConfig, itemId: string, device: RailDevice, surface: RailSurface): RailConfig {
  if (!config.items.some(item => item.id === itemId)) return config
  const next = cloneConfig(config)
  const rows = next.layouts[device][surface]
  const present = rows.some(row => row.items.includes(itemId))
  if (present) {
    for (const row of rows) row.items = row.items.filter(id => id !== itemId)
    return next
  }
  rows[rows.length - 1].items.push(itemId)
  return next
}

// ---------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------

export function addRailRow(config: RailConfig, device: RailDevice, surface: RailSurface): RailConfig {
  const next = cloneConfig(config)
  next.layouts[device][surface].push({ id: newRailRowId(), items: [] })
  return next
}

/** Remove a row and everything in it. The last row of a surface is emptied
 *  instead of removed, so the surface never loses its drop target. */
export function removeRailRow(config: RailConfig, device: RailDevice, surface: RailSurface, rowId: string): RailConfig {
  const next = cloneConfig(config)
  const rows = next.layouts[device][surface]
  const index = rows.findIndex(row => row.id === rowId)
  if (index < 0) return config
  if (rows.length === 1) rows[0] = { id: rows[0].id, items: [] }
  else rows.splice(index, 1)
  return next
}

export function setRailRowLabel(config: RailConfig, device: RailDevice, surface: RailSurface, rowId: string, label: string): RailConfig {
  const next = cloneConfig(config)
  const row = railRowAt(next, device, surface, rowId)
  if (!row) return config
  const trimmed = label.trim()
  if (trimmed) row.label = trimmed
  else delete row.label
  return next
}

/** Reorder the rows themselves (which strip row sits above which). */
export function moveRailRow(config: RailConfig, device: RailDevice, surface: RailSurface, rowId: string, delta: number): RailConfig {
  const next = cloneConfig(config)
  const rows = next.layouts[device][surface]
  const index = rows.findIndex(row => row.id === rowId)
  const target = index + delta
  if (index < 0 || target < 0 || target >= rows.length) return config
  const [row] = rows.splice(index, 1)
  rows.splice(target, 0, row)
  return next
}

/** Seed one device's surface from the other's, as a one-shot copy. Deliberately
 *  not a live link: the two layouts are independent, and a copy that kept
 *  tracking would be the shared-row model this design exists to avoid. */
export function copyRailSurface(config: RailConfig, from: RailDevice, to: RailDevice, surface: RailSurface): RailConfig {
  if (from === to) return config
  const next = cloneConfig(config)
  // Fresh row ids: the two devices must not share row identity, or a later edit
  // addressed by (device, surface, rowId) could be ambiguous across them.
  next.layouts[to][surface] = surfaceRows(config, from, surface).map(row => ({
    id: newRailRowId(),
    ...(row.label ? { label: row.label } : {}),
    items: [...row.items],
  }))
  if (!next.layouts[to][surface].length) next.layouts[to][surface] = [{ id: defaultRowId(to, surface), items: [] }]
  return next
}

// ---------------------------------------------------------------------------
// Catalog
// ---------------------------------------------------------------------------

/**
 * Add a command and place it. Placing into *both* device layouts is the point:
 * a new button you have to remember to add twice is a button that silently never
 * reaches the phone. Divergence is then a drag away, but it is a deliberate act.
 */
export function addRailCatalogItem(config: RailConfig, item: RailItem, devices: readonly RailDevice[] = RAIL_DEVICES): RailConfig {
  if (config.items.some(entry => entry.id === item.id)) return config
  const next = cloneConfig(config)
  next.items.push({ ...item })
  for (const device of devices) {
    const rows = next.layouts[device].strip
    rows[rows.length - 1].items.push(item.id)
  }
  return next
}

/** Delete a custom command and every button pointing at it. Built-ins are
 *  app-owned: unplacing them is how you get rid of them. */
export function deleteRailCatalogItem(config: RailConfig, itemId: string): RailConfig {
  if (isBuiltinRailId(itemId)) return config
  if (!config.items.some(item => item.id === itemId)) return config
  const next = cloneConfig(config)
  next.items = next.items.filter(item => item.id !== itemId)
  for (const device of RAIL_DEVICES) {
    for (const surface of RAIL_SURFACES) {
      for (const row of next.layouts[device][surface]) row.items = row.items.filter(id => id !== itemId)
    }
  }
  return next
}

/** Update a custom command's own fields (label, payload, backends). */
export function updateRailCatalogItem(config: RailConfig, itemId: string, patch: Partial<RailItem>): RailConfig {
  if (isBuiltinRailId(itemId)) return config
  const index = config.items.findIndex(item => item.id === itemId)
  if (index < 0) return config
  const next = cloneConfig(config)
  next.items[index] = { ...next.items[index], ...patch, id: itemId }
  return next
}

/** Update presentation fields on any catalog item without opening built-in behavior to edits. */
export function updateRailItemPresentation(
  config: RailConfig,
  itemId: string,
  patch: Pick<RailItem, 'display' | 'displayLabel'>,
): RailConfig {
  const index = config.items.findIndex(item => item.id === itemId)
  if (index < 0) return config
  const next = cloneConfig(config)
  const item = next.items[index]
  next.items[index] = {
    ...item,
    ...(patch.display && patch.display !== 'auto' ? { display: patch.display } : {}),
    ...(patch.displayLabel?.trim() ? { displayLabel: patch.displayLabel.trim() } : {}),
  }
  if (!patch.display || patch.display === 'auto') delete next.items[index].display
  if (!patch.displayLabel?.trim()) delete next.items[index].displayLabel
  return next
}

/**
 * Bind, rebind, re-mode or clear one direction of a pad.
 *
 * Open to built-in pads, unlike `updateRailCatalogItem`, and that is the deliberate
 * exception rather than an oversight. A pad is a *container*: which four things it holds
 * is the same kind of choice as which chips sit in a row, not a behaviour the app owns
 * the way it owns what `endSession` does. Locking the shipped four would leave the slot
 * editor able to build new pads and unable to adjust any of the ones anybody has.
 *
 * `item: null` clears the slot. Everything is rebuilt through `normalizeRailPad`, so the
 * stored shape stays canonical and a pad the operator never actually changed compares
 * equal to the shipped definition (see `normalizeRailPad`).
 */
export function updateRailPadSlot(
  config: RailConfig,
  itemId: string,
  slot: RailPadSlotKey,
  binding: { item: string | null; mode?: RailPadTriggerMode },
): RailConfig {
  const index = config.items.findIndex(entry => entry.id === itemId)
  if (index < 0 || config.items[index].type !== 'pad') return config
  const next = cloneConfig(config)
  const pad = next.items[index].pad ?? { wedges: 3, rings: 1, slots: {} }
  const slots = { ...pad.slots }
  if (binding.item === null) delete slots[slot]
  else slots[slot] = { item: binding.item, ...(binding.mode ? { mode: binding.mode } : {}) }
  next.items[index] = { ...next.items[index], pad: normalizeRailPad({ ...pad, slots }) }
  return next
}

/**
 * Change how many wedges or rings a pad divides its fan into.
 *
 * Bindings are carried across **position for position**, as far as the new shape has
 * positions: shrinking always leaves some behind, and dropping those is the honest answer -
 * the alternatives are stacking two actions on one wedge (a silent conflict) or refusing the
 * change (a control nobody touches twice). Growing keeps everything and adds empty wedges,
 * which is the common direction and costs nothing.
 *
 * The centre is untouched by either, because it has no wedge or ring to lose.
 */
export function setRailPadShape(
  config: RailConfig,
  itemId: string,
  shape: { wedges?: number; rings?: number },
): RailConfig {
  const index = config.items.findIndex(entry => entry.id === itemId)
  const current = index >= 0 ? config.items[index] : undefined
  if (!current || current.type !== 'pad') return config
  const pad = current.pad
  const wedges = shape.wedges ?? padWedgeCount(pad)
  const rings = shape.rings ?? padRingCount(pad)
  if (wedges === padWedgeCount(pad) && rings === padRingCount(pad)) return config
  const slots: RailPadConfig['slots'] = {}
  for (const key of padSlotKeys(pad)) {
    const binding = pad?.slots[key]
    if (!binding) continue
    const at = parsePadSlotKey(key)
    // The centre survives every reshape; a wedge survives only if the new shape still has
    // that position.
    if (!at) { slots[key] = binding; continue }
    if (at.wedge < wedges && at.ring < rings) slots[padSlotKey(at.ring, at.wedge)] = binding
  }
  const next = cloneConfig(config)
  next.items[index] = { ...next.items[index], pad: normalizeRailPad({ wedges, rings, slots }) }
  return next
}

// ---------------------------------------------------------------------------
// Hit testing
// ---------------------------------------------------------------------------

export interface RailChipRect {
  key: string
  left: number
  right: number
  top: number
  bottom: number
}

/**
 * The insertion index a pointer implies, measured against the row *without* the
 * chip being dragged.
 *
 * Two properties matter. Excluding the dragged chip makes the result a fixed
 * point: re-measuring after the preview moves it gives the same answer, so a
 * chip hovering over its own new home does not oscillate. And the test is
 * two-dimensional, because the editor wraps a row's chips over several visual
 * lines — a purely horizontal comparison would put every drop on the last line
 * into the middle of the first.
 *
 * Counting rather than scanning is safe because "comes before the pointer in
 * reading order" is monotone along a wrapped flow, so the count *is* the index.
 */
export function dropIndexForPoint(rects: readonly RailChipRect[], draggedKey: string | null, x: number, y: number): number {
  let index = 0
  for (const rect of rects) {
    if (rect.key === draggedKey) continue
    const onPointerLine = y >= rect.top && y <= rect.bottom
    const before = onPointerLine ? (rect.left + rect.right) / 2 < x : (rect.top + rect.bottom) / 2 < y
    if (before) index += 1
  }
  return index
}
