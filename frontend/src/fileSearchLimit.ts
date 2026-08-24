// What a truncated file search says about itself.
//
// A search stops for one of two unrelated reasons and the advice they deserve is opposite.
// Hitting the *result* limit means there is more of what you asked for, and a narrower query
// finds it. Hitting the *file* limit means the walk gave up before visiting the whole tree,
// so the matches on screen are not the best matches - they are whatever was reached - and a
// narrower query re-runs the same walk and gives up in the same place. Telling a reader to
// refine there sends them retyping instead of fixing the ignore list, which is the only thing
// that actually changes the answer.

export type SearchTruncationReason = 'results' | 'files'

export type SearchTruncation = {
  truncated?: boolean
  /** Which bound bit, or `null` when the search finished. */
  truncated_reason?: SearchTruncationReason | null
  /** Project-relative folder the walk was in when the file budget ran out; `''` is the root. */
  stopped_at?: string | null
}

/** The folder named in a file-limit notice, in words rather than as an empty string. */
export function describeStopPoint(stoppedAt: string | null | undefined): string {
  const value = (stoppedAt ?? '').trim()
  return value ? value : 'the Project root'
}

/** The one line under a truncated result list, or `null` when there is nothing to say. */
export function searchTruncationNotice(
  payload: SearchTruncation | null | undefined,
  matchCount: number,
): string | null {
  if (!payload?.truncated) return null
  // A daemon predating the reason field - which the redeploy rollback path makes real - still
  // reports the bare boolean. The result limit is what it always meant, so fall back to that
  // rather than going silent on the one build where a notice is all the reader gets.
  const reason = payload.truncated_reason ?? 'results'
  if (reason === 'results') {
    return `Showing the first ${matchCount} matches; refine to narrow.`
  }
  if (reason === 'files') {
    return (
      `Stopped at the file limit inside ${describeStopPoint(payload?.stopped_at)}; ` +
      'deeper folders were not searched. Add an ignore pattern to narrow the tree.'
    )
  }
  return null
}
