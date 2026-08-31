import type { Session } from './types.ts'
import { activateContainingStack, stackForView, type PaneLayout } from './layout.ts'
import { placePendingTerminal } from './pendingSession.ts'

export type PluginPaneTarget =
  | { mode: 'popup'; popupId: string }
  | { mode: 'workspace'; projectId: string; sessionId: string }

/** The browser destination for a pane contribution after its session is created. */
export function pluginPaneTarget(session: Session, placement: string): PluginPaneTarget {
  return placement === 'popup'
    ? { mode: 'popup', popupId: session.id }
    : { mode: 'workspace', projectId: session.project_id, sessionId: session.id }
}

/** Place a pane opened by this browser immediately, or focus its existing Project tab. */
export function placePluginPane(
  layout:PaneLayout,
  sessionId:string,
  placement:string,
  anchorId:string|null,
):PaneLayout{
  if(stackForView(layout,sessionId))return activateContainingStack(layout,sessionId)
  return placePendingTerminal(layout,sessionId,{
    split:placement==='split'?'vertical':false,
    targetId:anchorId,
    position:'after',
  })
}
