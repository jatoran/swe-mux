// How one row of the Files explorer's Recent view describes itself.
//
// The view has two kinds of row and they answer "when" in two different currencies: an
// uncommitted change has no timestamp at all (Git does not record one, and the file's mtime
// is exactly the filesystem reading the view exists to avoid), while a committed path has a
// committer date. So a working-tree row states *what* changed and a committed row states
// *how long ago* — rather than inventing a clock for the first or a verb for the second.

export type RecentOrigin = 'working' | 'committed'

export type RecentEntry = {
  origin: RecentOrigin
  /** Two-character `git status --porcelain` code; working-tree rows only. */
  status: string | null
  /** Unix seconds of the newest commit touching this path; committed rows only. */
  committed_at: number | null
}

/** The porcelain code in words. Both columns are consulted: `MM` is staged *and* further
 *  edited, and a reader wants "modified" for it rather than a code they must decode. */
export function describeStatus(code: string | null): string {
  if (!code) return 'changed'
  const flags = code.slice(0, 2)
  if (flags === '??') return 'new'
  if (flags.includes('U')) return 'conflicted'
  if (flags.includes('R')) return 'renamed'
  if (flags.includes('C')) return 'copied'
  if (flags.includes('A')) return 'added'
  if (flags.includes('D')) return 'deleted'
  if (flags.includes('M')) return 'modified'
  if (flags.includes('T')) return 'type changed'
  return 'changed'
}

/** Coarse relative age. Deliberately coarse: this is a sort key made readable, not a
 *  timestamp anybody acts on, and a precise one invites reading it as authoritative when
 *  it is the committer's clock rather than this machine's. */
export function describeAge(committedAt: number, now: number): string {
  const seconds = Math.max(0, now - committedAt)
  if (seconds < 90) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days}d ago`
  const months = Math.round(days / 30)
  if (months < 12) return `${months}mo ago`
  return `${Math.round(months / 12)}y ago`
}

/** The one line a Recent row carries beside its name. */
export function recentEntryTitle(entry: RecentEntry, now = Date.now() / 1000): string {
  if (entry.origin === 'working') return `${describeStatus(entry.status)} · uncommitted`
  if (entry.committed_at === null) return 'committed'
  return describeAge(entry.committed_at, now)
}
