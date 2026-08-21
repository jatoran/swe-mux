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
// The pin only outranks the dwell for the *visit* it was made in, though
// (`trackPinVisits`). Coming back to the pane later is the user reading the very
// thing they marked, and a pin that survived that left the row lit until someone
// hand-marked it read again - the mark behaved like a permanent flag rather than
// like "I have not read this yet".
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

/**
 * Whether each pinned session's hand-set unread mark is still owned by the visit
 * that set it. Entries exist only for sessions currently carrying `unread_pin`.
 */
export type PinState = 'held' | 'released'
export type PinVisits = Record<string, PinState>

/** One acknowledgement the dwell timer owes the daemon. */
export interface PendingAck {
  id: string
  /** The turn being acknowledged; also the key the dwell timer restarts on. */
  turnSeq: number
  /**
   * True when this acknowledgement must be written as an explicit read
   * (`{read: true}`) rather than a cursor, because it is also retiring a
   * released pin - the daemon refuses an implicit catch-up while one is set.
   */
  explicit: boolean
}
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
 * Follow each hand-set unread mark through the visit that set it.
 *
 * The pin exists so that marking the pane in front of you sticks: without it the
 * dwell acknowledgement re-reads the row a second later and the click looks
 * ignored. That reason lasts exactly as long as the visit. A pin is therefore
 * `held` from the moment it is first seen on a session that is on screen until
 * that session goes off screen, and `released` from then on - so returning to
 * the pane acknowledges it like any other pane you are looking at.
 *
 * A pin set from the sidebar of a session that is *not* on screen is released
 * immediately, because there was no visit to protect it from; the first visit
 * clears it. The two are distinguished by what the pin's first observation saw,
 * which is why released is sticky: a released pin that scrolls back into view
 * must not re-arm itself and start the loop again.
 *
 * A fresh tab is deliberately generous. With no prior state, a visible pin reads
 * as newly set, so a reload with the pane still on screen keeps the mark - the
 * mark is server-held and must not be undone by a refresh.
 *
 * Returns the same reference when nothing changed, so the caller can skip a
 * redundant state update and the render it would cause.
 */
export function trackPinVisits(prev: PinVisits, sessions: Session[], visibleIds: Iterable<string>): PinVisits {
  const visible = visibleIds instanceof Set ? visibleIds : new Set(visibleIds)
  const next: PinVisits = {}
  let changed = false
  for (const session of sessions) {
    if (!session.unread_pin) continue
    const before = prev[session.id]
    const state: PinState = before
      ? before === 'held' && visible.has(session.id) ? 'held' : 'released'
      : visible.has(session.id) ? 'held' : 'released'
    next[session.id] = state
    if (state !== before) changed = true
  }
  // Anything that lost its pin - the user marked it read, the daemon retired it
  // on a new turn, the session ended - drops out with it.
  for (const id in prev) if (!(id in next)) changed = true
  return changed ? next : prev
}

/**
 * Sessions on screen with an unacknowledged turn, and how to write each one.
 *
 * The caller acknowledges these after a dwell. Carries the turn being
 * acknowledged so a dwell timer restarts when a *new* turn lands and not on the
 * unrelated snapshot churn of a busy fleet - while an agent is mid-turn its
 * `turn_seq` does not move, which is what makes the timer able to fire at all.
 *
 * A session whose pin is still held by the visit that set it is skipped: the
 * daemon refuses the implicit acknowledgement anyway, and sending it would still
 * flip this tab's optimistic overlay and undo the mark locally. A released pin
 * is acknowledged instead, as an explicit read, which is what retires it. With
 * no visit state supplied every pin counts as held, so a caller that does not
 * track visits keeps the old always-skip behaviour.
 */
export function pendingAcks(
  sessions: Session[],
  visibleIds: Iterable<string>,
  acked: AckMap,
  pins: PinVisits = {},
): PendingAck[] {
  const visible = visibleIds instanceof Set ? visibleIds : new Set(visibleIds)
  const out: PendingAck[] = []
  for (const session of sessions) {
    if (!visible.has(session.id) || !isAgentSession(session)) continue
    const released = !session.unread_pin || pins[session.id] === 'released'
    if (!released) continue
    const seq = turnSeq(session)
    // A pin sits on a session whose counters may already read as caught up
    // (`mark_unread` only rolls back one turn, and a pin can be set on a session
    // with no counted turn at all), so the retirement cannot be conditional on
    // there being an unacknowledged turn - that is the row it would strand.
    if (session.unread_pin) out.push({ id: session.id, turnSeq: seq, explicit: true })
    else if (seq > ackedSeq(session, acked)) out.push({ id: session.id, turnSeq: seq, explicit: false })
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

/**
 * How many sessions are alive, across every Project.
 *
 * The same predicate the per-Project rail chip counts with (`projectSetRailStatus`),
 * lifted out so the sidebar's fleet-wide chip and a Project's own badge cannot drift
 * into disagreeing about what "live" means. Pending sessions are excluded for the
 * reason they are everywhere else: a session still starting up has no process yet.
 */
export function liveSessionCount(sessions: Session[]): number {
  return sessions.filter(session => !session.pending && !DEAD.includes(session.state)).length
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
