// Sidebar ordering: how Projects are sorted at the root and inside Groups, and how
// the Groups themselves sit. Pure so the guard logic can be unit tested under the
// node type-stripping runner; the caller supplies the records and the user-action recency map.
//
// Project sort is **one global mode** applied to the ungrouped root and every Group.
// It used to be per section, on the theory that a hand-arranged
// shortlist and an alphabetical pile should coexist; in practice that put a ⇅ on
// every section header for a preference nobody varied, so the control moved to a
// single PROJECTS header and the modes collapsed with it. Group order is a
// separate mode because it sorts different things (see SectionSortMode), but it is
// set from the same control.
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

/** How Groups are ordered. No date modes: a Group record is not dated, and "newest
 *  Group first" does not earn a column on a table that holds a name and a position. */
export type SectionSortMode = 'custom' | 'activity' | 'name' | 'name-desc'

export const SECTION_SORT_OPTIONS: { id: SectionSortMode; label: string; hint: string }[] = [
  { id: 'custom', label: 'Manual order', hint: 'The order you dragged the Groups into' },
  { id: 'activity', label: 'Recently used', hint: 'Group holding the latest user action first' },
  { id: 'name', label: 'Name (A→Z)', hint: 'Alphabetical Group names' },
  { id: 'name-desc', label: 'Name (Z→A)', hint: 'Reverse alphabetical' },
]

const SECTION_MODES = new Set<string>(SECTION_SORT_OPTIONS.map(option => option.id))

export function isSectionSortMode(value: unknown): value is SectionSortMode {
  return typeof value === 'string' && SECTION_MODES.has(value)
}

export function sectionSortLabel(mode: SectionSortMode): string {
  return SECTION_SORT_OPTIONS.find(option => option.id === mode)?.label || 'Manual order'
}

export interface SidebarOrderPrefs {
  /** How Projects are ordered at the root and inside every Group. */
  projectSort: ProjectSortMode
  /** How Groups are ordered. */
  sectionSort: SectionSortMode
  /** Project ids in most-recent-first order. Only explicit prompt submissions and
   *  session starts update this device-local navigation preference. */
  recentProjects: string[]
  /** Group ids folded shut. Presentation only: a collapsed Group's Projects
   *  keep their slot in the rail, the numbered shortcuts, and every order. */
  collapsed: string[]
}

export const EMPTY_SIDEBAR_ORDER: SidebarOrderPrefs = {
  projectSort: 'custom',
  sectionSort: 'custom',
  recentProjects: [],
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

export function loadSidebarOrder(raw: string | null): SidebarOrderPrefs {
  if (!raw) return EMPTY_SIDEBAR_ORDER
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return EMPTY_SIDEBAR_ORDER
    const record = parsed as {
      projectSort?: unknown; sort?: unknown
      sectionSort?: unknown; recentProjects?: unknown; collapsed?: unknown
    }
    const recentProjects = Array.isArray(record.recentProjects)
      ? [...new Set(record.recentProjects.filter((id): id is string => typeof id === 'string' && !!id))]
      : []
    return {
      projectSort: isProjectSortMode(record.projectSort)
        ? record.projectSort
        : migrateBucketSort(record.sort) || 'custom',
      sectionSort: isSectionSortMode(record.sectionSort) ? record.sectionSort : 'custom',
      recentProjects,
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
  // The legacy `sort` map is deliberately not written back: once this has saved
  // once, the migration above can never fire again for this device.
  return JSON.stringify({
    projectSort: prefs.projectSort,
    sectionSort: prefs.sectionSort,
    recentProjects: prefs.recentProjects,
    collapsed: prefs.collapsed,
  })
}

/** Drop state for Groups and Projects that no longer exist. */
export function pruneSidebarOrder(
  prefs: SidebarOrderPrefs,
  bucketIds: string[],
  projectIds?: string[],
): SidebarOrderPrefs {
  const liveBuckets = new Set(bucketIds)
  const collapsed = prefs.collapsed.filter(bucketId => liveBuckets.has(bucketId))
  const liveProjects = projectIds ? new Set(projectIds) : null
  const recentProjects = liveProjects
    ? prefs.recentProjects.filter(projectId => liveProjects.has(projectId))
    : prefs.recentProjects
  return collapsed.length === prefs.collapsed.length
    && recentProjects.length === prefs.recentProjects.length
    ? prefs
    : { ...prefs, collapsed, recentProjects }
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

/** Move one Project to the head of the explicit-action MRU without churning state
 *  when it is already first. Opening or focusing anything never calls this helper. */
export function touchProjectRecency(prefs: SidebarOrderPrefs, projectId: string): SidebarOrderPrefs {
  if (!projectId || prefs.recentProjects[0] === projectId) return prefs
  return {
    ...prefs,
    recentProjects: [projectId, ...prefs.recentProjects.filter(id => id !== projectId)],
  }
}

/** Convert most-recent-first ids into descending ranks consumed by the stable sort. */
export function projectRecency(recentProjects: string[]): Map<string, number> {
  const size = recentProjects.length
  return new Map(recentProjects.map((id, index) => [id, size - index]))
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

/** Sort the Groups. `buckets` must already be in manual Group position order,
 *  which is the stable tie-break and makes `custom` a pass-through - the same
 *  contract as `sortProjects`. */
export function sortBuckets(
  buckets: SidebarBucket[],
  mode: SectionSortMode,
  recency: Map<string, number>,
): SidebarBucket[] {
  if (mode === 'custom') return buckets
  const sorted = [...buckets]
  const byBucketName = (a: SidebarBucket, b: SidebarBucket) =>
    a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }) ||
    a.id.localeCompare(b.id)
  if (mode === 'name') return sorted.sort(byBucketName)
  if (mode === 'name-desc') return sorted.sort((a, b) => byBucketName(b, a))
  return sorted.sort((a, b) =>
    byStamp(bucketRecency(a, recency), bucketRecency(b, recency), true))
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
