// Read/unread tracking for agent (claude/codex) sidebar rows. An agent row is
// "unread" when the agent produced new output — advanced last_activity_ts —
// since the user last viewed that session. Viewing a session (having it visible
// in a pane) marks it read and keeps it read while the agent keeps talking on
// screen, so the eye is only drawn to genuine, unseen activity. Kept free of
// runtime imports so the logic can be unit tested under the node runner.
import type { Session } from './types'
import { isObservedHarness } from './harnessRegistry.ts'

// Ended sessions carry their own muted styling (dimmed, red/grey dot) and a
// last end-of-life last_activity_ts bump, so they never participate in unread.
const DEAD: ReadonlyArray<Session['state']> = ['exited', 'crashed']

// Unread means "this agent has finished saying something you have not seen".
// Only a settled agent qualifies: `idle` is ready for input and `awaiting` wants
// an approval, and both are states where the user is the one holding things up.
// A `working` (or `starting`) agent is mid-turn — its output is still growing and
// there is nothing to catch up on yet — so counting it as unread lit every
// off-screen agent for the whole length of its run, which is precisely the window
// in which the row means "nothing for you here". That made the loudest tier in
// the sidebar the least informative one.
const SETTLED: ReadonlyArray<Session['state']> = ['idle', 'awaiting']

export type SeenMap = Record<string, number>
export type ProjectRailActivity = 'attention' | 'working' | 'waiting' | 'running' | 'inactive'
export interface ProjectRailStatus {
  activity: ProjectRailActivity
  unread: boolean
  liveCount: number
  agentCount: number
}

function isAgentSession(session: Session): boolean {
  return isObservedHarness(session.backend) && !DEAD.includes(session.state)
}

/**
 * Recompute the seen map from the live session list and the set of session ids
 * currently visible on screen.
 *
 * - A newly-sighted agent is seen up to its current last_activity_ts, so a fresh
 *   page load starts with a clean slate rather than a wall of unread rows.
 * - A visible agent is seen up to its current last_activity_ts every pass, so an
 *   on-screen session never reads as unread even while its agent keeps working.
 * - An off-screen agent keeps its prior mark, so later output makes it unread.
 * - Sessions that no longer exist are dropped.
 *
 * Returns the same reference when nothing changed so the caller can skip a
 * redundant state update (and the re-render it would cause).
 */
export function reconcileSeen(prev: SeenMap, sessions: Session[], visibleIds: Iterable<string>): SeenMap {
  const visible = visibleIds instanceof Set ? visibleIds : new Set(visibleIds)
  const next: SeenMap = {}
  let changed = false
  for (const session of sessions) {
    if (!isAgentSession(session)) continue
    const had = session.id in prev
    const mark = (!had || visible.has(session.id)) ? session.last_activity_ts : prev[session.id]
    next[session.id] = mark
    if (!had || mark !== prev[session.id]) changed = true
  }
  if (!changed) for (const id in prev) if (!(id in next)) { changed = true; break }
  return changed ? next : prev
}

/**
 * True when a settled agent session has produced output the user has not seen.
 *
 * The read mark is only advanced while a session is visible, so an agent that
 * works off screen and then goes idle still compares against the mark from
 * before its run: it becomes unread the moment it settles, not while it runs.
 */
export function isUnread(session: Session, seen: SeenMap): boolean {
  if (!isAgentSession(session) || !SETTLED.includes(session.state)) return false
  const mark = seen[session.id]
  return mark !== undefined && session.last_activity_ts > mark
}

/** Aggregate the orthogonal activity and read state shown by a collapsed-rail
 *  Project chip. Approval wins over work, work over ready/waiting, while unread
 *  remains a separate signal so unseen output is never hidden by activity. */
export function projectRailStatus(sessions: Session[], projectId: string, seen: SeenMap): ProjectRailStatus {
  return projectSetRailStatus(sessions, [projectId], seen)
}

/** The same aggregate over several Projects at once, for a collapsed sidebar
 *  section. Collapsing hides whichever Project holds the waiting agent, so the
 *  section has to answer for all of them — with the same precedence, or a folded
 *  Group would quietly outrank an expanded one showing the identical state. */
export function projectSetRailStatus(sessions: Session[], projectIds: Iterable<string>, seen: SeenMap): ProjectRailStatus {
  const scope = projectIds instanceof Set ? projectIds : new Set(projectIds)
  const live = sessions.filter(session=>scope.has(session.project_id)&&!session.pending&&!DEAD.includes(session.state))
  const agents = live.filter(isAgentSession)
  const activity:ProjectRailActivity = agents.some(session=>session.state==='awaiting')?'attention'
    :agents.some(session=>session.state==='working')?'working'
      :agents.some(session=>session.state==='idle')?'waiting'
        :live.length?'running':'inactive'
  return {activity,unread:agents.some(session=>isUnread(session,seen)),liveCount:live.length,agentCount:agents.length}
}
