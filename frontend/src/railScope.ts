// Scope-aware editing over the Action configuration blob.
//
// The editors (the Configure Actions modal and the in-place rail editor) work on
// the *effective* config a project actually renders — global layout, or global
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
//
// Pinning lives here too: turning a discovered skill or a prompt template into a
// placed catalog item in one step, scoped to the project when the source itself
// is project-scoped. It exists so the common act — "give this skill a button" —
// never requires the full editor.

import {
  isProjectRailDelta, newRailRowId, railConfigFromBlob, railItemVisible, railProjectScopeKind,
  resolveDeltaScope, writeRailConfigBlob,
  RAIL_BLOB_VERSION, RAIL_DEVICES, RAIL_SURFACES,
  type RailBlob, type RailConfig, type RailDevice, type RailItem, type RailProjectDelta,
  type RailRow, type RailScopeKind, type RailSurface,
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
}

const EMPTY: ReadonlySet<string> = new Set()

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
    if (delta) {
      const { config, projectRowIds, projectItemIds } = resolveDeltaScope(railConfigFromBlob(blob), delta)
      return { kind, config, projectRowIds, projectItemIds }
    }
  }
  return { kind, config: railConfigFromBlob(blob, projectId), projectRowIds: EMPTY, projectItemIds: EMPTY }
}

function writeDelta(blob: RailBlob | undefined, projectId: string, delta: RailProjectDelta | null): RailBlob {
  const projects = { ...(blob?.projects || {}) }
  if (delta) projects[projectId] = delta
  else delete projects[projectId]
  return { ...(blob || {}), version: RAIL_BLOB_VERSION, projects }
}

/** True when a delta carries nothing — no items and no rows with content. An
 *  empty project row is kept: it is the drop target the user just created. */
function deltaIsEmpty(delta: RailProjectDelta): boolean {
  if (delta.items?.length) return false
  for (const device of RAIL_DEVICES) {
    for (const surface of RAIL_SURFACES) {
      if (delta.layouts?.[device]?.[surface]?.length) return false
    }
  }
  return true
}

/**
 * Write an edited effective config back to the blob, splitting by ownership.
 * `resolved` must be the resolution the edit was applied against — its ownership
 * sets are what route each row and item.
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
  for (const device of RAIL_DEVICES) {
    globalLayouts[device] = { strip: [], panel: [] }
    for (const surface of RAIL_SURFACES) {
      const shared: RailRow[] = []
      const own: RailRow[] = []
      for (const row of next.layouts[device]?.[surface] || []) {
        ;(resolved.projectRowIds.has(row.id) ? own : shared).push({ ...row, items: [...row.items] })
      }
      globalLayouts[device][surface] = shared
      if (own.length) {
        deltaLayouts[device] = deltaLayouts[device] || {}
        deltaLayouts[device]![surface] = own
      }
    }
  }
  const delta: RailProjectDelta = {
    mode: 'delta',
    ...(ownItems.length ? { items: ownItems } : {}),
    ...(Object.keys(deltaLayouts).length ? { layouts: deltaLayouts } : {}),
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
  const surface: RailSurface = item.defaultSurface === 'strip' ? 'strip' : 'panel'
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

/** Remove a custom item — wherever it lives — and every button pointing at it.
 *  Project rows the removal empties are pruned, so pinning and unpinning a
 *  project action leaves no stray delta behind and the project returns to plain
 *  inheritance. */
