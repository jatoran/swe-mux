import type { PaneLayout, PaneLeaf, PaneNode } from './layout'

export type MobileWorkspaceProjection = {
  tabs:PaneLeaf[]
  selected:PaneLeaf|null
}

export function mobileWorkspaceProjection(
  layout:PaneLayout,
  focusedViewId:string|null,
  activeTerminalId:string|null,
):MobileWorkspaceProjection {
  const tabs:PaneLeaf[]=[]
  let firstPaneActive:string|null=null
  const visit=(node:PaneNode|null)=>{
    if(!node)return
    if(node.type==='stack'){
      if(firstPaneActive===null)firstPaneActive=node.active_child_id
      tabs.push(...node.children)
      return
    }
    visit(node.first);visit(node.second)
  }
  visit(layout.root)
  const preferred=[
    focusedViewId,
    activeTerminalId,
    firstPaneActive,
  ]
  const selected=preferred.reduce<PaneLeaf|null>((match,id)=>match||tabs.find(tab=>tab.id===id)||null,null)||tabs[0]||null
  return {tabs,selected}
}

export function adjacentMobileTab(tabs:PaneLeaf[],closingId:string):PaneLeaf|null {
  const index=tabs.findIndex(tab=>tab.id===closingId)
  if(index<0)return tabs[0]||null
  return tabs[index+1]||tabs[index-1]||null
}
