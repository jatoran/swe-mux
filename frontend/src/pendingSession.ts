import {
  activateContainingStack,
  activateStackChild,
  openTab,
  paneStacks,
  replaceTerminal,
  splitTerminal,
  stackForView,
  stackTerminal,
  terminalLeaf,
  type PaneLayout,
  type SplitDirection,
} from './layout.ts'

export type PendingSpawnPlacement = {
  projectId: string
  split: false | SplitDirection | 'stack'
  targetId: string | null
  position: 'before' | 'after'
  resolvedId?: string
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

export function replacePendingTerminal(
  layout: PaneLayout,
  pendingId: string,
  nextId: string,
): PaneLayout {
  const pane = stackForView(layout, pendingId)
  const activeBefore = pane?.active_child_id
  const replaced = replaceTerminal(layout, pendingId, nextId)
  return pane && activeBefore && activeBefore !== pendingId
    ? activateStackChild(replaced, pane.id, activeBefore)
    : replaced
}
