export type DropSide='before'|'after'
export type ReorderAxis='horizontal'|'vertical'
export type ReorderRect={id:string;start:number;end:number}
export type ReorderTarget={id:string;side:DropSide}
export type PointerDragActivation=
  | {mode:'movement';threshold:number}
  | {mode:'hold';delayMs:number;slop:number}
export type PointerDragMoveDecision='wait'|'activate'|'cancel'

export const POINTER_MOVE_DRAG:PointerDragActivation={mode:'movement',threshold:5}
export const MOBILE_PROJECT_HOLD_DRAG:PointerDragActivation={mode:'hold',delayMs:325,slop:8}

export function pointerDragMoveDecision(activation:PointerDragActivation,distance:number):PointerDragMoveDecision{
  if(activation.mode==='movement')return distance<activation.threshold?'wait':'activate'
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

export function reorderTargetFromContainer(container:HTMLElement,draggedId:string,axis:ReorderAxis,point:number):ReorderTarget|null{
  const items=Array.from(container.querySelectorAll<HTMLElement>(':scope > [data-reorder-id]')).map(element=>{
    const box=element.getBoundingClientRect()
    return {id:element.dataset.reorderId||'',start:axis==='horizontal'?box.left:box.top,end:axis==='horizontal'?box.right:box.bottom}
  }).filter(item=>item.id)
  return reorderTargetForPoint(items,draggedId,point)
}
