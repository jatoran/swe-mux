export type FocusSession = { id: string; project_id: string; state: string }
export type ViewPreference = { projectId: string | null; sessionId: string | null }
export type FocusMemory = { lastProject: string | null; byProject: Record<string, string> }

export const emptyFocusMemory = (): FocusMemory => ({ lastProject: null, byProject: {} })

export function parseViewPreference(search: string): ViewPreference {
  const params = new URLSearchParams(search)
  return { projectId: params.get('project'), sessionId: params.get('session') }
}

export function parseFocusMemory(raw: string | null): FocusMemory {
  if (!raw) return emptyFocusMemory()
  try {
    const value = JSON.parse(raw) as Partial<FocusMemory>
    const byProject = value.byProject && typeof value.byProject === 'object'
      ? Object.fromEntries(Object.entries(value.byProject).filter((entry): entry is [string,string] => typeof entry[1] === 'string'))
      : {}
    return { lastProject: typeof value.lastProject === 'string' ? value.lastProject : null, byProject }
  } catch {
    return emptyFocusMemory()
  }
}

export function resolveInitialFocus(
  sessions: FocusSession[],
  projectIds: string[],
  visibleByProject: Record<string, string[]>,
  requested: ViewPreference,
  remembered: FocusMemory,
): { projectId: string; sessionId: string | null } {
  const live = sessions.filter(session => !['exited', 'crashed'].includes(session.state))
  const requestedSession = live.find(session => session.id === requested.sessionId)
  if (requestedSession) return { projectId: requestedSession.project_id, sessionId: requestedSession.id }

  const validProject = (value: string | null) => value && projectIds.includes(value) ? value : null
  const projectId = validProject(requested.projectId) ?? validProject(remembered.lastProject) ?? projectIds[0] ?? ''
  const candidates = [remembered.byProject[projectId], ...(visibleByProject[projectId] ?? [])]
  const sessionId = candidates.find(id => live.some(session => session.id === id && session.project_id === projectId))
    ?? live.find(session => session.project_id === projectId)?.id
    ?? null
  return { projectId, sessionId }
}

export function focusMemoryWith(memory: FocusMemory, projectId: string, sessionId: string | null): FocusMemory {
  const byProject = { ...memory.byProject }
  if (sessionId) byProject[projectId] = sessionId
  else delete byProject[projectId]
  return { lastProject: projectId, byProject }
}

export function viewUrl(href: string, projectId: string, sessionId: string | null): string {
  const url = new URL(href)
  url.searchParams.set('project', projectId)
  if (sessionId) url.searchParams.set('session', sessionId)
  else url.searchParams.delete('session')
  return `${url.pathname}${url.search}${url.hash}`
}
