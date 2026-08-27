// What the Git tab remembers about a Project between mounts.
//
// The tab is not `keepMounted`, and it is mounted per *segment*, so leaving it - to
// another drawer tab, to another Project - unmounts it and drops everything it had read.
// The overview alone was already kept here for exactly that reason; the rest of the tab
// was not, so Log opened blank on every visit, Provenance refetched five hundred rows,
// and a reader who had expanded a worktree came back to a collapsed list and paid the
// per-checkout read again.
//
// This is a display cache and nothing more. Every entry is painted immediately on mount
// and revalidated underneath in the same pass, so the worst an entry can do is show a
// real answer that is a few seconds old instead of a spinner - which is the trade the
// overview has been making since it got here. What is deliberately *not* cached is a
// search result: a query's answer is a different question from the default reading, and
// painting one as the other would be wrong rather than stale.
//
// Explicit `.ts` extension elsewhere in this directory is a node-test-runner
// requirement; this module has no imports at runtime beyond types.

import type { GitGraph, GitProvenance, GitRefMove, GitWorktreeOverview, ProvenanceGroup } from './gitWorktrees.ts'

export type GitProvenanceReading = {
  rows: GitProvenance[]
  groups: ProvenanceGroup[]
  refMoves: GitRefMove[]
}

export type GitTabMemory = {
  overview?: GitWorktreeOverview
  /** The unsearched graph at its default limit, which is what a fresh visit draws. */
  graph?: GitGraph
  /** The unsearched ledger, same reasoning as `graph`. */
  provenance?: GitProvenanceReading
  /** Which worktree the reader had open, so returning returns to it. */
  expandedTree?: string
  /** The Map's filter box, which is a reading position rather than a fetched answer. */
  treeFilter?: string
}

/** A handful of Projects is the working set; an evicted entry costs one blank paint. */
export const GIT_TAB_MEMORY_LIMIT = 8

const MEMORY = new Map<string, GitTabMemory>()

export function readGitTabMemory(projectId: string | undefined): GitTabMemory {
  if (!projectId) return {}
  return MEMORY.get(projectId) || {}
}

export function writeGitTabMemory(projectId: string | undefined, patch: GitTabMemory): void {
  if (!projectId) return
  const merged = { ...(MEMORY.get(projectId) || {}), ...patch }
  // Re-inserted rather than mutated in place so the Map's iteration order stays
  // least-recently-written, which is what makes the eviction below the right one.
  MEMORY.delete(projectId)
  MEMORY.set(projectId, merged)
  while (MEMORY.size > GIT_TAB_MEMORY_LIMIT) {
    const oldest = MEMORY.keys().next().value
    if (oldest === undefined) break
    MEMORY.delete(oldest)
  }
}

/** For tests, and for a reader switching accounts or repositories under the app. */
export function resetGitTabMemory(): void {
  MEMORY.clear()
}
