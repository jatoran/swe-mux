export type PaneLeafKind = 'terminal' | 'note' | 'preview'
export type SplitDirection = 'horizontal' | 'vertical'

export type PaneLeaf = { type: 'leaf'; kind: PaneLeafKind; id: string }
export type PaneSplit = {
  type: 'split'; id: string; direction: SplitDirection; ratio: number
  first: PaneNode; second: PaneNode
}
export type PaneStack = {
  type: 'stack'; id: string; children: PaneLeaf[]; active_child_id: string
}
export type PaneNode = PaneLeaf | PaneSplit | PaneStack
export type PaneLayout = { version: 3; root: PaneNode | null }

const groupId = () => `group-${crypto.randomUUID().slice(0, 12)}`
export const emptyLayout = (): PaneLayout => ({ version: 3, root: null })
export const terminalLeaf = (id: string): PaneLeaf => ({ type: 'leaf', kind: 'terminal', id })
export const resourceLeaf = (kind: PaneLeafKind, id: string): PaneLeaf => ({ type: 'leaf', kind, id })

export type NoteLeafIdentity = { kind: 'projects' | 'spaces' | 'sessions'; id: string }

export function noteResourceId(kind: NoteLeafIdentity['kind'], id: string): string {
  return `${kind}:${encodeURIComponent(id)}`
}

export function parseNoteResourceId(resourceId: string): NoteLeafIdentity | null {
  const separator = resourceId.indexOf(':')
  if (separator < 1) return null
  const kind = resourceId.slice(0, separator)
  if (kind !== 'projects' && kind !== 'spaces' && kind !== 'sessions') return null
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
    type: 'split', id: groupId(), direction: ids.length === 2 || ids.length === 4 ? 'horizontal' : 'vertical', ratio: .5,
    first: legacyTree(ids.slice(0, midpoint))!, second: legacyTree(ids.slice(midpoint))!,
  }
}

function isNode(value: unknown): value is PaneNode {
  if (!value || typeof value !== 'object') return false
  const node = value as Record<string, unknown>
  if (node.type === 'leaf') {
    return ['terminal', 'note', 'preview'].includes(String(node.kind)) && typeof node.id === 'string' && !!node.id
  }
  if (node.type === 'stack') return typeof node.id === 'string'
    && Array.isArray(node.children) && node.children.length > 0
    && node.children.every(child => isNode(child) && child.type === 'leaf' && child.kind === 'terminal')
    && typeof node.active_child_id === 'string'
  return node.type === 'split'
    && (node.direction === 'horizontal' || node.direction === 'vertical')
    && typeof node.ratio === 'number'
    && isNode(node.first) && isNode(node.second)
}

export function parseLayout(value: unknown): PaneLayout {
  if (!value || typeof value !== 'object') return emptyLayout()
  const raw = value as { version?: number; root?: unknown; panes?: unknown }
  if (raw.version === 2 || raw.version === 3) return raw.root === null || raw.root === undefined || isNode(raw.root)
    ? { version: 3, root: (raw.root as PaneNode | null | undefined) ?? null }
    : emptyLayout()
  if (Array.isArray(raw.panes)) {
    const ids = [...new Set(raw.panes.filter((id): id is string => typeof id === 'string' && !!id))]
    return { version: 3, root: legacyTree(ids) }
  }
  return emptyLayout()
}

export function resolveLayout(cached: PaneLayout | undefined, persisted: unknown): PaneLayout {
  return cached ?? parseLayout(persisted)
}

export function leaves(layout: PaneLayout, kind?: PaneLeafKind): PaneLeaf[] {
  const result: PaneLeaf[] = []
  const visit = (node: PaneNode | null) => {
    if (!node) return
    if (node.type === 'leaf') {
      if (!kind || node.kind === kind) result.push(node)
      return
    }
    if (node.type === 'stack') node.children.forEach(visit)
    else { visit(node.first); visit(node.second) }
  }
  visit(layout.root)
  return result
}

export const terminalIds = (layout: PaneLayout) => leaves(layout, 'terminal').map(leaf => leaf.id)

export function visibleTerminalIds(layout: PaneLayout): string[] {
  const result: string[] = []
  const visit = (node: PaneNode | null) => {
    if (!node) return
    if (node.type === 'leaf') {
      if (node.kind === 'terminal') result.push(node.id)
      return
    }
    if (node.type === 'stack') {
      const active = node.children.find(child => child.id === node.active_child_id) ?? node.children[0]
      visit(active)
      return
    }
    visit(node.first)
    visit(node.second)
  }
  visit(layout.root)
  return result
}

function mapNode(node: PaneNode, targetId: string, replace: (leaf: PaneLeaf) => PaneNode): PaneNode {
  if (node.type === 'leaf') return node.kind === 'terminal' && node.id === targetId ? replace(node) : node
  if (node.type === 'stack') return { ...node, children: node.children.map(child =>
    child.id === targetId ? replace(child) as PaneLeaf : child) }
  return { ...node, first: mapNode(node.first, targetId, replace), second: mapNode(node.second, targetId, replace) }
}

