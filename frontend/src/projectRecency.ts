export const PROJECT_RECENCY_EVENT = 'mux:project-recency'

export type ProjectUseReason = 'prompt_submitted' | 'session_started'

export interface ProjectRecencyEventDetail {
  sessionId: string
  reason: ProjectUseReason
}

/** Report a successful explicit prompt submission to the composition root. */
export function reportPromptSubmitted(sessionId: string): void {
  window.dispatchEvent(new CustomEvent<ProjectRecencyEventDetail>(PROJECT_RECENCY_EVENT, {
    detail: { sessionId, reason: 'prompt_submitted' },
  }))
}
