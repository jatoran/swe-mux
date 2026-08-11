import assert from 'node:assert/strict'
import test from 'node:test'
import {
  caretResolverForBackend,
  caretSteerCommand,
  resolveAnchoredCaretTarget,
  resolveCodexCaretTarget,
  resolveOmpCaretTarget,
  resolvePiCaretTarget,
  terminalCaretAtPoint,
  terminalTapAction,
  type TerminalCaretCell,
  type TerminalCaretSnapshot,
} from '../src/terminalCaretPlacement.ts'

function blankCell(bgMode=0,bg=0):TerminalCaretCell {
  return {chars:'',code:0,width:1,bgMode,bg,dim:false}
}

function snapshot(cursorX=7,cursorY=4):TerminalCaretSnapshot {
  const cols=20
  const rows=8
  const lines=Array.from({length:rows},(_,row)=>({
    row,
    cells:Array.from({length:cols},()=>blankCell()),
  }))
  const fillComposer=(row:number)=>{
    lines[row].cells=Array.from({length:cols},()=>blankCell(1,17))
  }
  fillComposer(2)
  fillComposer(3)
  fillComposer(4)
  fillComposer(5)
  const write=(row:number,column:number,text:string,dim=false)=>{
    for(const char of text){
      lines[row].cells[column]={chars:char,code:char.codePointAt(0)??0,width:1,bgMode:1,bg:17,dim}
      column+=1
    }
  }
  write(3,0,'›')
  write(3,2,'alpha')
  write(4,2,'bravo')
  return {cols,rows,viewportY:0,baseY:0,cursorX,cursorY,lines}
}

test('terminal caret coordinates resolve to cursor boundaries',()=>{
  assert.deepEqual(
    terminalCaretAtPoint(56,25,{left:0,top:0,width:100,height:50},10,5,20),
    {column:6,row:22},
  )
  assert.deepEqual(
    terminalCaretAtPoint(-10,100,{left:0,top:0,width:100,height:50},10,5,20),
    {column:0,row:24},
  )
})

test('tap routing forwards on measured mouse tracking and steers the mouse-less composers',()=>{
  const base={still:true,primary:true,modified:false,readMode:false,hasSelection:false}
  assert.equal(terminalTapAction({...base,backend:'claude',pointerType:'touch',mouseTracking:true}),'forward-mouse')
  // Tracking is a runtime fact, so every harness that negotiates it is forwarded
  // without being named: opencode positions its own caret exactly as Claude does,
  // and so would a shell application that turned mouse reporting on.
  assert.equal(terminalTapAction({...base,backend:'opencode',pointerType:'touch',mouseTracking:true}),'forward-mouse')
  assert.equal(terminalTapAction({...base,backend:'shell',pointerType:'touch',mouseTracking:true}),'forward-mouse')
  // A real mouse press reaches xterm's own reporting; only a touch's synthesized
  // mouse event is intercepted by the pane and needs re-dispatching.
  assert.equal(terminalTapAction({...base,backend:'claude',pointerType:'mouse',mouseTracking:true}),'none')
  assert.equal(terminalTapAction({...base,backend:'opencode',pointerType:'mouse',mouseTracking:true}),'none')
  assert.equal(terminalTapAction({...base,backend:'codex',pointerType:'mouse',mouseTracking:false}),'steer-caret')
  assert.equal(terminalTapAction({...base,backend:'omp',pointerType:'mouse',mouseTracking:false}),'steer-caret')
  assert.equal(terminalTapAction({...base,backend:'omp',pointerType:'touch',mouseTracking:false}),'steer-caret')
  assert.equal(terminalTapAction({...base,backend:'pi',pointerType:'touch',mouseTracking:false}),'steer-caret')
  assert.equal(terminalTapAction({...base,backend:'pi',pointerType:'mouse',mouseTracking:false}),'steer-caret')
  // opencode is mouse-driven, so it must never reach the steering path even if a
  // pane reports no tracking mode yet - it has no composer contract to steer.
  assert.equal(terminalTapAction({...base,backend:'opencode',pointerType:'touch',mouseTracking:false}),'none')
  // A shell composer is the shell's own line editor; arrows there edit history.
  assert.equal(terminalTapAction({...base,backend:'shell',pointerType:'mouse',mouseTracking:false}),'none')
  assert.equal(terminalTapAction({...base,backend:'codex',pointerType:'touch',mouseTracking:false,readMode:true}),'none')
  assert.equal(terminalTapAction({...base,backend:'codex',pointerType:'touch',mouseTracking:false,hasSelection:true}),'none')
  assert.equal(caretResolverForBackend('codex'),resolveCodexCaretTarget)
  assert.equal(caretResolverForBackend('omp'),resolveOmpCaretTarget)
  assert.equal(caretResolverForBackend('pi'),resolvePiCaretTarget)
  assert.equal(caretResolverForBackend('claude'),null)
  assert.equal(caretResolverForBackend('opencode'),null)
  // The registry is a Map, so an inherited property name is not a backend.
  assert.equal(caretResolverForBackend('__proto__'),null)
  assert.equal(caretResolverForBackend('constructor'),null)
})

