export type ProcessTreeItem = {pid:number;parent_pid?:number}

export type ProcessTreeNode<T extends ProcessTreeItem> = {
  process:T
  children:ProcessTreeNode<T>[]
}

const hasParentCycle=<T extends ProcessTreeItem>(
  node:ProcessTreeNode<T>,
  parent:ProcessTreeNode<T>,
  nodes:Map<number,ProcessTreeNode<T>>,
):boolean=>{
  const visited=new Set<number>()
  let current:ProcessTreeNode<T>|undefined=parent
  while(current){
    if(current===node||visited.has(current.process.pid))return true
    visited.add(current.process.pid)
    current=current.process.parent_pid===undefined
      ?undefined
      :nodes.get(current.process.parent_pid)
  }
  return false
}

export const buildProcessTree=<T extends ProcessTreeItem>(processes:T[]):ProcessTreeNode<T>[]=>{
  const nodes=new Map<number,ProcessTreeNode<T>>()
  for(const process of processes)nodes.set(process.pid,{process,children:[]})

  const roots:ProcessTreeNode<T>[]=[]
  for(const process of processes){
    const node=nodes.get(process.pid)!
    const parent=process.parent_pid===undefined?undefined:nodes.get(process.parent_pid)
    if(!parent||parent===node||hasParentCycle(node,parent,nodes))roots.push(node)
    else parent.children.push(node)
  }
  return roots
}
