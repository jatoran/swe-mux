// Scope-aware editing over the Action configuration blob.
//
// The Settings Actions editor works on the *effective* config a project actually renders:
// global layout, or global
// plus a project delta, or a detached fork. Every generic layout edit is applied
// to that effective config with the ordinary pure ops from `railLayout.ts`, and
// this module routes the result back to the scope that owns each piece:
//
//  * fork: the whole edited config is the project's own; write it there.
//  * delta: rows and items are split by ownership (recorded at resolve time), so
//    an edit to a shared row lands in the global scope — visible everywhere, by
//    design — while an edit to a project row or project action stays project
//    state. A delta with nothing left in it is dropped entirely, returning the
//    project to plain inheritance.
//  * global (no override): everything writes to the global scope.
//
// Row creation and catalog additions cannot be routed by diffing (a brand-new
// row has no recorded owner), so they go through the dedicated scoped helpers.

import {
  isProjectRailDelta, newRailRowId, railConfigFromBlob, railProjectScopeKind,
  railRowKey, resolveDeltaScope, writeRailConfigBlob,
  RAIL_BLOB_VERSION, RAIL_DEVICES, RAIL_SURFACES,
  type RailBlob, type RailConfig, type RailDeltaMap, type RailDevice, type RailHide,
  type RailHiddenEntry, type RailItem, type RailProjectDelta, type RailRow, type RailScopeKind,
  type RailSplice, type RailSurface,
} from './commandRail.ts'
import { addRailCatalogItem, deleteRailCatalogItem, toggleRailPlacement } from './railLayout.ts'

export interface ResolvedRail {
  kind: RailScopeKind
  /** The effective config this scope renders (and the one the editors edit). */
  config: RailConfig
  /** Row ids owned by the project delta; empty for global and fork scopes. */
  projectRowIds: ReadonlySet<string>
  /** Catalog item ids owned by the project delta; empty for global and fork. */
  projectItemIds: ReadonlySet<string>
  /** What this project hides from each shared row, by `railRowKey`. The editors
   *  render these as ghost chips, which is the only way back from a hide. */
  hiddenEntries: ReadonlyMap<string, readonly RailHiddenEntry[]>
  /** Ids this project placed into each shared row itself, by `railRowKey`. */
  projectPlacements: ReadonlyMap<string, ReadonlySet<string>>
}

/**
 * Is this entry of this shared row the project's own placement, or the row's
 * definition?
 *
 * The one rule both the editors and `applyScopedRail` route by, and it answers
 * from the *id* rather than the position — which is what lets an arbitrarily
 * dragged-about row be split back apart afterwards. `resolveDeltaScope` enforces
 * the one-owner-per-id rule that makes it exact.
 */
export function isProjectRailPlacement(
  resolved: ResolvedRail,
  device: RailDevice,
  surface: RailSurface,
  rowId: string,
  itemId: string,
): boolean {
  return resolved.projectItemIds.has(itemId)
    || !!resolved.projectPlacements.get(railRowKey(device, surface, rowId))?.has(itemId)
}

const EMPTY: ReadonlySet<string> = new Set()
const EMPTY_HIDDEN: ReadonlyMap<string, readonly RailHiddenEntry[]> = new Map()
const EMPTY_PLACEMENTS: ReadonlyMap<string, ReadonlySet<string>> = new Map()

const projectDelta = (blob: RailBlob | undefined, projectId: string): RailProjectDelta | null => {
  const override = blob?.projects?.[projectId]
  return isProjectRailDelta(override) ? override : null
}

/** Resolve a scope with ownership, for the editors. `railConfigFromBlob` is the
 *  render-path equivalent that returns only the config. */
export function resolveRail(blob: RailBlob | undefined, projectId?: string): ResolvedRail {
  const kind = railProjectScopeKind(blob, projectId)
  if (kind === 'delta' && projectId) {
    const delta = projectDelta(blob, projectId)
    if (delta) return { kind, ...resolveDeltaScope(railConfigFromBlob(blob), delta) }
  }
  return {
    kind,
    config: railConfigFromBlob(blob, projectId),
    projectRowIds: EMPTY,
    projectItemIds: EMPTY,
    hiddenEntries: EMPTY_HIDDEN,
    projectPlacements: EMPTY_PLACEMENTS,
  }
}