test('Codex targets resolve within the live composer and clamp after line text',()=>{
  const resolved=resolveCodexCaretTarget(snapshot(),{column:5,row:3})
  assert.deepEqual(resolved,{
    current:{column:7,row:4},
    target:{column:5,row:3},
    promptRow:3,
    textStart:2,
  })
  assert.deepEqual(
    resolveCodexCaretTarget(snapshot(),{column:18,row:4})?.target,
    {column:7,row:4},
  )
})

test('Codex target can move after the current row when visible draft text continues',()=>{
  const state=snapshot(4,3)
  assert.deepEqual(
    resolveCodexCaretTarget(state,{column:4,row:4}),
    {current:{column:4,row:3},target:{column:4,row:4},promptRow:3,textStart:2},
  )
})

test('anchored Codex targets follow the composer when a popup changes its screen row',()=>{
  const state=snapshot(7,3)
  for(let row=2;row<=4;row+=1)state.lines[row].cells=Array.from({length:20},()=>blankCell(1,17))
  const write=(row:number,column:number,text:string)=>{
    for(const char of text){
      state.lines[row].cells[column]={chars:char,code:char.codePointAt(0)??0,width:1,bgMode:1,bg:17,dim:false}
      column+=1
    }
  }
  write(2,0,'›')
  write(2,2,'alpha')
  write(3,2,'bravo')
  assert.deepEqual(
    resolveAnchoredCaretTarget(resolveCodexCaretTarget,state,{column:5,rowOffset:0})?.target,
    {column:5,row:2},
  )
})

test('Codex target refuses scrollback and rows outside the draft',()=>{
  const offTail=snapshot()
  offTail.viewportY=0
  offTail.baseY=2
  assert.equal(resolveCodexCaretTarget(offTail,{column:3,row:3}),null)
  assert.equal(resolveCodexCaretTarget(snapshot(),{column:3,row:6}),null)
})

test('Codex target resolves when palette detection leaves the composer unstyled',()=>{
  const state=snapshot()
  for(const line of state.lines){
    for(const cell of line.cells){cell.bgMode=0;cell.bg=0}
  }
  assert.deepEqual(
    resolveCodexCaretTarget(state,{column:3,row:3}),
    {current:{column:7,row:4},target:{column:3,row:3},promptRow:3,textStart:2},
  )
})

test('unstyled Codex target requires the blank composer frame around the cursor',()=>{
  const state=snapshot()
  for(const line of state.lines){
    for(const cell of line.cells){cell.bgMode=0;cell.bg=0}
  }
  state.lines[2].cells[0]={chars:'x',code:120,width:1,bgMode:0,bg:0,dim:false}
  assert.equal(resolveCodexCaretTarget(state,{column:3,row:3}),null)

  state.lines[2].cells[0]=blankCell()
  state.lines[5].cells[0]={chars:'x',code:120,width:1,bgMode:0,bg:0,dim:false}
  assert.equal(resolveCodexCaretTarget(state,{column:3,row:3}),null)
})

