export type DropSide='before'|'after'
export type ReorderAxis='horizontal'|'vertical'
export type ReorderRect={id:string;start:number;end:number}
export type ReorderTarget={id:string;side:DropSide}

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

export function beginDragPreview(event:{dataTransfer:DataTransfer|null},label:string):void{
  const transfer=event.dataTransfer
  if(!transfer)return
  transfer.effectAllowed='move'
  transfer.setData('text/plain',label)
  const ghost=document.createElement('div')
  ghost.className='mux-drag-ghost'
  ghost.textContent=label
  document.body.appendChild(ghost)
  transfer.setDragImage(ghost,18,14)
  window.setTimeout(()=>ghost.remove(),0)
}