function writeDelta(blob: RailBlob | undefined, projectId: string, delta: RailProjectDelta | null): RailBlob {
  const projects = { ...(blob?.projects || {}) }
  if (delta) projects[projectId] = delta
  else delete projects[projectId]
  return { ...(blob || {}), version: RAIL_BLOB_VERSION, projects }
}

/** True when a delta carries nothing — no items, no rows with content, and no
 *  shared-row overlay. An empty project row is kept: it is the drop target the
 *  user just created. */
function deltaIsEmpty(delta: RailProjectDelta): boolean {
  if (delta.items?.length) return false
  for (const device of RAIL_DEVICES) {
    for (const surface of RAIL_SURFACES) {
      if (delta.layouts?.[device]?.[surface]?.length) return false
      if (delta.splices?.[device]?.[surface]?.length) return false
      if (delta.hides?.[device]?.[surface]?.length) return false
    }
  }
  return true
}

/**
 * Split one edited **shared** row back into its global definition and this
 * project's overlay.
 *
 * The classification is a rule rather than a diff, which is what makes it total:
 * an entry is project-local exactly when the resolution said that id was this
 * project's in this row. Everything else is the row's own definition and
 * round-trips to global, so editing a shared row from a project view behaves
 * precisely as it did before splices existed.
 *
 * The hidden occurrences are written back into the definition at the indices they
 * held there — the project cannot see them, so it cannot have moved them, and
 * dropping them would delete them for every project.
 */
function unresolveSharedRow(
  edited: readonly string[],
  hidden: readonly RailHiddenEntry[],
  placements: ReadonlySet<string>,
  projectItemIds: ReadonlySet<string>,
  rowId: string,
): { items: string[]; splices: RailSplice[]; hides: RailHide[] } {
  const hiddenIds = new Set(hidden.map(entry => entry.item))
  const isProjectLocal = (id: string) => projectItemIds.has(id) || placements.has(id)
  const items: string[] = []
  const splices: RailSplice[] = []
  edited.forEach((id, index) => {
    if (!isProjectLocal(id)) { items.push(id); return }
    // The anchor is whatever the operator left this chip sitting behind, so a move
    // is recorded as a new anchor rather than as an index that global will shift.
    splices.push({ row: rowId, item: id, after: index > 0 ? edited[index - 1] : null })
  })
  for (const entry of [...hidden].sort((a, b) => a.index - b.index)) {
    items.splice(Math.max(0, Math.min(entry.index, items.length)), 0, entry.item)
  }
  // A hide is (row, item) and takes every occurrence, so several hidden entries
  // for one id collapse to the single record that produced them.
  return { items, splices, hides: [...hiddenIds].map(item => ({ row: rowId, item })) }
}

/**
 * Write an edited effective config back to the blob, splitting by ownership.
 * `resolved` must be the resolution the edit was applied against — its ownership
 * sets, splice positions and hidden entries are what route each row and item.
 */
