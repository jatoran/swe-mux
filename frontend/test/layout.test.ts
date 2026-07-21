import assert from 'node:assert/strict'
import test from 'node:test'
import {
  activateContainingStack, defaultProjectLayout, emptyLayout, groupTerminalsInStack, leaves, moveLeafToSplit,
  moveLeafToStack, noteResourceId, openTab, paneNeighborIds, paneStack, paneStacks, parseLayout, parseNoteResourceId,
  placeCompanionLeaf, reconcilePreviews, removeLeaf, reorderStack, resourceLeaf, setSplitRatio,
  openAnchorId, stackHasFiles,
  spawnAnchorId, splitTerminal, splitView, swapPanes, swapTerminals, terminalIds, terminalLeaf, visibleTerminalIds,
} from '../src/layout.ts'

const noteLeaf=(id='sessions:one')=>resourceLeaf('note',noteResourceId('session-note',id))

test('a companion note splits beside its terminal instead of covering it',()=>{
  const layout=openTab(emptyLayout(),null,terminalLeaf('one'))
  const placed=placeCompanionLeaf(layout,'one',noteLeaf())
  assert.equal(placed.root?.type,'split')
  // The terminal keeps its own pane and stays visible next to the note.
  assert.deepEqual(visibleTerminalIds(placed),['one'])
  assert.equal(paneStacks(placed).length,2)
  const ratio=placed.root?.type==='split'?placed.root.ratio:0
  assert.ok(ratio>.5&&ratio<.9,`terminal should keep the larger share, got ${ratio}`)
})

test('a companion note reuses an existing non-terminal pane',()=>{
  let layout=openTab(emptyLayout(),null,terminalLeaf('one'))
  layout=splitView(layout,'one',resourceLeaf('note','note:main'),'horizontal')
  const placed=placeCompanionLeaf(layout,'one',noteLeaf())
  assert.equal(paneStacks(placed).length,2)
  const resourcePane=paneStacks(placed).find(pane=>pane.children.some(child=>child.id==='note:main'))
  assert.ok(resourcePane?.children.some(child=>child.id===noteLeaf().id))
  assert.equal(resourcePane?.active_child_id,noteLeaf().id)
})

test('a companion note never reuses a Files pane, splitting beside the anchor instead',()=>{
  let layout=openTab(emptyLayout(),null,terminalLeaf('one'))
  layout=splitView(layout,'one',resourceLeaf('note',noteResourceId('files','project-a')),'horizontal')
  const placed=placeCompanionLeaf(layout,'one',noteLeaf())
  // The Files pane is not a valid companion host, so a third pane is created for the note.
  assert.equal(paneStacks(placed).length,3)
  const filesPane=paneStacks(placed).find(pane=>stackHasFiles(pane))!
  assert.ok(!filesPane.children.some(child=>child.id===noteLeaf().id))
})

test('a companion note that is already open is focused, never duplicated',()=>{
  let layout=openTab(emptyLayout(),null,terminalLeaf('one'))
  layout=placeCompanionLeaf(layout,'one',noteLeaf())
  layout=openTab(layout,'one',terminalLeaf('two'))
  const again=placeCompanionLeaf(layout,'one',noteLeaf())
  assert.equal(leaves(again).filter(leaf=>leaf.id===noteLeaf().id).length,1)
  const pane=paneStacks(again).find(item=>item.children.some(child=>child.id===noteLeaf().id))
  assert.equal(pane?.active_child_id,noteLeaf().id)
})

test('a companion note never splits a terminal that shares a pane with peers',()=>{
  let layout=openTab(emptyLayout(),null,terminalLeaf('one'))
  layout=splitTerminal(layout,'one','two','horizontal')
  const placed=placeCompanionLeaf(layout,'one',noteLeaf())
  // 'two' already owns a separate pane, so the note lands there rather than
  // splitting the workspace a third time.
  assert.equal(paneStacks(placed).length,2)
  const host=paneStacks(placed).find(pane=>pane.children.some(child=>child.id===noteLeaf().id))
  assert.ok(host?.children.some(child=>child.id==='two'))
})

test('v6 keeps every region as a tab pane inside an arbitrary split tree',()=>{
  let layout=openTab(emptyLayout(),null,terminalLeaf('one'))
  layout=splitTerminal(layout,'one','two','horizontal')
  layout=splitTerminal(layout,'two','three','vertical')
  const roundTrip=parseLayout(JSON.parse(JSON.stringify(layout)))
  assert.equal(roundTrip.version,6)
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
  assert.equal(migrated.version,6)
  assert.deepEqual(terminalIds(migrated),['one','two','three'])
  assert.equal(paneStacks(migrated).length,3)
})

test('visible v5 resources migrate into a real adjacent pane',()=>{
  const migrated=parseLayout({
    version:5,
    root:{type:'stack',id:'term-pane',active_child_id:'term-a',children:[{type:'leaf',kind:'terminal',id:'term-a'},{type:'leaf',kind:'preview',id:'preview-a'}]},
    note_workspace:{open_ids:['note:main','files:main'],active_id:'files:main',size:.44,visible:true,mode:'popout'},
  })
  assert.equal(migrated.version,6)
  assert.equal(migrated.root?.type,'split')
  assert.deepEqual(leaves(migrated,'note').map(leaf=>leaf.id),['note:main','files:main'])
  assert.equal(paneStacks(migrated).at(-1)?.active_child_id,'files:main')
  assert.equal(migrated.root?.type==='split'?migrated.root.ratio:0,.56)
})

