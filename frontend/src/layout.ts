export type PaneLeafKind = 'terminal' | 'note' | 'preview'
export type SplitDirection = 'horizontal' | 'vertical'

export type PaneLeaf = { type: 'leaf'; kind: PaneLeafKind; id: string }
export type PaneSplit = {
  type: 'split'; direction: SplitDirection; ratio: number
  first: PaneNode; second: PaneNode
}
export type PaneNode = PaneLeaf | PaneSplit
export type PaneLayout = { version: 2; root: PaneNode | null }

export const emptyLayout = (): PaneLayout => ({ version: 2, root: null })
export const terminalLeaf = (id: string): PaneLeaf => ({ type: 'leaf', kind: 'terminal', id })
export const resourceLeaf = (kind: PaneLeafKind, id: string): PaneLeaf => ({ type: 'leaf', kind, id })

export type NoteLeafIdentity = { kind: 'spaces' | 'sessions'; id: string }

export function noteResourceId(kind: NoteLeafIdentity['kind'], id: string): string {
  return `${kind}:${encodeURIComponent(id)}`
}

export function parseNoteResourceId(resourceId: string): NoteLeafIdentity | null {
  const separator = resourceId.indexOf(':')
  if (separator < 1) return null
  const kind = resourceId.slice(0, separator)
  if (kind !== 'spaces' && kind !== 'sessions') return null
  try {
    const id = decodeURIComponent(resourceId.slice(separator + 1))
    return id ? { kind, id } : null
  } catch {
    return null
  }
}

function legacyTree(ids: string[]): PaneNode | null {
  if (!ids.length) return null
  if (ids.length === 1) return terminalLeaf(ids[0])
  const midpoint = Math.ceil(ids.length / 2)
  return {
    type: 'split', direction: ids.length === 2 || ids.length === 4 ? 'horizontal' : 'vertical', ratio: .5,
    first: legacyTree(ids.slice(0, midpoint))!, second: legacyTree(ids.slice(midpoint))!,
  }
}

function isNode(value: unknown): value is PaneNode {
  if (!value || typeof value !== 'object') return false
  const node = value as Record<string, unknown>
  if (node.type === 'leaf') {
    return ['terminal', 'note', 'preview'].includes(String(node.kind)) && typeof node.id === 'string' && !!node.id
  }
  return node.type === 'split'
    && (node.direction === 'horizontal' || node.direction === 'vertical')
    && typeof node.ratio === 'number'
    && isNode(node.first) && isNode(node.second)
}

export function parseLayout(value: unknown): PaneLayout {
  if (!value || typeof value !== 'object') return emptyLayout()
  const raw = value as { version?: number; root?: unknown; panes?: unknown }
  if (raw.version === 2) return raw.root === null || raw.root === undefined || isNode(raw.root)
    ? { version: 2, root: (raw.root as PaneNode | null | undefined) ?? null }
    : emptyLayout()
  if (Array.isArray(raw.panes)) {
    const ids = [...new Set(raw.panes.filter((id): id is string => typeof id === 'string' && !!id))]
    return { version: 2, root: legacyTree(ids) }
  }
  return emptyLayout()
}

export function leaves(layout: PaneLayout, kind?: PaneLeafKind): PaneLeaf[] {
  const result: PaneLeaf[] = []
  const visit = (node: PaneNode | null) => {
    if (!node) return
    if (node.type === 'leaf') {
      if (!kind || node.kind === kind) result.push(node)
      return
    }
    visit(node.first); visit(node.second)
  }
  visit(layout.root)
  return result
}

export const terminalIds = (layout: PaneLayout) => leaves(layout, 'terminal').map(leaf => leaf.id)

function mapNode(node: PaneNode, targetId: string, replace: (leaf: PaneLeaf) => PaneNode): PaneNode {
  if (node.type === 'leaf') return node.kind === 'terminal' && node.id === targetId ? replace(node) : node
  return { ...node, first: mapNode(node.first, targetId, replace), second: mapNode(node.second, targetId, replace) }
}

