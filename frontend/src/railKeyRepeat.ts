// Pointer hold behavior for the Action rail's four arrow keys.
//
// Kept DOM-free so the repeat cadence and cancellation rules can be tested
// deterministically. TerminalPane owns pointer capture and focus preservation;
// this helper owns only the one-active-pointer timer state.

export const RAIL_KEY_REPEAT_DELAY_MS = 350
export const RAIL_KEY_REPEAT_INTERVAL_MS = 75

const REPEATABLE_RAIL_KEYS = new Set(['up', 'down', 'left', 'right'])

export function isRepeatableRailKey(id:string):boolean {
  return REPEATABLE_RAIL_KEYS.has(id)
}

export interface RailKeyRepeater {
  /** Send once immediately and arm repetition for this pointer. */
  press(pointerId:number,sequence:string):boolean
  /** Stop only when the pointer ending is the pointer that started the hold. */
  release(pointerId:number):boolean
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
  let timer:T|null=null

  const clearTimer=()=>{
    if(timer===null)return
    cancelTimer(timer)
    timer=null
  }
  const stop=()=>{
    clearTimer()
    activePointer=null
    activeSequence=''
  }
  const arm=(delayMs:number)=>{
    timer=schedule(()=>{
      timer=null
      if(activePointer===null)return
      send(activeSequence)
      arm(RAIL_KEY_REPEAT_INTERVAL_MS)
    },delayMs)
  }

  return {
    press(pointerId,sequence){
      if(activePointer!==null||!sequence)return false
      activePointer=pointerId
      activeSequence=sequence
      send(sequence)
      arm(RAIL_KEY_REPEAT_DELAY_MS)
      return true
    },
    release(pointerId){
      if(activePointer!==pointerId)return false
      stop()
      return true
    },
    cancel:stop,
  }
}
