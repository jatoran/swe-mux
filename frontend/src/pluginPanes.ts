import type { Session } from './types.ts'

export type PluginPaneTarget =
  | { mode: 'popup'; popupId: string }
  | { mode: 'workspace'; projectId: string; sessionId: string }

/** The browser destination for a pane contribution after its session is created. */
export function pluginPaneTarget(session: Session, placement: string): PluginPaneTarget {
  return placement === 'popup'
    ? { mode: 'popup', popupId: session.id }
    : { mode: 'workspace', projectId: session.project_id, sessionId: session.id }
}

