import assert from 'node:assert/strict'
import test from 'node:test'
import { parseLayout } from '../src/layout.ts'
import { adjacentMobileTab, mobileWorkspaceProjection } from '../src/mobileWorkspace.ts'

const layout=parseLayout({
  version:6,
  root:{
    type:'split',id:'split-root',direction:'horizontal',ratio:.5,
    first:{type:'stack',id:'pane-left',active_child_id:'note-a',children:[
      {type:'leaf',kind:'terminal',id:'term-a'},
      {type:'leaf',kind:'note',id:'note-a'},
    ]},
    second:{type:'stack',id:'pane-right',active_child_id:'preview-a',children:[
      {type:'leaf',kind:'terminal',id:'term-b'},
      {type:'leaf',kind:'preview',id:'preview-a'},
      {type:'leaf',kind:'history',id:'history:archive'},
    ]},
  },
})

test('mobile workspace flattens pane tabs without changing visual pane order',()=>{
  const projection=mobileWorkspaceProjection(layout,'term-b','term-a')
  assert.deepEqual(projection.tabs.map(tab=>tab.id),['term-a','note-a','term-b','preview-a','history:archive'])
  assert.equal(projection.selected?.id,'term-b')
})

test('mobile workspace falls back to the first pane active tab and chooses an adjacent close target',()=>{
  const projection=mobileWorkspaceProjection(layout,null,null)
  assert.equal(projection.selected?.id,'note-a')
  assert.equal(adjacentMobileTab(projection.tabs,'note-a')?.id,'term-b')
  assert.equal(adjacentMobileTab(projection.tabs,'preview-a')?.id,'history:archive')
})
