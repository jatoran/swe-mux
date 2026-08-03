// Pure helpers for sidebar project rows: which projects are collapsed
// (persisted set) and whether a project can be hidden from the sidebar.
// Kept free of runtime imports so the guard logic can be unit tested under the
// node type-stripping runner; the caller supplies preview leaf ids.
import type { Session } from './types'

export const COLLAPSED_PROJECTS_KEY = 'mux.sidebar.projectCollapsed.v1'

const DEAD_STATES: ReadonlyArray<Session['state']> = ['exited', 'crashed']

/** Compact Project label for the collapsed desktop rail. Hyphenated names use
 *  the first letter and the first letter after the first hyphen (`swe-mux` →
 *  `SM`); other names use their first two letters. */
export function projectInitials(name: string): string {
  const trimmed = name.trim()
  const letters = trimmed.match(/[\p{L}\p{N}]/gu) || []
  const first = letters[0] || ''
  const dash = trimmed.indexOf('-')
  if (dash >= 0) {
    const afterDash = trimmed.slice(dash + 1).match(/[\p{L}\p{N}]/u)?.[0]
    if (afterDash) return `${first}${afterDash}`.toLocaleUpperCase()
  }
  return (letters.slice(0, 2).join('') || '?').toLocaleUpperCase()
}

export function loadCollapsedProjects(raw: string | null): Set<string> {
  if (!raw) return new Set()
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return new Set()
    return new Set(parsed.filter((id): id is string => typeof id === 'string'))
  } catch {
    return new Set()
  }
}

export function serializeCollapsedProjects(ids: Set<string>): string {
  return JSON.stringify([...ids])
}

export function toggleCollapsed(ids: Set<string>, id: string): Set<string> {
  const next = new Set(ids)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}

/** Fold or unfold every Project at once, for the sidebar's collapse-all control.
 *  Collapsing takes the ids on screen; expanding clears the set outright rather than
 *  subtracting them, so ids left behind by a Project that was hidden or deleted while
 *  collapsed cannot survive an explicit "expand everything" and re-fold it if it
 *  comes back. */
export function setAllCollapsed(projectIds: string[], collapsed: boolean): Set<string> {
  return collapsed ? new Set(projectIds) : new Set()
}

/** Sessions that still hold a live process (a "starting" session counts; a
 *  pending optimistic row does not, and exited/crashed rows are inert). */
export function liveSessionsFor(sessions: Session[], projectId: string): Session[] {
  return sessions.filter(
    session =>
      session.project_id === projectId &&
      !session.pending &&
      !DEAD_STATES.includes(session.state),
  )
}

export interface ProjectOpenWork {
  liveSessions: Session[]
  previewIds: string[]
}

export function projectOpenWork(
  sessions: Session[],
  projectId: string,
  previewIds: string[],
): ProjectOpenWork {
  return { liveSessions: liveSessionsFor(sessions, projectId), previewIds }
}

/** A project may be hidden from the sidebar only once nothing live is attached
 *  to it — otherwise hiding would strand running terminals and previews. */
export function canHideProject(work: ProjectOpenWork): boolean {
  return work.liveSessions.length === 0 && work.previewIds.length === 0
}

export function describeOpenWork(work: ProjectOpenWork): string {
  const parts: string[] = []
  if (work.liveSessions.length)
    parts.push(`${work.liveSessions.length} live session${work.liveSessions.length === 1 ? '' : 's'}`)
  if (work.previewIds.length)
    parts.push(`${work.previewIds.length} preview${work.previewIds.length === 1 ? '' : 's'}`)
  return parts.join(' · ')
}
