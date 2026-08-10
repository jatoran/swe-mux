import assert from 'node:assert/strict'
import { requestTerminalAction, settleTerminalInsertion, TERMINAL_SUBMIT_SETTLE_MS } from '../src/terminalActions.ts'

const events:string[]=[]
let release:()=>void=()=>{}
const pending=settleTerminalInsertion(
  'hello',
  true,
  value=>events.push(`append:${value}`),
  ()=>events.push('submit'),
  delay=>{
    events.push(`wait:${delay}`)
    return new Promise<void>(resolve=>{release=resolve})
  },
)
assert.deepEqual(events,[`append:hello`,`wait:${TERMINAL_SUBMIT_SETTLE_MS}`])
release()
await pending
assert.deepEqual(events,[`append:hello`,`wait:${TERMINAL_SUBMIT_SETTLE_MS}`,'submit'])

const appendOnly:string[]=[]
await settleTerminalInsertion(
  '  first line\nsecond line  ',
  false,
  value=>appendOnly.push(`append:${value}`),
  ()=>appendOnly.push('submit'),
  async delay=>{appendOnly.push(`wait:${delay}`)},
)
assert.deepEqual(appendOnly,['append:  first line\nsecond line  '])

const previousWindow=(globalThis as {window?:unknown}).window
const actionBus=Object.assign(new EventTarget(),{
  setTimeout:globalThis.setTimeout.bind(globalThis),
  clearTimeout:globalThis.clearTimeout.bind(globalThis),
})
;(globalThis as {window?:unknown}).window=actionBus
try{
  let received:Record<string,unknown>|null=null
  actionBus.addEventListener('mux:terminal-action',event=>{
    received=(event as CustomEvent<Record<string,unknown>>).detail
    actionBus.dispatchEvent(new CustomEvent('mux:terminal-action-result',{detail:{requestId:received.requestId,ok:true}}))
  },{once:true})
  await requestTerminalAction('session-1',{action:'sendKey',text:'\x1b'})
  assert.equal(received?.sessionId,'session-1')
  assert.equal(received?.action,'sendKey')
  assert.equal(received?.text,'\x1b')

  actionBus.addEventListener('mux:terminal-action',event=>{
    const detail=(event as CustomEvent<Record<string,unknown>>).detail
    actionBus.dispatchEvent(new CustomEvent('mux:terminal-action-result',{detail:{requestId:detail.requestId,ok:false,error:'No selection'}}))
  },{once:true})
  await assert.rejects(requestTerminalAction('session-1',{action:'copy'}),/No selection/)
}finally{
  ;(globalThis as {window?:unknown}).window=previousWindow
}

console.log('terminal action tests passed')