export function replaceTerminal(layout: PaneLayout, targetId: string | null, nextId: string): PaneLayout {
  if (!layout.root) return { version: 3, root: terminalLeaf(nextId) }
  if (!targetId || !terminalIds(layout).includes(targetId)) {
    const first = terminalIds(layout)[0]
    return first ? replaceTerminal(layout, first, nextId) : {
      version:3,
      root:{type:'split',id:groupId(),direction:'horizontal',ratio:.62,first:terminalLeaf(nextId),second:layout.root},
    }
  }
  return { version: 3, root: mapNode(layout.root, targetId, () => terminalLeaf(nextId)) }
}

export function splitTerminal(
  layout: PaneLayout,
  targetId: string | null,
  nextId: string,
  direction: SplitDirection,
): PaneLayout {
  if (!layout.root) return { version: 3, root: terminalLeaf(nextId) }
  const ids = terminalIds(layout)
  if (ids.includes(nextId)) return layout
  const target = targetId && ids.includes(targetId) ? targetId : ids[0]
  if (!target) return layout
  const visit=(node:PaneNode):PaneNode=>{
    if(node.type==='leaf')return node.kind==='terminal'&&node.id===target
      ?{type:'split',id:groupId(),direction,ratio:.5,first:node,second:terminalLeaf(nextId)}:node
    if(node.type==='stack')return node.children.some(child=>child.id===target)
      ?{type:'split',id:groupId(),direction,ratio:.5,first:node,second:terminalLeaf(nextId)}:node
    return {...node,first:visit(node.first),second:visit(node.second)}
  }
  return {version:3,root:visit(layout.root)}
}

export function attachLeaf(
  layout: PaneLayout,
  targetId: string | null,
  next: PaneLeaf,
  direction: SplitDirection = 'horizontal',
  ratio = .5,
): PaneLayout {
  if (leaves(layout).some(leaf => leaf.kind === next.kind && leaf.id === next.id)) return layout
  if (!layout.root) return { version: 3, root: next }
  const ids = terminalIds(layout)
  const target = targetId && ids.includes(targetId) ? targetId : ids[0]
  if (!target) return layout
  const clampedRatio = Math.max(.1, Math.min(.9, ratio))
  const visit=(node:PaneNode):PaneNode=>{
    if(node.type==='leaf')return node.kind==='terminal'&&node.id===target
      ?{type:'split',id:groupId(),direction,ratio:clampedRatio,first:node,second:next}:node
    if(node.type==='stack')return node.children.some(child=>child.id===target)
      ?{type:'split',id:groupId(),direction,ratio:clampedRatio,first:node,second:next}:node
    return {...node,first:visit(node.first),second:visit(node.second)}
  }
  return {version:3,root:visit(layout.root)}
}

function removeNode(node: PaneNode, kind: PaneLeafKind, id: string): PaneNode | null {
  if (node.type === 'leaf') return node.kind === kind && node.id === id ? null : node
  if (node.type === 'stack') {
    const children = node.children.filter(child => !(child.kind === kind && child.id === id))
    if (!children.length) return null
    if (children.length === 1) return children[0]
    return { ...node, children, active_child_id: children.some(child => child.id === node.active_child_id)
      ? node.active_child_id : children[0].id }
  }
  const first = removeNode(node.first, kind, id)
  const second = removeNode(node.second, kind, id)
  if (!first) return second
  if (!second) return first
  return { ...node, first, second }
}

export function removeLeaf(layout: PaneLayout, kind: PaneLeafKind, id: string): PaneLayout {
  return { version: 3, root: layout.root ? removeNode(layout.root, kind, id) : null }
}

export function reconcileTerminals(layout: PaneLayout, liveIds: Set<string>): PaneLayout {
  let next = layout
  for (const id of terminalIds(layout)) if (!liveIds.has(id)) next = removeLeaf(next, 'terminal', id)
  return next
}

function updateSplit(node: PaneNode, path: string, ratio: number): PaneNode {
  if (node.type !== 'split') return node
  if (!path) return { ...node, ratio: Math.max(.1, Math.min(.9, Math.round(ratio * 10000) / 10000)) }
  return path[0] === 'f'
    ? { ...node, first: updateSplit(node.first, path.slice(1), ratio) }
    : { ...node, second: updateSplit(node.second, path.slice(1), ratio) }
}

export function setSplitRatio(layout: PaneLayout, path: string, ratio: number): PaneLayout {
  return layout.root ? { version: 3, root: updateSplit(layout.root, path, ratio) } : layout
}

