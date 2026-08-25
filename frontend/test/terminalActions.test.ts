import assert from 'node:assert/strict'
import test from 'node:test'
import { requestTerminalAction, settleTerminalInsertion, TERMINAL_SUBMIT_SETTLE_MS } from '../src/terminalActions.ts'

test('a submitted insertion appends, waits out the settle window, then submits', async () => {
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
})

test('an append-only insertion keeps its whitespace and never waits or submits', async () => {
  const appendOnly:string[]=[]
  await settleTerminalInsertion(
    '  first line\nsecond line  ',
    false,
    value=>appendOnly.push(`append:${value}`),
    ()=>appendOnly.push('submit'),
    async delay=>{appendOnly.push(`wait:${delay}`)},
  )
  assert.deepEqual(appendOnly,['append:  first line\nsecond line  '])
})

/** Stands in for `window` so the request/result event pair can be driven by hand. */
function withActionBus<T>(run:(bus:EventTarget)=>Promise<T>):Promise<T>{
  const previousWindow=(globalThis as {window?:unknown}).window
  const actionBus=Object.assign(new EventTarget(),{
    setTimeout:globalThis.setTimeout.bind(globalThis),
    clearTimeout:globalThis.clearTimeout.bind(globalThis),
  })
  ;(globalThis as {window?:unknown}).window=actionBus
  return run(actionBus).finally(()=>{
    ;(globalThis as {window?:unknown}).window=previousWindow
  })
}

test('a terminal action reaches the pane as an event and resolves on its result', async () => {
  await withActionBus(async actionBus=>{
    const received:Record<string,unknown>[]=[]
    actionBus.addEventListener('mux:terminal-action',event=>{
      const detail=(event as CustomEvent<Record<string,unknown>>).detail
      received.push(detail)
      actionBus.dispatchEvent(new CustomEvent('mux:terminal-action-result',{detail:{requestId:detail.requestId,ok:true}}))
    },{once:true})
    await requestTerminalAction('session-1',{action:'sendKey',text:'\x1b'})
    assert.equal(received.length,1)
    assert.equal(received[0].sessionId,'session-1')
    assert.equal(received[0].action,'sendKey')
    assert.equal(received[0].text,'\x1b')
  })
})

test('a refused terminal action rejects with the pane\'s reason', async () => {
  await withActionBus(async actionBus=>{
    actionBus.addEventListener('mux:terminal-action',event=>{
      const detail=(event as CustomEvent<Record<string,unknown>>).detail
      actionBus.dispatchEvent(new CustomEvent('mux:terminal-action-result',{detail:{requestId:detail.requestId,ok:false,error:'No selection'}}))
    },{once:true})
    await assert.rejects(requestTerminalAction('session-1',{action:'copy'}),/No selection/)
  })
})
