import type { ComponentChildren, JSX } from 'preact'
import { useRef } from 'preact/hooks'
import { useModalFocus } from './modalFocus'

type NoteWorkspaceMode='dock'|'popout'
type Tab={id:string;label:string}
type Props={
  visible:boolean;mode:NoteWorkspaceMode;size:number;tabs:Tab[];activeId:string|null
  onActivate:(id:string)=>void;onMode:(mode:NoteWorkspaceMode)=>void;onHide:()=>void
  onTabContext:(id:string,event:JSX.TargetedMouseEvent<HTMLButtonElement>)=>void
  onResize:(event:JSX.TargetedPointerEvent<HTMLDivElement>)=>void;children:ComponentChildren
}

export function NotesWorkspace({visible,mode,size,tabs,activeId,onActivate,onMode,onHide,onTabContext,onResize,children}:Props){
  const panel=useRef<HTMLElement>(null)
  const poppedOut=visible&&mode==='popout'
  useModalFocus(panel,onHide,poppedOut)
  return <>
    <div class={`notes-workspace-divider ${visible&&mode==='dock'?'':'hidden'}`} role="separator" aria-orientation="vertical" onPointerDown={onResize}/>
    <section class={`notes-workspace-shell ${visible?'visible':'hidden'} ${mode}`} style={mode==='dock'?{width:`${size*100}%`}:undefined} role={poppedOut?'dialog':undefined} aria-modal={poppedOut||undefined} aria-label="Notes workspace" onMouseDown={event=>{if(poppedOut&&event.target===event.currentTarget)onHide()}}>
      <aside class="notes-workspace-panel" ref={panel}>
        <div class="notes-workspace-tabs" role="tablist" aria-label="Open notes">
          {tabs.map(tab=><button role="tab" aria-selected={tab.id===activeId} class={tab.id===activeId?'active':''} title={`${tab.label} · right-click for presentation`} onClick={()=>onActivate(tab.id)} onContextMenu={event=>onTabContext(tab.id,event)}>{tab.label}</button>)}
          <span class="notes-workspace-spacer" />
          <button class="notes-workspace-mode" title={mode==='dock'?'Pop out the Notes workspace':'Dock the Notes workspace'} onClick={()=>onMode(mode==='dock'?'popout':'dock')}>{mode==='dock'?'pop out':'dock'}</button>
          <button class="notes-workspace-hide" aria-label="Minimize Notes workspace" title="Minimize Notes workspace" onClick={onHide}>−</button>
        </div>
        <div class="notes-workspace-active">{children}</div>
      </aside>
    </section>
  </>
}