export function swapTerminals(layout: PaneLayout, firstId: string, secondId: string): PaneLayout {
  if (!layout.root || firstId === secondId) return layout
  const ids = terminalIds(layout)
  if (!ids.includes(firstId) || !ids.includes(secondId)) return layout
  const swap = (node: PaneNode): PaneNode => {
    if (node.type === 'split') return { ...node, first: swap(node.first), second: swap(node.second) }
    if (node.type === 'stack') return { ...node, children: node.children.map(child => swap(child) as PaneLeaf),
      active_child_id: node.active_child_id === firstId ? secondId : node.active_child_id === secondId ? firstId : node.active_child_id }
    if (node.kind !== 'terminal') return node
    if (node.id === firstId) return { ...node, id: secondId }
    if (node.id === secondId) return { ...node, id: firstId }
    return node
  }
  return { version: 3, root: swap(layout.root) }
}

export function stackTerminal(layout: PaneLayout, targetId: string, nextId: string): PaneLayout {
  if (!layout.root || targetId === nextId || terminalIds(layout).includes(nextId)) return layout
  const visit=(node:PaneNode):PaneNode=>{
    if(node.type==='leaf')return node.kind==='terminal'&&node.id===targetId
      ?{type:'stack',id:groupId(),children:[node,terminalLeaf(nextId)],active_child_id:nextId}:node
    if(node.type==='stack')return node.children.some(child=>child.id===targetId)
      ?{...node,children:[...node.children,terminalLeaf(nextId)],active_child_id:nextId}:node
    return {...node,first:visit(node.first),second:visit(node.second)}
  }
  return {version:3,root:visit(layout.root)}
}

export function groupTerminalsInStack(layout:PaneLayout,targetId:string,nextId:string):PaneLayout{
  if(targetId===nextId||!terminalIds(layout).includes(targetId)||!terminalIds(layout).includes(nextId))return layout
  return stackTerminal(removeLeaf(layout,'terminal',nextId),targetId,nextId)
}

export function addToStack(layout: PaneLayout, stackId: string, sessionId: string): PaneLayout {
  if (!layout.root || terminalIds(layout).includes(sessionId)) return layout
  const visit = (node: PaneNode): PaneNode => {
    if (node.type === 'leaf') return node
    if (node.type === 'stack') return node.id === stackId
      ? { ...node, children: [...node.children, terminalLeaf(sessionId)], active_child_id: sessionId }
      : node
    return { ...node, first: visit(node.first), second: visit(node.second) }
  }
  return { version: 3, root: visit(layout.root) }
}

export function activateStackChild(layout: PaneLayout, stackId: string, sessionId: string): PaneLayout {
  if (!layout.root) return layout
  const visit = (node: PaneNode): PaneNode => {
    if (node.type === 'leaf') return node
    if (node.type === 'stack') return node.id === stackId && node.children.some(child => child.id === sessionId)
      ? { ...node, active_child_id: sessionId } : node
    return { ...node, first: visit(node.first), second: visit(node.second) }
  }
  return { version: 3, root: visit(layout.root) }
}

export function activateContainingStack(layout:PaneLayout,sessionId:string):PaneLayout{
  if(!layout.root)return layout
  const visit=(node:PaneNode):PaneNode=>{
    if(node.type==='leaf')return node
    if(node.type==='stack')return node.children.some(child=>child.id===sessionId)?{...node,active_child_id:sessionId}:node
    return {...node,first:visit(node.first),second:visit(node.second)}
  }
  return {version:3,root:visit(layout.root)}
}

export function reorderStack(layout: PaneLayout, stackId: string, orderedIds: string[]): PaneLayout {
  if (!layout.root) return layout
  const visit = (node: PaneNode): PaneNode => {
    if (node.type === 'leaf') return node
    if (node.type === 'stack' && node.id === stackId) {
      const byId = new Map(node.children.map(child => [child.id, child]))
      const children = orderedIds.map(id => byId.get(id)).filter((child): child is PaneLeaf => !!child)
      if (children.length !== node.children.length) return node
      return { ...node, children }
    }
    if (node.type === 'stack') return node
    return { ...node, first: visit(node.first), second: visit(node.second) }
  }
  return { version: 3, root: visit(layout.root) }
}

export function stackForSession(layout:PaneLayout,sessionId:string):PaneStack|null{
  let found:PaneStack|null=null
  const visit=(node:PaneNode|null)=>{if(!node||found)return;if(node.type==='stack'){if(node.children.some(child=>child.id===sessionId))found=node;return}if(node.type==='split'){visit(node.first);visit(node.second)}}
  visit(layout.root);return found
}

export function dissolveStack(layout:PaneLayout,stackId:string):PaneLayout{
  if(!layout.root)return layout
  const splitChildren=(children:PaneLeaf[]):PaneNode=>{
    const [first,...rest]=children
    return rest.reduce<PaneNode>((left,second)=>({type:'split',id:groupId(),direction:'horizontal',ratio:.5,first:left,second}),first)
  }
  const visit=(node:PaneNode):PaneNode=>{
    if(node.type==='leaf')return node
    if(node.type==='stack')return node.id===stackId?splitChildren(node.children):node
    return {...node,first:visit(node.first),second:visit(node.second)}
  }
  return {version:3,root:visit(layout.root)}
}
