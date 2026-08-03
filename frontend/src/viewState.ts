export type FocusSession = { id: string; project_id: string; state: string }
export type ViewPreference = { projectId: string | null; sessionId: string | null }
// `byProject` remembers the active terminal per project (used to resolve the
// session on load); `viewByProject` remembers the last focused *view* — which may
// be a note or file, not a terminal — so selecting a project restores exactly what
// was last looked at there rather than defaulting to whatever tab sorts first.
export type FocusMemory = { lastProject: string | null; byProject: Record<string, string>; viewByProject: Record<string, string> }

export const emptyFocusMemory = (): FocusMemory => ({ lastProject: null, byProject: {}, viewByProject: {} })

export function parseViewPreference(search: string): ViewPreference {
  const params = new URLSearchParams(search)
  return { projectId: params.get('project'), sessionId: params.get('session') }
}

export function parseFocusMemory(raw: string | null): FocusMemory {
  if (!raw) return emptyFocusMemory()
  try {
    const value = JSON.parse(raw) as Partial<FocusMemory>
    const stringMap = (input: unknown): Record<string, string> =>
      input && typeof input === 'object'
        ? Object.fromEntries(Object.entries(input as Record<string, unknown>).filter((entry): entry is [string,string] => typeof entry[1] === 'string'))
        : {}
    return {
      lastProject: typeof value.lastProject === 'string' ? value.lastProject : null,
      byProject: stringMap(value.byProject),
      viewByProject: stringMap(value.viewByProject),
    }
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

/** What focus should become once the layout has settled, given a view we asked for.
 *
 * Flows that spawn a pane on the daemon — history resume, branch, second opinion —
 * learn the new leaf's id from the response but only learn *where it sits* on the next
 * layout refresh. Calling for that focus immediately is not enough on its own: until
 * the refresh lands the id names nothing in the layout, and reconciliation reasonably
 * reads that as stale focus and falls back to the pane's current tab. The resumed
 * session then opens behind the tab you started from, which is the whole bug.
 *
 * So a request outlives the refresh it is waiting for — however many that takes, which
 * matters because a refresh already in flight can have been snapshotted before the
 * spawn committed. It is dropped only when something else deliberately lands on a real
 * leaf while we wait (a tab click, a project switch): that is a choice the user just
 * made, and it outranks a request the layout still cannot satisfy. Without that, the
 * pane would jump out from under them when the refresh finally arrived.
 *
 * `null` means "leave focus alone"; `keepRequest` says whether the request is still
 * outstanding. Pure so the ordering can be tested without a layout tree or a clock.
 */
export function reconcileFocusView(
  { requested, focused, hasRoot, holdsRequested, holdsFocused, firstPaneActive }: {
    requested: string | null
    focused: string | null
    hasRoot: boolean
    holdsRequested: boolean
    holdsFocused: boolean
    firstPaneActive: string | null
  },
): { focus: string | null; keepRequest: boolean } {
  if (requested) {
    if (holdsRequested) return { focus: requested, keepRequest: false }
    // Still waiting. Hold focus wherever it is rather than falling back, or the
    // fallback becomes the answer and the arriving pane never gets focus.
    if (focused === requested || !focused || !holdsFocused) return { focus: focused, keepRequest: true }
    return { focus: focused, keepRequest: false }
  }
  if (!hasRoot) return { focus: null, keepRequest: false }
  if (focused && holdsFocused) return { focus: focused, keepRequest: false }
  return { focus: firstPaneActive, keepRequest: false }
}

export function focusMemoryWith(memory: FocusMemory, projectId: string, sessionId: string | null, viewId: string | null = null): FocusMemory {
  const byProject = { ...memory.byProject }
  if (sessionId) byProject[projectId] = sessionId
  else delete byProject[projectId]
  const viewByProject = { ...memory.viewByProject }
  if (viewId) viewByProject[projectId] = viewId
  else delete viewByProject[projectId]
  return { lastProject: projectId, byProject, viewByProject }
}

// The view remembered for a project on load/select. May be a note or file id, so the
// caller validates it against the live layout before focusing and falls back if gone.
export function rememberedView(memory: FocusMemory, projectId: string): string | null {
  return memory.viewByProject[projectId] ?? null
}

export function viewUrl(href: string, projectId: string, sessionId: string | null): string {
  const url = new URL(href)
  url.searchParams.set('project', projectId)
  if (sessionId) url.searchParams.set('session', sessionId)
  else url.searchParams.delete('session')
  return `${url.pathname}${url.search}${url.hash}`
}
