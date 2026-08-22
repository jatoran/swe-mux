// Turning a detached Action fork back into a delta.
//
// A fork is a full copy of the layout that stopped tracking global edits, and
// before splices and hides existed it was the only way to put a project-scoped
// action on a shared row. Projects therefore carry forks whose real content is
// "the shared rail, plus three things" — and every global improvement since has
// been passing them by.
//
// This module reads such a fork, diffs it against the *current* global config,
// and emits the delta that reproduces it: project actions, project rows, anchored
// splices for what the fork added to a shared row, and hides for what it took out.
// The shared rows themselves go back to being global, so the project starts
// tracking again.
//
// Two rules keep it honest, because a migration that guesses is worse than one
// that refuses:
//
//  * **It verifies itself.** The candidate delta is resolved by
//    `resolveDeltaScope` — the same function that will render it — and the result
//    is compared row by row against the fork. Nothing is reported as reproduced
//    on the strength of the diff that produced it.
//  * **What it cannot express, it names.** A delta cannot rename a shared row,
//    reorder shared rows, interleave a project row between them, or override a
//    global action's own definition. Each of those becomes an issue on the plan
//    with the global value that would win, and the operator decides with that in
//    front of them rather than discovering it afterwards.
//
// It is never applied automatically. `planForkReattach` is a pure read;
// `applyForkReattach` is what the operator's button calls.

import {
  isProjectRailDelta, railConfigFromBlob, railProjectScopeKind, resolveDeltaScope,
  RAIL_BLOB_VERSION, RAIL_DEVICES, RAIL_SURFACES,
  type RailBlob, type RailConfig, type RailDevice, type RailHide, type RailItem,
  type RailProjectDelta, type RailRow, type RailSplice, type RailSurface,
} from './commandRail.ts'

/** Something the fork does that a delta has no vocabulary for. The plan still
 *  applies; this is what the operator loses (or keeps from global) by applying it. */
export interface ForkReattachIssue {
  kind: 'action-differs' | 'row-label' | 'row-order' | 'row-unreproduced'
  /** What it is about, in the operator's words. */
  subject: string
  detail: string
}

export interface ForkReattachPlan {
  /** The overlay that would replace the fork. */
  delta: RailProjectDelta
  counts: { items: number; rows: number; splices: number; hides: number }
  issues: ForkReattachIssue[]
  /** True when the resolved delta reproduces the fork's layouts exactly. */
  exact: boolean
}

const countOf = (list: readonly string[], id: string): number => list.reduce((n, entry) => n + (entry === id ? 1 : 0), 0)

/** Longest common subsequence of two id sequences. Rows hold tens of entries, so
 *  the quadratic table is free and the exactness is worth more than cleverness. */
function longestCommonSubsequence(a: readonly string[], b: readonly string[]): string[] {
  const table: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1])
    }
  }
  const out: string[] = []
  let i = 0, j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { out.push(a[i]); i += 1; j += 1 }
    else if (table[i + 1][j] >= table[i][j + 1]) i += 1
    else j += 1
  }
  return out
}

/**
 * Which ids of one row have to *float* — leave the shared definition and be
 * re-placed by this project — for the definition's remainder to sit inside the
 * fork's row in the same relative order.
 *
 * Project-owned actions always float: a shared row's definition may never name
 * one. Beyond that, an id floats when its count differs between the two rows, or
 * when keeping it would require the shared entries to be reordered.
 */
function floatingIds(
  globalItems: readonly string[],
  forkItems: readonly string[],
  projectItemIds: ReadonlySet<string>,
  all: boolean,
): Set<string> {
  const float = new Set<string>([...globalItems, ...forkItems].filter(id => all || projectItemIds.has(id)))
  if (all) return float
  for (const id of new Set([...globalItems, ...forkItems])) {
    if (countOf(globalItems, id) !== countOf(forkItems, id)) float.add(id)
  }
  const kept = (list: readonly string[]) => list.filter(id => !float.has(id))
  const common = longestCommonSubsequence(kept(globalItems), kept(forkItems))
  for (const id of new Set(kept(globalItems))) {
    if (countOf(common, id) !== countOf(globalItems, id)) float.add(id)
  }
  return float
}

/** The hides and splices that turn one global row into one fork row. */
function overlayForRow(
  globalItems: readonly string[],
  forkItems: readonly string[],
  rowId: string,
  projectItemIds: ReadonlySet<string>,
  all: boolean,
): { hides: RailHide[]; splices: RailSplice[] } {
  const float = floatingIds(globalItems, forkItems, projectItemIds, all)
  const hides: RailHide[] = [...new Set(globalItems.filter(id => float.has(id)))].map(id => ({ row: rowId, item: id }))
  const splices: RailSplice[] = []
  forkItems.forEach((id, index) => {
    if (!float.has(id)) return
    splices.push({ row: rowId, item: id, after: index > 0 ? forkItems[index - 1] : null })
  })
  return { hides, splices }
}