test('Codex Ultra live prefix resolves like the standard prefix',()=>{
  const state=snapshot()
  state.lines[3].cells[0]={chars:'»',code:'»'.codePointAt(0)??0,width:1,bgMode:1,bg:17,dim:false}
  assert.deepEqual(resolveCodexCaretTarget(state,{column:4,row:3})?.target,{column:4,row:3})
})

test('empty Codex placeholder text is not mistaken for editable draft content',()=>{
  const state=snapshot(2,3)
  for(let row=3;row<=4;row+=1){
    for(let column=2;column<20;column+=1)state.lines[row].cells[column]=blankCell(1,17)
  }
  const placeholder='Ask for follow-up changes'
  for(let column=0;column<Math.min(placeholder.length,18);column+=1){
    const char=placeholder[column]
    state.lines[3].cells[column+2]={chars:char,code:char.codePointAt(0)??0,width:1,bgMode:1,bg:17,dim:true}
  }
  assert.deepEqual(resolveCodexCaretTarget(state,{column:12,row:3})?.target,{column:2,row:3})
})

/**
 * The measured omp 17.2.10 composer (100x30 and 120x30, 2026-08-07): a rounded
 * box whose top border embeds the status line and reads `╭── π` at columns
 * 0-4, `│` interior draft rows, the final draft line fused into the `╰─ … ─╯`
 * bottom border row, and text starting at column 3 everywhere.
 */
function ompSnapshot(options:{interior?:string,bottom?:string,cursorX?:number,cursorY?:number,brand?:string}={}):TerminalCaretSnapshot {
  const cols=40
  const rows=10
  const interior=options.interior
  const bottom=options.bottom??'charlie'
  const lines=Array.from({length:rows},(_,row)=>({
    row,
    cells:Array.from({length:cols},()=>blankCell()),
  }))
  const write=(row:number,column:number,text:string,dim=false)=>{
    for(const char of text){
      lines[row].cells[column]={chars:char,code:char.codePointAt(0)??0,width:1,bgMode:0,bg:0,dim}
      column+=1
    }
  }
  write(3,0,'transcript line above the composer box')
  const topRow=interior===undefined?6:5
  write(topRow,0,`╭── ${options.brand??'π'} `)
  for(let column=6;column<cols-1;column+=1)write(topRow,column,'─')
  write(topRow,cols-1,'╮')
  if(interior!==undefined){
    write(6,0,'│')
    write(6,3,interior)
    write(6,cols-1,'│')
  }
  write(7,0,'╰─')
  write(7,3,bottom)
  write(7,cols-2,'─╯')
  const cursorX=options.cursorX??3+bottom.length
  const cursorY=options.cursorY??7
  return {cols,rows,viewportY:0,baseY:0,cursorX,cursorY,lines}
}

test('OMP targets resolve on the fused bottom row and clamp after the draft',()=>{
  const state=ompSnapshot()
  assert.deepEqual(
    resolveOmpCaretTarget(state,{column:5,row:7}),
    {current:{column:10,row:7},target:{column:5,row:7},promptRow:6,textStart:3},
  )
  assert.deepEqual(resolveOmpCaretTarget(state,{column:30,row:7})?.target,{column:10,row:7})
})

test('OMP targets reach interior draft rows of a wrapped draft',()=>{
  const state=ompSnapshot({interior:'alpha bravo'})
  assert.deepEqual(
    resolveOmpCaretTarget(state,{column:6,row:6}),
    {current:{column:10,row:7},target:{column:6,row:6},promptRow:5,textStart:3},
  )
})

test('OMP refuses the status border, the transcript, scrollback, and unbranded boxes',()=>{
  const state=ompSnapshot({interior:'alpha bravo'})
  assert.equal(resolveOmpCaretTarget(state,{column:5,row:5}),null)
  assert.equal(resolveOmpCaretTarget(state,{column:5,row:3}),null)
  assert.equal(resolveOmpCaretTarget(state,{column:5,row:8}),null)
  const offTail=ompSnapshot()
  offTail.baseY=2
  assert.equal(resolveOmpCaretTarget(offTail,{column:5,row:9}),null)
  // A picker's box carries a title where the composer carries its π brand, and
  // arrows sent into a picker move its selection - the refusal is the feature.
  const picker=ompSnapshot({interior:'alpha bravo',brand:'M'})
  assert.equal(resolveOmpCaretTarget(picker,{column:6,row:6}),null)
})

