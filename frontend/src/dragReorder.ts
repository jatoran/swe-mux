export type DropSide='before'|'after'
export type ReorderAxis='horizontal'|'vertical'
export type ReorderRect={id:string;start:number;end:number}
export type ReorderTarget={id:string;side:DropSide}
export type PointerDragActivation=
  | {mode:'movement';threshold:number}
  | {mode:'hold';delayMs:number;slop:number}
  // Hold, *then* drag: a still finger for `delayMs` arms the drag, and the first move
  // past `slop` after that starts it. A move past `slop` before then is a scroll this
  // gesture never owned. Unlike `hold`, it never activates on stillness alone, which is
  // what lets a long-press context menu keep the still-hold and the drag keep the move.
  | {mode:'hold-move';delayMs:number;slop:number}
export type PointerDragMoveDecision='wait'|'activate'|'cancel'

export const POINTER_MOVE_DRAG:PointerDragActivation={mode:'movement',threshold:5}
export const MOBILE_PROJECT_HOLD_DRAG:PointerDragActivation={mode:'hold',delayMs:325,slop:8}
// The mobile reorder activation for every surface that also carries a long-press menu.
export const MOBILE_HOLD_MOVE_DRAG:PointerDragActivation={mode:'hold-move',delayMs:250,slop:8}

export function pointerDragMoveDecision(activation:PointerDragActivation,distance:number):PointerDragMoveDecision{
  if(activation.mode==='movement')return distance<activation.threshold?'wait':'activate'
  // `hold` and `hold-move` share the slop gate; `hold-move`'s time-gated `activate`
  // transition lives in the drag driver, not this pure function, which is why a caller
  // must not route a `hold-move` activation through here expecting an `activate`.
  return distance<=activation.slop?'wait':'cancel'
}

export function edgeAutoScrollDelta(point:number,start:number,end:number,edgeSize=56,maxSpeed=18):number{
  if(edgeSize<=0||maxSpeed<=0||end<=start)return 0
  if(point<start+edgeSize){
    const strength=Math.min(1,Math.max(0,(start+edgeSize-point)/edgeSize))
    return -maxSpeed*strength*strength
  }
  if(point>end-edgeSize){
    const strength=Math.min(1,Math.max(0,(point-(end-edgeSize))/edgeSize))
    return maxSpeed*strength*strength
  }
  return 0
}

export function reorderForHover(ids:string[],draggedId:string,targetId:string,side:DropSide):string[]{
  if(draggedId===targetId||!ids.includes(draggedId)||!ids.includes(targetId))return ids
  const next=ids.filter(id=>id!==draggedId)
  const targetIndex=next.indexOf(targetId)
  next.splice(targetIndex+(side==='after'?1:0),0,draggedId)
  return next.join('\0')===ids.join('\0')?ids:next
}

export function itemsInOrder<T extends {id:string}>(items:T[],ids:string[]):T[]{
  const byId=new Map(items.map(item=>[item.id,item]))
  return [...ids.map(id=>byId.get(id)).filter((item):item is T=>item!==undefined),...items.filter(item=>!ids.includes(item.id))]
}

export function reorderTargetForPoint(items:ReorderRect[],draggedId:string,point:number):ReorderTarget|null{
  const peers=items.filter(item=>item.id!==draggedId).sort((a,b)=>a.start-b.start)
  if(peers.length===0)return null
  const next=peers.find(item=>point<(item.start+item.end)/2)
  return next?{id:next.id,side:'before'}:{id:peers[peers.length-1].id,side:'after'}
}

/** A list whose rows are also drop targets in their own right — sidebar sessions, where landing
 *  *between* two rows reorders and landing *on* one joins them into a single tabbed pane. */
export type ListDropTarget={kind:'insert';id:string;side:DropSide}|{kind:'group';id:string}

/** How far outside a list's own box the pointer may stray and still be dropping into it. Past
 *  it the drag has left, and a list nowhere near the pointer must not commit anything on
 *  release. Wide enough to forgive the overshoot of aiming at the first or last gap. */
export const DROP_LIST_MARGIN=16

/** How much of a row's near edge reads as "between rows" rather than "on this row".
 *
 *  A fraction alone gives a 44px phone row a 13px landing strip and a 22px desktop row a 7px
 *  one, so it is clamped at both ends: every row keeps a reachable insertion edge, and no row
 *  is so generous with it that grouping becomes hard to aim at. */
export function insertionEdge(height:number,ratio=.3,minimum=5,maximum=12):number{
  if(!(height>0))return minimum
  return Math.min(maximum,Math.max(minimum,height*ratio))
}

/** `groupable` answers whether landing *on* that row would mean anything. A row it rejects is
 *  pure insertion over its whole height, rather than a middle band that silently does nothing —
 *  or, worse, does something the preview never showed. */
export function listDropTargetForPoint(items:ReorderRect[],draggedId:string,point:number,groupable?:(id:string)=>boolean):ListDropTarget|null{
  const peers=items.filter(item=>item.id!==draggedId).sort((a,b)=>a.start-b.start)
  if(peers.length===0)return null
  const hit=peers.find(item=>point>=item.start&&point<item.end)
  if(hit){
    const edge=insertionEdge(hit.end-hit.start)
    if(point<hit.start+edge)return {kind:'insert',id:hit.id,side:'before'}
    if(point>=hit.end-edge)return {kind:'insert',id:hit.id,side:'after'}
    if(!groupable||groupable(hit.id))return {kind:'group',id:hit.id}
    return {kind:'insert',id:hit.id,side:point<(hit.start+hit.end)/2?'before':'after'}
  }
  // Gaps between rows, and the space past either end of the list, resolve to the nearest slot:
  // the pointer is demonstrably not on a row, so there is nothing there to group with.
  const target=reorderTargetForPoint(peers,draggedId,point)
  return target?{kind:'insert',id:target.id,side:target.side}:null
}

export function reorderTargetFromContainer(container:HTMLElement,draggedId:string,axis:ReorderAxis,point:number):ReorderTarget|null{
  const items=Array.from(container.querySelectorAll<HTMLElement>(':scope > [data-reorder-id]')).map(element=>{
    const box=element.getBoundingClientRect()
    return {id:element.dataset.reorderId||'',start:axis==='horizontal'?box.left:box.top,end:axis==='horizontal'?box.right:box.bottom}
  }).filter(item=>item.id)
  return reorderTargetForPoint(items,draggedId,point)
}