const sameRow = (a: RailRow | undefined, b: RailRow | undefined): boolean =>
  !!a && !!b && (a.label || '') === (b.label || '') && a.items.length === b.items.length
  && a.items.every((id, index) => id === b.items[index])

type SurfacePlan = { rows: RailRow[]; hides: RailHide[]; splices: RailSplice[] }

function buildDelta(
  global: RailConfig,
  fork: RailConfig,
  projectItems: RailItem[],
  projectItemIds: Set<string>,
  redo: ReadonlySet<string>,
): RailProjectDelta {
  const layouts: RailProjectDelta['layouts'] = {}
  const splices: RailProjectDelta['splices'] = {}
  const hides: RailProjectDelta['hides'] = {}
  for (const device of RAIL_DEVICES) {
    for (const surface of RAIL_SURFACES) {
      const plan: SurfacePlan = { rows: [], hides: [], splices: [] }
      const globalRows = global.layouts[device]?.[surface] || []
      const forkRows = fork.layouts[device]?.[surface] || []
      const forkById = new Map(forkRows.map(row => [row.id, row]))
      for (const row of globalRows) {
        // A shared row the fork dropped is reproduced by hiding everything in it:
        // a row with no surviving entry is not rendered at all.
        const forkRow = forkById.get(row.id)
        const overlay = overlayForRow(
          row.items,
          forkRow ? forkRow.items : [],
          row.id,
          projectItemIds,
          redo.has(`${device}|${surface}|${row.id}`),
        )
        plan.hides.push(...overlay.hides)
        plan.splices.push(...overlay.splices)
      }
      const globalIds = new Set(globalRows.map(row => row.id))
      for (const row of forkRows) {
        if (globalIds.has(row.id)) continue
        plan.rows.push({ ...row, items: [...row.items] })
      }
      if (plan.rows.length) { layouts[device] = layouts[device] || {}; layouts[device]![surface] = plan.rows }
      if (plan.splices.length) { splices[device] = splices[device] || {}; splices[device]![surface] = plan.splices }
      if (plan.hides.length) { hides[device] = hides[device] || {}; hides[device]![surface] = plan.hides }
    }
  }
  return {
    mode: 'delta',
    ...(projectItems.length ? { items: projectItems } : {}),
    ...(Object.keys(layouts).length ? { layouts } : {}),
    ...(Object.keys(splices).length ? { splices } : {}),
    ...(Object.keys(hides).length ? { hides } : {}),
  }
}

/** Compare a candidate delta's resolution against the fork, returning the row
 *  keys that did not come back identical. */
function unreproducedRows(global: RailConfig, fork: RailConfig, delta: RailProjectDelta): string[] {
  const resolved = resolveDeltaScope(global, delta).config
  const bad: string[] = []
  for (const device of RAIL_DEVICES) {
    for (const surface of RAIL_SURFACES) {
      const got = resolved.layouts[device]?.[surface] || []
      const want = fork.layouts[device]?.[surface] || []
      const gotById = new Map(got.map(row => [row.id, row]))
      for (const row of want) {
        // Row *label* and row *order* are reported separately; this is about content.
        const mine = gotById.get(row.id)
        if (!mine || mine.items.length !== row.items.length || mine.items.some((id, i) => id !== row.items[i])) {
          bad.push(`${device}|${surface}|${row.id}`)
        }
      }
    }
  }
  return bad
}

const DEVICE_WORD: Record<RailDevice, string> = { desktop: 'Desktop', mobile: 'Mobile' }
const SURFACE_WORD: Record<RailSurface, string> = { strip: 'rail' }

/** Read a project's fork and produce the delta that reproduces it, with whatever
 *  it cannot express named. Returns null when the project is not forked. */
