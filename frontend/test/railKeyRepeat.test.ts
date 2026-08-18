import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createRailKeyRepeater,
  isRepeatableRailKey,
  RAIL_KEY_HOLD_SLOP_PX,
  RAIL_KEY_REPEAT_DELAY_MS,
  RAIL_KEY_REPEAT_INTERVAL_MS,
} from '../src/railKeyRepeat.ts'
import { RAIL_PAN_SLOP_PX } from '../src/railOverflow.ts'
import { pointerDragOwnsPointer } from '../src/pointerDragClaim.ts'

interface PendingTimer {
  callback:()=>void
  delayMs:number
  cancelled:boolean
}

function harness() {
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
  return { sent, pending, repeater }
}

test('only the four command-rail arrows repeat',()=>{
  for(const id of ['up','down','left','right'])assert.equal(isRepeatableRailKey(id),true)
  for(const id of ['enter','tab','ctrlC','home','ctrlUp'])assert.equal(isRepeatableRailKey(id),false)
})

test('a press sends nothing, leaving the tap to the click the rail can still suppress',()=>{
  const { sent, pending, repeater } = harness()

  assert.equal(repeater.press(7,100,100,'\x1b[A'),true)
  assert.deepEqual(sent,[])
  assert.equal(pending[0].delayMs,RAIL_KEY_REPEAT_DELAY_MS)

  assert.equal(repeater.release(9),false)
  assert.equal(repeater.release(7),true)
  assert.equal(pending[0].cancelled,true)
  assert.deepEqual(sent,[])
  // The button sends this one itself; nothing here claims it.
  assert.equal(repeater.consumeHeldClick(),false)
})

test('a hold repeats at the configured cadence and answers its own trailing click',()=>{
  const { sent, pending, repeater } = harness()

  assert.equal(repeater.press(7,100,100,'\x1b[A'),true)
  assert.equal(pointerDragOwnsPointer(),false)

  pending[0].callback()
  assert.deepEqual(sent,['\x1b[A'])
  assert.equal(pending[1].delayMs,RAIL_KEY_REPEAT_INTERVAL_MS)
  // The committed hold owns the pointer, so the rail's pan stands down rather than
  // scrolling the strip out from under the key being spammed.
  assert.equal(pointerDragOwnsPointer(),true)

  pending[1].callback()
  assert.deepEqual(sent,['\x1b[A','\x1b[A'])

  assert.equal(repeater.release(7),true)
  assert.equal(pending[2].cancelled,true)
  assert.equal(pointerDragOwnsPointer(),false)
  // The click that follows a hold was already served, so the button must swallow it, and
  // only once.
  assert.equal(repeater.consumeHeldClick(),true)
  assert.equal(repeater.consumeHeldClick(),false)
})

test('a committed hold survives the drift of the finger holding it',()=>{
  const { sent, pending, repeater } = harness()

  repeater.press(7,100,100,'\x1b[B')
  pending[0].callback()
  assert.equal(repeater.move(7,100+RAIL_KEY_HOLD_SLOP_PX*4,100),false)
  pending[1].callback()
  assert.deepEqual(sent,['\x1b[B','\x1b[B'])
  repeater.release(7)
  assert.equal(pointerDragOwnsPointer(),false)
})

test('a press that travels as far as the rail pans can no longer become a hold',()=>{
  assert.equal(RAIL_KEY_HOLD_SLOP_PX,RAIL_PAN_SLOP_PX)
  const { sent, pending, repeater } = harness()

  repeater.press(7,100,100,'\x1b[D')
  assert.equal(repeater.move(7,100+RAIL_KEY_HOLD_SLOP_PX-1,100),false)
  assert.equal(repeater.move(9,500,500),false)
  assert.equal(repeater.move(7,100+RAIL_KEY_HOLD_SLOP_PX,100),true)
  assert.equal(pending[0].cancelled,true)

  // Past the slop the hold is off, and the timer that would have started it is inert.
  pending[0].callback()
  assert.deepEqual(sent,[])
  // Nothing is claimed either way: whether the swipe suppresses the click is the rail's
  // call, not this module's.
  assert.equal(repeater.consumeHeldClick(),false)
  assert.equal(pointerDragOwnsPointer(),false)
})

test('vertical travel ends the hold too, and the released pointer is the only one that may',()=>{
  const { pending, repeater } = harness()

  repeater.press(7,100,100,'\x1b[C')
  assert.equal(repeater.move(7,100,100+RAIL_KEY_HOLD_SLOP_PX),true)
  assert.equal(pending[0].cancelled,true)
  assert.equal(repeater.move(7,100,400),false)
  assert.equal(repeater.release(7),false)
})

test('a second pointer cannot replace an open press, and cancellation frees the repeater',()=>{
  const { sent, pending, repeater } = harness()

  assert.equal(repeater.press(1,0,0,'left'),true)
  assert.equal(repeater.press(2,0,0,'right'),false)
  assert.deepEqual(sent,[])

  repeater.cancel()
  assert.equal(pending[0].cancelled,true)
  // An item with no bytes has nothing to repeat, so it never opens a press.
  assert.equal(repeater.press(2,0,0,''),false)
  assert.equal(repeater.press(2,0,0,'right'),true)
  pending[1].callback()
  assert.deepEqual(sent,['right'])
  repeater.cancel()
  assert.equal(pointerDragOwnsPointer(),false)
})

test('a hold whose click never lands does not swallow the next tap instead',()=>{
  const { pending, repeater } = harness()

  repeater.press(7,100,100,'\x1b[A')
  pending[0].callback()
  repeater.release(7)
  // No click arrived — the finger left the button. The next press clears the debt rather
  // than letting it eat a tap that has nothing to do with it.
  assert.equal(repeater.press(8,100,100,'\x1b[A'),true)
  assert.equal(repeater.consumeHeldClick(),false)
  repeater.cancel()
})
