import { RailEditor } from './RailEditor'
import { railProjectScopeKind, type RailBlob } from './commandRail'
import { currentRailBlob } from './deviceSettings'

/** Detached projects open directly. Every project following Global starts at Global. */
export function actionEditorInitialScope(projectId: string | undefined, blob: RailBlob | undefined): string {
  return projectId && railProjectScopeKind(blob, projectId) === 'fork' ? projectId : ''
}

/** Settings-owned editor for the shared Action catalog and device rails. */
export function ActionEditorPanel({ projectId }: { projectId?: string }) {
  const initialScope = actionEditorInitialScope(projectId, currentRailBlob())
  return <div class="action-editor-body">
    <p class="profile-hint">Layout and catalog changes apply immediately, separately from Settings Save.</p>
    <RailEditor initialScope={initialScope} contextProjectId={projectId} />
  </div>
}
