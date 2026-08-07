export const PROJECT_RECENCY_EVENT = 'mux:project-recency'

export interface ProjectRecencyEventDetail {
  sessionId: string
  reason: 'prompt_submitted'
}

/** Report a successful explicit prompt submission to the composition root. */
export function reportPromptSubmitted(sessionId: string): void {
  window.dispatchEvent(new CustomEvent<ProjectRecencyEventDetail>(PROJECT_RECENCY_EVENT, {
    detail: { sessionId, reason: 'prompt_submitted' },
  }))
}
