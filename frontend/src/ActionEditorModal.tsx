import { useRef } from 'preact/hooks'
import { RailEditor } from './RailEditor'
import { railProjectScopeKind, type RailBlob } from './commandRail'
import { currentRailBlob } from './deviceSettings'
import { useModalFocus } from './modalFocus'

/** Detached projects open directly. Every project following Global starts at Global. */
export function actionEditorInitialScope(projectId: string | undefined, blob: RailBlob | undefined): string {
  return projectId && railProjectScopeKind(blob, projectId) === 'fork' ? projectId : ''
}

/** Standalone editor for the shared Action catalog and its four device surfaces. */
export function ActionEditorModal({ projectId, onClose }: { projectId?: string; onClose: () => void }) {
  const panel = useRef<HTMLElement>(null)
  useModalFocus(panel, onClose)
  const initialScope = actionEditorInitialScope(projectId, currentRailBlob())

  return <div class="modal-layer action-editor-layer" onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <section ref={panel} class="modal action-editor-modal" role="dialog" aria-modal="true" aria-label="Configure Actions">
      <div class="modal-heading">
        <div><span>ACTIONS::LAYOUT</span><h2>Configure Actions</h2></div>
        <button type="button" aria-label="Close Configure Actions" onClick={onClose}>×</button>
      </div>
      <div class="action-editor-body"><RailEditor initialScope={initialScope} contextProjectId={projectId} /></div>
      <div class="modal-footer"><span>Changes apply immediately.</span><button type="button" onClick={onClose}>Done</button></div>
    </section>
  </div>
}
