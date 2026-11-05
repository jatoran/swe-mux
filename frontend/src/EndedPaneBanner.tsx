import { rich, t } from './localization/index.ts'
import { checkpointAge, isInactiveSession, missingContentReason } from './coldSession.ts'
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
  /** True while this pane's own resume is in flight. The daemon proves a resumed pane
   *  stayed up before it answers, which takes seconds, and a button that looks untouched
   *  for that long reads as one that did not work. */
  resuming?: boolean
}

export function EndedPaneBanner(
  { session, onResume, onRestart, onOpenTranscript, resuming = false }: EndedPaneBannerProps,
) {
  const cold = session.cold === true
  const inactive = isInactiveSession(session)
  // The daemon stamps this only when it actually restored bytes, so its absence
  // *is* "this pane is empty" — the pane does not have to report back.
  const captured = session.cold_terminal_at
  const missing = cold && !captured ? missingContentReason(session) : null
  return <div class="cold-pane-banner" role="status">
    <strong>{inactive ? t("EndedPaneBanner.d1022618b9") : cold ? t("EndedPaneBanner.f6e09cc89f") : t("EndedPaneBanner.e87ba7a0b7")}</strong>
    <span>
      {inactive ? t("EndedPaneBanner.124f8939e1") : cold ? t("EndedPaneBanner.40af4d5d3b") : t("EndedPaneBanner.bc9aa1f7a8")}
    </span>
    {cold && captured ? <span>{rich("EndedPaneBanner.6f834a759f", { value1: checkpointAge(captured, Date.now()) })}</span> : null}
    {missing ? <span class="cold-pane-empty">{missing}</span> : null}
    {onOpenTranscript ? <button onClick={onOpenTranscript}>{t("EndedPaneBanner.09a94f0023")}</button> : null}
    {onResume ? <button disabled={resuming} onClick={onResume}>{resuming ? t("EndedPaneBanner.c494e3ca3e") : t("EndedPaneBanner.d640c7421d")}</button> : null}
    {onRestart ? <button disabled={resuming} onClick={onRestart}>{resuming ? t("EndedPaneBanner.75d0f1469d") : t("EndedPaneBanner.0c709e67bb")}</button> : null}
  </div>
}