export function applyScopedRail(
  blob: RailBlob | undefined,
  projectId: string | undefined,
  resolved: ResolvedRail,
  next: RailConfig,
): RailBlob {
  if (projectId && resolved.kind === 'fork') return writeRailConfigBlob(blob, next, projectId)
  if (!projectId || resolved.kind === 'global') return writeRailConfigBlob(blob, next)
  // Delta: shared pieces to the global scope, project pieces to the delta.
  const globalItems = next.items.filter(item => !resolved.projectItemIds.has(item.id))
  const ownItems = next.items.filter(item => resolved.projectItemIds.has(item.id))
  const globalLayouts = {} as RailConfig['layouts']
  const deltaLayouts: RailProjectDelta['layouts'] = {}
  const deltaSplices: RailProjectDelta['splices'] = {}
  const deltaHides: RailProjectDelta['hides'] = {}
  for (const device of RAIL_DEVICES) {
    globalLayouts[device] = { strip: [] }
    for (const surface of RAIL_SURFACES) {
      const shared: RailRow[] = []
      const own: RailRow[] = []
      const splices: RailSplice[] = []
      const hides: RailHide[] = []
      for (const row of next.layouts[device]?.[surface] || []) {
        if (resolved.projectRowIds.has(row.id)) { own.push({ ...row, items: [...row.items] }); continue }
        const key = railRowKey(device, surface, row.id)
        const hidden = resolved.hiddenEntries.get(key) || []
        const placements = resolved.projectPlacements.get(key) || EMPTY
        const split = unresolveSharedRow(row.items, hidden, placements, resolved.projectItemIds, row.id)
        shared.push({ ...row, items: split.items })
        splices.push(...split.splices)
        hides.push(...split.hides)
      }
      globalLayouts[device][surface] = shared
      if (own.length) {
        deltaLayouts[device] = deltaLayouts[device] || {}
        deltaLayouts[device]![surface] = own
      }
      if (splices.length) {
        deltaSplices[device] = deltaSplices[device] || {}
        deltaSplices[device]![surface] = splices
      }
      if (hides.length) {
        deltaHides[device] = deltaHides[device] || {}
        deltaHides[device]![surface] = hides
      }
    }
  }
  const delta: RailProjectDelta = {
    mode: 'delta',
    ...(ownItems.length ? { items: ownItems } : {}),
    ...(Object.keys(deltaLayouts).length ? { layouts: deltaLayouts } : {}),
    ...(Object.keys(deltaSplices).length ? { splices: deltaSplices } : {}),
    ...(Object.keys(deltaHides).length ? { hides: deltaHides } : {}),
  }
  const withGlobal = writeRailConfigBlob(blob, { items: globalItems, layouts: globalLayouts })
  return writeDelta(withGlobal, projectId, deltaIsEmpty(delta) ? null : delta)
}

/** Add a project-owned row to a device surface, creating the delta if needed. */
export function addProjectRailRow(
  blob: RailBlob | undefined,
  projectId: string,
  device: RailDevice,
  surface: RailSurface,
  label = 'Project',
): RailBlob {
  if (railProjectScopeKind(blob, projectId) === 'fork') return blob || {}
  const current = projectDelta(blob, projectId) || { mode: 'delta' as const }
  const layouts: RailProjectDelta['layouts'] = { ...(current.layouts || {}) }
  const deviceRows = { ...(layouts[device] || {}) }
  deviceRows[surface] = [...(deviceRows[surface] || []), { id: newRailRowId(), ...(label ? { label } : {}), items: [] }]
  layouts[device] = deviceRows
  return writeDelta(blob, projectId, { ...current, layouts })
}

/** Replace one device/surface list inside a delta map, dropping the entry when
 *  the list is empty so an undone overlay leaves nothing behind. */
function writeDeltaRecords<T>(
  map: RailDeltaMap<T> | undefined,
  device: RailDevice,
  surface: RailSurface,
  records: T[],
): RailDeltaMap<T> | undefined {
  const next: RailDeltaMap<T> = { ...(map || {}) }
  const forDevice = { ...(next[device] || {}) }
  if (records.length) forDevice[surface] = records
  else delete forDevice[surface]
  if (Object.keys(forDevice).length) next[device] = forDevice
  else delete next[device]
  return Object.keys(next).length ? next : undefined
}

const deltaList = <T>(map: RailDeltaMap<T> | undefined, device: RailDevice, surface: RailSurface): T[] =>
  [...(map?.[device]?.[surface] || [])]

/**
 * Hide one shared-row button in this project only.
 *
 * The subtractive mirror of a splice, and the thing that removes the other common
 * reason to fork: without it, "everything shared except that one button" costs a
 * detached copy that no later global improvement reaches. The shared row keeps the
 * button for every other project, and unhiding restores it because the definition
 * was never touched.
 *
 * Refused on a fork (which owns its rows outright and should just delete the
 * entry) and creates the delta on a project that had no override yet.
 */