export function planForkReattach(blob: RailBlob | undefined, projectId: string): ForkReattachPlan | null {
  if (railProjectScopeKind(blob, projectId) !== 'fork') return null
  const global = railConfigFromBlob(blob)
  const fork = railConfigFromBlob(blob, projectId)
  const issues: ForkReattachIssue[] = []

  const globalById = new Map(global.items.map(item => [item.id, item]))
  const projectItems: RailItem[] = []
  for (const item of fork.items) {
    const shared = globalById.get(item.id)
    if (!shared) { projectItems.push({ ...item }); continue }
    // A delta cannot override a global action's own definition: `resolveDeltaScope`
    // drops a colliding id so the base always wins. Say which value survives.
    if (JSON.stringify(shared) !== JSON.stringify(item)) {
      issues.push({
        kind: 'action-differs',
        subject: item.label || item.id,
        detail: `This project's copy differs from the shared action. The shared definition (“${shared.label || shared.id}”) is what it will use.`,
      })
    }
  }
  const projectItemIds = new Set(projectItems.map(item => item.id))

  for (const device of RAIL_DEVICES) {
    for (const surface of RAIL_SURFACES) {
      const globalRows = global.layouts[device]?.[surface] || []
      const forkRows = fork.layouts[device]?.[surface] || []
      const where = `${DEVICE_WORD[device]} ${SURFACE_WORD[surface]}`
      const globalIds = new Set(globalRows.map(row => row.id))
      for (const row of forkRows) {
        const shared = globalRows.find(entry => entry.id === row.id)
        if (shared && (shared.label || '') !== (row.label || '')) {
          issues.push({
            kind: 'row-label',
            subject: `${where}: ${row.label || 'unnamed row'}`,
            detail: `A shared row's name belongs to every project, so it stays “${shared.label || 'unnamed'}”.`,
          })
        }
      }
      // Delta rows are appended after every shared row, and shared rows keep the
      // global order. Either being different in the fork is a real loss, so it is
      // stated rather than silently flattened.
      const sharedOrder = forkRows.filter(row => globalIds.has(row.id)).map(row => row.id)
      const globalOrder = globalRows.filter(row => sharedOrder.includes(row.id)).map(row => row.id)
      if (sharedOrder.join(' ') !== globalOrder.join(' ')) {
        issues.push({
          kind: 'row-order',
          subject: where,
          detail: 'This project ordered the shared rows differently. They will follow the shared order.',
        })
      }
      const lastShared = forkRows.reduce((at, row, index) => globalIds.has(row.id) ? index : at, -1)
      if (forkRows.some((row, index) => !globalIds.has(row.id) && index < lastShared)) {
        issues.push({
          kind: 'row-order',
          subject: where,
          detail: 'A project row sits between shared rows here. Project rows are drawn after the shared ones.',
        })
      }
    }
  }

  // Build, verify, and re-do the rows that did not come back identical with every
  // id floated — which always reproduces a row, at the cost of no longer tracking
  // global changes to it.
  let delta = buildDelta(global, fork, projectItems, projectItemIds, new Set())
  let bad = unreproducedRows(global, fork, delta)
  if (bad.length) {
    delta = buildDelta(global, fork, projectItems, projectItemIds, new Set(bad))
    const stillBad = unreproducedRows(global, fork, delta)
    for (const key of stillBad) {
      const [device, surface, ...rest] = key.split('|')
      issues.push({
        kind: 'row-unreproduced',
        subject: `${DEVICE_WORD[device as RailDevice]} ${SURFACE_WORD[surface as RailSurface]}: row ${rest.join('|')}`,
        detail: 'This row could not be reproduced from the shared layout. Check it after reattaching.',
      })
    }
    bad = stillBad
  }

  // Exactness is judged on what *renders*: an emptied row is not drawn, so a
  // shared row this project dropped is reproduced rather than merely tolerated.
  const resolved = resolveDeltaScope(global, delta).config
  const drawn = (rows: readonly RailRow[]): RailRow[] => rows.filter(row => row.items.length)
  const exact = !bad.length && RAIL_DEVICES.every(device => RAIL_SURFACES.every(surface => {
    const got = drawn(resolved.layouts[device]?.[surface] || [])
    const want = drawn(fork.layouts[device]?.[surface] || [])
    return got.length === want.length && got.every((row, index) => sameRow(row, want[index]))
  }))

  const total = <T>(map: Partial<Record<RailDevice, Partial<Record<RailSurface, T[]>>>> | undefined): number =>
    RAIL_DEVICES.reduce((sum, device) =>
      sum + RAIL_SURFACES.reduce((inner, surface) => inner + (map?.[device]?.[surface]?.length || 0), 0), 0)

  return {
    delta,
    counts: {
      items: projectItems.length,
      rows: total(delta.layouts),
      splices: total(delta.splices),
      hides: total(delta.hides),
    },
    issues,
    exact,
  }
}

/** Replace the project's fork with the plan's delta. User-invoked only: nothing
 *  in the app calls this on its own, because a fork is somebody's arrangement and
 *  a migration that happens to you is one you cannot review first. */
export function applyForkReattach(blob: RailBlob | undefined, projectId: string, plan: ForkReattachPlan): RailBlob {
  if (!isProjectRailDelta(plan.delta)) return blob || {}
  const projects = { ...(blob?.projects || {}) }
  projects[projectId] = plan.delta
  return { ...(blob || {}), version: RAIL_BLOB_VERSION, projects }
}
