// Where a conversation can be forked, as the browser sees it.
//
// Pure: everything here is the daemon's answer reshaped for a picker, with no fetch
// and no DOM. What it deliberately does NOT do is decide eligibility. Whether a cut
// is legal depends on the provider's own rule about unanswered tool calls, and the
// daemon is the only side that has read the transcript; a browser recomputing that
// would eventually disagree with the writer and offer branches that fail on create.

export type BranchMode = 'before' | 'after'

/** Why a cut is unavailable. `null` on an eligible one. */
export type BranchIneligibility = 'unanswered_tool_calls' | 'outside_window' | string

export type BranchModeState = Readonly<{
  eligible: boolean
  reason: BranchIneligibility | null
}>

export type BranchPoint = Readonly<{
  message_id: string
  ordinal: number
  role: 'user' | 'assistant'
  ts: string | number | null
  text: string
  default_mode: BranchMode
  modes: Readonly<Record<BranchMode, BranchModeState>>
}>

/** Why the daemon has no points to offer. `null` when it does. */
export type BranchPointsReason =
  | 'not_agent'
  | 'no_transcript'
  | 'unreadable'
  | 'dialect_unsupported'
  | 'strategy_has_no_points'
  | null

export type BranchPoints = Readonly<{
  session_id: string
  backend: string
  conversation_id: string
  strategy: string | null
  /** Whether this harness honours a chosen point at all, as opposed to forking from now. */
  from_message: boolean
  points: BranchPoint[]
  truncated: boolean
  reason: BranchPointsReason
}>

/** The same listing read from a History row instead of a live pane.
 *
 *  Its own type rather than a widened `BranchPoints`, because the identity differs and
 *  conflating them is how a caller ends up sending a history id where a session id was
 *  meant. Only the point list is shared, which is the part that has to agree: a
 *  schedule that forks at a point the picker would have refused is an unattended
 *  session opened on a conversation the provider rejects. */
export type HistoryBranchPoints = Readonly<{
  history_id: string
  backend: string
  conversation_id: string
  strategy: string | null
  from_message: boolean
  points: BranchPoint[]
  truncated: boolean
  reason: BranchPointsReason
}>

/** What the branch request carries, and what the daemon hands back on success. */
export type BranchRequest = Readonly<{ from_message_id?: string; mode?: BranchMode }>

export type BranchFork = Readonly<{
  conversation_id: string
  from_message_id: string
  mode: BranchMode
  records_written: number
  attachments_copied: number
}>

/**
 * The cut this point is normally branched at.
 *
 * Role-derived rather than chosen by the reader, because the two are opposite acts on
 * opposite kinds of message: an agent's reply is a place to *continue* from, while a
 * prompt is a thing to *redo*. Offering both on every message would make the common
 * case a decision, and the uncommon one is reachable by asking for it explicitly.
 */
export const branchModeFor = (point: BranchPoint): BranchMode => point.default_mode

export const branchModeState = (point: BranchPoint, mode: BranchMode): BranchModeState =>
  point.modes[mode] || { eligible: false, reason: 'outside_window' }

export const branchPointEligible = (point: BranchPoint, mode: BranchMode): boolean =>
  branchModeState(point, mode).eligible

/** The action offered on this point, in the words of what it will do. */
export function branchActionLabel(mode: BranchMode): string {
  return mode === 'before' ? 'Branch before this' : 'Branch after this'
}

/** What the branch will contain, said once so the reader is never guessing. */
export function branchOutcomeSummary(mode: BranchMode): string {
  return mode === 'before'
    ? 'The new session carries everything up to the message before this one, and this prompt comes back for you to edit.'
    : 'The new session carries everything up to and including this reply.'
}

/** Why this point cannot be branched at, in the words of the cause. */
export function branchIneligibilityMessage(reason: BranchIneligibility | null): string {
  if (reason === 'unanswered_tool_calls') {
    return 'This reply asked for a tool whose result had not arrived yet. A conversation cut here would not load.'
  }
  if (reason === 'outside_window') return 'Nothing loaded before this message to branch from.'
  return reason ? `Unavailable: ${reason}` : ''
}

/** Why there is nothing to pick, in the words of the cause. */
export function branchPointsEmptyMessage(reason: BranchPointsReason, backend: string): string {
  if (reason === 'not_agent') return 'This session has no agent conversation to branch.'
  if (reason === 'no_transcript') return 'This agent has not written its first message yet.'
  if (reason === 'unreadable') return 'The conversation could not be read just now. Try again.'
  if (reason === 'dialect_unsupported') {
    return `swemux cannot yet fork a ${backend} conversation at a chosen point.`
  }
  if (reason === 'strategy_has_no_points') {
    return `${backend} branches from where the conversation stands now, not from a chosen point.`
  }
  return 'Nothing has been said in this conversation yet.'
}

/** The points a picker should show, newest first: a branch is normally a recent regret. */
export function orderedBranchPoints(points: readonly BranchPoint[]): BranchPoint[] {
  return [...points].sort((left, right) => right.ordinal - left.ordinal)
}

/** The point a picker opens on: the newest one its default cut is available at. */
export function defaultBranchPoint(points: readonly BranchPoint[]): BranchPoint | null {
  for (let index = points.length - 1; index >= 0; index -= 1) {
    const point = points[index]
    if (branchPointEligible(point, branchModeFor(point))) return point
  }
  return null
}

/** One line of the message, bounded, for a row the reader scans rather than reads. */
export const branchPointPreview = (text: string, limit = 160): string => {
  const flattened = text.replace(/\s+/g, ' ').trim()
  return flattened.length > limit ? `${flattened.slice(0, limit - 1)}…` : flattened
}
