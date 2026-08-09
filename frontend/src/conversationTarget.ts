import type { EditorHandle, InsertTarget, TextSurfaceKind } from './insertTarget.ts'

export type VoiceSessionCandidate = {
  id: string
  label: string
  available: () => boolean
}

export type ConversationTarget =
  | {
      kind: 'session'
      id: string
      label: string
      available: () => boolean
    }
  | {
      kind: 'text'
      id: string
      label: string
      surface: TextSurfaceKind
      editor: EditorHandle
    }

/**
 * Resolve the current dictation sink from the same focus ledger used by injected text.
 * A text surface wins only while its real editor remains mounted; otherwise the focused
 * agent is the safe fallback. Shell terminals are omitted by the caller's candidate list.
 */
export function resolveConversationTarget(
  focused: InsertTarget | null,
  sessions: readonly VoiceSessionCandidate[],
  fallbackSessionId: string | null,
): ConversationTarget | null {
  if (
    focused?.kind === 'editor'
    && focused.surface
    && focused.editor.isConnected !== false
  ) {
    return {
      kind: 'text',
      id: focused.surface.id,
      label: focused.surface.label,
      surface: focused.surface.kind,
      editor: focused.editor,
    }
  }
  const sessionId = focused?.kind === 'terminal' ? focused.sessionId : fallbackSessionId
  const session = sessions.find(candidate => candidate.id === sessionId && candidate.available())
    || sessions.find(candidate => candidate.id === fallbackSessionId && candidate.available())
  return session
    ? { kind: 'session', id: session.id, label: session.label, available: session.available }
    : null
}

export function conversationTargetAvailable(target: ConversationTarget | null): boolean {
  if (!target) return false
  return target.kind === 'session' ? target.available() : target.editor.isConnected !== false
}

export function effectiveConversationTarget(
  following: ConversationTarget | null,
  pinned: ConversationTarget | null,
): ConversationTarget | null {
  return pinned || following
}

/** Pin toggles between the exact current sink and focus-following mode. */
export function toggleConversationTargetPin(
  pinned: ConversationTarget | null,
  current: ConversationTarget | null,
): ConversationTarget | null {
  return pinned ? null : current
}
