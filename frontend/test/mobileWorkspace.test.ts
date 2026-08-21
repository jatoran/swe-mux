import assert from 'node:assert/strict'
import test from 'node:test'
import { parseLayout } from '../src/layout.ts'
import { adjacentMobileTab, mobileWorkspaceProjection } from '../src/mobileWorkspace.ts'
import { joinSessions } from '../src/sessionJoin.ts'

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

test('rail order is the layout, with no device-local permutation left to apply',()=>{
  // The mobile `Move tab` row and the mobileTabOrder overlay behind it were removed
  // together: an orphaned overlay would have kept permuting a phone that had already
  // saved one, unwritable and inescapable. The projection takes no order argument at
  // all now, so there is nowhere for a stale permutation to re-enter.
  assert.equal(mobileWorkspaceProjection.length,3)
  const projection=mobileWorkspaceProjection(layout,null,'term-b')
  assert.deepEqual(projection.tabs.map(tab=>tab.id),['term-a','note-a','term-b','preview-a','history:archive'])
  assert.equal(adjacentMobileTab(projection.tabs,'term-a')?.id,'note-a')
})

test('a daemon-started session that joined the layout reaches the rail without moving selection',()=>{
  // The rail is a projection, so the join needs no mobile-specific placement - but it does have
  // to be a real leaf. While these sessions floated outside the pane tree the phone had nowhere
  // to draw them at all, which is the mobile half of the same defect.
  const joined=joinSessions(layout,['daemon-1'],'term-b')
  const projection=mobileWorkspaceProjection(joined,'term-b','term-b')
  assert.deepEqual(projection.tabs.map(tab=>tab.id),['term-a','note-a','term-b','preview-a','history:archive','daemon-1'])
  assert.equal(projection.selected?.id,'term-b')
})
