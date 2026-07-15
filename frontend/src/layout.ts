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
export type NoteWorkspace = { open_ids:string[];active_id:string|null;size:number;visible:boolean;mode:'dock'|'popout' }
export type PaneLayout = { version: 5; root: PaneNode | null;note_workspace:NoteWorkspace }

const groupId = () => `group-${crypto.randomUUID().slice(0, 12)}`
const emptyNoteWorkspace=():NoteWorkspace=>({open_ids:[],active_id:null,size:.38,visible:false,mode:'dock'})
export const emptyLayout = (): PaneLayout => ({ version: 5, root: null,note_workspace:emptyNoteWorkspace() })
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
  // Tab regions hold a session and the previews it spawned. Notes are excluded:
  // they live in the space note workspace, not the terminal tree.
  if (node.type === 'stack') return typeof node.id === 'string'
    && Array.isArray(node.children) && node.children.length > 0
    && node.children.every(child => isNode(child) && child.type === 'leaf'
      && (child.kind === 'terminal' || child.kind === 'preview'))
    && typeof node.active_child_id === 'string'
  return node.type === 'split'
    && (node.direction === 'horizontal' || node.direction === 'vertical')
    && typeof node.ratio === 'number'
    && isNode(node.first) && isNode(node.second)
}

function stripEmbeddedNotes(node:PaneNode|null):{root:PaneNode|null;notes:string[]}{
  if(!node)return {root:null,notes:[]}
  if(node.type==='leaf')return node.kind==='note'?{root:null,notes:[node.id]}:{root:node,notes:[]}
  if(node.type==='stack')return {root:node,notes:[]}
  const first=stripEmbeddedNotes(node.first),second=stripEmbeddedNotes(node.second)
  const notes=[...first.notes,...second.notes]
  if(!first.root)return {root:second.root,notes}
  if(!second.root)return {root:first.root,notes}
  return {root:{...node,first:first.root,second:second.root},notes}
}

function parseNoteWorkspace(value:unknown):NoteWorkspace{
  if(!value||typeof value!=='object')return emptyNoteWorkspace()
  const raw=value as {open_ids?:unknown;active_id?:unknown;size?:unknown;visible?:unknown;mode?:unknown}
  const open_ids=Array.isArray(raw.open_ids)?[...new Set(raw.open_ids.filter((item):item is string=>typeof item==='string'&&!!item))].slice(0,32):[]
  const size=typeof raw.size==='number'&&Number.isFinite(raw.size)?Math.max(.2,Math.min(.7,raw.size)):.38
  const mode=raw.mode==='popout'?'popout':'dock'
  return {open_ids,active_id:typeof raw.active_id==='string'&&open_ids.includes(raw.active_id)?raw.active_id:open_ids[0]||null,size,visible:!!raw.visible&&open_ids.length>0,mode}
}

