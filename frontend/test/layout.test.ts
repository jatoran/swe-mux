import assert from 'node:assert/strict'
import test from 'node:test'
import {
  attachLeaf, emptyLayout, leaves, noteResourceId, parseLayout, parseNoteResourceId,
  activateContainingStack, activateNoteWorkspace, groupTerminalsInStack, hideNoteWorkspace, removeLeaf, replaceTerminal, resourceLeaf, setNoteWorkspaceMode, setNoteWorkspaceSize, setSplitRatio, showNoteWorkspace, splitTerminal,
  reconcilePreviews, resolveLayout, swapTerminals, terminalIds, visibleTerminalIds,
} from '../src/layout.ts'

test('arbitrary split trees round-trip and preserve terminal membership', () => {
  let layout = splitTerminal(emptyLayout(), null, 'one', 'horizontal')
  layout = splitTerminal(layout, 'one', 'two', 'horizontal')
  layout = splitTerminal(layout, 'two', 'three', 'vertical')
  assert.deepEqual(terminalIds(parseLayout(JSON.parse(JSON.stringify(layout)))), ['one', 'two', 'three'])
  assert.equal(layout.root?.type, 'split')
})

test('ratio, swap, detach, and replacement do not lose displaced live identities', () => {
  let layout = splitTerminal(parseLayout({ version: 3, root: { type: 'leaf', kind: 'terminal', id: 'one' } }), 'one', 'two', 'horizontal')
  layout = setSplitRatio(layout, '', .72)
  assert.equal(layout.root?.type === 'split' ? layout.root.ratio : 0, .72)
  assert.deepEqual(terminalIds(swapTerminals(layout, 'one', 'two')), ['two', 'one'])
  assert.deepEqual(terminalIds(removeLeaf(layout, 'terminal', 'one')), ['two'])
  assert.deepEqual(terminalIds(replaceTerminal(layout, 'one', 'three')), ['three', 'two'])
})

test('legacy membership migrates to the recursive v5 contract', () => {
  const migrated = parseLayout({ version: 1, panes: ['one', 'two', 'three'] })
  assert.equal(migrated.version, 5)
  assert.deepEqual(terminalIds(migrated), ['one', 'two', 'three'])
})

test('an empty reconciled layout overrides a stale persisted tree', () => {
  const persisted = { version: 3, root: { type: 'leaf', kind: 'terminal', id: 'ended' } }
  const resolved = resolveLayout(emptyLayout(), persisted)
  assert.equal(resolved.root, null)
  assert.deepEqual(terminalIds(resolved), [])
})

test('opening a terminal preserves a resource-only layout and makes the terminal visible', () => {
  const noteOnly = parseLayout({version:3,root:{type:'leaf',kind:'note',id:'projects:scope'}})
  const next = replaceTerminal(noteOnly, null, 'shell-a')
  assert.deepEqual(terminalIds(next), ['shell-a'])
  assert.equal(leaves(next, 'note')[0]?.id, 'projects:scope')
})

test('notes attach to the space workspace without changing the terminal tree', () => {
  const base = parseLayout({ version: 3, root: { type: 'leaf', kind: 'terminal', id: 'term-a' } })
  const resourceId = noteResourceId('sessions', 'session/a')
  const docked = attachLeaf(base, 'term-a', resourceLeaf('note', resourceId), 'horizontal', .62)
  assert.deepEqual(terminalIds(docked), ['term-a'])
  assert.deepEqual(leaves(docked, 'note').map(leaf => leaf.id), [resourceId])
  assert.deepEqual(parseNoteResourceId(resourceId), { kind: 'sessions', id: 'session/a' })
  assert.equal(docked.root?.type, 'leaf')
  assert.equal(docked.note_workspace.active_id, resourceId)
  assert.equal(setNoteWorkspaceSize(docked, .55).note_workspace.size, .55)
  assert.deepEqual(leaves(removeLeaf(docked, 'note', resourceId), 'note'), [])
})

test('embedded v3 notes migrate out of terminal splits into the v5 workspace', () => {
  const migrated=parseLayout({version:3,root:{type:'split',direction:'horizontal',ratio:.62,first:{type:'leaf',kind:'terminal',id:'term-a'},second:{type:'leaf',kind:'note',id:'projects:scope'}}})
  assert.deepEqual(terminalIds(migrated),['term-a'])
  assert.equal(migrated.root?.type,'leaf')
  assert.deepEqual(migrated.note_workspace.open_ids,['projects:scope'])
  assert.equal(activateNoteWorkspace(migrated,'projects:scope').note_workspace.active_id,'projects:scope')
})

