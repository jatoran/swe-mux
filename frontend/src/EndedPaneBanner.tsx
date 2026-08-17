import { checkpointAge, missingContentReason } from './coldSession.ts'
import type { Session } from './types'

/**
 * The strip above a pane whose session has no process behind it.
 *
 * Ended panes stay open now, so a pane can be showing bytes from a session that
 * cannot answer — and the operator needs to know that before they type. For a
 * recovered session it also has to account for content that is missing on
 * purpose: an alternate-screen or repaint-heavy harness is excluded from
 * checkpointing because its retained bytes are a differential frame stream that
 * reconstructs to a blank or half-drawn screen, and repairing that needs a live
 * child to pulse. For those the real recovery is the conversation transcript, so
 * that is what this points at.
 *
 * Wording and age formatting live in `coldSession.ts`, where they are tested.
 */
export type EndedPaneBannerProps = {
  session: Session
  onResume?: () => void
  onRestart?: () => void
  onOpenTranscript?: () => void
}

export function EndedPaneBanner(
  { session, onResume, onRestart, onOpenTranscript }: EndedPaneBannerProps,
) {
  const cold = session.cold === true
  // The daemon stamps this only when it actually restored bytes, so its absence
  // *is* "this pane is empty" — the pane does not have to report back.
  const captured = session.cold_terminal_at
  const missing = cold && !captured ? missingContentReason(session) : null
  return <div class="cold-pane-banner" role="status">
    <strong>{cold ? 'recovered' : 'ended'}</strong>
    <span>
      {cold
        ? 'This session was recovered after an unexpected shutdown. Its process is gone.'
        : 'This session has ended. The pane is read-only.'}
    </span>
    {cold && captured
      ? <span>Terminal content is from {checkpointAge(captured, Date.now())}.</span>
      : null}
    {missing ? <span class="cold-pane-empty">{missing}</span> : null}
    {onOpenTranscript ? <button onClick={onOpenTranscript}>Read transcript</button> : null}
    {onResume ? <button onClick={onResume}>Resume as new…</button> : null}
    {onRestart ? <button onClick={onRestart}>Restart terminal</button> : null}
  </div>
}
