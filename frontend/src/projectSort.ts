// Sidebar ordering: how Projects are sorted at the root and inside Groups, and where
// the Groups themselves sit among them. Pure so the guard logic can be unit tested under
// the node type-stripping runner; the caller supplies the records and the user-action
// recency map.
//
// Project sort is **one global mode** applied to the ungrouped root, every Group, and the
// root ordering that mixes the two. It used to be per section, on the theory that a
// hand-arranged shortlist and an alphabetical pile should coexist; in practice that put a ⇅
// on every section header for a preference nobody varied, so the control moved to a single
// PROJECTS header and the modes collapsed with it.
//
// Group placement then collapsed into that same mode. It was its own setting
// (`sectionSort`) ordering Groups *among Groups* below the whole ungrouped pile, which made
// "Recently used" unable to answer the only question it is asked: a Group whose Project was
// used a minute ago still sat under every root Project, including ones never opened. Under
// any non-manual mode a Group is now a peer of a root Project, keyed by the member that
// leads it (see `bucketStamp`). Manual order keeps the two-tier tree, because hand-placed
// Group positions are a separate order from hand-placed Project positions and interleaving
// them would have no key to interleave by.
import type { Project } from './types'

export type ProjectSortMode =
  | 'custom'
  | 'activity'
  | 'name'
  | 'name-desc'
  | 'created-desc'
  | 'created'

export const SIDEBAR_ORDER_KEY = 'mux.sidebar.order.v1'
const LEGACY_UNGROUPED_BUCKET_ID = 'ungrouped'

export interface ProjectSortOption {
  id: ProjectSortMode
  label: string
  /** Menu subtext; also the header button's tooltip for the active mode. */
  hint: string
}

export const PROJECT_SORT_OPTIONS: ProjectSortOption[] = [
  { id: 'custom', label: 'Manual order', hint: 'The order you dragged them into' },
  { id: 'activity', label: 'Recently used', hint: 'Latest prompt submit or session start first' },
  { id: 'name', label: 'Name (A→Z)', hint: 'Alphabetical, numbers in numeric order' },
  { id: 'name-desc', label: 'Name (Z→A)', hint: 'Reverse alphabetical' },
  { id: 'created-desc', label: 'Newest first', hint: 'Most recently added Project first' },
  { id: 'created', label: 'Oldest first', hint: 'Longest-registered Project first' },
]

const SORT_MODES = new Set<string>(PROJECT_SORT_OPTIONS.map(option => option.id))

export function isProjectSortMode(value: unknown): value is ProjectSortMode {
  return typeof value === 'string' && SORT_MODES.has(value)
}

export function projectSortLabel(mode: ProjectSortMode): string {
  return PROJECT_SORT_OPTIONS.find(option => option.id === mode)?.label || 'Manual order'
}

export interface SidebarOrderPrefs {
  /** How Projects are ordered at the root, inside every Group, and how Groups are
   *  placed among the root Projects. */
  projectSort: ProjectSortMode
  /** Group ids folded shut. Presentation only: a collapsed Group's Projects
   *  keep their slot in the rail, the numbered shortcuts, and every order. */
  collapsed: string[]
}

export const EMPTY_SIDEBAR_ORDER: SidebarOrderPrefs = {
  projectSort: 'custom',
  collapsed: [],
}

/** Legacy per-bucket sort map → the single mode that replaced it. Whichever bucket
 *  the user had actually set wins; with several set, the first in stored key order
 *  does. Arbitrary between two explicit choices, but the alternative is silently
 *  dropping a preference on upgrade, and one ⇅ click re-states it. */
function migrateBucketSort(value: unknown): ProjectSortMode | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  for (const mode of Object.values(value as Record<string, unknown>)) {
    if (isProjectSortMode(mode) && mode !== 'custom') return mode
  }
  return null
}

/** Legacy Group-only sort mode → the single mode that replaced it. Its four modes were a
 *  subset of the Project modes, so the value carries over as-is; only an explicit
 *  Manual is dropped, since it stated nothing that a missing mode does not. Project-level
 *  evidence outranks it below: a device that had both was ordering its Projects by the
 *  Project setting, and that is the one now doing both jobs. */
function migrateSectionSort(value: unknown): ProjectSortMode | null {
  return isProjectSortMode(value) && value !== 'custom' ? value : null
}

export function loadSidebarOrder(raw: string | null): SidebarOrderPrefs {
  if (!raw) return EMPTY_SIDEBAR_ORDER
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return EMPTY_SIDEBAR_ORDER
    const record = parsed as {
      projectSort?: unknown; sort?: unknown
      sectionSort?: unknown; collapsed?: unknown
    }
    return {
      projectSort: isProjectSortMode(record.projectSort)
        ? record.projectSort
        : migrateBucketSort(record.sort) || migrateSectionSort(record.sectionSort) || 'custom',
      collapsed: Array.isArray(record.collapsed)
        ? record.collapsed.filter((id): id is string =>
          typeof id === 'string' && id !== LEGACY_UNGROUPED_BUCKET_ID)
        : [],
    }
  } catch {
    return EMPTY_SIDEBAR_ORDER
  }
}