test('v4 note docks migrate and the whole workspace changes presentation',()=>{
  const migrated=parseLayout({version:4,root:null,note_dock:{open_ids:['spaces:main','projects:scope'],active_id:'spaces:main',size:.44}})
  assert.deepEqual(migrated.note_workspace,{open_ids:['spaces:main','projects:scope'],active_id:'spaces:main',size:.44,visible:true,mode:'dock'})
  const popped=setNoteWorkspaceMode(migrated,'popout')
  assert.equal(popped.note_workspace.mode,'popout')
  assert.deepEqual(popped.note_workspace.open_ids,migrated.note_workspace.open_ids)
  assert.equal(hideNoteWorkspace(popped).note_workspace.visible,false)
  assert.deepEqual(showNoteWorkspace(hideNoteWorkspace(popped),'projects:scope','dock').note_workspace,{open_ids:['spaces:main','projects:scope'],active_id:'projects:scope',size:.44,visible:true,mode:'dock'})
})

test('stacks keep sessions atomic while switching the visible child',()=>{
  let layout=splitTerminal(emptyLayout(),null,'one','horizontal')
  layout=splitTerminal(layout,'one','two','horizontal')
  layout=groupTerminalsInStack(layout,'one','two')
  assert.deepEqual(terminalIds(layout),['one','two'])
  assert.equal(layout.root?.type,'stack')
  layout=activateContainingStack(layout,'one')
  assert.equal(layout.root?.type==='stack'?layout.root.active_child_id:'','one')
  assert.deepEqual(visibleTerminalIds(layout),['one'])
  assert.deepEqual(terminalIds(removeLeaf(layout,'terminal','one')),['two'])
  assert.deepEqual(visibleTerminalIds(removeLeaf(layout,'terminal','one')),['two'])
})

test('a stack keeps a preview tab beside its session',()=>{
  const layout=parseLayout({
    version:5,
    root:{
      type:'stack',id:'tabs-a',active_child_id:'preview-1',
      children:[
        {type:'leaf',kind:'terminal',id:'a'},
        {type:'leaf',kind:'preview',id:'preview-1'},
      ],
    },
  })
  // The whole layout would be discarded if preview tabs failed validation.
  assert.equal(layout.root?.type,'stack')
  assert.deepEqual(terminalIds(layout),['a'])
  assert.deepEqual(leaves(layout,'preview').map(leaf=>leaf.id),['preview-1'])
})

test('notes are still rejected from tab regions',()=>{
  const layout=parseLayout({
    version:5,
    root:{
      type:'stack',id:'bad',active_child_id:'n',
      children:[{type:'leaf',kind:'note',id:'n'}],
    },
  })
  assert.equal(layout.root,null)
})

test('removing a session from a stack leaves its preview tab intact',()=>{
  const layout=parseLayout({
    version:5,
    root:{
      type:'stack',id:'tabs-a',active_child_id:'a',
      children:[
        {type:'leaf',kind:'terminal',id:'a'},
        {type:'leaf',kind:'preview',id:'preview-1'},
      ],
    },
  })
  const without=removeLeaf(layout,'terminal','a')
  assert.deepEqual(without.root,{type:'leaf',kind:'preview',id:'preview-1'})
})

test('a preview the daemon still lists keeps its tab',()=>{
  const layout=parseLayout({
    version:5,
    root:{
      type:'stack',id:'tabs-a',active_child_id:'preview-1',
      children:[
        {type:'leaf',kind:'terminal',id:'a'},
        {type:'leaf',kind:'preview',id:'preview-1'},
      ],
    },
  })
  const kept=reconcilePreviews(layout,new Set(['preview-1']))
  assert.equal(kept.root?.type,'stack')
  assert.deepEqual(leaves(kept,'preview').map(leaf=>leaf.id),['preview-1'])
})

test('a preview whose server stopped loses its tab and collapses the region',()=>{
  const layout=parseLayout({
    version:5,
    root:{
      type:'stack',id:'tabs-a',active_child_id:'preview-1',
      children:[
        {type:'leaf',kind:'terminal',id:'a'},
        {type:'leaf',kind:'preview',id:'preview-1'},
      ],
    },
  })
  const dropped=reconcilePreviews(layout,new Set())
  assert.deepEqual(dropped.root,{type:'leaf',kind:'terminal',id:'a'})
  assert.deepEqual(leaves(dropped,'preview'),[])
})
