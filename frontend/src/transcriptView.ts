// The drawer's Transcript tab: shared types, its scroll memory, and the pure
// helpers behind reading and copying.
//
// Split out of the component because the drawer unmounts a tab body on every tab
// switch. Anything that has to survive that — the scroll position, above all —
// cannot live in the component's own state, and everything here is testable
// under the plain node runner precisely because none of it renders.

/** One side of the conversation, already merged and filtered by the daemon. */
export type TranscriptMessage = { ordinal: number; role: 'user' | 'assistant'; ts?: string; text: string }

/** Why there is nothing to read. `null` means the transcript loaded. */
export type TranscriptReason = 'not_agent' | 'no_transcript' | 'unreadable' | null

export type SessionTranscript = {
  session_id: string
  agent_run_id?: string | null
  backend: string
  observation_stale_since?: number | null
  messages: TranscriptMessage[]
  /** Records the daemon classified as CLI machinery rather than conversation. */
  hidden: number
  truncated: boolean
  reason: TranscriptReason
}

/** Dispatched by the events socket when a session finishes a turn, which is the
 *  only moment its conversation can have gained a message. */
export const TURN_ENDED_EVENT = 'mux:turn-ended'

/** A reader lands at the newest message; only a deliberate scroll up overrides that. */
export const TRANSCRIPT_BOTTOM_SLACK = 48

/** Above this many characters a message is folded behind "Show more".
 *
 * A character count rather than a measured height: the drawer is a 300–620px
 * column whose body is unmounted and remounted on every tab switch, so measuring
 * would mean a layout pass per message per mount to answer a question a threshold
 * answers well enough. Set where a long-but-ordinary reply still shows whole and a
 * dumped file listing does not eat the scrollbar. */
export const TRANSCRIPT_CLAMP_CHARS = 1200

export const transcriptClamped = (text: string): boolean => text.length > TRANSCRIPT_CLAMP_CHARS

/**
 * Whether the view is close enough to the bottom to keep following new messages.
 *
 * The slack matters more than it looks: a browser's fractional scroll metrics mean
 * `scrollTop + clientHeight` lands a pixel or two short of `scrollHeight` while
 * visually pinned to the bottom, and an exact comparison would drop the follow the
 * first time an agent replied.
 */
export function isPinnedToBottom(scrollTop: number, scrollHeight: number, clientHeight: number): boolean {
  return scrollHeight - (scrollTop + clientHeight) <= TRANSCRIPT_BOTTOM_SLACK
}

// One slot, not a map. The rule is "keep my place while I am on this session, and
// start at the newest message when I move to another one", so remembering more than
// the current session would be remembering something nobody asked to keep — and a
// map keyed by session id is a leak that grows for the life of the tab.
let scrollMemory: { sessionId: string; offset: number } | null = null

export function rememberTranscriptScroll(sessionId: string, offset: number): void {
  scrollMemory = { sessionId, offset }
}

/** The remembered offset for this session, or `null` to open at the newest message. */
export function recallTranscriptScroll(sessionId: string): number | null {
  return scrollMemory?.sessionId === sessionId ? scrollMemory.offset : null
}

export function forgetTranscriptScroll(): void {
  scrollMemory = null
}

/** What the copy button puts on the clipboard: the message, and nothing else.
 *
 * No role prefix and no timestamp, because a copied reply is nearly always on its
 * way into a prompt, an issue, or a note, where a `agent:` prefix is something the
 * user then has to delete. The whole-conversation copy below is the one that needs
 * speakers, since without them it is unreadable. */
export function transcriptMessageText(message: TranscriptMessage): string {
  return message.text
}

export const transcriptSpeaker = (role: TranscriptMessage['role']): string => role === 'assistant' ? 'agent' : 'you'

export function transcriptConversationText(messages: TranscriptMessage[]): string {
  return messages.map(message => `${transcriptSpeaker(message.role)}: ${message.text}`).join('\n\n')
}

/** The sentence shown in place of a conversation, per daemon-reported reason. */
export function transcriptEmptyMessage(reason: TranscriptReason, backend?: string): string {
  if (reason === 'not_agent') return backend === 'shell'
    ? 'This is a shell session, so it has no agent conversation to read.'
    : 'This session has no agent conversation to read.'
  if (reason === 'no_transcript') return 'This agent has not written its first message yet.'
  if (reason === 'unreadable') return 'The native transcript could not be read just now. It will retry on the next turn.'
  return 'Nothing has been said in this conversation yet.'
}
