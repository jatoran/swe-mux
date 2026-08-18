import type { Session, Project } from './types'

// Group session-process rows by project, ordered by project position then session
// created_at. Extracted as a pure function so the O(1) Map lookups replace the
// per-comparison sessions.find()/projects.find() scans (O(n^2 log n) per refresh)
// and the exact fallback/ordering semantics can be unit-tested. Note the operator
// choices are load-bearing: `|| group.project_id || 'unknown'` (empty-string project id
// falls through), `?? Number.MAX_SAFE_INTEGER` (position 0 is honoured), and
// `|| 0` on created_at.
//
// `focusedSessionId` pins the focused terminal's row to the top of its own Project rather than
// to the top of the listing. Hoisting it above its Project heading would put a row under the
// wrong heading; within the Project it answers "what is *this* session running" without a
// scroll, which is the question the drawer is open for.
export function buildProjectGroups<G extends { session_id: string; project_id: string }>(
  sessionGroups: G[],
  sessions: Session[],
  projects: Project[],
  focusedSessionId: string | null = null,
): { id: string; label: string; groups: G[] }[] {
  const sessionById = new Map(sessions.map(item => [item.id, item]))
  const projectById = new Map(projects.map(item => [item.id, item]))
  const grouped = new Map<string, G[]>()
  for (const group of sessionGroups) {
    const id = sessionById.get(group.session_id)?.project_id || group.project_id || 'unknown'
    grouped.set(id, [...(grouped.get(id) || []), group])
  }
  return [...grouped]
    .sort(
      ([first], [second]) =>
        (projectById.get(first)?.position ?? Number.MAX_SAFE_INTEGER) -
        (projectById.get(second)?.position ?? Number.MAX_SAFE_INTEGER),
    )
    .map(([id, groups]) => ({
      id,
      label: projectById.get(id)?.name || 'Unknown project',
      groups: groups.sort((first, second) => {
        const focus = Number(second.session_id === focusedSessionId) - Number(first.session_id === focusedSessionId)
        return focus || (sessionById.get(first.session_id)?.created_at || 0) -
          (sessionById.get(second.session_id)?.created_at || 0)
      }),
    }))
}