export function serializeSidebarOrder(prefs: SidebarOrderPrefs): string {
  // Neither the legacy `sort` map nor the legacy `sectionSort` is written back: once this
  // has saved once, the migrations above can never fire again for this device.
  return JSON.stringify({
    projectSort: prefs.projectSort,
    collapsed: prefs.collapsed,
  })
}

/** Drop fold state for Groups that no longer exist, so a recreated Group id cannot
 *  inherit a fold the user never applied to it.
 *
 *  `groupIds` is `null` until the daemon's Groups have actually been fetched, and
 *  then this is the identity. An empty list means "every Group was deleted" and is
 *  destructive by design, which is exactly why the not-yet-loaded case may not be
 *  spelled as one: the caller mounts holding `[]`, so encoding "unloaded" that way
 *  wiped the whole preference on every page load and persisted the wipe.
 */
export function pruneSidebarOrder(
  prefs: SidebarOrderPrefs,
  groupIds: string[] | null,
): SidebarOrderPrefs {
  if (!groupIds) return prefs
  const liveGroups = new Set(groupIds)
  const collapsed = prefs.collapsed.filter(groupId => liveGroups.has(groupId))
  return collapsed.length === prefs.collapsed.length ? prefs : { ...prefs, collapsed }
}

export function isBucketCollapsed(prefs: SidebarOrderPrefs, bucketId: string): boolean {
  return prefs.collapsed.includes(bucketId)
}

export function toggleBucketCollapsed(
  prefs: SidebarOrderPrefs,
  bucketId: string,
): SidebarOrderPrefs {
  return {
    ...prefs,
    collapsed: isBucketCollapsed(prefs, bucketId)
      ? prefs.collapsed.filter(id => id !== bucketId)
      : [...prefs.collapsed, bucketId],
  }
}

/** Fold or unfold every section at once, for the sidebar's collapse-all control.
 *  Collapsing takes the ids on screen; expanding clears the list outright rather
 *  than subtracting them, so a stale id left by a Group that vanished while folded
 *  cannot survive an explicit "expand everything". */
export function setAllBucketsCollapsed(
  prefs: SidebarOrderPrefs,
  bucketIds: string[],
  collapsed: boolean,
): SidebarOrderPrefs {
  return { ...prefs, collapsed: collapsed ? [...new Set(bucketIds)] : [] }
}

export function setProjectSortMode(
  prefs: SidebarOrderPrefs,
  mode: ProjectSortMode,
): SidebarOrderPrefs {
  return prefs.projectSort === mode ? prefs : { ...prefs, projectSort: mode }
}

/** Project recency is daemon-owned and already comparable as epoch seconds. */
export function projectRecency(projects: Project[]): Map<string, number> {
  return new Map(projects.map(project => [project.id, project.last_used_at || 0]))
}

const byName = (a: Project, b: Project) =>
  a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }) ||
  a.id.localeCompare(b.id)

/** Zero means "no evidence" for both stamps, and an unknown Project belongs at the
 *  end of a date ordering in either direction rather than posing as the oldest. */
const byStamp = (first: number, second: number, descending: boolean) => {
  if (!first !== !second) return first ? -1 : 1
  return descending ? second - first : first - second
}

/** Sort one bucket's Projects. `items` must already be in manual (position) order:
 *  the sort is stable, so that order is every mode's tie-break and `custom` is a
 *  pass-through. */
export function sortProjects(
  items: Project[],
  mode: ProjectSortMode,
  recency: Map<string, number>,
): Project[] {
  if (mode === 'custom') return items
  const sorted = [...items]
  if (mode === 'name') return sorted.sort(byName)
  if (mode === 'name-desc') return sorted.sort((a, b) => byName(b, a))
  if (mode === 'activity')
    return sorted.sort((a, b) =>
      byStamp(recency.get(a.id) || 0, recency.get(b.id) || 0, true))
  return sorted.sort((a, b) =>
    byStamp(a.created_at || 0, b.created_at || 0, mode === 'created-desc'))
}

export interface SidebarBucket {
  id: string
  name: string
  items: Project[]
}

/** A Group is as recent as its most recently used Project. An empty or untouched
 *  Group reads as 0 and lands last. */
export function bucketRecency(bucket: SidebarBucket, recency: Map<string, number>): number {
  return bucket.items.reduce((latest, item) => Math.max(latest, recency.get(item.id) || 0), 0)
}

/** The dated key a Group is placed by, borrowed from the member that leads it under the
 *  active mode: the most recent use, the newest registration, or the oldest one. A Group
 *  record itself is not dated — it holds a name and a position — so this is what makes a
 *  Group comparable to a Project at all, and it is the honest key besides: a Group is on
 *  screen for the Projects in it.
 *
 *  Undated members are skipped rather than counted as 0: `byStamp` reads 0 as "no evidence"
 *  and parks it at the end of a date ordering, so letting one unregistered-date Project pull
 *  the minimum to 0 would send a Group full of old Projects to the bottom of "Oldest first".
 *  A Group with nothing measurable in it reads as 0 and lands last in either direction. */
