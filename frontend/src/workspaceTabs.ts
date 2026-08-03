import type { PaneLeaf, PaneStack } from './layout'

/** Return the adjacent tab in one desktop pane, wrapping in its stored order. */
export function relativeStackTab(
  stack: PaneStack | null,
  currentId: string | null,
  offset: number,
): PaneLeaf | null {
  if (!stack || stack.children.length < 2 || !Number.isInteger(offset)) return null
  const current = stack.children.findIndex(child => child.id === currentId)
  const active = stack.children.findIndex(child => child.id === stack.active_child_id)
  const index = current >= 0 ? current : active >= 0 ? active : 0
  const next = (index + (offset % stack.children.length) + stack.children.length) % stack.children.length
  return stack.children[next] || null
}
