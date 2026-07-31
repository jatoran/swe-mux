/**
 * Input held while the terminal replays its scrollback.
 *
 * Re-attaching replays up to `attach_replay_bytes` (512 KiB by default) into xterm,
 * and the pane counts itself as replaying until every one of those writes has been
 * flushed. That window is short on a fresh session and seconds long on a deep one,
 * because a deep session is what actually fills the replay budget. Anything the user
 * sends inside it has to be held and flushed in order rather than raced against the
 * replayed bytes.
 *
 * The ceiling exists only to stop a replay that never completes (socket died
 * mid-stream) from accumulating input forever. It is not a judgement about how much
 * the user is allowed to send, which is why it sits far above any realistic paste:
 * at 4 KiB it silently swallowed ordinary pastes, and did so more often the longer a
 * session had been running, since that is exactly when the replay window is longest.
 */
export const MAX_PENDING_INPUT_CHARS = 1024 * 1024

export type PendingInputDecision = 'hold' | 'overflow'

/** Whether more input can be held, or the hold buffer is past its ceiling. */
export function pendingInputDecision(
  heldChars: number,
  incomingChars: number,
  limit: number = MAX_PENDING_INPUT_CHARS,
): PendingInputDecision {
  return heldChars + incomingChars > limit ? 'overflow' : 'hold'
}
