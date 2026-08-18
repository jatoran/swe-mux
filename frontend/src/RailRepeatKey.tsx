// The Action rail's arrow keys: the tap-or-hold button whose rules `railKeyRepeat.ts`
// owns, plus the listeners those rules need.
//
// Its own module rather than JSX inside TerminalPane because the gesture *is* this
// button, and a gesture is only worth trusting once real touches have been driven at it
// through a real rail (`test/renderer/command-rail-keys.spec.ts`). Mounting a terminal
// pane — xterm, a WebSocket, a session — is not a way to do that, and a harness that
// re-implemented the button would be testing a copy of the thing that can break.

import { useEffect, useRef } from 'preact/hooks'
import { createRailKeyRepeater, type RailKeyRepeater } from './railKeyRepeat'

export interface RailKeyRepeatController {
  repeater: RailKeyRepeater
  /** Send one sequence now — the tap path, and what the repeater repeats. */
  send(sequence:string):void
  /** Follow the open press at the window until it ends. */
  watch():void
}

/**
 * One controller per rail rather than one per key: two arrows must never repeat at once,
 * and whatever takes the window away (blur, a hidden tab, a session swap) has to be able
 * to stop whichever one is.
 *
 * The press is followed at the *window* and not on the button that received it. The rail's
 * horizontal pan takes pointer capture as soon as the same touch starts scrolling, and
 * capture retargets every later pointer event to the scroller — so a button-local
 * `pointermove` would never see the swipe it has to stand down for. The listeners exist
 * only while a press is open: a standing window `pointermove` for every pane on screen is
 * not worth the four keys it serves.
 */
export function useRailKeyRepeat(send:(sequence:string)=>void,resetKey:string):RailKeyRepeatController {
  const sendRef=useRef(send)
  sendRef.current=send
  const stateRef=useRef<{controller:RailKeyRepeatController;unwatch:()=>void}|null>(null)
  if(!stateRef.current){
    const repeater=createRailKeyRepeater(
      sequence=>sendRef.current(sequence),
      (callback,delayMs)=>window.setTimeout(callback,delayMs),
      timer=>window.clearTimeout(timer),
    )
    let detach:(()=>void)|null=null
    const unwatch=()=>{detach?.();detach=null}
    const watch=()=>{
      unwatch()
      const move=(event:PointerEvent)=>{if(repeater.move(event.pointerId,event.clientX,event.clientY))unwatch()}
      const end=(event:PointerEvent)=>{if(repeater.release(event.pointerId))unwatch()}
      window.addEventListener('pointermove',move)
      window.addEventListener('pointerup',end)
      window.addEventListener('pointercancel',end)
      detach=()=>{
        window.removeEventListener('pointermove',move)
        window.removeEventListener('pointerup',end)
        window.removeEventListener('pointercancel',end)
      }
    }
    stateRef.current={controller:{repeater,send:sequence=>sendRef.current(sequence),watch},unwatch}
  }
  const {controller,unwatch}=stateRef.current
  useEffect(()=>{
    const cancel=()=>{controller.repeater.cancel();unwatch()}
    window.addEventListener('blur',cancel)
    document.addEventListener('visibilitychange',cancel)
    return()=>{
      window.removeEventListener('blur',cancel)
      document.removeEventListener('visibilitychange',cancel)
      cancel()
    }
  },[resetKey])
  return controller
}

export interface RailRepeatKeyProps {
  repeat: RailKeyRepeatController
  /** The bytes this key writes to the pty. */
  sequence: string
  label: string
  title: string
  className: string
}

/**
 * Tap to send, hold to repeat.
 *
 * The tap rides the same `click` every other rail button sends on, which is the whole
 * point: a touch the rail turns into a horizontal pan has its click suppressed, so a flick
 * that merely began on an arrow scrolls the rail instead of firing the key. The press
 * below only decides whether this one *also* becomes a hold.
 *
 * Focus is refused on `mousedown` rather than `pointerdown` — the same guard the rest of
 * the rail uses — because the click is now load-bearing, and cancelling the pointer's
 * default would leave the tap to each browser's touch-compatibility rules.
 */
export function RailRepeatKey({repeat,sequence,label,title,className}:RailRepeatKeyProps) {
  return <button
    class={`${className} rail-key-repeat`}
    title={`${title} (hold to repeat)`}
    onMouseDown={event=>event.preventDefault()}
    onPointerDown={event=>{
      if(!event.isPrimary||event.button!==0)return
      if(repeat.repeater.press(event.pointerId,event.clientX,event.clientY,sequence))repeat.watch()
    }}
    onContextMenu={event=>event.preventDefault()}
    onClick={()=>{if(!repeat.repeater.consumeHeldClick())repeat.send(sequence)}}
  >{label}</button>
}
