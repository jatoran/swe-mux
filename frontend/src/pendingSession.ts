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
 * Ordinary spawns already own an optimistic leaf and should activate it. Worktree setup stays
 * unpanned, so the App can render it as a temporary full-workspace surface without mutating the
 * Project's durable split tree.
 */
export function selectPendingTerminal(layout: PaneLayout, id: string): PaneLayout {
  return stackForView(layout, id) ? activateContainingStack(layout, id) : layout
}
