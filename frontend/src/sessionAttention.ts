// Read/unread tracking for agent (claude/codex) sidebar rows. An agent row is
// "unread" when the agent has completed a turn the user has not acknowledged.
//
// Both halves of that live on the session record the daemon serves: `turn_seq`
// counts semantic turn completions (the status contract settling a working
// session, or raising an approval), and `read_turn_seq` is the acknowledgement.
// Neither is derived here, which is the point. The previous model compared
// `last_activity_ts` - a PTY-byte timestamp - against a mark held in this tab's
// memory, and failed in both directions at once:
//
//   - False unread. Every SIGWINCH makes an agent TUI repaint its whole screen,
//     so resizing the window, collapsing the sidebar, changing UI scale, or
//     attaching a phone stamped activity on every attached session within
//     milliseconds of each other and lit up a whole project. Idle spinners and
//     status footers did the same, slowly.
//   - False read. The mark lived in `useState`, so a reload, a UI reload, or a
//     phone evicting the tab silently marked the entire fleet caught up; and a
//     second device never saw the first device's marks at all.
//
// A server-held integer fixes both: repaints cannot move it, it survives a
// reload, and every device compares against the same number.
//
// One thing overrides the comparison: `unread_pin`, set by an explicit "Mark as
// unread". Everything above is a measurement of what the agent did, and the pin
// is the user contradicting it, so it wins outright - including over the dwell
// acknowledgement, which would otherwise re-read the very pane the menu was
// opened on. The daemon retires it on the session's next completed turn.
//
// Kept free of runtime imports so the logic can be unit tested under the node
// runner.
import type { Session } from './types'
import { isObservedHarness } from './harnessRegistry.ts'

// Ended sessions carry their own muted styling (dimmed, red/grey dot), so they
// never participate in unread.
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

/**
 * Acknowledgements this tab has sent but not yet seen reflected in a fleet
 * snapshot. Purely an optimistic overlay on `read_turn_seq`: without it, a row
 * stays lit for the round-trip of its own POST, which reads as the click having
 * missed.
 */
export type AckMap = Record<string, number>
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

/** Completed turns the daemon has counted for this session. */
export function turnSeq(session: Session): number {
  return Number(session.turn_seq || 0)
}

/** Turns acknowledged, taking the local overlay when it is ahead of the server. */
export function ackedSeq(session: Session, acked: AckMap): number {
  return Math.max(Number(session.read_turn_seq || 0), acked[session.id] || 0)
}

/**
 * True when a settled agent session has completed a turn nobody has read.
 *
 * A mid-turn agent is deliberately excluded even when it is behind: the row
 * turns unread the moment it settles, not while it runs.
 *
 * An explicit `unread_pin` outranks both halves of that. It is a statement, not
 * a measurement, so it holds whatever the counters and the state say - marking a
 * working agent unread and having the row stay read until it happened to settle
 * would read as the click being ignored.
 */
export function isUnread(session: Session, acked: AckMap): boolean {
  if (!isAgentSession(session)) return false
  if (session.unread_pin) return true
  if (!SETTLED.includes(session.state)) return false
  return turnSeq(session) > ackedSeq(session, acked)
}

/**
 * Sessions on screen with an unacknowledged turn, as `id:turn_seq` pairs.
 *
 * The caller acknowledges these after a dwell. Returned keyed by the turn being
 * acknowledged so a dwell timer restarts when a *new* turn lands and not on the
 * unrelated snapshot churn of a busy fleet - while an agent is mid-turn its
 * `turn_seq` does not move, which is what makes the timer able to fire at all.
 *
 * A hand-marked session is skipped: the daemon refuses the implicit
 * acknowledgement anyway, and sending it would still flip this tab's optimistic
 * overlay and undo the mark locally.
 */
export function pendingAcks(sessions: Session[], visibleIds: Iterable<string>, acked: AckMap): Array<[string, number]> {
  const visible = visibleIds instanceof Set ? visibleIds : new Set(visibleIds)
  const out: Array<[string, number]> = []
  for (const session of sessions) {
    if (!visible.has(session.id) || !isAgentSession(session) || session.unread_pin) continue
    const seq = turnSeq(session)
    if (seq > ackedSeq(session, acked)) out.push([session.id, seq])
  }
  return out
}

/**
 * Drop overlay entries the server has caught up on, and sessions that are gone.
 *
 * Returns the same reference when nothing changed so the caller can skip a
 * redundant state update (and the re-render it would cause). Note the asymmetry
 * with the old model: an entry that disappears here can only *lose* an
 * acknowledgement this tab already sent and had confirmed, never invent one -
 * a session missing from one snapshot pass used to come back marked read.
 */
export function pruneAcks(prev: AckMap, sessions: Session[]): AckMap {
  const byId = new Map(sessions.map(session => [session.id, session]))
  const next: AckMap = {}
  let changed = false
  for (const id in prev) {
    const session = byId.get(id)
    // A hand-marked session drops its overlay too: the entry was written by an
    // earlier acknowledgement of the same turns, and `ackedSeq` takes the max,
    // so keeping it would report the row read against the mark just rolled back.
    if (session && !session.unread_pin && prev[id] > Number(session.read_turn_seq || 0)) next[id] = prev[id]
    else changed = true
  }
  return changed ? next : prev
}

/** Aggregate the orthogonal activity and read state shown by a collapsed-rail
 *  Project chip. Approval wins over work, work over ready/waiting, while unread
 *  remains a separate signal so unseen output is never hidden by activity. */
export function projectRailStatus(sessions: Session[], projectId: string, acked: AckMap): ProjectRailStatus {
  return projectSetRailStatus(sessions, [projectId], acked)
}

/** The same aggregate over several Projects at once, for a collapsed sidebar
 *  section. Collapsing hides whichever Project holds the waiting agent, so the
 *  section has to answer for all of them — with the same precedence, or a folded
 *  Group would quietly outrank an expanded one showing the identical state. */
export function projectSetRailStatus(sessions: Session[], projectIds: Iterable<string>, acked: AckMap): ProjectRailStatus {
  const scope = projectIds instanceof Set ? projectIds : new Set(projectIds)
  const live = sessions.filter(session=>scope.has(session.project_id)&&!session.pending&&!DEAD.includes(session.state))
  const agents = live.filter(isAgentSession)
  const activity:ProjectRailActivity = agents.some(session=>session.state==='awaiting')?'attention'
    :agents.some(session=>session.state==='working')?'working'
      :agents.some(session=>session.state==='idle')?'waiting'
        :live.length?'running':'inactive'
  return {activity,unread:agents.some(session=>isUnread(session,acked)),liveCount:live.length,agentCount:agents.length}
}