test('empty OMP placeholder text is not mistaken for editable draft content',()=>{
  const state=ompSnapshot({bottom:'',cursorX:3})
  const placeholder='Plan, search, build anything'
  for(let column=0;column<placeholder.length;column+=1){
    const char=placeholder[column]
    state.lines[7].cells[column+3]={chars:char,code:char.codePointAt(0)??0,width:1,bgMode:0,bg:0,dim:true}
  }
  assert.deepEqual(resolveOmpCaretTarget(state,{column:12,row:7})?.target,{column:3,row:7})
})

/**
 * The measured pi 0.84.1 composer (live panes at 100x30 and 64x24, 2026-08-10):
 * two rules of `─` spanning every column bracket the draft, text starts at
 * column 0 with no gutter or border, and the rows between the rules are exactly
 * the wrapped draft - an empty draft is one blank row. pi hides the hardware
 * cursor and paints its own reverse-video caret, which leaves a written blank
 * cell at the caret column, so `caretCell` reproduces that too.
 */
function piSnapshot(options:{draft?:string[],cursor?:{column:number,row:number},marker?:number,gap?:boolean}={}):TerminalCaretSnapshot {
  const cols=40
  const rows=14
  const draft=options.draft??['']
  const topRow=4
  const lines=Array.from({length:rows},(_,row)=>({
    row,
    cells:Array.from({length:cols},()=>blankCell()),
  }))
  const write=(row:number,column:number,text:string)=>{
    for(const char of text){
      lines[row].cells[column]={chars:char,code:char.codePointAt(0)??0,width:1,bgMode:0,bg:0,dim:false}
      column+=1
    }
  }
  write(1,0,'a transcript line above the composer')
  const rule='─'.repeat(cols)
  write(topRow,0,rule)
  // A picker lays a blank row directly under the top rule and its own content
  // below that; the composer never does.
  const body=options.gap?['',...draft]:draft
  body.forEach((line,index)=>write(topRow+1+index,0,line))
  const bottomRow=topRow+1+body.length
  write(bottomRow,0,rule)
  write(bottomRow+1,0,'~\\scratch\\repo (master)')
  write(bottomRow+2,0,'$0.000 (sub) 0.0%/272k (auto)')
  if(options.marker!==undefined)write(topRow+1+options.marker,0,'→')
  const cursor=options.cursor??{column:body[body.length-1].length,row:bottomRow-1}
  // pi writes its own caret cell, so the caret column always holds a written glyph.
  const existing=lines[cursor.row].cells[cursor.column]
  if(!existing.chars)lines[cursor.row].cells[cursor.column]={chars:' ',code:32,width:1,bgMode:0,bg:0,dim:false}
  return {cols,rows,viewportY:0,baseY:0,cursorX:cursor.column,cursorY:cursor.row,lines}
}

test('pi targets resolve inside the rules and clamp after the draft text',()=>{
  const state=piSnapshot({draft:['alpha bravo']})
  assert.deepEqual(
    resolvePiCaretTarget(state,{column:4,row:5}),
    {current:{column:11,row:5},target:{column:4,row:5},promptRow:4,textStart:0},
  )
  // The caret's own written blank must not read as content past the line end.
  assert.deepEqual(resolvePiCaretTarget(state,{column:30,row:5})?.target,{column:11,row:5})
})

test('pi targets reach every row of a wrapped draft and its leading column',()=>{
  const state=piSnapshot({draft:['alpha bravo charlie delta echo foxtrot','golf hotel']})
  assert.deepEqual(
    resolvePiCaretTarget(state,{column:9,row:5}),
    {current:{column:10,row:6},target:{column:9,row:5},promptRow:4,textStart:0},
  )
  assert.deepEqual(resolvePiCaretTarget(state,{column:0,row:6})?.target,{column:0,row:6})
})

test('an empty pi composer resolves to its single blank row',()=>{
  const state=piSnapshot()
  assert.deepEqual(
    resolvePiCaretTarget(state,{column:20,row:5}),
    {current:{column:0,row:5},target:{column:0,row:5},promptRow:4,textStart:0},
  )
})

