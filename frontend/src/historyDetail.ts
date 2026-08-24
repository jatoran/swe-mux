// The single-session History view's section model: which sections start open, what each
// one says while it is closed, and how the behavioural timeline is cut down to a preview.
//
// It is pure so the rules that are easy to regress are pinned by a unit test rather than
// only by a screenshot: a conversation opens on its transcript with every other band
// collapsed (the transcript is what the reader came for, and expanded bands leave it a
// strip at the bottom of the view), and a closed band still says the thing a reader would
// otherwise expand it to find - except where that line is longer than the band it labels.

/** A band of the detail view that collapses. The transcript is one of them. */
export type HistorySectionKey = 'stats' | 'commits' | 'lineage' | 'notes' | 'timeline' | 'transcript'

export const HISTORY_SECTION_KEYS: readonly HistorySectionKey[] = ['stats', 'commits', 'lineage', 'notes', 'timeline', 'transcript']

export type HistorySectionState = Record<HistorySectionKey, boolean>

/** How many timeline entries the section shows before the "earlier entries" disclosure. */
export const HISTORY_TIMELINE_PREVIEW = 2

/**
 * What a freshly opened conversation looks like: the transcript, and nothing else.
 *
 * Every band above the transcript is metadata about the run, and each one that opens by
 * default is height subtracted from the only thing the reader came for. Their closed lines
 * already carry the fact most readers want - how the run ended, how many commits, what the
 * newest one was - and expanding is one click, so the cheaper default is closed.
 *
 * This is the same on a phone and on a desktop. It used to differ, which meant the view a
 * reader learned on one device was not the view they got on the other.
 *
 * Defaults are applied when a conversation is opened rather than watched on a media query,
 * so a reader who expands a section keeps it expanded while they read, and a window dragged
 * across the breakpoint does not fold up what they were reading.
 */
export function defaultHistorySections(): HistorySectionState {
  return { stats: false, commits: false, lineage: false, notes: false, timeline: false, transcript: true }
}

/**
 * Bands whose closed line is suppressed, leaving the title alone.
 *
 * The general rule is that a closed band says what a reader would open it to find, and it
 * holds while that line is a count. Commits is the exception: its line carries a whole
 * commit subject, so on any realistic width the "collapsed" band is two wrapped lines of
 * prose - taller than several of the bands it sits between, and the opposite of what
 * collapsing it was for. Open, the rows say the same thing and say it per commit.
 */
export const HISTORY_QUIET_WHEN_CLOSED: readonly HistorySectionKey[] = ['commits']

/** Whether a band's summary line is drawn, given whether the band is open. */
export function sectionKeysVisible(key: HistorySectionKey, open: boolean): boolean {
  return open || !HISTORY_QUIET_WHEN_CLOSED.includes(key)
}

/**
 * The behavioural timeline's preview split: the newest `limit` entries, then the rest.
 *
 * Sorted here rather than trusted from the daemon, which returns this run's records
 * oldest-first: "the two most recent" is the contract this view states, and a reader
 * cannot tell a mis-ordered preview from a run that did those things in that order.
 * Ties keep the daemon's own order, so equal timestamps stay stable across renders.
 */
export function splitRecentScans<T extends { t0?: number | null }>(
  records: readonly T[],
  limit: number = HISTORY_TIMELINE_PREVIEW,
): { recent: T[]; earlier: T[] } {
  const newestFirst = records
    .map((record, index) => ({ record, index }))
    .sort((a, b) => ((b.record.t0 || 0) - (a.record.t0 || 0)) || (a.index - b.index))
    .map(item => item.record)
  const cut = Math.max(0, limit)
  return { recent: newestFirst.slice(0, cut), earlier: newestFirst.slice(cut) }
}

/** `n items` with the singular spelled out, for a closed section's summary line. */
export function countLabel(count: number, noun: string, plural = `${noun}s`): string {
  return `${count} ${count === 1 ? noun : plural}`
}

export type HistoryCommitLike = { commitOid: string; subject?: string; observedAt?: number }

/**
 * What the Commits section says once it is open: how many, and the newest one.
 *
 * It is not drawn while the section is closed - see `HISTORY_QUIET_WHEN_CLOSED`.
 *
 * Newest by `observedAt` rather than by position, for the same reason the timeline sorts:
 * the caller's order is the provenance read's, which is not promised to be chronological.
 */
export function commitsSummary(
  commits: readonly HistoryCommitLike[],
  shortSha: (oid: string) => string,
): string {
  if (!commits.length) return 'no commits'
  const newest = commits.reduce((best, item) => ((item.observedAt || 0) > (best.observedAt || 0) ? item : best))
  const subject = (newest.subject || '').trim()
  return `${countLabel(commits.length, 'commit')} · latest ${shortSha(newest.commitOid)}${subject ? ` ${subject}` : ''}`
}

export type HistoryStatSource = {
  final_state?: string; exit_reason?: string; model?: string; cost_usd?: number
  last_message_at?: number | null; last_message_role?: 'user' | 'assistant' | null
}

/** How the final conversational message is attributed, in the app's own words. */
export function speakerLabel(role?: 'user' | 'assistant' | null): string {
  return role === 'assistant' ? 'agent' : role === 'user' ? 'you' : 'message'
}

/**
 * The four figures the closed stats section shows: what became of the run, what model it
 * ran on, when it last said anything, and what it cost.
 *
 * Everything else the section knows - context percentages, token and cache counts,
 * transcript size, provider identity, compaction evidence, abandoned branches, and the
 * start time the results list already shows on the row - is one expansion away. The cut is
 * "what decides whether to open this conversation at all" against "what is read once it is
 * open", which is why cost is here and token counts are not.
 *
 * The model is returned as its raw id and rendered by `ModelName`, so this stays pure and
 * one component keeps owning how a model is displayed.
 */
export function historyKeyStats(
  entry: HistoryStatSource,
  format: { timestamp: (value?: number | null) => string; money: (value: number) => string },
): { label: string; value: string; model?: boolean }[] {
  return [
    { label: 'state', value: entry.exit_reason || entry.final_state || 'indexed' },
    { label: 'model', value: entry.model || '', model: true },
    { label: `last ${speakerLabel(entry.last_message_role)}`, value: format.timestamp(entry.last_message_at) },
    { label: 'cost', value: format.money(entry.cost_usd || 0) },
  ]
}
