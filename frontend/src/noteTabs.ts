export type NoteTabRecord = {
  note_id: string
  created_at: number
}

export const SCRATCHPAD_TAB_ID = 'global-note:scratchpad'

export function projectNoteTabId(noteId: string): string {
  return `note:${encodeURIComponent(noteId)}`
}

/** Old session-note resources resolve to ordinary notes, so their remembered selection
 * still selects the canonical project-note tab after migration. */
export function canonicalNoteTabId(resourceId: string | null): string | null {
  if (!resourceId) return null
  if (!resourceId.startsWith('sessions:')) return resourceId
  return `note:${resourceId.slice('sessions:'.length)}`
}

/** Creation order is durable while updated_at changes on every edit. */
export function stableProjectNoteTabs<T extends NoteTabRecord>(notes: readonly T[]): T[] {
  return [...notes].sort((left, right) =>
    left.created_at - right.created_at || left.note_id.localeCompare(right.note_id))
}

export function fallbackNoteTab(
  selected: string | null,
  notes: readonly NoteTabRecord[],
  scratchpadEnabled = true,
): string | null {
  const canonical = canonicalNoteTabId(selected)
  if (scratchpadEnabled && canonical === SCRATCHPAD_TAB_ID) return canonical
  if (canonical && notes.some(note => projectNoteTabId(note.note_id) === canonical)) return canonical
  return notes.length ? projectNoteTabId(notes[0].note_id) : scratchpadEnabled ? SCRATCHPAD_TAB_ID : null
}

/** How many notes each Project in a listing owns, keyed by Project id.
 *
 * Built from the whole loaded collection rather than the search results, because the
 * question it answers — is this the Project's only note — is about the Project and not
 * about what the reader is currently filtering for. */
export function projectNoteCounts(notes: readonly { project_id: string }[]): Map<string, number> {
  const counts = new Map<string, number>()
  for (const note of notes) counts.set(note.project_id, (counts.get(note.project_id) ?? 0) + 1)
  return counts
}

/** Whether deleting this note would empty its Project's collection.
 *
 * The daemon owns the rule (`ProjectNoteProtected`); this only lets the UI disable the
 * action and say why instead of offering a delete that is going to be refused. A Project
 * missing from the map has not been counted, so nothing is claimed about it. */
export function lastNoteInProject(
  note: { project_id: string },
  counts: ReadonlyMap<string, number>,
): boolean {
  return counts.get(note.project_id) === 1
}

/** Keep spatial continuity after deletion: use the next tab, then the previous tab, and
 * reach Scratchpad only when no Project note remains. */
export function noteTabAfterDelete(
  selected: string | null,
  deletedNoteId: string,
  notesBeforeDelete: readonly NoteTabRecord[],
  scratchpadEnabled = true,
): string | null {
  const deletedTab = projectNoteTabId(deletedNoteId)
  if (canonicalNoteTabId(selected) !== deletedTab) return null
  const index = notesBeforeDelete.findIndex(note => note.note_id === deletedNoteId)
  const remaining = notesBeforeDelete.filter(note => note.note_id !== deletedNoteId)
  if (!remaining.length) return scratchpadEnabled ? SCRATCHPAD_TAB_ID : null
  return projectNoteTabId(remaining[Math.min(Math.max(index, 0), remaining.length - 1)].note_id)
}
