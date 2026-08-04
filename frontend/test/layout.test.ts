import assert from 'node:assert/strict'
import test from 'node:test'
import {
  activateContainingStack, emptyLayout, groupTerminalsInStack, leaves, moveLeafToSplit,
  moveLeafToStack, noteResourceId, openTab, paneNeighborIds, paneStacks, parseLayout, parseNoteResourceId,
  reconcilePreviews, removeLeaf, reorderStack, resourceLeaf, setSplitRatio,
  openAnchorId,
  spawnAnchorId, splitTerminal, splitView, swapPanes, swapTerminals, terminalIds, terminalLeaf, visibleTerminalIds,
  worktreeFileResourceId,
} from '../src/layout.ts'

const noteLeaf=(id='sessions:one')=>resourceLeaf('note',noteResourceId('session-note',id))

test('a session note joins its anchor pane instead of splitting the workspace',()=>{
  // Opening a note is not a layout command. It used to split a pane off so the note sat
  // beside its terminal, which spent workspace geometry on a guess; splitting is now only
  // ever explicit (the tab menu, a drag onto a pane edge).
  const layout=openTab(emptyLayout(),null,terminalLeaf('one'))
  const placed=openTab(layout,'one',noteLeaf())
  assert.equal(placed.root?.type,'stack')
  assert.equal(paneStacks(placed).length,1)
  assert.deepEqual(paneStacks(placed)[0].children.map(child=>child.id),['one',noteLeaf().id])
  assert.equal(paneStacks(placed)[0].active_child_id,noteLeaf().id)
})

test('a session note opens in the pane its anchor is in, not the first one',()=>{
  let layout=openTab(emptyLayout(),null,terminalLeaf('one'))
  layout=splitTerminal(layout,'one','two','horizontal')
  const placed=openTab(layout,'two',noteLeaf())
  assert.equal(paneStacks(placed).length,2)
  const host=paneStacks(placed).find(pane=>pane.children.some(child=>child.id===noteLeaf().id))
  assert.ok(host?.children.some(child=>child.id==='two'),'the note follows its anchor')
  assert.equal(host?.active_child_id,noteLeaf().id)
})

test('a persisted Files leaf is pruned on read rather than rendered as a tab',()=>{
  // Files moved to the utility drawer, so `files:` is no longer a renderable resource.
  // A layout persisted before the move must lose it without losing anything else.
  const pruned=parseLayout({
    version:6,
    root:{type:'split',id:'split-a',direction:'horizontal',ratio:.22,
      first:{type:'stack',id:'files-pane',active_child_id:'files:project-a',children:[{type:'leaf',kind:'note',id:'files:project-a'}]},
      second:{type:'stack',id:'note-pane',active_child_id:'note:project-a',children:[{type:'leaf',kind:'note',id:'note:project-a'},{type:'leaf',kind:'terminal',id:'term-a'}]}},
  })
  assert.equal(pruned.version,7)
  // The pane that held only Files is gone, and its split collapsed into the survivor.
  assert.equal(pruned.root?.type,'stack')
  assert.deepEqual(leaves(pruned).map(leaf=>leaf.id),['note:project-a','term-a'])
  // A Files tab that shared a pane leaves that pane standing.
  const shared=parseLayout({
    version:6,
    root:{type:'stack',id:'pane-a',active_child_id:'files:project-a',children:[{type:'leaf',kind:'terminal',id:'term-a'},{type:'leaf',kind:'note',id:'files:project-a'}]},
  })
  assert.deepEqual(leaves(shared).map(leaf=>leaf.id),['term-a'])
  assert.equal(paneStacks(shared)[0].active_child_id,'term-a')
  // A workspace whose only leaf was Files becomes the empty stage.
  assert.equal(parseLayout({version:6,root:{type:'stack',id:'pane-a',active_child_id:'files:p',children:[{type:'leaf',kind:'note',id:'files:p'}]}}).root,null)
  // `files:` is no longer a resource identity at all.
  assert.equal(parseNoteResourceId('files:project-a'),null)
})

test('a session note already open is focused where it is, never duplicated or moved',()=>{
  // Reopening from a different pane must not tear the note out of the pane the user put
  // it in; it activates in place.
  let layout=openTab(emptyLayout(),null,terminalLeaf('one'))
  layout=splitView(layout,'one',noteLeaf(),'horizontal')
  layout=openTab(layout,'one',terminalLeaf('two'))
  const notePane=paneStacks(layout).find(pane=>pane.children.some(child=>child.id===noteLeaf().id))!
  const again=openTab(layout,'two',noteLeaf())
  assert.equal(leaves(again).filter(leaf=>leaf.id===noteLeaf().id).length,1)
  const host=paneStacks(again).find(pane=>pane.children.some(child=>child.id===noteLeaf().id))
  assert.equal(host?.id,notePane.id)
  assert.equal(host?.active_child_id,noteLeaf().id)
})