export function parseLayout(value: unknown): PaneLayout {
  if (!value || typeof value !== 'object') return emptyLayout()
  const raw = value as { version?: number; root?: unknown; panes?: unknown;note_dock?:unknown;note_workspace?:unknown }
  if ([2,3,4,5].includes(raw.version||0)) {
    if(raw.root!==null&&raw.root!==undefined&&!isNode(raw.root))return emptyLayout()
    const migrated=stripEmbeddedNotes((raw.root as PaneNode|null|undefined)??null)
    const legacyDock=raw.note_dock&&typeof raw.note_dock==='object'?raw.note_dock:{}
    const workspace=raw.version===5?parseNoteWorkspace(raw.note_workspace):raw.version===4?parseNoteWorkspace({...legacyDock,visible:true,mode:'dock'}):emptyNoteWorkspace()
    workspace.open_ids=[...new Set([...workspace.open_ids,...migrated.notes])].slice(0,32)
    if(!workspace.active_id||!workspace.open_ids.includes(workspace.active_id))workspace.active_id=workspace.open_ids[0]||null
    if(migrated.notes.length)workspace.visible=true
    return {version:5,root:migrated.root,note_workspace:workspace}
  }
  if (Array.isArray(raw.panes)) {
    const ids = [...new Set(raw.panes.filter((id): id is string => typeof id === 'string' && !!id))]
    return { version: 5, root: legacyTree(ids),note_workspace:emptyNoteWorkspace() }
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
  if(!kind||kind==='note')layout.note_workspace.open_ids.forEach(id=>result.push(resourceLeaf('note',id)))
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
  if (!layout.root) return { ...layout, root: terminalLeaf(nextId) }
  if (!targetId || !terminalIds(layout).includes(targetId)) {
    const first = terminalIds(layout)[0]
    return first ? replaceTerminal(layout, first, nextId) : {
      ...layout,
      root:{type:'split',id:groupId(),direction:'horizontal',ratio:.62,first:terminalLeaf(nextId),second:layout.root},
    }
  }
  return { ...layout, root: mapNode(layout.root, targetId, () => terminalLeaf(nextId)) }
}

export function splitTerminal(
  layout: PaneLayout,
  targetId: string | null,
  nextId: string,
  direction: SplitDirection,
): PaneLayout {
  if (!layout.root) return { ...layout, root: terminalLeaf(nextId) }
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
  return {...layout,root:visit(layout.root)}
}

export function attachLeaf(
  layout: PaneLayout,
  targetId: string | null,
  next: PaneLeaf,
  direction: SplitDirection = 'horizontal',
  ratio = .5,
): PaneLayout {
  if (leaves(layout).some(leaf => leaf.kind === next.kind && leaf.id === next.id)) return layout
  if(next.kind==='note')return {...layout,note_workspace:{...layout.note_workspace,open_ids:[...layout.note_workspace.open_ids,next.id],active_id:next.id,visible:true}}
  if (!layout.root) return { ...layout, root: next }
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
  return {...layout,root:visit(layout.root)}
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
  if(kind==='note'){
    const open_ids=layout.note_workspace.open_ids.filter(item=>item!==id)
    return {...layout,note_workspace:{...layout.note_workspace,open_ids,active_id:layout.note_workspace.active_id===id?open_ids[0]||null:layout.note_workspace.active_id,visible:open_ids.length>0&&layout.note_workspace.visible}}
  }
  return { ...layout, root: layout.root ? removeNode(layout.root, kind, id) : null }
}

export function reconcileTerminals(layout: PaneLayout, liveIds: Set<string>): PaneLayout {
  let next = layout
  for (const id of terminalIds(layout)) if (!liveIds.has(id)) next = removeLeaf(next, 'terminal', id)
  return next
}

/** Drop preview leaves the daemon no longer lists, e.g. once their server stopped. */
export function reconcilePreviews(layout: PaneLayout, liveIds: Set<string>): PaneLayout {
  let next = layout
  for (const leaf of leaves(layout, 'preview')) {
    if (!liveIds.has(leaf.id)) next = removeLeaf(next, 'preview', leaf.id)
  }
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
  return layout.root ? { ...layout, root: updateSplit(layout.root, path, ratio) } : layout
}

export function activateNoteWorkspace(layout:PaneLayout,id:string):PaneLayout{
  return layout.note_workspace.open_ids.includes(id)?{...layout,note_workspace:{...layout.note_workspace,active_id:id}}:layout
}

export function showNoteWorkspace(layout:PaneLayout,id:string,mode:'dock'|'popout'):PaneLayout{
  const withNote=layout.note_workspace.open_ids.includes(id)?layout:attachLeaf(layout,null,resourceLeaf('note',id))
  return {...withNote,note_workspace:{...withNote.note_workspace,active_id:id,visible:true,mode}}
}

export function hideNoteWorkspace(layout:PaneLayout):PaneLayout{
  return {...layout,note_workspace:{...layout.note_workspace,visible:false}}
}

export function setNoteWorkspaceMode(layout:PaneLayout,mode:'dock'|'popout'):PaneLayout{
  return {...layout,note_workspace:{...layout.note_workspace,mode,visible:layout.note_workspace.open_ids.length>0}}
}

export function setNoteWorkspaceSize(layout:PaneLayout,size:number):PaneLayout{
  return {...layout,note_workspace:{...layout.note_workspace,size:Math.max(.2,Math.min(.7,Math.round(size*10000)/10000))}}
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
  return { ...layout, root: swap(layout.root) }
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
  return {...layout,root:visit(layout.root)}
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
  return { ...layout, root: visit(layout.root) }
}

export function activateStackChild(layout: PaneLayout, stackId: string, sessionId: string): PaneLayout {
  if (!layout.root) return layout
  const visit = (node: PaneNode): PaneNode => {
    if (node.type === 'leaf') return node
    if (node.type === 'stack') return node.id === stackId && node.children.some(child => child.id === sessionId)
      ? { ...node, active_child_id: sessionId } : node
    return { ...node, first: visit(node.first), second: visit(node.second) }
  }
  return { ...layout, root: visit(layout.root) }
}

export function activateContainingStack(layout:PaneLayout,sessionId:string):PaneLayout{
  if(!layout.root)return layout
  const visit=(node:PaneNode):PaneNode=>{
    if(node.type==='leaf')return node
    if(node.type==='stack')return node.children.some(child=>child.id===sessionId)?{...node,active_child_id:sessionId}:node
    return {...node,first:visit(node.first),second:visit(node.second)}
  }
  return {...layout,root:visit(layout.root)}
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
  return { ...layout, root: visit(layout.root) }
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
  return {...layout,root:visit(layout.root)}
}
