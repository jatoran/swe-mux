// Pointer behavior for the Action rail's four arrow keys.
//
// Every other rail button sends on `click`, and that is precisely what makes it safe to
// start a swipe on one: a touch that turns into the rail's horizontal pan has its click
// suppressed, while a touch that merely landed and lifted still sends. The arrows used to
// send on `pointerdown` instead, so touching one to flick the rail sideways fired the key
// before the finger had moved — the one decision the pan could no longer take back. They
// now send on `click` like everything else on the rail.
//
// What remains here is only the *extra* thing the arrows do: a press held in place turns
// into repetition. The rules that split the two:
//
//  * A press sends nothing. The tap is the button's own click, arbitrated by the rail.
//  * A press that travels `RAIL_PAN_SLOP_PX` stops being a hold candidate. It is not
//    cancelled outright — the rail decides whether the click that follows still counts, so
//    a wobbly tap is still a tap and a real swipe is still silent.
//  * A hold that reaches the repeat delay *commits*: it claims the pointer, so the rail's
//    pan stands down and a drifting finger cannot scroll the strip out from under the keys
//    it is spamming, and it marks the click it will end with as already served.
//
// Kept DOM-free so the cadence, the slop, and the tap/hold split are testable without a
// browser. TerminalPane owns the listeners and the button; this owns one pointer's state.

import { claimPointerDrag } from './pointerDragClaim.ts'
import { RAIL_PAN_SLOP_PX } from './railOverflow.ts'

export const RAIL_KEY_REPEAT_DELAY_MS = 350
export const RAIL_KEY_REPEAT_INTERVAL_MS = 75
/** Travel that ends a press's claim on becoming a hold. Deliberately the rail pan's own
 *  threshold: both decisions are about the same pixels and must not disagree. */
export const RAIL_KEY_HOLD_SLOP_PX = RAIL_PAN_SLOP_PX

const REPEATABLE_RAIL_KEYS = new Set(['up', 'down', 'left', 'right'])

export function isRepeatableRailKey(id:string):boolean {
  return REPEATABLE_RAIL_KEYS.has(id)
}

export interface RailKeyRepeater {
  /** Open a press. Sends nothing: a clean tap is delivered by the button's own click. */
  press(pointerId:number,x:number,y:number,sequence:string):boolean
  /** Report where the pointer now is. Returns true when this ends the hold candidacy. */
  move(pointerId:number,x:number,y:number):boolean
  /** Close the press. Only the pointer that opened it may. */
  release(pointerId:number):boolean
  /** Whether the click now arriving was already answered by a committed hold, and so must
   *  be swallowed rather than sent again. One-shot: reading it clears it. */
  consumeHeldClick():boolean
  /** Stop unconditionally on blur, visibility loss, session change, or unmount. */
  cancel():void
}

export function createRailKeyRepeater<T>(
  send:(sequence:string)=>void,
  schedule:(callback:()=>void,delayMs:number)=>T,
  cancelTimer:(timer:T)=>void,
):RailKeyRepeater {
  let activePointer:number|null=null
  let activeSequence=''
  let startX=0
  let startY=0
  let timer:T|null=null
  let releaseClaim:(()=>void)|null=null
  let heldClickPending=false

  const clearTimer=()=>{
    if(timer===null)return
    cancelTimer(timer)
    timer=null
  }
  const stop=()=>{
    clearTimer()
    releaseClaim?.()
    releaseClaim=null
    activePointer=null
    activeSequence=''
  }
  const arm=(delayMs:number)=>{
    timer=schedule(()=>{
      timer=null
      if(activePointer===null)return
      // The first repetition is where the hold commits, so it is where the rest of the
      // gesture stops belonging to anyone else.
      if(!releaseClaim){
        releaseClaim=claimPointerDrag()
        heldClickPending=true
      }
      send(activeSequence)
      arm(RAIL_KEY_REPEAT_INTERVAL_MS)
    },delayMs)
  }

  return {
    press(pointerId,x,y,sequence){
      // A hold whose click never arrived (the finger left the button, the pane lost the
      // window) must not swallow the next press's tap instead.
      heldClickPending=false
      if(activePointer!==null||!sequence)return false
      activePointer=pointerId
      activeSequence=sequence
      startX=x
      startY=y
      arm(RAIL_KEY_REPEAT_DELAY_MS)
      return true
    },
    move(pointerId,x,y){
      if(activePointer!==pointerId)return false
      // A committed hold already owns the pointer; drifting during one does not end it.
      if(releaseClaim)return false
      if(Math.max(Math.abs(x-startX),Math.abs(y-startY))<RAIL_KEY_HOLD_SLOP_PX)return false
      stop()
      return true
    },
    release(pointerId){
      if(activePointer!==pointerId)return false
      stop()
      return true
    },
    consumeHeldClick(){
      const held=heldClickPending
      heldClickPending=false
      return held
    },
    cancel:stop,
  }
}