test('v6 keeps every region as a tab pane inside an arbitrary split tree',()=>{
  let layout=openTab(emptyLayout(),null,terminalLeaf('one'))
  layout=splitTerminal(layout,'one','two','horizontal')
  layout=splitTerminal(layout,'two','three','vertical')
  const roundTrip=parseLayout(JSON.parse(JSON.stringify(layout)))
  assert.equal(roundTrip.version,7)
  assert.deepEqual(terminalIds(roundTrip),['one','two','three'])
  assert.equal(roundTrip.root?.type,'split')
  assert.equal(paneStacks(roundTrip).every(pane=>pane.children.length>0),true)
})

test('ratio, swap, detach, and activation preserve view identities',()=>{
  let layout=splitTerminal(parseLayout({version:3,root:{type:'leaf',kind:'terminal',id:'one'}}),'one','two','horizontal')
  layout=setSplitRatio(layout,'',.72)
  assert.equal(layout.root?.type==='split'?layout.root.ratio:0,.72)
  assert.deepEqual(terminalIds(swapTerminals(layout,'one','two')),['two','one'])
  const [firstPane,secondPane]=paneStacks(layout)
  assert.deepEqual(terminalIds(swapPanes(layout,firstPane.id,secondPane.id)),['two','one'])
  assert.deepEqual(terminalIds(removeLeaf(layout,'terminal','one')),['two'])
  assert.deepEqual(visibleTerminalIds(activateContainingStack(layout,'two')),['one','two'])
})

test('legacy membership migrates to v6 panes',()=>{
  const migrated=parseLayout({version:1,panes:['one','two','three']})
  assert.equal(migrated.version,7)
  assert.deepEqual(terminalIds(migrated),['one','two','three'])
  assert.equal(paneStacks(migrated).length,3)
})

test('visible v5 resources migrate into a real adjacent pane',()=>{
  const migrated=parseLayout({
    version:5,
    root:{type:'stack',id:'term-pane',active_child_id:'term-a',children:[{type:'leaf',kind:'terminal',id:'term-a'},{type:'leaf',kind:'preview',id:'preview-a'}]},
    // `files:main` is dropped on the way through: the legacy dock could hold a Files view,
    // which is now the drawer's Files tab rather than any kind of leaf.
    note_workspace:{open_ids:['note:main','files:main'],active_id:'note:main',size:.44,visible:true,mode:'popout'},
  })
  assert.equal(migrated.version,7)
  assert.equal(migrated.root?.type,'split')
  assert.deepEqual(leaves(migrated,'note').map(leaf=>leaf.id),['note:main'])
  assert.equal(paneStacks(migrated)[paneStacks(migrated).length-1]?.active_child_id,'note:main')
  assert.equal(migrated.root?.type==='split'?migrated.root.ratio:0,.56)
})

test('openAnchorId honors a live preference and falls back to the first pane',()=>{
  const noteId=noteResourceId('note','project-a')
  let layout=openTab(emptyLayout(),null,resourceLeaf('note',noteId))
  layout=splitView(layout,noteId,terminalLeaf('term-a'),'horizontal')
  assert.equal(openAnchorId(layout,'term-a'),'term-a')
  assert.equal(openAnchorId(layout,noteId),noteId)
  // A preference that is not in this layout (a stale focus, another Project's view) yields
  // the first pane's active tab rather than nothing.
  assert.equal(openAnchorId(layout,'gone'),noteId)
  assert.equal(openAnchorId(layout,null),noteId)
  assert.equal(openAnchorId(emptyLayout(),null),null)
})

test('a background-project spawn prefers an existing terminal',()=>{
  // No focused view exists for a Project the browser is not viewing, so the anchor comes
  // from the layout itself: a terminal first, then whatever leaf is there.
  const noteOnly=openTab(emptyLayout(),null,resourceLeaf('note',noteResourceId('note','project-a')))
  assert.equal(spawnAnchorId(noteOnly),noteResourceId('note','project-a'))
  const withTerminal=openTab(noteOnly,noteResourceId('note','project-a'),terminalLeaf('term-a'))
  assert.equal(spawnAnchorId(withTerminal),'term-a')
  assert.equal(spawnAnchorId(emptyLayout()),null)
})

test('hidden v5 resource workspace migrates as closed views',()=>{
  const migrated=parseLayout({version:5,root:null,note_workspace:{open_ids:['note:main'],active_id:'note:main',size:.4,visible:false,mode:'dock'}})
  assert.equal(migrated.root,null)
  assert.deepEqual(leaves(migrated,'note'),[])
})

test('terminals, previews, notes, file editors, and History share one pane',()=>{
  const note=noteResourceId('note','project-a'),sessionNote=noteResourceId('session-note','terminal-a'),file=noteResourceId('file','src/app.ts')
  let layout=openTab(emptyLayout(),null,terminalLeaf('term-a'))
  layout=openTab(layout,'term-a',resourceLeaf('preview','preview-a'))
  layout=openTab(layout,'preview-a',resourceLeaf('note',note))
  layout=openTab(layout,note,resourceLeaf('note',sessionNote))
  layout=openTab(layout,sessionNote,resourceLeaf('note',file))
  layout=openTab(layout,file,resourceLeaf('history','history:archive'))
  assert.equal(paneStacks(layout).length,1)
  assert.deepEqual(leaves(layout).map(leaf=>leaf.kind),['terminal','preview','note','note','note','history'])
  assert.deepEqual(parseNoteResourceId(sessionNote),{kind:'session-note',id:'terminal-a'})
  assert.deepEqual(parseNoteResourceId(file),{kind:'file',id:'src/app.ts'})
})