test('a blank line inside a pi draft stays reachable',()=>{
  // Ctrl+J inserts a newline, so an interior blank row is ordinary draft content
  // rather than evidence that the block is not a composer.
  const state=piSnapshot({draft:['first','','third']})
  assert.deepEqual(resolvePiCaretTarget(state,{column:12,row:6})?.target,{column:0,row:6})
  assert.deepEqual(resolvePiCaretTarget(state,{column:2,row:7})?.target,{column:2,row:7})
})

test('pi refuses the rules, the transcript, the footer, and scrollback',()=>{
  const state=piSnapshot({draft:['alpha bravo']})
  assert.equal(resolvePiCaretTarget(state,{column:5,row:4}),null)
  assert.equal(resolvePiCaretTarget(state,{column:5,row:1}),null)
  assert.equal(resolvePiCaretTarget(state,{column:5,row:6}),null)
  assert.equal(resolvePiCaretTarget(state,{column:5,row:7}),null)
  const offTail=piSnapshot({draft:['alpha bravo']})
  offTail.baseY=2
  assert.equal(resolvePiCaretTarget(offTail,{column:5,row:5}),null)
})

test('pi refuses its own pickers, which reuse the same bracketing rules',()=>{
  // `/model` and `/settings` open a blank row directly under the top rule and
  // mark the selected row with `→`; arrows there move a list, not a caret.
  const selector=piSnapshot({
    draft:['','> gpt-5.4','','  gpt-5.4 [openai-codex]','  (1/13)'],
    cursor:{column:9,row:6},
    marker:3,
  })
  assert.equal(resolvePiCaretTarget(selector,{column:9,row:6}),null)
  assert.equal(resolvePiCaretTarget(selector,{column:5,row:8}),null)
  // Either signal alone is enough: the leading blank row...
  const gapped=piSnapshot({draft:['> filter','','  an entry'],cursor:{column:8,row:6},gap:true})
  assert.equal(resolvePiCaretTarget(gapped,{column:8,row:6}),null)
  // ...and the selection marker.
  const marked=piSnapshot({draft:['> filter','  an entry'],cursor:{column:8,row:5},marker:1})
  assert.equal(resolvePiCaretTarget(marked,{column:8,row:5}),null)
})

test('pi refuses a composer with no rule above or below the cursor',()=>{
  const noTop=piSnapshot({draft:['alpha']})
  noTop.lines[4].cells=Array.from({length:noTop.cols},()=>blankCell())
  assert.equal(resolvePiCaretTarget(noTop,{column:3,row:5}),null)
  const noBottom=piSnapshot({draft:['alpha']})
  noBottom.lines[6].cells=Array.from({length:noBottom.cols},()=>blankCell())
  assert.equal(resolvePiCaretTarget(noBottom,{column:3,row:5}),null)
  // A rule must span every column, so transcript prose containing box drawing
  // cannot pass for one.
  const shortRule=piSnapshot({draft:['alpha']})
  shortRule.lines[4].cells[shortRule.cols-1]=blankCell()
  assert.equal(resolvePiCaretTarget(shortRule,{column:3,row:5}),null)
})

test('anchored pi targets follow the composer when the transcript grows',()=>{
  const state=piSnapshot({draft:['alpha bravo']})
  assert.deepEqual(
    resolveAnchoredCaretTarget(resolvePiCaretTarget,state,{column:4,rowOffset:1})?.target,
    {column:4,row:5},
  )
})

test('steering batches at distance and switches to single-step precision after crossing',()=>{
  assert.deepEqual(
    caretSteerCommand({column:2,row:3},{column:14,row:3},20,null),
    {sequence:'\x1b[C',count:6,direction:1,distance:12},
  )
  assert.deepEqual(
    caretSteerCommand({column:15,row:3},{column:14,row:3},20,1),
    {sequence:'\x1b[D',count:1,direction:-1,distance:1},
  )
  assert.equal(caretSteerCommand({column:14,row:3},{column:14,row:3},20,-1),null)
})
