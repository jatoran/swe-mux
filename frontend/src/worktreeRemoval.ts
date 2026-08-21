// Removing worktrees from Map: what is still going, and what may be acted on in bulk.
//
// Two rules this module exists to hold, both learned from the surface getting them
// wrong:
//
//  * **The list owns the removing indication, not the row.** It used to live in the
//    expanded row's local state, so collapsing the row - or simply the API answering
//    before the next overview poll - rendered a worktree being deleted as an ordinary
//    one, and it sat there looking normal for several more seconds before vanishing.
//    A pending set keyed by path, held by the list, cannot do that: a worktree stays
//    dimmed from the click until the *refreshed inventory* no longer contains it,
//    which is the same sentence on the fast path and the slow one.
//  * **A bulk act is the single act repeated, with the same refusals.** Nothing here
//    invents a permission the row does not have: the main tree is never a candidate,
//    a checkout with a live session in it is not offered, and a locked one is Git's
//    to refuse. What bulk adds is that a dirty or unlanded checkout has to be *named*
//    before it can be swept up with thirty others, because "remove 30" is not a
//    sentence anyone can check by reading it.
//
// Explicit `.ts` extension: this module is reachable from the node test runner, whose
// type-stripping ESM loader does not resolve extensionless specifiers.
import { localMeasurement, normalizePath, type GitOverviewWorktree } from './gitWorktrees.ts'

/** Worktrees whose removal was asked for and whose disappearance has not been read back
 *  yet, keyed by normalized path so a separator or case difference cannot hide one. */
export type PendingRemovals = Readonly<Record<string, string>>

export function beginRemovals(pending: PendingRemovals, paths: readonly string[]): PendingRemovals {
  const next = { ...pending }
  for (const path of paths) if (path) next[normalizePath(path)] = path
  return next
}

export function forgetRemoval(pending: PendingRemovals, path: string): PendingRemovals {
  const key = normalizePath(path)
  if (!(key in pending)) return pending
  const next = { ...pending }
  delete next[key]
  return next
}

/**
 * Drop every pending entry the refreshed inventory no longer lists.
 *
 * The inventory is the only thing that ends the indication. A successful response is
 * not enough: on the fast path the daemon has renamed the tree away and the row is
 * already gone, and on the slow path Git is still deleting files while the request
 * is answering. One rule covers both, and neither can make a removing worktree blend
 * back in.
 */
export function settleRemovals(
  pending: PendingRemovals,
  present: readonly { path: string }[],
): PendingRemovals {
  const listed = new Set(present.map(tree => normalizePath(tree.path)))
  const next: Record<string, string> = {}
  for (const [key, path] of Object.entries(pending)) if (listed.has(key)) next[key] = path
  return Object.keys(next).length === Object.keys(pending).length ? pending : next
}

export function isRemoving(pending: PendingRemovals, path: string): boolean {
  return normalizePath(path) in pending
}

/** Why a worktree cannot be removed at all. Each is Git's refusal or the app's own. */
export type RemovalBlock =
  /** The trunk the others are measured against; Git refuses, and it is not a candidate. */
  | 'main'
  /** A process is working in this directory. Its files are open and its agent is not done. */
  | 'live_session'
  /** Git refuses to remove a locked worktree, and unlocking is a deliberate act elsewhere. */
  | 'locked'

/** What a reader has to be told before a removal, even though it may proceed. */
export type RemovalWarning =
  /** Uncommitted files that removal discards. Needs `force`, and Git refuses without it. */
  | 'dirty'
  /** Commits the comparison ref does not have: work that only exists in this checkout. */
  | 'unlanded'
  /** The daemon could not measure one of the two. An unmeasured tree is never called clean. */
  | 'unmeasured'

export type RemovalAssessment = {
  path: string
  branch: string | null
  blocks: RemovalBlock[]
  warnings: RemovalWarning[]
  /** Whether Git will refuse without `--force`. True for an unmeasured tree too: the
   *  claim "this is clean" is the one that must never be made without evidence. */
  needsForce: boolean
  /** How many commits this branch holds that the comparison ref does not, or null when
   *  the daemon could not measure it. */
  unlanded: number | null
}

