import { useRef } from 'preact/hooks'
import { RailEditor } from './RailEditor'
import { useModalFocus } from './modalFocus'

/** Standalone editor for the shared Action catalog and its four device surfaces.
 *  `projectId` is where the operator was standing, and the scope the editor opens
 *  on — a Project's own actions and prompt templates are reachable from no other. */
export function ActionEditorModal({ projectId, onClose }: { projectId?: string; onClose: () => void }) {
  const panel = useRef<HTMLElement>(null)
  useModalFocus(panel, onClose)

  return <div class="modal-layer action-editor-layer" onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <section ref={panel} class="modal action-editor-modal" role="dialog" aria-modal="true" aria-label="Configure Actions">
      <div class="modal-heading">
        <div><span>ACTIONS::LAYOUT</span><h2>Configure Actions</h2></div>
        <button type="button" aria-label="Close Configure Actions" onClick={onClose}>×</button>
      </div>
      <div class="action-editor-body"><RailEditor initialScope={projectId || ''} /></div>
      <div class="modal-footer"><span>Changes apply immediately.</span><button type="button" onClick={onClose}>Done</button></div>
    </section>
  </div>
}