export function hideScopedRailEntry(
  blob: RailBlob | undefined,
  projectId: string,
  itemId: string,
  device: RailDevice,
  surface: RailSurface,
  rowId: string,
): RailBlob {
  if (railProjectScopeKind(blob, projectId) === 'fork') return blob || {}
  const current = projectDelta(blob, projectId) || { mode: 'delta' as const }
  const hides = deltaList(current.hides, device, surface)
  if (hides.some(hide => hide.row === rowId && hide.item === itemId)) return blob || {}
  hides.push({ row: rowId, item: itemId })
  // A splice of the same id into the same row is what a project-local *move*
  // looks like, so hiding an item the project already spliced back keeps both.
  return writeDelta(blob, projectId, { ...current, hides: writeDeltaRecords(current.hides, device, surface, hides) })
}

/** Undo a hide: the shared row's own definition supplies the button again. */
export function unhideScopedRailEntry(
  blob: RailBlob | undefined,
  projectId: string,
  itemId: string,
  device: RailDevice,
  surface: RailSurface,
  rowId: string,
): RailBlob {
  const current = projectDelta(blob, projectId)
  if (!current) return blob || {}
  const hides = deltaList(current.hides, device, surface).filter(hide => !(hide.row === rowId && hide.item === itemId))
  // Any splice of that id into that row was the project-local move the hide made
  // room for; without the hide it would render as a duplicate, so it goes too.
  const splices = deltaList(current.splices, device, surface).filter(splice => !(splice.row === rowId && splice.item === itemId))
  const next: RailProjectDelta = {
    ...current,
    hides: writeDeltaRecords(current.hides, device, surface, hides),
    splices: writeDeltaRecords(current.splices, device, surface, splices),
  }
  if (!next.hides) delete next.hides
  if (!next.splices) delete next.splices
  return writeDelta(blob, projectId, deltaIsEmpty(next) ? null : next)
}

export type RailAddTarget = 'global' | 'project'

/**
 * Add a catalog item and place it on both devices' chosen surface.
 * `project` scope appends to (or creates) a project row on each device; on a
 * fork, everything is fork state regardless of the requested target.
 */
export function addScopedRailItem(
  blob: RailBlob | undefined,
  projectId: string | undefined,
  item: RailItem,
  target: RailAddTarget = 'global',
): RailBlob {
  const kind = railProjectScopeKind(blob, projectId)
  if (projectId && kind === 'fork') {
    return writeRailConfigBlob(blob, addRailCatalogItem(railConfigFromBlob(blob, projectId), item), projectId)
  }
  if (!projectId || target === 'global') {
    return writeRailConfigBlob(blob, addRailCatalogItem(railConfigFromBlob(blob), item))
  }
  // Refuse an id the effective catalog already has, mirroring addRailCatalogItem.
  if (railConfigFromBlob(blob, projectId).items.some(entry => entry.id === item.id)) return blob || {}
  const current = projectDelta(blob, projectId) || { mode: 'delta' as const }
  const surface: RailSurface = 'strip'
  const layouts: RailProjectDelta['layouts'] = { ...(current.layouts || {}) }
  for (const device of RAIL_DEVICES) {
    const deviceRows = { ...(layouts[device] || {}) }
    const rows = [...(deviceRows[surface] || [])]
    if (!rows.length) rows.push({ id: newRailRowId(), label: 'Project', items: [] })
    const last = rows.length - 1
    rows[last] = { ...rows[last], items: [...rows[last].items, item.id] }
    deviceRows[surface] = rows
    layouts[device] = deviceRows
  }
  const items = [...(current.items || []), { ...item }]
  return writeDelta(blob, projectId, { ...current, items, layouts })
}

/** Remove a custom item — wherever it lives — and every button pointing at it,
 *  including the splices that placed it into shared rows. Project rows the
 *  removal empties are pruned, so deleting a project action leaves no stray
 *  delta behind and the project returns to plain inheritance. */
