import { useEffect, useRef } from 'preact/hooks'
import type { RefObject } from 'preact'

const SELECTOR = 'button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[href],[tabindex]:not([tabindex="-1"])'

export function useModalFocus(ref:RefObject<HTMLElement>,onClose:()=>void,enabled=true) {
  const closeRef=useRef(onClose)
  closeRef.current=onClose
  useEffect(()=>{
    if(!enabled)return
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:null
    const frame=requestAnimationFrame(()=>ref.current?.querySelector<HTMLElement>(SELECTOR)?.focus())
    const keydown=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeRef.current();return}
      if(event.key!=='Tab'||!ref.current)return
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
