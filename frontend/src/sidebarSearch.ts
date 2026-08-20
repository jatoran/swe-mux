/**
 * The sidebar's typed filter over Groups, Projects, and sessions.
 *
 * It hides rows out of the sidebar's own tree rather than drawing a list of its
 * own. The tree is an arrangement the user built - Group sections, Project
 * sections, and session rows whose order and nesting come from each Project's
 * pane layout - and a flat ranked list in its place threw all of that away at the
 * first keystroke: everything on screen moved, and finding a row meant re-reading
 * a column that no longer looked like the one being searched. Filtering in place
 * means the only thing that changes is which rows are still there.
 *
 * What this module produces is therefore three sets of ids, not a list. The host
 * walks its ordinary tree and skips what is missing from them, so nesting,
 * clusters, ordering, and row rendering stay exactly where they already live.
 *
 * Two containment rules make the sets a *tree* filter rather than three
 * independent ones:
 *
 * - A node that matched keeps its subtree. A Group matched by name keeps its
 *   Projects and their sessions; a Project matched by name keeps its sessions.
 *   Without this, typing a Project's name renders it as an empty heading.
 * - A node that is kept keeps its ancestors. A matching session pulls its Project
 *   and that Project's Group back on screen, because a row with no heading over it
 *   does not say where it lives.
 *
 * Ranking still exists, for exactly one purpose: `best` is what Enter commits to
 * before an arrow key moves. It is `fuzzyText.ts`, the same ladder the Settings
 * index uses, so a name typed here and typed there resolve the same way. The rows
 * themselves are never reordered by it - re-sorting a hand-arranged tree behind
 * the user is the one thing this must not do.
 *
 * Pure and DOM-free. The structural input types are deliberately narrower than
 * `Project`/`Session`: this module reads a handful of fields, and declaring the
 * whole payload would make the tests build one.
 */
import { fieldScore, normalizeSearchText } from './fuzzyText.ts'
import { sessionDisplayName } from './sessionNames.ts'

/** How long typing settles before the filter re-runs. Long enough to swallow a
 *  burst of keystrokes, short enough that the list feels attached to the keyboard. */
export const SIDEBAR_SEARCH_DEBOUNCE_MS = 110
/** How long the filter survives with nobody touching it, after which the sidebar
 *  goes back to the tree. A filter is a transient lens, and one left standing over
 *  a sidebar the user walked away from misreports the fleet at a glance. */
export const SIDEBAR_SEARCH_IDLE_MS = 5000
/** How often idleness is checked. Interaction is recorded in a ref rather than in
 *  state, so pointer movement over the tree costs no re-render; the cost of that is
 *  that expiry is polled instead of scheduled, to within one tick. */
export const SIDEBAR_SEARCH_IDLE_TICK_MS = 500

export type SidebarSearchKind = 'project' | 'session'

/** The five fields a Project is matched on. */
export type SearchableProject = { id: string; name: string; root?: string }

/** The fields a session is matched on, plus what the row needs to draw itself. */
export type SearchableSession = {
  id: string
  project_id: string
  name?: string
  auto_named?: boolean
  generated_title?: string
  backend?: string
  state?: string
  created_at?: number
  git?: { branch?: string; worktree?: string | null } | null
}

export type SidebarSearchCandidate = {
  kind: SidebarSearchKind
  /** Project id for a Project row, session id for a session row: what activating it navigates to. */
  id: string
  /** The Project this row lives under, which a session row names as its context. */
  projectId: string
  projectName: string
  /** The row's title, exactly as the sidebar would draw it. */
  label: string
  /** `label`, normalized. The primary match field. */
  key: string
  /** Secondary match text: the Project's name and root, the harness, the branch or worktree. */
  keywords: string
  /** Sidebar reading order. The tie-break, so equally scored rows keep tree order. */
  order: number
  /** Exited or crashed. Still findable - an ended pane stays readable - but outranked. */
  ended: boolean
}

const DEAD_STATES = ['exited', 'crashed']