export function removeScopedRailItem(blob: RailBlob | undefined, projectId: string | undefined, itemId: string): RailBlob {
  const resolved = resolveRail(blob, projectId)
  if (projectId && resolved.projectItemIds.has(itemId)) {
    const current = projectDelta(blob, projectId)
    if (!current) return blob || {}
    const layouts: RailProjectDelta['layouts'] = {}
    for (const device of RAIL_DEVICES) {
      const deviceRows = current.layouts?.[device]
      if (!deviceRows) continue
      for (const surface of RAIL_SURFACES) {
        const rows = deviceRows[surface]
        if (!rows) continue
        const kept = rows
          .map(row => ({ ...row, items: row.items.filter(id => id !== itemId) }))
          .filter((row, index) => row.items.length || rows[index].items.every(id => id !== itemId))
        if (!kept.length) continue
        layouts[device] = layouts[device] || {}
        layouts[device]![surface] = kept
      }
    }
    const delta: RailProjectDelta = {
      mode: 'delta',
      ...(current.items?.length ? { items: current.items.filter(item => item.id !== itemId) } : {}),
      ...(Object.keys(layouts).length ? { layouts } : {}),
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

// ---------------------------------------------------------------------------
// Pinning discovered skills and prompt templates
// ---------------------------------------------------------------------------

const bareName = (text: string | undefined): string => (text || '').trim().replace(/^[/$]/, '').toLowerCase()

/** The catalog item a pinned skill (or slash command) resolves to, if any. The
 *  match is by payload, not by id, so a hand-authored button counts as pinned.
 *  With a backend given, only items that backend can see count: a skill pinned
 *  for Claude must read as unpinned in a Codex session, where pinning creates a
 *  second, Codex-restricted button rather than stealing Claude's. */
export function pinnedSkillItem(config: RailConfig, name: string, backend?: string): RailItem | null {
  const wanted = bareName(name)
  if (!wanted) return null
  return config.items.find(item =>
    (item.type === 'skill' || item.type === 'slash')
    && bareName(item.text) === wanted
    && (!backend || railItemVisible(item, backend))) || null
}

/** The catalog item a pinned prompt template resolves to, if any. */
export function pinnedPromptItem(config: RailConfig, promptKey: string): RailItem | null {
  return config.items.find(item => item.type === 'prompt' && item.promptKey === promptKey) || null
}

function uniquePinId(config: RailConfig, base: string): string {
  let candidate = base, suffix = 1
  while (config.items.some(item => item.id === candidate)) { suffix += 1; candidate = `${base}:${suffix}` }
  return candidate
}

export interface SkillPin {
  /** Bare skill or command name as discovered (no `/`/`$` sigil required). */
  name: string
  label: string
  /** 'command' pins as a literal slash command; anything else as a skill. */
  kind?: 'skill' | 'command'
  /** Restrict the button to the harness the skill was discovered for: the same
   *  name is not guaranteed to exist for any other CLI. */
  backend: string
  /** The skill came from the project's own checkout, so the button belongs to
   *  the project rather than to every project. */
  projectScoped: boolean
}

/** Pin a discovered skill: create a drawer button on both devices, scoped to the
 *  project when the skill itself is project-scoped (and the project not forked). */
export function pinSkill(blob: RailBlob | undefined, projectId: string | undefined, pin: SkillPin): RailBlob {
  const effective = railConfigFromBlob(blob, projectId)
  if (pinnedSkillItem(effective, pin.name, pin.backend)) return blob || {}
  const name = pin.name.trim().replace(/^[/$]/, '')
  if (!name) return blob || {}
  const type = pin.kind === 'command' ? 'slash' : 'skill'
  const item: RailItem = {
    id: uniquePinId(effective, `pin:${type}:${name}`),
    type,
    label: pin.label || name,
    text: name,
    submit: false,
    defaultSurface: 'panel',
    backends: [pin.backend],
  }
  const target: RailAddTarget = pin.projectScoped && projectId ? 'project' : 'global'
  return addScopedRailItem(blob, projectId, item, target)
}

export interface PromptPin {
  key: string
  id: string
  title: string
  /** Backends the template declares; carried as a restriction only when it is one. */
  backends: readonly string[]
  allBackends: readonly string[]
  projectScoped: boolean
}

/** Pin a prompt template: a pointer at the template (never a copy of its body). */
export function pinPrompt(blob: RailBlob | undefined, projectId: string | undefined, pin: PromptPin): RailBlob {
  const effective = railConfigFromBlob(blob, projectId)
  if (pinnedPromptItem(effective, pin.key)) return blob || {}
  const restricted = pin.backends.length === pin.allBackends.length ? undefined : [...pin.backends]
  const item: RailItem = {
    id: uniquePinId(effective, `pin:prompt:${pin.id}`),
    type: 'prompt',
    label: pin.title,
    promptKey: pin.key,
    defaultSurface: 'panel',
    ...(restricted ? { backends: restricted } : {}),
  }
  const target: RailAddTarget = pin.projectScoped && projectId ? 'project' : 'global'
  return addScopedRailItem(blob, projectId, item, target)
}
