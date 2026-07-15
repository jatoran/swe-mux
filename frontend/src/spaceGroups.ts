import type { Session, Space } from './types'

// Group session-process rows by space, ordered by space position then session
// created_at. Extracted as a pure function so the O(1) Map lookups replace the
// per-comparison sessions.find()/spaces.find() scans (O(n^2 log n) per refresh)
// and the exact fallback/ordering semantics can be unit-tested. Note the operator
// choices are load-bearing: `|| group.space_id || 'unknown'` (empty-string space id
// falls through), `?? Number.MAX_SAFE_INTEGER` (position 0 is honoured), and
// `|| 0` on created_at.
export function buildSpaceGroups<G extends { session_id: string; space_id: string }>(
  sessionGroups: G[],
  sessions: Session[],
  spaces: Space[],
): { id: string; label: string; groups: G[] }[] {
  const sessionById = new Map(sessions.map(item => [item.id, item]))
  const spaceById = new Map(spaces.map(item => [item.id, item]))
  const grouped = new Map<string, G[]>()
  for (const group of sessionGroups) {
    const id = sessionById.get(group.session_id)?.space_id || group.space_id || 'unknown'
    grouped.set(id, [...(grouped.get(id) || []), group])
  }
  return [...grouped]
    .sort(
      ([first], [second]) =>
        (spaceById.get(first)?.position ?? Number.MAX_SAFE_INTEGER) -
        (spaceById.get(second)?.position ?? Number.MAX_SAFE_INTEGER),
    )
    .map(([id, groups]) => ({
      id,
      label: spaceById.get(id)?.name || 'Unknown space',
      groups: groups.sort(
        (first, second) =>
          (sessionById.get(first.session_id)?.created_at || 0) -
          (sessionById.get(second.session_id)?.created_at || 0),
      ),
    }))
}
