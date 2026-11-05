import { t } from './localization/index.ts'
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
  if (!code) return t("recentFiles.d67e2e9449")
  const flags = code.slice(0, 2)
  if (flags === '??') return t("recentFiles.11507a0e2f")
  if (flags.includes('U')) return t("recentFiles.34a337336b")
  if (flags.includes('R')) return t("recentFiles.017e9e217a")
  if (flags.includes('C')) return t("recentFiles.fb30593ca3")
  if (flags.includes('A')) return t("recentFiles.279b8a60f4")
  if (flags.includes('D')) return t("recentFiles.1185f37d33")
  if (flags.includes('M')) return t("recentFiles.b80012851c")
  if (flags.includes('T')) return t("recentFiles.fc3ef65af2")
  return t("recentFiles.d67e2e9449")
}

/** Coarse relative age. Deliberately coarse: this is a sort key made readable, not a
 *  timestamp anybody acts on, and a precise one invites reading it as authoritative when
 *  it is the committer's clock rather than this machine's. */
export function describeAge(committedAt: number, now: number): string {
  const seconds = Math.max(0, now - committedAt)
  if (seconds < 90) return t("recentFiles.7ddb44d8a5")
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return t("recentFiles.0e9182800a", { minutes: minutes })
  const hours = Math.round(minutes / 60)
  if (hours < 24) return t("recentFiles.bbfb1e5199", { hours: hours })
  const days = Math.round(hours / 24)
  if (days < 30) return t("recentFiles.c2c3d1fae7", { days: days })
  const months = Math.round(days / 30)
  if (months < 12) return t("recentFiles.ead1a35340", { months: months })
  return t("recentFiles.51e6c1ee73", { value1: Math.round(months / 12) })
}

/** The one line a Recent row carries beside its name. */
export function recentEntryTitle(entry: RecentEntry, now = Date.now() / 1000): string {
  if (entry.origin === 'working') return t("recentFiles.e1985a2b6a", { value1: describeStatus(entry.status) })
  if (entry.committed_at === null) return t("recentFiles.cc962289af")
  return describeAge(entry.committed_at, now)
}