/**
 * Build the candidate list, in sidebar reading order.
 *
 * `projects` must arrive in the order the tree draws them, because that order is
 * the tie-break and nothing here recovers it. Sessions follow their Project in
 * creation order, which is the order the tree lists the ones its layout has no
 * leaf for; the ones it does are ordered by the pane tree instead, and that
 * distinction is not worth threading a layout through a pure module for, because
 * it only ever decides which of two identically scored rows is drawn first.
 */
export function buildSidebarSearchIndex(
  projects: SearchableProject[],
  sessions: SearchableSession[],
): SidebarSearchCandidate[] {
  const byProject = new Map<string, SearchableSession[]>()
  for (const session of sessions) {
    const bucket = byProject.get(session.project_id)
    if (bucket) bucket.push(session)
    else byProject.set(session.project_id, [session])
  }
  const candidates: SidebarSearchCandidate[] = []
  for (const project of projects) {
    candidates.push({
      kind: 'project',
      id: project.id,
      projectId: project.id,
      projectName: project.name,
      label: project.name,
      key: normalizeSearchText(project.name),
      keywords: normalizeSearchText(project.root || ''),
      order: candidates.length,
      ended: false,
    })
    const children = [...(byProject.get(project.id) || [])].sort(
      (left, right) => (left.created_at || 0) - (right.created_at || 0) || left.id.localeCompare(right.id),
    )
    for (const session of children) {
      // The same rule the row draws by, so what you read is what you match against.
      const label = sessionDisplayName(session) || session.id
      const checkout = session.git?.worktree || session.git?.branch || ''
      candidates.push({
        kind: 'session',
        id: session.id,
        projectId: project.id,
        projectName: project.name,
        label,
        key: normalizeSearchText(label),
        keywords: normalizeSearchText(`${project.name} ${session.backend || ''} ${checkout}`),
        order: candidates.length,
        ended: DEAD_STATES.includes(session.state || ''),
      })
    }
  }
  return candidates
}

// A Project outranks a session it ties with: it is the coarser destination and it
// contains the session, so landing on it is the recoverable half of the mistake.
const KIND_BONUS: Record<SidebarSearchKind, number> = { project: 40, session: 0 }
// Enough that a live session always outranks an ended one of the same name, and
// not so much that an ended session falls behind an unrelated weaker match.
const ENDED_PENALTY = 120

/**
 * How well one candidate answers every term, or `null` for no match at all.
 *
 * Every whitespace-separated term has to match something, so a second word narrows
 * rather than widening - "mux front" keeps the frontend session in swe-mux and not
 * every session in either.
 */
function scoreCandidate(candidate: SidebarSearchCandidate, terms: string[]): number | null {
  let total = 0
  for (const term of terms) {
    const value = fieldScore(candidate.key, candidate.keywords, term)
    if (!value) return null
    total += value
  }
  total += KIND_BONUS[candidate.kind] + Math.max(0, 30 - candidate.key.length / 2)
  return candidate.ended ? total - ENDED_PENALTY : total
}

/** A Group, as the filter needs to see it: a name to match and the Projects it holds. */
export type SearchableGroup = { id: string; name: string; projectIds: string[] }

export type SidebarTreeFilter = {
  /** Group sections still drawn. */
  groups: Set<string>
  /** Project sections still drawn. */
  projects: Set<string>
  /** Session rows still drawn. */
  sessions: Set<string>
  /** Every drawn row in sidebar order - what the arrow keys walk. Group headers are
   *  absent: a Group is a container, and activating one would only fold it. */
  order: SidebarSearchCandidate[]
  /** The highest-ranked row that matched *directly*, or null. What Enter commits to
   *  before an arrow key moves. Rows kept only by containment are never it - landing
   *  on a Project you were shown because one of its sessions matched would go to the
   *  wrong place. */
  best: SidebarSearchCandidate | null
}

/**
 * Decide what the sidebar still draws for `query`.
 *
 * Returns `null` for an empty query, which means *not filtering*: the host draws its
 * ordinary tree untouched. That is not the same as an empty filter, and the
 * distinction is the point - opening the filter must change nothing on screen until
 * a character is typed.
 *
 * `index` must arrive in sidebar reading order (`buildSidebarSearchIndex`), each
 * Project immediately followed by its own sessions, because the containment rules
 * are applied in one pass over it.
 */