export function replaceTerminal(layout: PaneLayout, targetId: string | null, nextId: string): PaneLayout {
  if (!layout.root) return { version: 2, root: terminalLeaf(nextId) }
  if (!targetId || !terminalIds(layout).includes(targetId)) {
    const first = terminalIds(layout)[0]
    return first ? replaceTerminal(layout, first, nextId) : layout
  }
  return { version: 2, root: mapNode(layout.root, targetId, () => terminalLeaf(nextId)) }
}

export function splitTerminal(
  layout: PaneLayout,
  targetId: string | null,
  nextId: string,
  direction: SplitDirection,
): PaneLayout {
  if (!layout.root) return { version: 2, root: terminalLeaf(nextId) }
  const ids = terminalIds(layout)
  if (ids.includes(nextId)) return layout
  const target = targetId && ids.includes(targetId) ? targetId : ids[0]
  if (!target) return layout
  return {
    version: 2,
    root: mapNode(layout.root, target, leaf => ({
      type: 'split', direction, ratio: .5, first: leaf, second: terminalLeaf(nextId),
    })),
  }
}

export function attachLeaf(
  layout: PaneLayout,
  targetId: string | null,
  next: PaneLeaf,
  direction: SplitDirection = 'horizontal',
  ratio = .5,
): PaneLayout {
  if (leaves(layout).some(leaf => leaf.kind === next.kind && leaf.id === next.id)) return layout
  if (!layout.root) return { version: 2, root: next }
  const ids = terminalIds(layout)
  const target = targetId && ids.includes(targetId) ? targetId : ids[0]
  if (!target) return layout
  const clampedRatio = Math.max(.1, Math.min(.9, ratio))
  return {
    version: 2,
    root: mapNode(layout.root, target, leaf => ({
      type: 'split', direction, ratio: clampedRatio, first: leaf, second: next,
    })),
  }
}

function removeNode(node: PaneNode, kind: PaneLeafKind, id: string): PaneNode | null {
  if (node.type === 'leaf') return node.kind === kind && node.id === id ? null : node
  const first = removeNode(node.first, kind, id)
  const second = removeNode(node.second, kind, id)
  if (!first) return second
  if (!second) return first
  return { ...node, first, second }
}

export function removeLeaf(layout: PaneLayout, kind: PaneLeafKind, id: string): PaneLayout {
  return { version: 2, root: layout.root ? removeNode(layout.root, kind, id) : null }
}

export function reconcileTerminals(layout: PaneLayout, liveIds: Set<string>): PaneLayout {
  let next = layout
  for (const id of terminalIds(layout)) if (!liveIds.has(id)) next = removeLeaf(next, 'terminal', id)
  return next
}

function updateSplit(node: PaneNode, path: string, ratio: number): PaneNode {
  if (node.type === 'leaf') return node
  if (!path) return { ...node, ratio: Math.max(.1, Math.min(.9, Math.round(ratio * 10000) / 10000)) }
  return path[0] === 'f'
    ? { ...node, first: updateSplit(node.first, path.slice(1), ratio) }
    : { ...node, second: updateSplit(node.second, path.slice(1), ratio) }
}

export function setSplitRatio(layout: PaneLayout, path: string, ratio: number): PaneLayout {
  return layout.root ? { version: 2, root: updateSplit(layout.root, path, ratio) } : layout
}

export function swapTerminals(layout: PaneLayout, firstId: string, secondId: string): PaneLayout {
  if (!layout.root || firstId === secondId) return layout
  const ids = terminalIds(layout)
  if (!ids.includes(firstId) || !ids.includes(secondId)) return layout
  const swap = (node: PaneNode): PaneNode => {
    if (node.type === 'split') return { ...node, first: swap(node.first), second: swap(node.second) }
    if (node.kind !== 'terminal') return node
    if (node.id === firstId) return { ...node, id: secondId }
    if (node.id === secondId) return { ...node, id: firstId }
    return node
  }
  return { version: 2, root: swap(layout.root) }
}
