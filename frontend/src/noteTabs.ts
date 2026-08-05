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

export function fallbackNoteTab(selected: string | null, notes: readonly NoteTabRecord[]): string {
  const canonical = canonicalNoteTabId(selected)
  if (canonical === SCRATCHPAD_TAB_ID) return canonical
  if (canonical && notes.some(note => projectNoteTabId(note.note_id) === canonical)) return canonical
  return notes.length ? projectNoteTabId(notes[0].note_id) : SCRATCHPAD_TAB_ID
}

/** Keep spatial continuity after deletion: use the next tab, then the previous tab, and
 * reach Scratchpad only when no Project note remains. */
export function noteTabAfterDelete(
  selected: string | null,
  deletedNoteId: string,
  notesBeforeDelete: readonly NoteTabRecord[],
): string | null {
  const deletedTab = projectNoteTabId(deletedNoteId)
  if (canonicalNoteTabId(selected) !== deletedTab) return null
  const index = notesBeforeDelete.findIndex(note => note.note_id === deletedNoteId)
  const remaining = notesBeforeDelete.filter(note => note.note_id !== deletedNoteId)
  if (!remaining.length) return SCRATCHPAD_TAB_ID
  return projectNoteTabId(remaining[Math.min(Math.max(index, 0), remaining.length - 1)].note_id)
}
