export type PaneLeafKind = 'terminal' | 'note' | 'preview' | 'history'
export type SplitDirection = 'horizontal' | 'vertical'
export type PaneDirection = 'left'|'right'|'up'|'down'

export type PaneLeaf = { type: 'leaf'; kind: PaneLeafKind; id: string }
export type PaneStack = {
  type: 'stack'; id: string; children: PaneLeaf[]; active_child_id: string
}
export type PaneSplit = {
  type: 'split'; id: string; direction: SplitDirection; ratio: number
  first: PaneNode; second: PaneNode
}
export type PaneNode = PaneStack | PaneSplit
export type PaneLayout = { version: 6; root: PaneNode | null }

export type NoteLeafIdentity = { kind: 'note' | 'session-note' | 'files' | 'file'; id: string }

type BrowserCrypto = Pick<Crypto, 'getRandomValues'> & Partial<Pick<Crypto, 'randomUUID'>>

/** Browser-local UUID that also works on direct tailnet HTTP. */
export function browserUuid(cryptoApi: BrowserCrypto | undefined = globalThis.crypto): string {
  if (typeof cryptoApi?.randomUUID === 'function') {
    try { return cryptoApi.randomUUID() } catch { /* use the compatible path */ }
  }
  const bytes = new Uint8Array(16)
  if (cryptoApi?.getRandomValues) cryptoApi.getRandomValues(bytes)
  else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

const groupId = () => `group-${browserUuid().slice(0, 12)}`
const clampRatio=(ratio:number)=>Math.max(.1,Math.min(.9,Math.round(ratio*10000)/10000))
export const emptyLayout = (): PaneLayout => ({ version: 6, root: null })
export const terminalLeaf = (id: string): PaneLeaf => ({ type: 'leaf', kind: 'terminal', id })
export const resourceLeaf = (kind: PaneLeafKind, id: string): PaneLeaf => ({ type: 'leaf', kind, id })
export const paneStack=(children:PaneLeaf[],activeId=children[0]?.id||'',id=groupId()):PaneStack=>({type:'stack',id,children,active_child_id:children.some(child=>child.id===activeId)?activeId:children[0]?.id||''})

export function noteResourceId(kind: NoteLeafIdentity['kind'], id: string): string {
  return `${kind === 'session-note' ? 'sessions' : kind}:${encodeURIComponent(id)}`
}

export function parseNoteResourceId(resourceId: string): NoteLeafIdentity | null {
  const separator = resourceId.indexOf(':')
  if (separator < 1) return null
  const kind = resourceId.slice(0, separator)
  if (kind !== 'note' && kind !== 'sessions' && kind !== 'files' && kind !== 'file') return null
  try {
    const id = decodeURIComponent(resourceId.slice(separator + 1))
    return id ? { kind: kind === 'sessions' ? 'session-note' : kind, id } as NoteLeafIdentity : null
  } catch {
    return null
  }
}

const isLeaf=(value:unknown):value is PaneLeaf=>{
  if(!value||typeof value!=='object')return false
  const leaf=value as Record<string,unknown>
  return leaf.type==='leaf'&&['terminal','note','preview','history'].includes(String(leaf.kind))&&typeof leaf.id==='string'&&!!leaf.id
}

function parseNode(value:unknown,seen:Set<string>,legacy=false):PaneNode|null{
  if(!value||typeof value!=='object')return null
  const node=value as Record<string,unknown>
  if(legacy&&isLeaf(value)){
    const key=value.id
    if(seen.has(key))return null
    seen.add(key)
    return paneStack([value])
  }
  if(node.type==='stack'){
    if(!Array.isArray(node.children)||!node.children.length)return null
    const children:PaneLeaf[]=[]
    for(const value of node.children){
      if(!isLeaf(value))return null
      const key=value.id
      if(seen.has(key))continue
      seen.add(key);children.push({...value})
    }
    if(!children.length)return null
    const active=typeof node.active_child_id==='string'?node.active_child_id:children[0].id
    return paneStack(children,active,typeof node.id==='string'&&node.id?node.id:groupId())
  }
  if(node.type==='split'&&(node.direction==='horizontal'||node.direction==='vertical')&&typeof node.ratio==='number'){
    const first=parseNode(node.first,seen,legacy),second=parseNode(node.second,seen,legacy)
    if(!first)return second
    if(!second)return first
    return {type:'split',id:typeof node.id==='string'&&node.id?node.id:groupId(),direction:node.direction,ratio:clampRatio(node.ratio),first,second}
  }
  return null
}

function legacyTree(ids:string[]):PaneNode|null{
  if(!ids.length)return null
  if(ids.length===1)return paneStack([terminalLeaf(ids[0])])
  const midpoint=Math.ceil(ids.length/2)
  return {type:'split',id:groupId(),direction:ids.length===2||ids.length===4?'horizontal':'vertical',ratio:.5,first:legacyTree(ids.slice(0,midpoint))!,second:legacyTree(ids.slice(midpoint))!}
}

function migrateResourceWorkspace(raw:unknown,seen:Set<string>):{pane:PaneStack|null;size:number}{
  if(!raw||typeof raw!=='object')return {pane:null,size:.38}
  const workspace=raw as {open_ids?:unknown;active_id?:unknown;size?:unknown;visible?:unknown}
  if(!workspace.visible||!Array.isArray(workspace.open_ids))return {pane:null,size:.38}
  const children:PaneLeaf[]=[]
  for(const id of workspace.open_ids){
    if(typeof id!=='string'||!id)continue
    const key=id
    if(seen.has(key))continue
    seen.add(key);children.push(resourceLeaf('note',id))
  }
  const size=typeof workspace.size==='number'&&Number.isFinite(workspace.size)?Math.max(.2,Math.min(.7,workspace.size)):.38
  const active=typeof workspace.active_id==='string'?workspace.active_id:children[0]?.id
  return {pane:children.length?paneStack(children,active):null,size}
}

export function parseLayout(value:unknown):PaneLayout{
  if(!value||typeof value!=='object')return emptyLayout()
  const raw=value as {version?:number;root?:unknown;panes?:unknown;note_dock?:unknown;note_workspace?:unknown}
  if(raw.version===6){
    if(raw.root===null||raw.root===undefined)return emptyLayout()
    const root=parseNode(raw.root,new Set())
    return root?{version:6,root}:emptyLayout()
  }
  if([2,3,4,5].includes(raw.version||0)){
    const seen=new Set<string>()
    let root=raw.root===null||raw.root===undefined?null:parseNode(raw.root,seen,true)
    const workspace=migrateResourceWorkspace(raw.version===5?raw.note_workspace:raw.version===4?{...(raw.note_dock as object||{}),visible:true}:null,seen)
    if(workspace.pane)root=root?{type:'split',id:groupId(),direction:'horizontal',ratio:clampRatio(1-workspace.size),first:root,second:workspace.pane}:workspace.pane
    return {version:6,root}
  }
  if(Array.isArray(raw.panes)){
    const ids=[...new Set(raw.panes.filter((id):id is string=>typeof id==='string'&&!!id))]
    return {version:6,root:legacyTree(ids)}
  }
  return emptyLayout()
}

export function resolveLayout(cached:PaneLayout|undefined,persisted:unknown):PaneLayout{return cached??parseLayout(persisted)}

/** Share of the workspace given to the seeded Files column. */
export const DEFAULT_FILES_RATIO=.22

/** Seed a never-arranged Project with a narrow Files column beside its Project note.
 *
 * Both leaves are pure viewports: `project.md` already exists from Project creation and
 * Files reads on demand, so a first open still costs no process. The note's stack is
 * deliberately the wide one, because new terminals open as a tab in the focused stack and
 * therefore land there without any placement special case.
 */
export function defaultProjectLayout(projectId:string):PaneLayout{
  const files=paneStack([resourceLeaf('note',noteResourceId('files',projectId))])
  const note=paneStack([resourceLeaf('note',noteResourceId('note',projectId))])
  return {version:6,root:{type:'split',id:groupId(),direction:'horizontal',ratio:clampRatio(DEFAULT_FILES_RATIO),first:files,second:note}}
}

/** Anchor a terminal spawned into a Project the browser is not currently viewing.
 *
 * There is no focused view to inherit, and the first leaf in tree order is the seeded
 * Files column, so a naive anchor buries terminals in a narrow sidebar-width pane.
 * Prefer an existing terminal, then any pane that is not a file browser.
 */
export function spawnAnchorId(layout:PaneLayout):string|null{
  const all=leaves(layout)
  return all.find(leaf=>leaf.kind==='terminal')?.id
    ??all.find(leaf=>leaf.kind!=='note'||parseNoteResourceId(leaf.id)?.kind!=='files')?.id
    ??all[0]?.id
    ??null
}

/** A Files browser leaf is a `note`-kind leaf whose resource id decodes to kind `files`. */
export function isFilesLeaf(leaf:PaneLeaf):boolean{return leaf.kind==='note'&&parseNoteResourceId(leaf.id)?.kind==='files'}
export function stackHasFiles(stack:PaneStack):boolean{return stack.children.some(isFilesLeaf)}

/** A leaf id to anchor a newly opened view, honoring the caller's preference but never a Files
 *  pane: prefer the preferred leaf's stack when it is not a Files pane, then the first non-Files
 *  pane, and only when Files is the sole pane fall back to it. Returns null for an empty layout. */
export function openAnchorId(layout:PaneLayout,preferredId:string|null):string|null{
  const stacks=paneStacks(layout)
  const preferred=preferredId?stacks.find(stack=>stack.children.some(child=>child.id===preferredId)):null
  if(preferred&&!stackHasFiles(preferred))return preferredId
  return (stacks.find(stack=>!stackHasFiles(stack))||stacks[0])?.active_child_id??null
}

export function leaves(layout:PaneLayout,kind?:PaneLeafKind):PaneLeaf[]{
  const result:PaneLeaf[]=[]
  const visit=(node:PaneNode|null)=>{if(!node)return;if(node.type==='stack'){for(const child of node.children)if(!kind||child.kind===kind)result.push(child);return}visit(node.first);visit(node.second)}
  visit(layout.root);return result
}

export const terminalIds=(layout:PaneLayout)=>leaves(layout,'terminal').map(leaf=>leaf.id)
export const paneStacks=(layout:PaneLayout):PaneStack[]=>{const result:PaneStack[]=[];const visit=(node:PaneNode|null)=>{if(!node)return;if(node.type==='stack'){result.push(node);return}visit(node.first);visit(node.second)};visit(layout.root);return result}

export function visibleTerminalIds(layout:PaneLayout):string[]{
  const result:string[]=[]
  for(const pane of paneStacks(layout)){
    const active=pane.children.find(child=>child.id===pane.active_child_id)||pane.children[0]
    if(active?.kind==='terminal')result.push(active.id)
  }
  return result
}

export function stackForView(layout:PaneLayout,viewId:string):PaneStack|null{return paneStacks(layout).find(pane=>pane.children.some(child=>child.id===viewId))||null}
export const stackForSession=stackForView

function mapPanes(node:PaneNode,visit:(pane:PaneStack)=>PaneNode):PaneNode{
  if(node.type==='stack')return visit(node)
  return {...node,first:mapPanes(node.first,visit),second:mapPanes(node.second,visit)}
}

export function activateStackChild(layout:PaneLayout,stackId:string,viewId:string):PaneLayout{
  if(!layout.root)return layout
  return {...layout,root:mapPanes(layout.root,pane=>pane.id===stackId&&pane.children.some(child=>child.id===viewId)?{...pane,active_child_id:viewId}:pane)}
}

export function activateContainingStack(layout:PaneLayout,viewId:string):PaneLayout{
  const pane=stackForView(layout,viewId)
  return pane?activateStackChild(layout,pane.id,viewId):layout
}

export function openTab(layout:PaneLayout,targetId:string|null,next:PaneLeaf):PaneLayout{
  const existing=leaves(layout).find(leaf=>leaf.id===next.id)
  if(existing)return activateContainingStack(layout,existing.id)
  if(!layout.root)return {...layout,root:paneStack([next])}
  const target=targetId?stackForView(layout,targetId):null
  // Never default into a Files pane: an unanchored open lands in the first non-Files pane.
  const pane=target||paneStacks(layout).find(stack=>!stackHasFiles(stack))||paneStacks(layout)[0]
  if(!pane)return {...layout,root:paneStack([next])}
  return {...layout,root:mapPanes(layout.root,item=>item.id===pane.id?{...item,children:[...item.children,next],active_child_id:next.id}:item)}
}

function removeNode(node:PaneNode,kind:PaneLeafKind,id:string):PaneNode|null{
  if(node.type==='stack'){
    const children=node.children.filter(child=>!(child.kind===kind&&child.id===id))
    if(!children.length)return null
    return {...node,children,active_child_id:children.some(child=>child.id===node.active_child_id)?node.active_child_id:children[0].id}
  }
  const first=removeNode(node.first,kind,id),second=removeNode(node.second,kind,id)
  if(!first)return second
  if(!second)return first
  return {...node,first,second}
}

export function removeLeaf(layout:PaneLayout,kind:PaneLeafKind,id:string):PaneLayout{return {...layout,root:layout.root?removeNode(layout.root,kind,id):null}}

function replaceStack(node:PaneNode,stackId:string,replace:(pane:PaneStack)=>PaneNode):PaneNode{
  if(node.type==='stack')return node.id===stackId?replace(node):node
  return {...node,first:replaceStack(node.first,stackId,replace),second:replaceStack(node.second,stackId,replace)}
}

export function splitView(layout:PaneLayout,targetId:string|null,next:PaneLeaf,direction:SplitDirection,position:'before'|'after'='after'):PaneLayout{
  if(!layout.root)return {...layout,root:paneStack([next])}
  const existing=leaves(layout).find(leaf=>leaf.id===next.id)
  if(existing&&existing.id===targetId)return activateContainingStack(layout,targetId!)
  const targetPane=(targetId&&stackForView(layout,targetId))||paneStacks(layout).find(stack=>!stackHasFiles(stack))||paneStacks(layout)[0]
  if(!targetPane)return openTab(layout,null,next)
  const without=existing?removeLeaf(layout,next.kind,next.id):layout
  if(!without.root)return {...without,root:paneStack([next])}
  const resolvedTarget=paneStacks(without).find(pane=>pane.id===targetPane.id)||paneStacks(without)[0]
  const added=paneStack([next])
  return {...without,root:replaceStack(without.root,resolvedTarget.id,pane=>({type:'split',id:groupId(),direction,ratio:.5,first:position==='before'?added:pane,second:position==='before'?pane:added}))}
}

export function replaceTerminal(layout:PaneLayout,targetId:string|null,nextId:string):PaneLayout{
  if(!targetId||!terminalIds(layout).includes(targetId))return openTab(layout,targetId,terminalLeaf(nextId))
  if(!layout.root)return openTab(layout,null,terminalLeaf(nextId))
  return {...layout,root:mapPanes(layout.root,pane=>pane.children.some(child=>child.kind==='terminal'&&child.id===targetId)?{...pane,children:pane.children.map(child=>child.kind==='terminal'&&child.id===targetId?terminalLeaf(nextId):child),active_child_id:nextId}:pane)}
}

export function splitTerminal(layout:PaneLayout,targetId:string|null,nextId:string,direction:SplitDirection,position:'before'|'after'='after'):PaneLayout{return splitView(layout,targetId,terminalLeaf(nextId),direction,position)}
export function attachLeaf(layout:PaneLayout,targetId:string|null,next:PaneLeaf,direction:SplitDirection='horizontal',ratio=.5):PaneLayout{
  const result=splitView(layout,targetId,next,direction)
  if(result.root?.type==='split'&&ratio!==.5)return {...result,root:{...result.root,ratio:clampRatio(ratio)}}
  return result
}
export function stackTerminal(layout:PaneLayout,targetId:string,nextId:string):PaneLayout{return openTab(layout,targetId,terminalLeaf(nextId))}
export function groupTerminalsInStack(layout:PaneLayout,targetId:string,nextId:string):PaneLayout{
  if(targetId===nextId||!terminalIds(layout).includes(targetId)||!terminalIds(layout).includes(nextId))return layout
  return openTab(removeLeaf(layout,'terminal',nextId),targetId,terminalLeaf(nextId))
}

export function addToStack(layout:PaneLayout,stackId:string,sessionId:string):PaneLayout{return addLeafToStack(layout,stackId,terminalLeaf(sessionId))}
export function addLeafToStack(layout:PaneLayout,stackId:string,next:PaneLeaf):PaneLayout{
  if(leaves(layout).some(leaf=>leaf.id===next.id)||!layout.root)return layout
  return {...layout,root:mapPanes(layout.root,pane=>pane.id===stackId?{...pane,children:[...pane.children,next],active_child_id:next.id}:pane)}
}

function setSplitRatioContaining(layout:PaneLayout,leafId:string,ratio:number):PaneLayout{
  if(!layout.root)return layout
  const visit=(node:PaneNode):PaneNode=>{
    if(node.type==='stack')return node
    const first=visit(node.first),second=visit(node.second)
    const owns=(branch:PaneNode)=>branch.type==='stack'&&branch.children.some(child=>child.id===leafId)
    const next={...node,first,second}
    if(owns(node.first))return {...next,ratio:clampRatio(ratio)}
    if(owns(node.second))return {...next,ratio:clampRatio(1-ratio)}
    return next
  }
  return {...layout,root:visit(layout.root)}
}

/** Place a companion view (a session note) beside its anchor rather than over it.
 *
 * Stacking a note onto the terminal that opened it hides the terminal, which
 * defeats jotting while an agent works. Preference order: an already-open copy,
 * then an existing non-terminal pane, then a new split beside the anchor.
 */
export function placeCompanionLeaf(
  layout:PaneLayout,
  anchorId:string|null,
  next:PaneLeaf,
  direction:SplitDirection='horizontal',
  ratio=1/3,
):PaneLayout{
  if(leaves(layout).some(leaf=>leaf.id===next.id))return activateContainingStack(layout,next.id)
  if(!layout.root)return {...layout,root:paneStack([next])}
  const anchorPane=anchorId?stackForView(layout,anchorId):null
  const others=paneStacks(layout).filter(pane=>pane.id!==anchorPane?.id&&!stackHasFiles(pane))
  const companion=others.find(pane=>pane.children.every(child=>child.kind!=='terminal'))||others[0]
  if(companion)return addLeafToStack(layout,companion.id,next)
  const anchor=anchorPane?.children.find(child=>child.id===anchorId)?.id
    ||anchorPane?.active_child_id
    ||paneStacks(layout)[0]?.active_child_id
    ||null
  return setSplitRatioContaining(splitView(layout,anchor,next,direction),next.id,ratio)
}

export function moveLeafToStack(layout:PaneLayout,kind:PaneLeafKind,id:string,targetStackId:string,orderedIds?:string[]):PaneLayout{
  const leaf=leaves(layout).find(item=>item.kind===kind&&item.id===id)
  const source=stackForView(layout,id)
  if(!leaf||!source)return layout
  if(source.id===targetStackId)return orderedIds?reorderStack(layout,targetStackId,orderedIds):activateStackChild(layout,targetStackId,id)
  const without=removeLeaf(layout,kind,id)
  return addLeafToStack(without,targetStackId,leaf)
}

export function moveLeafToSplit(layout:PaneLayout,kind:PaneLeafKind,id:string,targetStackId:string,direction:SplitDirection,position:'before'|'after'):PaneLayout{
  const leaf=leaves(layout).find(item=>item.kind===kind&&item.id===id)
  const target=paneStacks(layout).find(pane=>pane.id===targetStackId)
  if(!leaf||!target)return layout
  const anchor=target.children.find(child=>child.id!==id)?.id||target.children[0]?.id
  if(!anchor||anchor===id)return layout
  return splitView(layout,anchor,leaf,direction,position)
}

function boundaryStack(node:PaneNode,direction:PaneDirection):PaneStack{
  if(node.type==='stack')return node
  if(direction==='left')return boundaryStack(node.direction==='horizontal'?node.second:node.first,direction)
  if(direction==='right')return boundaryStack(node.first,direction)
  if(direction==='up')return boundaryStack(node.direction==='vertical'?node.second:node.first,direction)
  return boundaryStack(node.first,direction)
}

/** Resolve one structural neighbor in each visual direction from a view's pane. */
export function paneNeighborIds(layout:PaneLayout,viewId:string):Partial<Record<PaneDirection,string>>{
  if(!layout.root)return {}
  const path:Array<{split:PaneSplit;side:'first'|'second'}>=[]
  const find=(node:PaneNode):boolean=>{
    if(node.type==='stack')return node.children.some(child=>child.id===viewId)
    path.push({split:node,side:'first'});if(find(node.first))return true;path.pop()
    path.push({split:node,side:'second'});if(find(node.second))return true;path.pop()
    return false
  }
  if(!find(layout.root))return {}
  const result:Partial<Record<PaneDirection,string>>={}
  for(let index=path.length-1;index>=0;index--){
    const {split,side}=path[index]
    if(split.direction==='horizontal'&&side==='second'&&!result.left)result.left=boundaryStack(split.first,'left').id
    if(split.direction==='horizontal'&&side==='first'&&!result.right)result.right=boundaryStack(split.second,'right').id
    if(split.direction==='vertical'&&side==='second'&&!result.up)result.up=boundaryStack(split.first,'up').id
    if(split.direction==='vertical'&&side==='first'&&!result.down)result.down=boundaryStack(split.second,'down').id
  }
  return result
}

export function reconcileTerminals(layout:PaneLayout,liveIds:Set<string>):PaneLayout{let next=layout;for(const id of terminalIds(layout))if(!liveIds.has(id))next=removeLeaf(next,'terminal',id);return next}
export function reconcilePreviews(layout:PaneLayout,liveIds:Set<string>):PaneLayout{let next=layout;for(const leaf of leaves(layout,'preview'))if(!liveIds.has(leaf.id))next=removeLeaf(next,'preview',leaf.id);return next}

function updateSplit(node:PaneNode,path:string,ratio:number):PaneNode{
  if(node.type!=='split')return node
  if(!path)return {...node,ratio:clampRatio(ratio)}
  return path[0]==='f'?{...node,first:updateSplit(node.first,path.slice(1),ratio)}:{...node,second:updateSplit(node.second,path.slice(1),ratio)}
}
export function setSplitRatio(layout:PaneLayout,path:string,ratio:number):PaneLayout{return layout.root?{...layout,root:updateSplit(layout.root,path,ratio)}:layout}

export function reorderStack(layout:PaneLayout,stackId:string,orderedIds:string[]):PaneLayout{
  if(!layout.root)return layout
  return {...layout,root:mapPanes(layout.root,pane=>{
    if(pane.id!==stackId)return pane
    const byId=new Map(pane.children.map(child=>[child.id,child]))
    const children=orderedIds.map(id=>byId.get(id)).filter((child):child is PaneLeaf=>!!child)
    return children.length===pane.children.length&&new Set(orderedIds).size===pane.children.length?{...pane,children}:pane
  })}
}

export function swapTerminals(layout:PaneLayout,firstId:string,secondId:string):PaneLayout{
  if(!layout.root||firstId===secondId||!terminalIds(layout).includes(firstId)||!terminalIds(layout).includes(secondId))return layout
  return {...layout,root:mapPanes(layout.root,pane=>({...pane,children:pane.children.map(child=>child.kind!=='terminal'?child:child.id===firstId?terminalLeaf(secondId):child.id===secondId?terminalLeaf(firstId):child),active_child_id:pane.active_child_id===firstId?secondId:pane.active_child_id===secondId?firstId:pane.active_child_id}))}
}

/** Swap two complete pane regions while preserving every tab and active tab in them. */
export function swapPanes(layout:PaneLayout,firstStackId:string,secondStackId:string):PaneLayout{
  if(!layout.root||firstStackId===secondStackId)return layout
  const first=paneStacks(layout).find(pane=>pane.id===firstStackId)
  const second=paneStacks(layout).find(pane=>pane.id===secondStackId)
  if(!first||!second)return layout
  const replace=(node:PaneNode):PaneNode=>{
    if(node.type==='stack')return node.id===firstStackId?second:node.id===secondStackId?first:node
    return {...node,first:replace(node.first),second:replace(node.second)}
  }
  return {...layout,root:replace(layout.root)}
}

export function dissolveStack(layout:PaneLayout,stackId:string):PaneLayout{
  if(!layout.root)return layout
  return {...layout,root:replaceStack(layout.root,stackId,pane=>{
    const panes=pane.children.map(child=>paneStack([child],child.id))
    return panes.slice(1).reduce<PaneNode>((first,second)=>({type:'split',id:groupId(),direction:'horizontal',ratio:.5,first,second}),panes[0])
  })}
}
