import type { Session, SessionState } from './types'

// Single source of truth for rendering the user-visible session status.
// Desktop sidebar rows, pane headers, tab strips, and the mobile unified-tab
// projection all render through these functions — no surface may keep an
// independent heuristic. tests/sessionStatus.test.ts asserts the mapping is
// total: every SessionState renders a non-empty label and dot class.

const STATE_DOT: Record<SessionState, string> = {
  starting: 'starting',
  running: 'running',
  working: 'working',
  idle: 'idle',
  awaiting: 'awaiting',
  exited: 'exited',
  crashed: 'crashed',
}

/** CSS class suffix for the colored state dot; total over SessionState. */
export function stateDotClass(state: SessionState | undefined): string {
  return `state-dot ${state ? STATE_DOT[state] ?? 'running' : 'running'}`
}

/**
 * Awaiting label by typed sub-reason. `idle_prompt`-driven idle never reaches
 * here (it maps to state 'idle'), so a finished agent can never render as an
 * approval. Unknown/missing sub-reason falls back to the generic approval
 * affordance (the conservative historical default).
 */
export function awaitingLabel(session: Session): string {
  switch (session.awaiting_reason) {
    case 'question': return 'awaiting answer'
    case 'elicitation': return 'awaiting input'
    case 'rate_limit': return 'rate limited'
    case 'approval':
    default: return 'awaiting approval'
  }
}

function isAgentBackend(session: Session): boolean {
  return session.backend === 'claude' || session.backend === 'codex'
}

/** Status line text shown next to a session; total over SessionState. */
export function sessionStatus(session: Session): string {
  if (!isAgentBackend(session)) return session.state
  const context = session.context_pct > 0 ? ` · ${Math.round(session.context_pct * 100)}%` : ''
  const compactions = session.compaction_count > 0 ? ` · compacted ${session.compaction_count}×` : ''
  if (session.state === 'working') return `working${session.state_detail ? ` · ${session.state_detail}` : ''}${context}${compactions}`
  if (session.state === 'idle') return `ready · turn complete${context}${compactions}`
  if (session.state === 'awaiting') return `${awaitingLabel(session)}${session.state_detail ? ` · ${session.state_detail}` : ''}${context}${compactions}`
  if (session.state === 'starting') return 'starting agent…'
  return `${session.state}${context}${compactions}`
}