export function bucketStamp(
  bucket: SidebarBucket,
  mode: ProjectSortMode,
  recency: Map<string, number>,
): number {
  if (mode === 'activity') return bucketRecency(bucket, recency)
  const oldestFirst = mode === 'created'
  return bucket.items.reduce((best, item) => {
    const stamp = item.created_at || 0
    if (!stamp) return best
    if (!best) return stamp
    return oldestFirst ? Math.min(best, stamp) : Math.max(best, stamp)
  }, 0)
}

/** One row of the sidebar's root ordering: an ungrouped Project, or a whole Group. */
export type SidebarRootEntry =
  | { kind: 'project'; project: Project }
  | { kind: 'group'; bucket: SidebarBucket }

const entryId = (entry: SidebarRootEntry) =>
  entry.kind === 'project' ? entry.project.id : entry.bucket.id
const entryName = (entry: SidebarRootEntry) =>
  entry.kind === 'project' ? entry.project.name : entry.bucket.name

/** Interleave the ungrouped Projects and the Groups into one root ordering.
 *
 *  Both inputs must already be in manual (position) order: the sort is stable, so the
 *  baseline below is every mode's tie-break, and `custom` is a pass-through that keeps the
 *  two-tier tree — root Projects, then Groups.
 *
 *  Under every other mode a Group is a peer of a root Project, keyed by name or by
 *  `bucketStamp`. Groups used to be a block below the entire ungrouped pile, ordered by
 *  their own setting, which meant a Group could not rise for being used: under "Recently
 *  used" a Group holding this minute's work still sat beneath root Projects that had never
 *  been opened, and the fix is placement rather than a better Group key. */
export function sortRootEntries(
  ungrouped: Project[],
  buckets: SidebarBucket[],
  mode: ProjectSortMode,
  recency: Map<string, number>,
): SidebarRootEntry[] {
  const entries: SidebarRootEntry[] = [
    ...ungrouped.map((project): SidebarRootEntry => ({ kind: 'project', project })),
    ...buckets.map((bucket): SidebarRootEntry => ({ kind: 'group', bucket })),
  ]
  if (mode === 'custom') return entries
  const byEntryName = (a: SidebarRootEntry, b: SidebarRootEntry) =>
    entryName(a).localeCompare(entryName(b), undefined, { numeric: true, sensitivity: 'base' }) ||
    entryId(a).localeCompare(entryId(b))
  if (mode === 'name') return entries.sort(byEntryName)
  if (mode === 'name-desc') return entries.sort((a, b) => byEntryName(b, a))
  const stamp = (entry: SidebarRootEntry) =>
    entry.kind === 'group'
      ? bucketStamp(entry.bucket, mode, recency)
      : mode === 'activity'
        ? recency.get(entry.project.id) || 0
        : entry.project.created_at || 0
  return entries.sort((a, b) => byStamp(stamp(a), stamp(b), mode !== 'created'))
}

/** One rendered child of the tree: a run of consecutive root Projects sharing one
 *  `data-group-id=""` list, or a Group's own section. Interleaving splits the root into
 *  runs, and each run has to be its own list element so a Project dragged between two
 *  Groups resolves to the root rather than to whichever Group it landed nearest. */
export type SidebarRootRow =
  | { kind: 'root'; key: string; items: Project[] }
  | { kind: 'group'; key: string; bucket: SidebarBucket }

export function sidebarRootRows(entries: SidebarRootEntry[]): SidebarRootRow[] {
  const rows: SidebarRootRow[] = []
  for (const entry of entries) {
    if (entry.kind === 'group') {
      rows.push({ kind: 'group', key: `group:${entry.bucket.id}`, bucket: entry.bucket })
      continue
    }
    const last = rows[rows.length - 1]
    if (last && last.kind === 'root') last.items.push(entry.project)
    else rows.push({ kind: 'root', key: `root:${entry.project.id}`, items: [entry.project] })
  }
  // Dragging a Project out of a Group needs somewhere to drop it, so a root list renders
  // even with nothing in it — once, at the top, since with no root Projects there is
  // nothing to interleave it with. Without this, grouping every Project would leave no way
  // to ungroup one by hand.
  if (rows.length && !rows.some(row => row.kind === 'root'))
    rows.unshift({ kind: 'root', key: 'root:empty', items: [] })
  return rows
}

/** Fold a permutation of the *rendered* subset back into the full list, leaving
 *  everything hidden from the sidebar (a Project with `sidebar_visible:false`, a
 *  Group with no Projects in it) in the slot it already held. Without this, a drag
 *  would reorder what is on screen by silently rewriting positions for rows the
 *  user cannot see. */
export function mergeVisibleOrder(all: string[], visible: string[]): string[] {
  const known = new Set(all)
  const moving = visible.filter(id => known.has(id))
  const movingSet = new Set(moving)
  let cursor = 0
  return all.map(id => (movingSet.has(id) ? moving[cursor++] : id))
}
