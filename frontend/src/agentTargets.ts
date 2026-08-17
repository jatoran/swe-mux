// Which sessions a note/markdown selection may be sent to, and how one is named in the
// dialog's radio group.
//
// Pure so the filtering rules can be unit-tested: they are the part of send-to-agent that can
// quietly do the wrong thing (deliver into a dead session, into another project's session, or
// into a shell that would execute the prose as commands).

import type { Project, Session } from './types'
import { deliversHarnessPrompts, promptDeliveryHarnesses } from './harnessRegistry.ts'
import { sessionDisplayName } from './sessionNames.ts'

export type NewAgentBackend = string

export const NEW_TARGET_PREFIX = 'new:'
export const SESSION_TARGET_PREFIX = 'session:'

export const newTargetKey = (backend: NewAgentBackend) => `${NEW_TARGET_PREFIX}${backend}`
export const sessionTargetKey = (sessionId: string) => `${SESSION_TARGET_PREFIX}${sessionId}`

/** The session id a key names, or null when the key means "start a new session". */
export function sessionIdFromTargetKey(key: string): string | null {
  return key.startsWith(SESSION_TARGET_PREFIX) ? key.slice(SESSION_TARGET_PREFIX.length) : null
}

/** The backend a "new session" key names, falling back to the first deliverable harness. */
export function backendFromTargetKey(key: string): NewAgentBackend {
  const candidate = key.startsWith(NEW_TARGET_PREFIX) ? key.slice(NEW_TARGET_PREFIX.length) : ''
  return deliversHarnessPrompts(candidate) ? candidate : promptDeliveryHarnesses()[0]?.name || ''
}

/**
 * Live agent sessions of one project, most recently active first.
 *
 * Shell sessions are excluded on purpose: an agent composer holds a paste inert until the human
 * submits it, while a shell would run whatever arrived. Ended and still-spawning sessions are
 * excluded because neither can receive input.
 */
export function agentTargets(sessions: Session[], projectId: string): Session[] {
  return sessions
    .filter(
      session =>
        session.project_id === projectId &&
        !session.pending &&
        deliversHarnessPrompts(session.backend) &&
        session.state !== 'exited' &&
        session.state !== 'crashed',
    )
    .sort((a, b) => (b.last_activity_ts || 0) - (a.last_activity_ts || 0))
}

/** A new session follows the project's own backend, falling back to the first harness: this dialog only
 *  ever starts agents, so a shell-defaulted project still gets an agent. */
export function defaultNewTarget(project: Project | undefined): string {
  const backend = project?.effective_options?.backend || project?.default_backend
  const selected = backend && deliversHarnessPrompts(backend) ? backend : promptDeliveryHarnesses()[0]?.name || ''
  return newTargetKey(selected)
}

/**
 * The target to hold after the project selector moves. A session belonging to the project the
 * user just left is never a valid target, so it falls back to that project's new-session
 * default instead of silently pointing somewhere else.
 */
export function retargetForProject(
  current: string,
  candidates: Session[],
  project: Project | undefined,
): string {
  const sessionId = sessionIdFromTargetKey(current)
  if (!sessionId) return current
  return candidates.some(session => session.id === sessionId) ? current : defaultNewTarget(project)
}

/** Same rule as the sidebar, which is `sessionDisplayName` and lives in `sessionNames.ts`. */
export function agentTargetName(session: Session): string {
  return sessionDisplayName(session)
}