test('canonical and worktree files have distinct durable unambiguous identities',()=>{
  const canonical=noteResourceId('file','src/example.ts')
  const first=worktreeFileResourceId('D:\\worktrees\\one','src/example.ts')
  const second=worktreeFileResourceId('D:\\worktrees\\two:colon','src/example.ts')
  assert.deepEqual(parseNoteResourceId(canonical),{kind:'file',id:'src/example.ts'})
  assert.deepEqual(parseNoteResourceId(first),{kind:'worktree-file',worktree:'D:\\worktrees\\one',id:'src/example.ts'})
  assert.deepEqual(parseNoteResourceId(second),{kind:'worktree-file',worktree:'D:\\worktrees\\two:colon',id:'src/example.ts'})
  assert.notEqual(first,second)
  assert.equal(parseNoteResourceId('worktree-file::src%2Fexample.ts'),null)
  assert.equal(parseNoteResourceId('worktree-file:%E0%A4%A:src'),null)
})

test('view ids remain globally unique across mixed leaf kinds',()=>{
  const layout=parseLayout({version:6,root:{type:'stack',id:'pane',active_child_id:'same',children:[
    {type:'leaf',kind:'terminal',id:'same'},
    {type:'leaf',kind:'preview',id:'same'},
  ]}})
  assert.deepEqual(leaves(layout),[{type:'leaf',kind:'terminal',id:'same'}])
})

test('mixed views move between panes, reorder, and create edge splits',()=>{
  const resource=resourceLeaf('note',noteResourceId('file','src/app.ts'))
  let layout=splitTerminal(openTab(emptyLayout(),null,terminalLeaf('one')),'one','two','horizontal')
  const [left,right]=paneStacks(layout)
  layout=openTab(layout,'one',resource)
  layout=moveLeafToStack(layout,'note',resource.id,right.id)
  assert.deepEqual(paneStacks(layout).find(pane=>pane.id===right.id)?.children.map(child=>child.id),['two',resource.id])
  layout=reorderStack(layout,right.id,[resource.id,'two'])
  assert.deepEqual(paneStacks(layout).find(pane=>pane.id===right.id)?.children.map(child=>child.id),[resource.id,'two'])
  layout=moveLeafToSplit(layout,'note',resource.id,left.id,'vertical','before')
  assert.equal(paneStacks(layout).length,3)
  assert.deepEqual(leaves(layout,'note').map(leaf=>leaf.id),[resource.id])
})

test('pane neighbors expose only directions supported by the split tree',()=>{
  let layout=splitTerminal(openTab(emptyLayout(),null,terminalLeaf('left')),'left','right','horizontal')
  layout=splitTerminal(layout,'right','bottom','vertical')
  assert.deepEqual(Object.keys(paneNeighborIds(layout,'left')).sort(),['right'])
  assert.deepEqual(Object.keys(paneNeighborIds(layout,'right')).sort(),['down','left'])
  assert.deepEqual(Object.keys(paneNeighborIds(layout,'bottom')).sort(),['left','up'])
})

test('grouping sessions creates one pane and removing one leaves the other tab',()=>{
  let layout=splitTerminal(openTab(emptyLayout(),null,terminalLeaf('one')),'one','two','horizontal')
  layout=groupTerminalsInStack(layout,'one','two')
  assert.equal(paneStacks(layout).length,1)
  assert.deepEqual(terminalIds(layout),['one','two'])
  assert.deepEqual(terminalIds(removeLeaf(layout,'terminal','one')),['two'])
})

test('preview reconciliation removes only unavailable preview views',()=>{
  let layout=openTab(emptyLayout(),null,terminalLeaf('a'))
  layout=openTab(layout,'a',resourceLeaf('preview','preview-1'))
  assert.deepEqual(leaves(reconcilePreviews(layout,new Set(['preview-1'])),'preview').map(leaf=>leaf.id),['preview-1'])
  const dropped=reconcilePreviews(layout,new Set())
  assert.deepEqual(leaves(dropped,'preview'),[])
  assert.deepEqual(terminalIds(dropped),['a'])
})

test('splitView moves an existing view instead of duplicating it',()=>{
  let layout=openTab(emptyLayout(),null,terminalLeaf('a'))
  layout=openTab(layout,'a',terminalLeaf('b'))
  layout=splitView(layout,'a',terminalLeaf('b'),'horizontal')
  assert.deepEqual(terminalIds(layout),['a','b'])
  assert.equal(paneStacks(layout).length,2)
})