test('a seeded Project opens Files narrow beside its Project note',()=>{
  const layout=defaultProjectLayout('project-a')
  assert.equal(layout.root?.type,'split')
  const split=layout.root?.type==='split'?layout.root:null
  assert.equal(split?.direction,'horizontal')
  assert.ok(split&&split.ratio<.3,`Files must stay narrow, got ${split?.ratio}`)
  const [filesPane,notePane]=paneStacks(layout)
  assert.deepEqual(filesPane.children.map(child=>child.id),[noteResourceId('files','project-a')])
  assert.deepEqual(notePane.children.map(child=>child.id),[noteResourceId('note','project-a')])
  // Nothing is spawned: both seeded leaves are viewports, so no terminal exists yet.
  assert.deepEqual(terminalIds(layout),[])
})

test('the first terminal joins the seeded note pane rather than the Files column',()=>{
  const layout=defaultProjectLayout('project-a')
  const opened=openTab(layout,noteResourceId('note','project-a'),terminalLeaf('term-a'))
  assert.equal(paneStacks(opened).length,2)
  const notePane=paneStacks(opened).at(-1)
  assert.deepEqual(notePane?.children.map(child=>child.id),[noteResourceId('note','project-a'),'term-a'])
  assert.equal(notePane?.active_child_id,'term-a')
  assert.deepEqual(visibleTerminalIds(opened),['term-a'])
})

test('openAnchorId and openTab never default a new leaf into a Files pane',()=>{
  const seeded=defaultProjectLayout('project-a')
  const filesId=noteResourceId('files','project-a'),noteId=noteResourceId('note','project-a')
  // Files is first in tree order; an unanchored open and a Files-focused open both avoid it.
  assert.equal(openAnchorId(seeded,null),noteId)
  assert.equal(openAnchorId(seeded,filesId),noteId)
  assert.equal(openAnchorId(seeded,noteId),noteId)
  const opened=openTab(seeded,null,terminalLeaf('term-a'))
  const filesPane=paneStacks(opened).find(pane=>pane.children.some(child=>child.id===filesId))!
  assert.ok(!filesPane.children.some(child=>child.id==='term-a'),'terminal must not join the Files pane')
  assert.deepEqual(visibleTerminalIds(opened),['term-a'])
  // When Files is the only pane, it is the last-resort anchor (nothing else exists).
  const onlyFiles={version:6 as const,root:paneStack([resourceLeaf('note',filesId)])}
  assert.equal(openAnchorId(onlyFiles,null),filesId)
  assert.ok(stackHasFiles(paneStacks(onlyFiles)[0])&&!stackHasFiles(paneStacks(seeded).find(p=>p.children.some(c=>c.id===noteId))!))
})

test('a background-project spawn anchors outside the Files column',()=>{
  const seeded=defaultProjectLayout('project-a')
  // No focused view exists for a Project the browser is not viewing, and Files is first in
  // tree order, so the anchor must skip it rather than bury a terminal in a narrow pane.
  assert.equal(spawnAnchorId(seeded),noteResourceId('note','project-a'))
  const withTerminal=openTab(seeded,noteResourceId('note','project-a'),terminalLeaf('term-a'))
  assert.equal(spawnAnchorId(withTerminal),'term-a')
  assert.equal(spawnAnchorId(emptyLayout()),null)
  const filesOnly=openTab(emptyLayout(),null,resourceLeaf('note',noteResourceId('files','project-a')))
  assert.equal(spawnAnchorId(filesOnly),noteResourceId('files','project-a'))
})

test('hidden v5 resource workspace migrates as closed views',()=>{
  const migrated=parseLayout({version:5,root:null,note_workspace:{open_ids:['note:main'],active_id:'note:main',size:.4,visible:false,mode:'dock'}})
  assert.equal(migrated.root,null)
  assert.deepEqual(leaves(migrated,'note'),[])
})

test('terminals, previews, notes, Files, file editors, and History share one pane',()=>{
  const note=noteResourceId('note','project-a'),sessionNote=noteResourceId('session-note','terminal-a'),files=noteResourceId('files','project-a'),file=noteResourceId('file','src/app.ts')
  let layout=openTab(emptyLayout(),null,terminalLeaf('term-a'))
  layout=openTab(layout,'term-a',resourceLeaf('preview','preview-a'))
  layout=openTab(layout,'preview-a',resourceLeaf('note',note))
  layout=openTab(layout,note,resourceLeaf('note',sessionNote))
  layout=openTab(layout,sessionNote,resourceLeaf('note',files))
  layout=openTab(layout,files,resourceLeaf('note',file))
  layout=openTab(layout,file,resourceLeaf('history','history:archive'))
  assert.equal(paneStacks(layout).length,1)
  assert.deepEqual(leaves(layout).map(leaf=>leaf.kind),['terminal','preview','note','note','note','note','history'])
  assert.deepEqual(parseNoteResourceId(sessionNote),{kind:'session-note',id:'terminal-a'})
  assert.deepEqual(parseNoteResourceId(file),{kind:'file',id:'src/app.ts'})
})

test('view ids remain globally unique across mixed leaf kinds',()=>{
  const layout=parseLayout({version:6,root:{type:'stack',id:'pane',active_child_id:'same',children:[
    {type:'leaf',kind:'terminal',id:'same'},
    {type:'leaf',kind:'preview',id:'same'},
  ]}})
  assert.deepEqual(leaves(layout),[{type:'leaf',kind:'terminal',id:'same'}])
})

test('mixed views move between panes, reorder, and create edge splits',()=>{
  const resource=resourceLeaf('note',noteResourceId('files','project-a'))
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
