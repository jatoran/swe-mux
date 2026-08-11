import { useEffect, useRef } from 'preact/hooks'
import type { RefObject } from 'preact'
import { isFocusTraversalKey } from './keys'
import { dismissStack } from './dismissStack.ts'

const SELECTOR = 'button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[href],[tabindex]:not([tabindex="-1"])'

/** Stack membership alone, shared by the two public hooks. */
function useStackLevel(dismiss:()=>void,enabled:boolean,resolveLabel:()=>string) {
  const dismissRef=useRef(dismiss)
  dismissRef.current=dismiss
  const idRef=useRef<number|null>(null)
  // Registration is mount-scoped on purpose: re-registering on the `enabled` gate would
  // move the level to the top of the stack, above levels that opened after it.
  useEffect(()=>{
    const id=dismissStack.register({label:resolveLabel(),active:enabled,dismiss:()=>dismissRef.current()})
    idRef.current=id
    return()=>{idRef.current=null;dismissStack.unregister(id)}
  },[])
  useEffect(()=>{
    if(idRef.current!==null)dismissStack.setActive(idRef.current,enabled)
  },[enabled])
}

/**
 * Register a dismissable level that is not its own focus-trapped modal — a drill-down
 * inside one, such as the transcript inside the history browser.
 *
 * Mount this *after* the surrounding modal's `useModalFocus`, which is what puts it above
 * that modal in the stack: back then closes the drill-down first and the modal second,
 * while the modal keeps its focus trap the whole time. Gating the parent's `enabled`
 * instead would hand the level ordering over at the cost of dropping Tab containment for
 * as long as the drill-down is open.
 */
export function useDismissLevel(dismiss:()=>void,enabled=true,label='level') {
  useStackLevel(dismiss,enabled,()=>label)
}

/**
 * Focus containment plus dismiss-stack membership for one overlay level.
 *
 * Calling this is how a surface declares "I am open, and this is how back closes me",
 * which is what gives it the platform back gesture and the mobile back swipe alongside
 * Escape. `enabled` gates whether this level is currently the dismiss target *and*
 * whether the focus trap is installed, so a surface that wants to keep containment while
 * a drill-down is open should leave it alone and let the drill-down register above it.
 * Registration is tied to mount, so a gated level keeps its position in the stack.
 */
export function useModalFocus(ref:RefObject<HTMLElement>,onClose:()=>void,enabled=true,label?:string) {
  const closeRef=useRef(onClose)
  closeRef.current=onClose
  // Named from the panel's own class so the trace ring reads as the UI does.
  useStackLevel(()=>closeRef.current(),enabled,()=>label||ref.current?.classList.item(0)||'modal')
  useEffect(()=>{
    if(!enabled)return
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:null
    const frame=requestAnimationFrame(()=>ref.current?.querySelector<HTMLElement>(SELECTOR)?.focus())
    const keydown=(event:KeyboardEvent)=>{
      // Escape dismisses one level rather than this one: with nested overlays every
      // enabled hook has a listener on window at the same phase, so all of them see the
      // key. Routing through the stack makes the innermost level the one that closes,
      // and the stack's own repeat guard absorbs the duplicate calls.
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();dismissStack.pop();return}
      if(!isFocusTraversalKey(event)||!ref.current)return
      const items=[...ref.current.querySelectorAll<HTMLElement>(SELECTOR)].filter(item=>item.offsetParent!==null)
      if(!items.length)return
      const first=items[0],last=items[items.length-1]
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
    }
    window.addEventListener('keydown',keydown,true)
    return()=>{cancelAnimationFrame(frame);window.removeEventListener('keydown',keydown,true);previous?.focus()}
  },[ref,enabled])
}
