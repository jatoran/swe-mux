/**
 * Which note this device is editing *inside the drawer* rather than in a pane.
 *
 * A note has exactly one live editor per browser, and that is a correctness rule rather than
 * a tidiness one. `noteSaveQueue` keys one entry per `(project, resource)` at module scope, so
 * two mounted editors on one note share it: each submits its own whole document, newest wins,
 * and the loser's text is discarded with no conflict for the daemon to detect (the revision
 * each holds is correct). Mounting the second editor is worse still — its load calls
 * `reset`, which drops whatever the first had pending. Two *devices* are safe by contrast:
 * separate queues, separate storage revisions, so the second write 409s into the ordinary
 * conflict banner. Only same-browser duplication is silent, and this module is what prevents
 * it: the drawer and the pane are mutually exclusive hosts, and moving between them is an
 * explicit act.
 *
 * Ownership is **device-local and never touches the layout.** `project.layout` is persisted
 * server-side and shared, so if opening a note in the drawer removed its pane leaf, a phone
 * would rearrange the desktop's panes — which is the opposite of the point. The leaf stays
 * exactly where it is and renders a placeholder while this device's drawer holds the note.
 *
 * Keyed by Project because the drawer is Project-scoped: switching Projects shows that
 * Project's notes, and switching back has to restore what the drawer was holding rather than
 * silently dropping it.
 *
 * Pure and dependency-free so the rules are testable without a DOM.
 */

/** Project id → the note resource id its drawer is editing. */
export type DrawerNoteMap = Readonly<Record<string, string>>

export const DRAWER_NOTE_KEY = 'mux.drawer.note.v1'

export const EMPTY_DRAWER_NOTES: DrawerNoteMap = {}

/**
 * Read the persisted map, discarding anything that is not a `string → non-empty string`
 * entry. Device-local storage is written by older builds and by hand, so a bad shape has to
 * degrade to "no note claimed" rather than throw during boot.
 */
export function parseDrawerNotes(raw: string | null): DrawerNoteMap {
  if (!raw) return EMPTY_DRAWER_NOTES
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    return EMPTY_DRAWER_NOTES
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return EMPTY_DRAWER_NOTES
  const map: Record<string, string> = {}
  for (const [projectId, resourceId] of Object.entries(value as Record<string, unknown>)) {
    if (projectId && typeof resourceId === 'string' && resourceId) map[projectId] = resourceId
  }
  return map
}

export function serializeDrawerNotes(map: DrawerNoteMap): string {
  return JSON.stringify(map)
}

/** The note this Project's drawer is editing, or null. */
export function drawerNoteFor(map: DrawerNoteMap, projectId: string): string | null {
  return (projectId && map[projectId]) || null
}

/** Move a note into the drawer. One per Project: claiming replaces any previous claim. */
export function claimDrawerNote(map: DrawerNoteMap, projectId: string, resourceId: string): DrawerNoteMap {
  if (!projectId || !resourceId) return map
  if (map[projectId] === resourceId) return map
  return { ...map, [projectId]: resourceId }
}

/** Hand the note back to the workspace. */
export function releaseDrawerNote(map: DrawerNoteMap, projectId: string): DrawerNoteMap {
  if (!projectId || !(projectId in map)) return map
  const next = { ...map }
  delete next[projectId]
  return next
}

/**
 * Whether a pane leaf must stand down because the drawer is showing the same note.
 *
 * `drawerOpen` is part of the predicate, not an afterthought: the drawer unmounts with the
 * panel, so a closed drawer holds no editor and the pane has to take the note back. That also
 * keeps the placeholder honest — it never points at a panel that is not on screen. The claim
 * itself survives the close, so reopening the drawer resumes where it left off.
 */
export function isDrawerOwned(
  map: DrawerNoteMap,
  projectId: string,
  resourceId: string,
  drawerOpen: boolean,
): boolean {
  return drawerOpen && !!resourceId && drawerNoteFor(map, projectId) === resourceId
}

/**
 * Drop claims for Projects that no longer exist, so a deleted Project cannot keep a slot in
 * device-local storage forever. Returns the same reference when nothing is stale, so this is
 * safe to run on every Projects refresh without churning state.
 */
export function pruneDrawerNotes(map: DrawerNoteMap, knownProjectIds: readonly string[]): DrawerNoteMap {
  const known = new Set(knownProjectIds)
  const stale = Object.keys(map).filter(projectId => !known.has(projectId))
  if (!stale.length) return map
  const next = { ...map }
  for (const projectId of stale) delete next[projectId]
  return next
}
