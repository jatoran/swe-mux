import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createRailKeyRepeater,
  isRepeatableRailKey,
  RAIL_KEY_REPEAT_DELAY_MS,
  RAIL_KEY_REPEAT_INTERVAL_MS,
} from '../src/railKeyRepeat.ts'

interface PendingTimer {
  callback:()=>void
  delayMs:number
  cancelled:boolean
}

test('only the four command-rail arrows repeat',()=>{
  for(const id of ['up','down','left','right'])assert.equal(isRepeatableRailKey(id),true)
  for(const id of ['enter','tab','ctrlC','home','ctrlUp'])assert.equal(isRepeatableRailKey(id),false)
})

test('rail arrows send on press, then repeat at the configured cadence until release',()=>{
  const sent:string[]=[]
  const pending:PendingTimer[]=[]
  const repeater=createRailKeyRepeater(
    sequence=>sent.push(sequence),
    (callback,delayMs)=>{
      const timer={callback,delayMs,cancelled:false}
      pending.push(timer)
      return timer
    },
    timer=>{timer.cancelled=true},
  )

  assert.equal(repeater.press(7,'\x1b[A'),true)
  assert.deepEqual(sent,['\x1b[A'])
  assert.equal(pending[0].delayMs,RAIL_KEY_REPEAT_DELAY_MS)

  pending[0].callback()
  assert.deepEqual(sent,['\x1b[A','\x1b[A'])
  assert.equal(pending[1].delayMs,RAIL_KEY_REPEAT_INTERVAL_MS)

  assert.equal(repeater.release(9),false)
  pending[1].callback()
  assert.equal(sent.length,3)

  assert.equal(repeater.release(7),true)
  assert.equal(pending[2].cancelled,true)
  pending[2].callback()
  assert.equal(sent.length,3)
})

test('a second pointer cannot replace an active hold and cancellation frees the repeater',()=>{
  const sent:string[]=[]
  const pending:PendingTimer[]=[]
  const repeater=createRailKeyRepeater(
    sequence=>sent.push(sequence),
    (callback,delayMs)=>{
      const timer={callback,delayMs,cancelled:false}
      pending.push(timer)
      return timer
    },
    timer=>{timer.cancelled=true},
  )

  assert.equal(repeater.press(1,'left'),true)
  assert.equal(repeater.press(2,'right'),false)
  assert.deepEqual(sent,['left'])
  repeater.cancel()
  assert.equal(pending[0].cancelled,true)
  assert.equal(repeater.press(2,'right'),true)
  assert.deepEqual(sent,['left','right'])
})
