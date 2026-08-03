import type { PaneLayout, PaneLeaf, PaneNode } from './layout'

export type MobileWorkspaceProjection = {
  tabs:PaneLeaf[]
  selected:PaneLeaf|null
}

// Rail order is derived from the layout, full stop: depth-first over the tree,
// each stack's children in order. A device-local permutation overlay used to sit
// on top of this, driven by a `Move tab` row in the mobile long-press menu. Both
// are gone — no context menu reorders anything now — and the overlay could not
// simply be orphaned, because a phone that had already saved a permutation would
// have kept applying it forever with nothing able to write it again.
export function mobileWorkspaceProjection(
  layout:PaneLayout,
  focusedViewId:string|null,
  activeTerminalId:string|null,
):MobileWorkspaceProjection {
  const layoutTabs:PaneLeaf[]=[]
  let firstPaneActive:string|null=null
  const visit=(node:PaneNode|null)=>{
    if(!node)return
    if(node.type==='stack'){
      if(firstPaneActive===null)firstPaneActive=node.active_child_id
      layoutTabs.push(...node.children)
      return
    }
    visit(node.first);visit(node.second)
  }
  visit(layout.root)
  const tabs=layoutTabs
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
