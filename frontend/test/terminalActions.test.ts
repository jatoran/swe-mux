import assert from 'node:assert/strict'
import { settleTerminalInsertion, TERMINAL_SUBMIT_SETTLE_MS } from '../src/terminalActions.ts'

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
  'draft',
  false,
  value=>appendOnly.push(`append:${value}`),
  ()=>appendOnly.push('submit'),
  async delay=>{appendOnly.push(`wait:${delay}`)},
)
assert.deepEqual(appendOnly,['append:draft'])

console.log('terminal action tests passed')
