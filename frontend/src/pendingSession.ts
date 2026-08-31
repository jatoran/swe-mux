import {
  activateContainingStack,
  openTab,
  paneStacks,
  splitTerminal,
  stackForView,
  stackTerminal,
  terminalLeaf,
  type PaneLayout,
  type SplitDirection,
} from './layout.ts'

export type PendingSpawnPlacement = {
  split: false | SplitDirection | 'stack'
  targetId: string | null
  position: 'before' | 'after'
}

export function placePendingTerminal(
  layout: PaneLayout,
  id: string,
  placement: PendingSpawnPlacement,
  activate = true,
): PaneLayout {
  if (!placement.split) {
    const activeBefore = placement.targetId
      ? stackForView(layout, placement.targetId)?.active_child_id
      : paneStacks(layout)[0]?.active_child_id
    const placed = openTab(layout, placement.targetId, terminalLeaf(id))
    return !activate && activeBefore ? activateContainingStack(placed, activeBefore) : placed
  }
  if (placement.split === 'stack' && placement.targetId) {
    return stackTerminal(layout, placement.targetId, id)
  }
  return splitTerminal(
    layout,
    placement.targetId,
    id,
    placement.split as SplitDirection,
    placement.position,
  )
}

/** Select a pending session without inventing pane membership for it.
 *
 * Every launch this device starts now owns an optimistic leaf - worktree setup included, which
 * used to stay unpanned - so the ordinary answer is to activate the tab it already has. The
 * guard covers the window where this render's layout has not caught up with the placement yet:
 * minting a leaf here would put the session in a pane nobody chose, which is the placement
 * decision `sessionJoin.ts` exists to make on one authoritative snapshot instead.
 */
export function selectPendingTerminal(layout: PaneLayout, id: string): PaneLayout {
  return stackForView(layout, id) ? activateContainingStack(layout, id) : layout
}
