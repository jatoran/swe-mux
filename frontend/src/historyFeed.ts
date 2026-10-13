// The History results list's shape: which Project heading each conversation sits under,
// what order the headings come in, and how long the search box waits before asking.
//
// Pure so the ordering rule is pinned by a unit test rather than by looking at the list,
// because the rule is exactly the thing that made a working sort control look broken.

/** How long the search box waits after a keystroke before searching. */
export const HISTORY_SEARCH_DEBOUNCE_MS = 220

/** The wait when the word being typed is one character long.
 *
 *  That is the state between the space and the second letter of the next word, and it is
 *  the broadest search a typist ever sends: `contact s` asks for every message with any
 *  word starting with `s`, measured at 4.3 s against the live archive before the prefix
 *  index existed (2026-10-12). The index makes it cheap, but it is still a search whose
 *  answer is about to be replaced, so it is given time to be. */
export const HISTORY_SEARCH_SHORT_WORD_DEBOUNCE_MS = 450

export function historySearchDelay(query: string): number {
  const words = query.trim().split(/\s+/u).filter(Boolean)
  if (!words.length) return 0
  const last = words[words.length - 1].replace(/[^\p{L}\p{N}_]/gu, '')
  return last.length === 1 ? HISTORY_SEARCH_SHORT_WORD_DEBOUNCE_MS : HISTORY_SEARCH_DEBOUNCE_MS
}

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
