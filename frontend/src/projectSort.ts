// Sidebar ordering: how the Projects inside one bucket are sorted, and where the
// buckets themselves sit. Pure so the guard logic can be unit tested under the node
// type-stripping runner; the caller supplies the records and the activity map.
//
// A "bucket" is one sidebar section: a Project Group, or the `ungrouped` remainder
// that the sidebar labels PROJECTS. Sorting is per bucket because groups are how a
// person separates unlike things — one may be a hand-arranged shortlist while
// another is a long alphabetical pile, and a single global mode cannot be both.
import type { Project, Session } from './types'

export type ProjectSortMode =
  | 'custom'
  | 'activity'
  | 'name'
  | 'name-desc'
  | 'created-desc'
  | 'created'

export const UNGROUPED_BUCKET_ID = 'ungrouped'
export const SIDEBAR_ORDER_KEY = 'mux.sidebar.order.v1'

export interface ProjectSortOption {
  id: ProjectSortMode
  label: string
  /** Menu subtext; also the header button's tooltip for the active mode. */
  hint: string
}

export const PROJECT_SORT_OPTIONS: ProjectSortOption[] = [
  { id: 'custom', label: 'Manual order', hint: 'The order you dragged them into' },
  { id: 'activity', label: 'Recently active', hint: 'Latest session activity first' },
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

/** How the sections themselves are ordered. No date modes: neither a Group record
 *  nor the synthetic ungrouped remainder is dated, and "newest Group first" does
 *  not earn a column on a table that holds a name and a position. */
export type SectionSortMode = 'custom' | 'activity' | 'name' | 'name-desc'

export const SECTION_SORT_OPTIONS: { id: SectionSortMode; label: string; hint: string }[] = [
  { id: 'custom', label: 'Manual order', hint: 'The order you dragged the sections into' },
  { id: 'activity', label: 'Recently active', hint: 'Section holding the latest activity first' },
  { id: 'name', label: 'Name (A→Z)', hint: 'Alphabetical, PROJECTS sorted under its own name' },
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
  /** Sort mode per bucket id; an absent bucket is `custom`. */
  sort: Record<string, ProjectSortMode>
  /** How the buckets themselves are ordered. */
  sectionSort: SectionSortMode
  /** Slot the ungrouped bucket occupies among the Groups; null pins it last.
   *  Device-local because the remainder has no record to hang a position on —
   *  Group order itself is server-side, on `ProjectGroup.position`. Only consulted
   *  while `sectionSort` is `custom`, exactly as Project positions are. */
  ungroupedIndex: number | null
  /** Bucket ids folded shut. Presentation only: a collapsed section's Projects
   *  keep their slot in the rail, the numbered shortcuts, and every order. */
  collapsed: string[]
}

export const EMPTY_SIDEBAR_ORDER: SidebarOrderPrefs = {
  sort: {},
  sectionSort: 'custom',
  ungroupedIndex: null,
  collapsed: [],
}

export function loadSidebarOrder(raw: string | null): SidebarOrderPrefs {
  if (!raw) return EMPTY_SIDEBAR_ORDER
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return EMPTY_SIDEBAR_ORDER
    const record = parsed as {
      sort?: unknown; sectionSort?: unknown; ungroupedIndex?: unknown; collapsed?: unknown
    }
    const sort: Record<string, ProjectSortMode> = {}
    if (record.sort && typeof record.sort === 'object' && !Array.isArray(record.sort)) {
      for (const [bucketId, mode] of Object.entries(record.sort as Record<string, unknown>)) {
        if (isProjectSortMode(mode) && mode !== 'custom') sort[bucketId] = mode
      }
    }
    const index = record.ungroupedIndex
    return {
      sort,
      sectionSort: isSectionSortMode(record.sectionSort) ? record.sectionSort : 'custom',
      ungroupedIndex: typeof index === 'number' && Number.isInteger(index) && index >= 0
        ? index
        : null,
      collapsed: Array.isArray(record.collapsed)
        ? record.collapsed.filter((id): id is string => typeof id === 'string')
        : [],
    }
  } catch {
    return EMPTY_SIDEBAR_ORDER
  }
}

export function serializeSidebarOrder(prefs: SidebarOrderPrefs): string {
  // `custom` is the default, so storing it would only grow the blob with entries
  // that mean "nothing set" and keep buckets alive after their Group is deleted.
  const sort = Object.fromEntries(
    Object.entries(prefs.sort).filter(([, mode]) => mode !== 'custom'),
  )
  return JSON.stringify({
    sort,
    sectionSort: prefs.sectionSort,
    ungroupedIndex: prefs.ungroupedIndex,
    collapsed: prefs.collapsed,
  })
}