export function sidebarTreeFilter(
  index: SidebarSearchCandidate[],
  groups: SearchableGroup[],
  query: string,
): SidebarTreeFilter | null {
  const terms = normalizeSearchText(query).split(' ').filter(Boolean)
  if (!terms.length) return null

  const groupOf = new Map<string, string>()
  for (const group of groups) for (const projectId of group.projectIds) groupOf.set(projectId, group.id)

  const visibleGroups = new Set<string>()
  const visibleProjects = new Set<string>()
  const visibleSessions = new Set<string>()
  // Containers whose own name matched, which is what keeps their contents.
  const wholeGroups = new Set<string>()
  const wholeProjects = new Set<string>()
  const hits: Array<{ candidate: SidebarSearchCandidate; score: number }> = []

  for (const group of groups) {
    const name = normalizeSearchText(group.name)
    if (terms.every(term => fieldScore(name, '', term) > 0)) {
      wholeGroups.add(group.id)
      visibleGroups.add(group.id)
    }
  }

  for (const candidate of index) {
    const groupId = groupOf.get(candidate.projectId) || ''
    const score = scoreCandidate(candidate, terms)
    if (score !== null) hits.push({ candidate, score })
    if (candidate.kind === 'project') {
      if (score === null && !wholeGroups.has(groupId)) continue
      wholeProjects.add(candidate.id)
      visibleProjects.add(candidate.id)
      if (groupId) visibleGroups.add(groupId)
      continue
    }
    if (score === null && !wholeProjects.has(candidate.projectId)) continue
    visibleSessions.add(candidate.id)
    visibleProjects.add(candidate.projectId)
    if (groupId) visibleGroups.add(groupId)
  }

  hits.sort((left, right) => right.score - left.score || left.candidate.order - right.candidate.order)
  return {
    groups: visibleGroups,
    projects: visibleProjects,
    sessions: visibleSessions,
    order: index.filter(candidate => candidate.kind === 'project'
      ? visibleProjects.has(candidate.id)
      : visibleSessions.has(candidate.id)),
    best: hits.length ? hits[0].candidate : null,
  }
}

/** Whether a candidate is the same row as another. Rows are identified by kind and
 *  id together, because a Project and a session can never share an id but a caller
 *  holding one of each should not have to know that. */
export function sameSearchRow(
  left: SidebarSearchCandidate | null,
  right: SidebarSearchCandidate | null,
): boolean {
  return !!left && !!right && left.kind === right.kind && left.id === right.id
}

/**
 * No row is the cursor.
 *
 * The state the filter opens in, and the one it returns to whenever the query is
 * emptied. With nothing typed the sidebar is its ordinary tree rather than a set of
 * results, so there is nothing highlighted and nothing for `Enter` to commit to.
 */
export const NO_SEARCH_CURSOR = -1

/** Where the keyboard cursor actually sits, given a stored index and a list that
 *  shrank under it. Clamped rather than reset, so refining a query keeps the
 *  cursor near what it was on instead of jumping back to the top. An unset cursor
 *  stays unset, and an empty list has nowhere to put one. */
export function clampSearchCursor(cursor: number, length: number): number {
  if (length <= 0 || cursor <= NO_SEARCH_CURSOR) return NO_SEARCH_CURSOR
  return Math.min(cursor, length - 1)
}

/** The cursor after an arrow key, over the drawn rows in sidebar order. Stops at
 *  both ends rather than wrapping: wrapping from the last row back to the first
 *  reads as the selection having been lost. From unset, a step enters the list at
 *  the end it came from. */
export function moveSearchCursor(cursor: number, delta: number, length: number): number {
  if (length <= 0) return NO_SEARCH_CURSOR
  if (cursor <= NO_SEARCH_CURSOR) return delta > 0 ? 0 : length - 1
  return Math.min(Math.max(cursor + delta, 0), length - 1)
}

/** Whether the filter has gone untouched long enough to retire itself. */
export function sidebarSearchExpired(
  lastInteractionAt: number,
  now: number,
  idleMs = SIDEBAR_SEARCH_IDLE_MS,
): boolean {
  return now - lastInteractionAt >= idleMs
}
