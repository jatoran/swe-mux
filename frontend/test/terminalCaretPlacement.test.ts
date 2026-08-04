import assert from 'node:assert/strict'
import test from 'node:test'
import {
  caretSteerCommand,
  resolveAnchoredCodexCaretTarget,
  resolveCodexCaretTarget,
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

test('tap routing forwards Claude touch mouse and steers only Codex',()=>{
  const base={still:true,primary:true,modified:false,readMode:false,hasSelection:false}
  assert.equal(terminalTapAction({...base,backend:'claude',pointerType:'touch',mouseTracking:true}),'forward-mouse')
  assert.equal(terminalTapAction({...base,backend:'claude',pointerType:'mouse',mouseTracking:true}),'none')
  assert.equal(terminalTapAction({...base,backend:'codex',pointerType:'mouse',mouseTracking:false}),'steer-codex-caret')
  assert.equal(terminalTapAction({...base,backend:'codex',pointerType:'touch',mouseTracking:false,readMode:true}),'none')
  assert.equal(terminalTapAction({...base,backend:'codex',pointerType:'touch',mouseTracking:false,hasSelection:true}),'none')
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
    resolveAnchoredCodexCaretTarget(state,{column:5,rowOffset:0})?.target,
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

test('Codex target refuses an unstyled prompt that could be transcript text',()=>{
  const state=snapshot()
  for(const line of state.lines){
    for(const cell of line.cells){cell.bgMode=0;cell.bg=0}
  }
  assert.equal(resolveCodexCaretTarget(state,{column:3,row:3}),null)
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
