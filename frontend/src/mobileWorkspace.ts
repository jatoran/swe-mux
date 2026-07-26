import type { PaneLayout, PaneLeaf, PaneNode } from './layout'
// Explicit extension: this module is exercised by the node type-stripping test
// runner, which does not resolve extensionless specifiers.
import { orderMobileTabs } from './mobileTabOrder.ts'

export type MobileWorkspaceProjection = {
  tabs:PaneLeaf[]
  selected:PaneLeaf|null
}

// `savedOrder` is the device-local rail permutation (mobileTabOrder). It only
// reorders what the layout already contains, so every consumer of `tabs` —
// rendering, swipe navigation, focus-after-close — follows one order without
// the layout itself ever being rewritten.
export function mobileWorkspaceProjection(
  layout:PaneLayout,
  focusedViewId:string|null,
  activeTerminalId:string|null,
  savedOrder?:readonly string[],
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
  const tabs=orderMobileTabs(layoutTabs,savedOrder)
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