export function assessRemoval(
  tree: GitOverviewWorktree,
  liveSessions: number,
): RemovalAssessment {
  const blocks: RemovalBlock[] = []
  if (tree.main) blocks.push('main')
  if (liveSessions > 0) blocks.push('live_session')
  if (tree.locked !== null) blocks.push('locked')
  const warnings: RemovalWarning[] = []
  const local = localMeasurement(tree)
  const ahead = tree.comparisonCounts?.ahead ?? null
  if (local.measured && local.total > 0) warnings.push('dirty')
  if (ahead !== null && ahead > 0) warnings.push('unlanded')
  // A prunable checkout has no measurements at all, and neither does one whose
  // comparison ref is unavailable. Saying so is the point: rendering it as clean is
  // the one wrong answer.
  if (!local.measured || ahead === null) warnings.push('unmeasured')
  return {
    path: tree.path,
    branch: tree.branch,
    blocks,
    warnings,
    needsForce: !local.measured || local.total > 0,
    unlanded: ahead,
  }
}

export function removalBlockLabel(block: RemovalBlock): string {
  if (block === 'main') return 'main tree'
  if (block === 'live_session') return 'in use'
  return 'locked'
}

export function removalWarningLabel(warning: RemovalWarning): string {
  if (warning === 'dirty') return 'uncommitted'
  if (warning === 'unlanded') return 'unlanded'
  return 'unmeasured'
}

export type BulkRemoval = {
  /** In map order, so what runs is what the reader saw. */
  removable: RemovalAssessment[]
  /** Selected but refused, each with why. Named rather than silently dropped. */
  blocked: RemovalAssessment[]
  /** Removable, but carrying something a reader has to agree to discard. */
  warned: RemovalAssessment[]
  /** Whether any removable checkout needs `--force`, which is what the confirmation
   *  is actually granting. */
  needsForce: boolean
}

export function planBulkRemoval(assessments: readonly RemovalAssessment[]): BulkRemoval {
  const removable = assessments.filter(item => item.blocks.length === 0)
  return {
    removable,
    blocked: assessments.filter(item => item.blocks.length > 0),
    warned: removable.filter(item => item.warnings.length > 0),
    needsForce: removable.some(item => item.needsForce),
  }
}

/** Why a worktree cannot be landed. Landing has its own two, and they are not removal's. */
export type LandBlock = 'main' | 'detached'

export type LandCandidate = { path: string; branch: string }

export type BulkLand = {
  /** In map order. The queue serializes them - one request per branch is the whole act. */
  landable: LandCandidate[]
  blocked: { path: string; reason: LandBlock }[]
}

export function planBulkLand(
  trees: readonly Pick<GitOverviewWorktree, 'path' | 'branch' | 'main'>[],
): BulkLand {
  const landable: LandCandidate[] = []
  const blocked: { path: string; reason: LandBlock }[] = []
  for (const tree of trees) {
    if (tree.main) { blocked.push({ path: tree.path, reason: 'main' }); continue }
    // A detached HEAD has no branch name to fast-forward from, which the row already
    // states rather than offering and then refusing.
    if (!tree.branch) { blocked.push({ path: tree.path, reason: 'detached' }); continue }
    landable.push({ path: tree.path, branch: tree.branch })
  }
  return { landable, blocked }
}

export function landBlockLabel(reason: LandBlock): string {
  return reason === 'main' ? 'main tree' : 'detached HEAD'
}

/** One line naming what a bulk act will not touch, or an empty string when it is
 *  taking everything that was selected. */
export function skippedLabel(counts: Partial<Record<RemovalBlock | LandBlock, number>>): string {
  const parts: string[] = []
  for (const [reason, count] of Object.entries(counts)) {
    if (!count) continue
    const label = reason === 'detached'
      ? landBlockLabel('detached')
      : removalBlockLabel(reason as RemovalBlock)
    parts.push(`${count} ${label}`)
  }
  return parts.length ? `skipping ${parts.join(', ')}` : ''
}
