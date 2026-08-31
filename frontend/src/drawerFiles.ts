/**
 * Which files each Project has open in the drawer's Files tab, and which of them is showing.
 *
 * This is the Files half of `drawerNotes.ts`, and it exists for the same two reasons rather
 * than for symmetry.
 *
 * **A file has one live editor per browser, and that is a correctness rule.** A file opened
 * from the tree is edited through `noteSaveQueue`, which keys one entry per
 * `(project, resource)` at module scope (`fileSaveTarget`). Two mounted editors on one file
 * share it: each submits its whole document, newest wins, and the loser's text is discarded
 * with no conflict for the daemon to detect. So the drawer and a pane are mutually exclusive
 * hosts here exactly as they are for a note, and only the *active* tab is mounted - the other
 * open tabs are rail entries and nothing more, which is what makes a set of them safe.
 *
 * **Ownership is device-local and never touches the layout.** Before this module, opening a
 * file from the drawer inserted a leaf into `project.layout`, which is persisted server-side
 * and shared, so browsing files on a phone permanently rearranged the desktop's panes. That
 * is the same hazard `drawerNotes.ts` was written to avoid, and Files was the surface still
 * committing it.
 *
 * Two things differ from notes, and both follow from files being unbounded where a Project's
 * notes are not. Note tabs are every note in the Project and cannot be closed; file tabs are
 * opened one at a time, close, and are capped, because a rail with four hundred entries is not
 * navigation. And the cap evicts by recency rather than by position, while refusing to evict
 * anything with unsaved work - eviction is a convenience and must never be a way to lose text.
 *
 * Pure and dependency-free so the rules are testable without a DOM.
 */

/** One open file tab. `touched` orders eviction; the array's own order is the rail. */
export type DrawerFileTab = {
  /** Project-relative path, exactly as the daemon's tree reports it. */
  path: string
  /** Monotonic within a Project. Larger is more recently selected. */
  touched: number
}

export type DrawerFileState = {
  /** Rail order, which is the order files were opened. Never re-sorted by use: a rail that
   *  rearranged itself under the pointer is one you cannot build a habit against. */
  open: readonly DrawerFileTab[]
  /** The file being shown, or null when the tab is showing its index (Explorer or Recent). */
  active: string | null
}

/** Project id to that Project's open set. */
export type DrawerFileMap = Readonly<Record<string, DrawerFileState>>

export const DRAWER_FILES_KEY = 'mux.drawer.files.v1'

/**
 * How many files one Project keeps in the rail.
 *
 * Eight rather than a round ten: it is the point where the chips stop being scannable at a
 * phone's drawer width, and the rail is navigation rather than storage - the tree, Recent,
 * and search are all still one click away and none of them forgets anything.
 */
export const DRAWER_FILE_TAB_LIMIT = 8

const EMPTY_STATE: DrawerFileState = { open: [], active: null }

export const EMPTY_DRAWER_FILES: DrawerFileMap = {}

/** The next `touched` value for a Project, derived rather than held in module state so this
 *  module stays pure and a test can assert on exact values. */
function nextTouch(state: DrawerFileState): number {
  return state.open.reduce((highest, tab) => Math.max(highest, tab.touched), 0) + 1
}

/**
 * Read the persisted map, discarding anything that is not the shape this module writes.
 *
 * Device-local storage is written by older builds and by hand, so a bad shape has to degrade
 * to "nothing open" rather than throw during boot. An `active` that is not in `open` is
 * dropped rather than honoured: it would render a file with no way back to its own tab.
 */