export function removeScopedRailItem(blob: RailBlob | undefined, projectId: string | undefined, itemId: string): RailBlob {
  const resolved = resolveRail(blob, projectId)
  if (projectId && resolved.projectItemIds.has(itemId)) {
    const current = projectDelta(blob, projectId)
    if (!current) return blob || {}
    const layouts: RailProjectDelta['layouts'] = {}
    const splices: RailProjectDelta['splices'] = {}
    for (const device of RAIL_DEVICES) {
      for (const surface of RAIL_SURFACES) {
        const rows = current.layouts?.[device]?.[surface]
        if (rows) {
          const kept = rows
            .map(row => ({ ...row, items: row.items.filter(id => id !== itemId) }))
            .filter((row, index) => row.items.length || rows[index].items.every(id => id !== itemId))
          if (kept.length) {
            layouts[device] = layouts[device] || {}
            layouts[device]![surface] = kept
          }
        }
        // A splice naming a deleted action would resolve to nothing anyway; dropping
        // it here is what keeps the delta from accumulating dead records.
        const remaining = deltaList(current.splices, device, surface).filter(splice => splice.item !== itemId)
        if (remaining.length) {
          splices[device] = splices[device] || {}
          splices[device]![surface] = remaining
        }
      }
    }
    const delta: RailProjectDelta = {
      mode: 'delta',
      ...(current.items?.length ? { items: current.items.filter(item => item.id !== itemId) } : {}),
      ...(Object.keys(layouts).length ? { layouts } : {}),
      ...(Object.keys(splices).length ? { splices } : {}),
      ...(current.hides ? { hides: current.hides } : {}),
    }
    if (!delta.items?.length) delete delta.items
    return writeDelta(blob, projectId, deltaIsEmpty(delta) ? null : delta)
  }
  if (projectId && resolved.kind === 'fork') {
    return writeRailConfigBlob(blob, deleteRailCatalogItem(resolved.config, itemId), projectId)
  }
  return writeRailConfigBlob(blob, deleteRailCatalogItem(railConfigFromBlob(blob), itemId))
}

/** Detach a project from the global layout: freeze its current effective config
 *  as a fork. Deliberate and reversible only by "Use global layout". */
export function detachProjectRail(blob: RailBlob | undefined, projectId: string): RailBlob {
  return writeRailConfigBlob(blob, railConfigFromBlob(blob, projectId), projectId)
}

/**
 * Toggle an item's presence on one device/surface, routed by ownership.
 *
 * A project-owned item may only occupy project rows (a shared row's write to the
 * global scope would drop its id), so enabling one ensures a project row exists
 * on that device/surface first. Everything else is the ordinary effective-config
 * toggle, split by `applyScopedRail`.
 */
export function toggleScopedPlacement(
  blob: RailBlob | undefined,
  projectId: string | undefined,
  resolved: ResolvedRail,
  itemId: string,
  device: RailDevice,
  surface: RailSurface,
): RailBlob {
  if (projectId && resolved.projectItemIds.has(itemId)) {
    const current = projectDelta(blob, projectId)
    if (!current) return blob || {}
    const layouts: RailProjectDelta['layouts'] = { ...(current.layouts || {}) }
    const deviceRows = { ...(layouts[device] || {}) }
    const rows = [...(deviceRows[surface] || [])]
    const present = rows.some(row => row.items.includes(itemId))
    if (present) {
      deviceRows[surface] = rows.map(row => ({ ...row, items: row.items.filter(id => id !== itemId) }))
    } else {
      if (!rows.length) rows.push({ id: newRailRowId(), label: 'Project', items: [] })
      const last = rows.length - 1
      rows[last] = { ...rows[last], items: [...rows[last].items, itemId] }
      deviceRows[surface] = rows
    }
    layouts[device] = deviceRows
    return writeDelta(blob, projectId, { ...current, layouts })
  }
  const next = toggleRailPlacement(resolved.config, itemId, device, surface)
  return applyScopedRail(blob, projectId, resolved, next)
}
