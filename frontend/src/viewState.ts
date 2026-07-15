export type FocusSession = { id: string; space_id: string; state: string }
export type ViewPreference = { spaceId: string | null; sessionId: string | null }
export type FocusMemory = { lastSpace: string | null; bySpace: Record<string, string> }

export const emptyFocusMemory = (): FocusMemory => ({ lastSpace: null, bySpace: {} })

export function parseViewPreference(search: string): ViewPreference {
  const params = new URLSearchParams(search)
  return { spaceId: params.get('space'), sessionId: params.get('session') }
}

export function parseFocusMemory(raw: string | null): FocusMemory {
  if (!raw) return emptyFocusMemory()
  try {
    const value = JSON.parse(raw) as Partial<FocusMemory>
    const bySpace = value.bySpace && typeof value.bySpace === 'object'
      ? Object.fromEntries(Object.entries(value.bySpace).filter((entry): entry is [string,string] => typeof entry[1] === 'string'))
      : {}
    return { lastSpace: typeof value.lastSpace === 'string' ? value.lastSpace : null, bySpace }
  } catch {
    return emptyFocusMemory()
  }
}

export function resolveInitialFocus(
  sessions: FocusSession[],
  spaceIds: string[],
  visibleBySpace: Record<string, string[]>,
  requested: ViewPreference,
  remembered: FocusMemory,
): { spaceId: string; sessionId: string | null } {
  const live = sessions.filter(session => !['exited', 'crashed'].includes(session.state))
  const requestedSession = live.find(session => session.id === requested.sessionId)
  if (requestedSession) return { spaceId: requestedSession.space_id, sessionId: requestedSession.id }

  const validSpace = (value: string | null) => value && spaceIds.includes(value) ? value : null
  const spaceId = validSpace(requested.spaceId) ?? validSpace(remembered.lastSpace) ?? spaceIds[0] ?? 'default'
  const candidates = [remembered.bySpace[spaceId], ...(visibleBySpace[spaceId] ?? [])]
  const sessionId = candidates.find(id => live.some(session => session.id === id && session.space_id === spaceId))
    ?? live.find(session => session.space_id === spaceId)?.id
    ?? null
  return { spaceId, sessionId }
}

export function focusMemoryWith(memory: FocusMemory, spaceId: string, sessionId: string | null): FocusMemory {
  const bySpace = { ...memory.bySpace }
  if (sessionId) bySpace[spaceId] = sessionId
  else delete bySpace[spaceId]
  return { lastSpace: spaceId, bySpace }
}

export function viewUrl(href: string, spaceId: string, sessionId: string | null): string {
  const url = new URL(href)
  url.searchParams.set('space', spaceId)
  if (sessionId) url.searchParams.set('session', sessionId)
  else url.searchParams.delete('session')
  return `${url.pathname}${url.search}${url.hash}`
}
