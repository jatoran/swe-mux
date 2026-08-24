import {
  leaves, parseLayout, reconcilePreviews, reconcileTerminals, removeLeaf, type PaneLayout,
} from './layout.ts'
import { placePendingTerminal, type PendingSpawnPlacement } from './pendingSession.ts'
import {
  forgetJoinAttempts, joinSessions, joinableSessionIds, unjoinedSessionIds, type JoinAttempts,
} from './sessionJoin.ts'
import type { Project, Session } from './types.ts'

/** A launch this device started: its Project, where it asked for the pane, and the
 *  daemon id once the POST answered. Held per pending id at the composition root. */
export type PendingSpawn = { projectId: string; placement: PendingSpawnPlacement | null; resolvedId?: string }

export type FleetLayoutInput = {
  /** The fleet as the operator sees it: kill tombstones already applied. */
  sessions: readonly Session[]
  projects: readonly Project[]
  previewIds: Set<string>
  pendingSpawns: Readonly<Record<string, PendingSpawn>>
  joinAttempts: JoinAttempts
  joinAnchor: { projectId: string; viewId: string | null }
  /** True while a layout PATCH for that Project is in flight; its layout is left alone. */
  hasPendingLayoutWrite: (projectId: string) => boolean
  isEnded: (session: Session) => boolean
}

export type FleetLayoutJoin = { projectId: string; layout: PaneLayout; ids: string[] }

export type FleetLayoutPlan = {
  /** Only the Projects this pass reconciled; Projects with a write in flight are absent. */
  layouts: Record<string, PaneLayout>
  /** Layouts that gained a leaf and should be persisted quietly. */
  joins: FleetLayoutJoin[]
  liveSessionIds: Set<string>
  /** `joinAttempts` with departed sessions forgotten; adopt this, not the input. */
  joinAttempts: JoinAttempts
}

/**
 * Reconcile every Project's pane layout against one fleet snapshot.
 *
 * Pure: it reads a snapshot and returns what the composition root should store and
 * persist, so the join rules below are testable without a browser or a daemon.
 *
 * Every session the daemon still holds keeps its leaf, ended ones included. A session
 * that ends on its own used to keep its sidebar row and lose its tab in the same
 * instant, so the pane showing what it printed was destroyed at exactly the moment
 * somebody wanted to read it - and the pruned layout was written back, so it did not
 * come back. A session leaves the layout when it leaves the fleet: killed, or dismissed.
 * Kill tombstones are applied by the caller, which is what removes a closed tab immediately.
 */
export function planFleetLayouts(input: FleetLayoutInput): FleetLayoutPlan {
  const {
    sessions, projects, previewIds, pendingSpawns, joinAnchor, hasPendingLayoutWrite, isEnded,
  } = input
  const live = new Set(sessions.map(session => session.id))
  const joinAttempts = forgetJoinAttempts(input.joinAttempts, live)
  // A session this device is mid-spawn on already owns an optimistic leaf under its
  // pending id; joining it here as well would leave the layout holding it twice once
  // `replaceTerminal` swaps the real id in.
  const spawningHere = new Set(
    Object.values(pendingSpawns).map(pending => pending.resolvedId).filter(Boolean) as string[],
  )
  // A launch still waiting on its POST is worse than that: the daemon has created the
  // session and announced it, so this GET can carry a session that is *ours* under an id
  // this client does not know yet, and there is no way to tell it from a daemon-started
  // one. The whole join pass is therefore withheld from that Project until the launch
  // resolves; the refresh after it joins whatever is still floating.
  const launchingHere = new Set(
    Object.values(pendingSpawns).filter(pending => !pending.resolvedId).map(pending => pending.projectId),
  )
  // Grouped once rather than per Project: this runs on every refresh, and the fleet and the
  // Project list both grow.
  const joinCandidates = new Map<string, string[]>()
  for (const session of sessions) {
    if (isEnded(session) || session.pending || spawningHere.has(session.id)) continue
    if (launchingHere.has(session.project_id)) continue
    const forProject = joinCandidates.get(session.project_id)
    if (forProject) forProject.push(session.id)
    else joinCandidates.set(session.project_id, [session.id])
  }
  const layouts: Record<string, PaneLayout> = {}
  const joins: FleetLayoutJoin[] = []
  for (const project of projects) {
    // This GET may have been snapshotted before an in-flight layout PATCH
    // committed. Overwriting optimistic state with it snaps a just-dropped
    // tab back; the PATCH's own generation-guarded path reconciles instead.
    if (hasPendingLayoutWrite(project.id)) continue
    // History graduated from a per-project pane tab to a global overlay;
    // drop any persisted history leaf so old layouts don't dangle.
    let base = parseLayout(project.layout)
    for (const leaf of leaves(base, 'history')) base = removeLeaf(base, 'history', leaf.id)
    let reconciled = reconcilePreviews(reconcileTerminals(base, live), previewIds)
    for (const [pendingId, pending] of Object.entries(pendingSpawns)) {
      if (pending.projectId !== project.id || !pending.placement) continue
      reconciled = placePendingTerminal(reconciled, pending.resolvedId || pendingId, pending.placement, false)
    }
    // Sessions the daemon started on its own - an approved `request_spawn`, the
    // assistant's daemon spawn path, a scheduled run - reach the fleet with no leaf,
    // because only a device launch writes one. They used to float: a sidebar row
    // attached to no pane group, joining the tabs only once the operator tapped it.
    // They join here instead, off this same authoritative snapshot, so a client that
    // was asleep while they spawned still finds them as tabs. Ended sessions are left
    // alone: a pane is kept for a session that ended in one, never minted for one
    // nobody ever opened, and adopting a long archive of them at boot would spend the
    // layout's 64 leaves on sessions with nothing running. `sessionJoin.ts` owns the
    // placement rule and the no-focus-stealing contract.
    const candidates = joinableSessionIds(joinCandidates.get(project.id) || [], joinAttempts)
    const joined = joinSessions(
      reconciled,
      candidates,
      joinAnchor.projectId === project.id ? joinAnchor.viewId : null,
    )
    if (joined !== reconciled) joins.push({ projectId: project.id, layout: joined, ids: unjoinedSessionIds(reconciled, candidates) })
    layouts[project.id] = joined
  }
  return { layouts, joins, liveSessionIds: live, joinAttempts }
}