/** Drop per-bucket state for buckets that no longer exist (deleted Groups). */
export function pruneSidebarOrder(prefs: SidebarOrderPrefs, bucketIds: string[]): SidebarOrderPrefs {
  const live = new Set(bucketIds)
  const sort = Object.fromEntries(
    Object.entries(prefs.sort).filter(([bucketId]) => live.has(bucketId)),
  )
  const collapsed = prefs.collapsed.filter(bucketId => live.has(bucketId))
  return Object.keys(sort).length === Object.keys(prefs.sort).length
    && collapsed.length === prefs.collapsed.length
    ? prefs
    : { ...prefs, sort, collapsed }
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

export function bucketSortMode(prefs: SidebarOrderPrefs, bucketId: string): ProjectSortMode {
  return prefs.sort[bucketId] || 'custom'
}

export function setBucketSortMode(
  prefs: SidebarOrderPrefs,
  bucketId: string,
  mode: ProjectSortMode,
): SidebarOrderPrefs {
  if (bucketSortMode(prefs, bucketId) === mode) return prefs
  const sort = { ...prefs.sort }
  if (mode === 'custom') delete sort[bucketId]
  else sort[bucketId] = mode
  return { ...prefs, sort }
}

/** Live sessions only refresh `last_activity_ts` on the WebSocket, so a project
 *  under load would otherwise re-sort on every output chunk and pull rows out from
 *  under the pointer. Minute granularity keeps "recently active" honest while
 *  bounding how often the list can move. */
const ACTIVITY_GRANULARITY_S = 60

/** Merge the daemon's per-Project history stamp with what live sessions are doing
 *  now. History alone lags a running session; live sessions alone cannot rank the
 *  Projects whose work has already ended, which is most of them. */
export function projectActivity(projects: Project[], sessions: Session[]): Map<string, number> {
  const activity = new Map<string, number>()
  for (const project of projects) activity.set(project.id, project.last_activity || 0)
  for (const session of sessions) {
    if (session.pending || !activity.has(session.project_id)) continue
    const stamp = session.last_activity_ts || session.created_at || 0
    if (!stamp) continue
    const coarse = Math.floor(stamp / ACTIVITY_GRANULARITY_S) * ACTIVITY_GRANULARITY_S
    activity.set(session.project_id, Math.max(activity.get(session.project_id) || 0, coarse))
  }
  return activity
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
  activity: Map<string, number>,
): Project[] {
  if (mode === 'custom') return items
  const sorted = [...items]
  if (mode === 'name') return sorted.sort(byName)
  if (mode === 'name-desc') return sorted.sort((a, b) => byName(b, a))
  if (mode === 'activity')
    return sorted.sort((a, b) =>
      byStamp(activity.get(a.id) || 0, activity.get(b.id) || 0, true))
  return sorted.sort((a, b) =>
    byStamp(a.created_at || 0, b.created_at || 0, mode === 'created-desc'))
}

export interface SidebarBucket {
  id: string
  name: string
  items: Project[]
}

/** A section is as recent as the most recent thing in it, so a Group ranks on the
 *  work inside it rather than on when it was made. An empty one reads as 0 and
 *  lands last, which is also where a Group nobody has used belongs. */
export function bucketActivity(bucket: SidebarBucket, activity: Map<string, number>): number {
  return bucket.items.reduce((latest, item) => Math.max(latest, activity.get(item.id) || 0), 0)
}

/** Sort the sections. `buckets` must already be in manual order (Group position
 *  with the ungrouped remainder in its slot), which is the stable tie-break and
 *  makes `custom` a pass-through — same contract as `sortProjects`. */
export function sortBuckets(
  buckets: SidebarBucket[],
  mode: SectionSortMode,
  activity: Map<string, number>,
): SidebarBucket[] {
  if (mode === 'custom') return buckets
  const sorted = [...buckets]
  // The ungrouped remainder competes on its visible name like any other section;
  // being unnamed in the database is not a reason to exempt it from the ordering
  // the user asked for.
  const byBucketName = (a: SidebarBucket, b: SidebarBucket) =>
    a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }) ||
    a.id.localeCompare(b.id)
  if (mode === 'name') return sorted.sort(byBucketName)
  if (mode === 'name-desc') return sorted.sort((a, b) => byBucketName(b, a))
  return sorted.sort((a, b) =>
    byStamp(bucketActivity(a, activity), bucketActivity(b, activity), true))
}

/** Bucket order: Groups by their server-side position, with the ungrouped remainder
 *  spliced into its device-local slot (clamped, so adding or deleting Groups can
 *  never strand it off the end). */
export function placeUngrouped(groupIds: string[], ungroupedIndex: number | null): string[] {
  const ids = [...groupIds]
  const at = ungroupedIndex === null ? ids.length : Math.max(0, Math.min(ungroupedIndex, ids.length))
  ids.splice(at, 0, UNGROUPED_BUCKET_ID)
  return ids
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
