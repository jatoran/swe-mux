// What a session is called on screen, in one place.
//
// A session carries two names: the `name` it was spawned or renamed with, and the
// `generated_title` an observer produced from what it is actually doing. The title wins
// only while the session is still auto-named, because renaming is the human overriding
// the generator and a later title must not silently take the name back.
//
// The rule is duplicated nowhere else on purpose: it decides sidebar rows, workspace tabs,
// drawer headings, Git provenance, voice, and history, and a surface that spells it out
// itself is the one that eventually disagrees with the sidebar.
//
// Two entry points because the two payload shapes disagree about booleans: a live session
// comes from JSON with `auto_named: boolean`, while a run row comes from SQLite with 0/1.
// Both default to "auto-named" when the field is absent, which is what a row predating the
// column means.

import type { Session } from './types'

/** A history/automation run row, which is the same naming question against SQLite integers. */
export type NamedRun = { name?: string; auto_named?: number; generated_title?: string }

/** The display name of a live session. */
export function sessionDisplayName(session: Session): string {
  return session.auto_named !== false && session.generated_title ? session.generated_title : session.name
}

/** The display name of an ended run, as History and the automation dashboard hold it. */
export function runDisplayName(run: NamedRun): string {
  return run.auto_named !== 0 && run.generated_title ? run.generated_title : run.name || ''
}
