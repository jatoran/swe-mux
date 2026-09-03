// The History results list's shape: which Project heading each conversation sits under,
// and what order the headings come in.
//
// Pure so the ordering rule is pinned by a unit test rather than by looking at the list,
// because the rule is exactly the thing that made a working sort control look broken.

export type HistoryFeedEntry = { project_id?: string; project_label?: string }
export type HistoryFeedProject = { project_id: string | null; label: string; removed_at?: number }
export type HistoryFeedRegistration = { id: string; name: string }
export type HistoryFeedGroup<T> = { label: string; entries: T[] }

/**
 * Bucket a page of history under Project headings, in the feed's own order.
 *
 * The feed arrives sorted - by last activity, or by session start, whichever the browser
 * asked for - and both levels of this listing keep that order: a heading appears where its
 * most recent conversation does, and the conversations under it stay as they came.
 *
 * Ordering the headings *by name* instead was half of why changing the sort looked like it
 * did nothing. The rows under each heading did reorder, but the alphabetically-first
 * Project stayed pinned to the top of a listing that is meant to be in activity order, so
 * the part of the screen anyone looks at first never moved. The Project dropdown is how you
 * ask for one Project; the headings are orientation, not an index.
 *
 * The unassigned bucket still sorts last rather than into the middle of the feed: it is a
 * catch-all rather than a Project, and conversations land in it because mux could not
 * attribute them, not because that is where the work is.
 */
export function groupHistoryFeed<T extends HistoryFeedEntry>(
  items: T[],
  historyProjects: HistoryFeedProject[],
  projects: HistoryFeedRegistration[],
): [string | null, HistoryFeedGroup<T>][] {
  const groups = new Map<string | null, HistoryFeedGroup<T>>()
  for (const entry of items) {
    const key = entry.project_id || null
    const known = historyProjects.find(item => item.project_id === key)
    const configured = projects.find(item => item.id === key)
    const baseLabel = known?.label || configured?.name || entry.project_label || 'Unassigned'
    const group = groups.get(key)
      || { label: known?.removed_at ? `${baseLabel} (removed)` : baseLabel, entries: [] }
    group.entries.push(entry)
    groups.set(key, group)
  }
  // Insertion order is first-appearance order, which is the feed's order. Only the
  // unassigned bucket is moved out of it.
  const appearance = [...groups.keys()]
  return [...groups.entries()].sort(([left], [right]) =>
    Number(left === null) - Number(right === null)
    || appearance.indexOf(left) - appearance.indexOf(right))
}