export function parseDrawerFiles(raw: string | null): DrawerFileMap {
  if (!raw) return EMPTY_DRAWER_FILES
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    return EMPTY_DRAWER_FILES
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return EMPTY_DRAWER_FILES
  const map: Record<string, DrawerFileState> = {}
  for (const [projectId, stored] of Object.entries(value as Record<string, unknown>)) {
    if (!projectId || !stored || typeof stored !== 'object' || Array.isArray(stored)) continue
    const entry = stored as { open?: unknown; active?: unknown }
    if (!Array.isArray(entry.open)) continue
    const seen = new Set<string>()
    const open: DrawerFileTab[] = []
    for (const item of entry.open) {
      if (!item || typeof item !== 'object' || Array.isArray(item)) continue
      const tab = item as { path?: unknown; touched?: unknown }
      if (typeof tab.path !== 'string' || !tab.path || seen.has(tab.path)) continue
      seen.add(tab.path)
      open.push({ path: tab.path, touched: Number.isFinite(tab.touched) ? Number(tab.touched) : 0 })
    }
    if (!open.length) continue
    const active = typeof entry.active === 'string' && seen.has(entry.active) ? entry.active : null
    map[projectId] = { open, active }
  }
  return map
}

export function serializeDrawerFiles(map: DrawerFileMap): string {
  return JSON.stringify(map)
}

/** A Project's open set. Always the same reference for a Project with nothing open, so this
 *  is safe to call in a render without churning dependency arrays. */
export function drawerFilesFor(map: DrawerFileMap, projectId: string): DrawerFileState {
  return (projectId && map[projectId]) || EMPTY_STATE
}

/** The file this Project's Files tab is showing, or null when it is showing its index. */
export function activeDrawerFile(map: DrawerFileMap, projectId: string): string | null {
  return drawerFilesFor(map, projectId).active
}

export function isDrawerFileOpen(map: DrawerFileMap, projectId: string, path: string): boolean {
  return drawerFilesFor(map, projectId).open.some(tab => tab.path === path)
}

function write(map: DrawerFileMap, projectId: string, state: DrawerFileState): DrawerFileMap {
  if (!state.open.length) {
    if (!(projectId in map)) return map
    const next = { ...map }
    delete next[projectId]
    return next
  }
  return { ...map, [projectId]: state }
}

export type OpenDrawerFileOptions = {
  limit?: number
  /**
   * Paths the cap may not evict, whatever their recency.
   *
   * Taken as a parameter rather than read from the draft cache, so this module stays pure and
   * so the refusal is testable without mounting an editor. The caller passes the files with
   * unsaved edits (`unsavedFilePaths` in `ProjectResource.tsx`).
   */
  keep?: Iterable<string>
}

/**
 * Open a file in the drawer and show it.
 *
 * Already-open is a selection, not a second tab, and it keeps its place in the rail: an
 * editor that reordered its tabs every time you revisited one would make the rail unlearnable.
 *
 * The cap evicts the least recently selected tab that is neither protected nor the one being
 * opened. When every candidate is protected the list is allowed to exceed the cap instead:
 * the cap is a convenience and unsaved text is not, so the tie goes to the text.
 */
export function openDrawerFile(
  map: DrawerFileMap,
  projectId: string,
  path: string,
  options: OpenDrawerFileOptions = {},
): DrawerFileMap {
  if (!projectId || !path) return map
  const limit = Math.max(1, options.limit ?? DRAWER_FILE_TAB_LIMIT)
  const protectedPaths = new Set(options.keep ?? [])
  const state = drawerFilesFor(map, projectId)
  const touched = nextTouch(state)
  // Already open is a selection. Re-selecting the tab that is already showing still counts as
  // a use, so it cannot drift to the back of the eviction queue while you are reading it.
  if (state.open.some(tab => tab.path === path)) {
    return write(map, projectId, {
      open: state.open.map(tab => tab.path === path ? { path, touched } : tab),
      active: path,
    })
  }
  let open = [...state.open, { path, touched }]
  while (open.length > limit) {
    const victim = open
      .filter(tab => tab.path !== path && !protectedPaths.has(tab.path))
      .reduce<DrawerFileTab | null>((oldest, tab) => !oldest || tab.touched < oldest.touched ? tab : oldest, null)
    if (!victim) break
    open = open.filter(tab => tab.path !== victim.path)
  }
  return write(map, projectId, { open, active: path })
}

