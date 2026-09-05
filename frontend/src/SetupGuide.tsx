import type { ComponentChildren } from 'preact'
import { useRef } from 'preact/hooks'
import { useModalFocus } from './modalFocus'

/** Focus and dismissal stay with a guide, including when opened over Settings. */
export function SetupGuide({title,label,onClose,busy=false,children}:{title:string;label:string;onClose:()=>void;busy?:boolean;children:ComponentChildren}){
  const panel=useRef<HTMLElement>(null)
  useModalFocus(panel,()=>{if(!busy)onClose()},true,'setup-guide')
  return <div class="harness-setup-backdrop" role="dialog" aria-modal="true" aria-label={label}><section ref={panel} class="harness-setup"><header><strong>SET UP::{title}</strong><button disabled={busy} onClick={onClose}>Continue later</button></header><div class="harness-setup-body">{children}</div></section></div>
}