/** Show an already-open tab. Opens the file when it is not open, so a caller never has to
 *  ask which of the two it meant. */
export function selectDrawerFile(map: DrawerFileMap, projectId: string, path: string): DrawerFileMap {
  return openDrawerFile(map, projectId, path)
}

/** Return the tab to its index (Explorer or Recent) without closing anything. */
export function showDrawerFileIndex(map: DrawerFileMap, projectId: string): DrawerFileMap {
  const state = drawerFilesFor(map, projectId)
  if (!state.active) return map
  return write(map, projectId, { open: state.open, active: null })
}

/**
 * Close one tab.
 *
 * Closing the *showing* tab keeps spatial continuity the way `noteTabAfterDelete` does: the
 * next tab, then the previous one, and the index only when none remains. Closing any other
 * tab leaves the selection alone, because nothing about what you are reading changed.
 */
export function closeDrawerFile(map: DrawerFileMap, projectId: string, path: string): DrawerFileMap {
  const state = drawerFilesFor(map, projectId)
  const index = state.open.findIndex(tab => tab.path === path)
  if (index < 0) return map
  const open = state.open.filter(tab => tab.path !== path)
  if (state.active !== path) return write(map, projectId, { open, active: state.active })
  const active = open.length ? open[Math.min(index, open.length - 1)].path : null
  return write(map, projectId, { open, active })
}

/** Close every tab but this one, and show it. */
export function closeOtherDrawerFiles(map: DrawerFileMap, projectId: string, path: string): DrawerFileMap {
  const state = drawerFilesFor(map, projectId)
  const kept = state.open.find(tab => tab.path === path)
  if (!kept) return map
  if (state.open.length === 1 && state.active === path) return map
  return write(map, projectId, { open: [kept], active: path })
}

/**
 * Whether a pane leaf must stand down because the drawer is showing the same file.
 *
 * Only the *showing* tab is mounted, so only it conflicts: a file that is merely open in the
 * rail has no editor and takes nothing away from a pane. `drawerOpen` is part of the
 * predicate for the reason `isDrawerOwned` gives - the drawer unmounts with the panel, so a
 * closed drawer holds no editor and the pane has to take the file back.
 */
export function isDrawerFileOwned(
  map: DrawerFileMap,
  projectId: string,
  path: string,
  drawerOpen: boolean,
): boolean {
  return drawerOpen && !!path && activeDrawerFile(map, projectId) === path
}

/** Drop entries for Projects that no longer exist. Returns the same reference when nothing is
 *  stale, so this is safe to run on every Projects refresh. */
export function pruneDrawerFiles(map: DrawerFileMap, knownProjectIds: readonly string[]): DrawerFileMap {
  const known = new Set(knownProjectIds)
  const stale = Object.keys(map).filter(projectId => !known.has(projectId))
  if (!stale.length) return map
  const next = { ...map }
  for (const projectId of stale) delete next[projectId]
  return next
}

/**
 * What each open file is called in the rail.
 *
 * A basename, widened by as many parent segments as it takes to be unique among the open set.
 * Two chips both reading `index.ts` is the one label that is worse than no label: it names
 * the wrong file with complete confidence, and the reader has no way to tell from the chip
 * which one they are about to close. Only the colliding names widen, so the common case stays
 * a bare filename.
 */
export function fileTabLabels(paths: readonly string[]): Map<string, string> {
  const segments = new Map<string, string[]>()
  for (const path of paths) segments.set(path, path.split('/').filter(Boolean))
  const labels = new Map<string, string>()
  for (const path of paths) {
    const parts = segments.get(path) || []
    if (!parts.length) {
      labels.set(path, path)
      continue
    }
    let depth = 1
    while (depth < parts.length) {
      const candidate = parts.slice(-depth).join('/')
      const collides = paths.some(other => other !== path
        && (segments.get(other) || []).slice(-depth).join('/') === candidate)
      if (!collides) break
      depth += 1
    }
    labels.set(path, parts.slice(-depth).join('/'))
  }
  return labels
}
