// Per-message read-aloud in the transcript reader: which reply has audio, in which
// kind, and what a request for one more would cost.
//
// Everything here is pure, and that is the point of it existing separately from
// `TranscriptTab.tsx`. The one rule this feature turns on - a reply that has already
// been spoken is *found*, not spoken again - is a lookup over the clip rows, and a
// lookup is worth being able to test without a drawer, a daemon, or an audio element.

import type { VoiceClip, VoiceContent } from './types'

/** The two kinds of audio one reply can have. Both are legitimate for the same message. */
export const TRANSCRIPT_AUDIO_KINDS: readonly VoiceContent[] = ['summary', 'verbatim'] as const

/**
 * What the reader draws beside one reply, per kind.
 *
 * - `none` - no audio for this message in this kind. Asking for it generates it.
 * - `generating` - this reader asked, and the request is in flight. Local to this tab:
 *   another device generating the same clip is not something this one can observe until
 *   the clip lands, and claiming otherwise would show a spinner that never resolves.
 * - `ready` - a clip exists and plays from the store.
 * - `failed` - synthesis was attempted and did not produce audio. Distinct from `none`
 *   because retrying is a decision, and because a summary that failed on a budget wall
 *   would otherwise re-spend on every click.
 */
export type TranscriptAudioState = 'none' | 'generating' | 'ready' | 'failed'

/** Every clip that speaks one message, keyed by kind. */
export type MessageAudio = Partial<Record<VoiceContent, VoiceClip>>

/**
 * Index a run's clips by the message they speak.
 *
 * Only anchored, non-abandoned clips can be indexed: application speech and every clip
 * written before the anchor existed have no message, and a run's clips are already
 * scoped by the query. Newest wins within a kind - a regenerated clip supersedes the one
 * it was asked to replace - and `voice_clips` is returned newest-first, so the first row
 * seen for a (message, kind) pair is the one to keep.
 *
 * A `synthesizing` row is reported as `generating` even though this tab did not ask for
 * it: the automatic path may be making exactly this clip right now, and a reader offered
 * a "generate" button for audio already in flight would pay for it twice.
 */
export function indexTranscriptAudio(clips: readonly VoiceClip[]): Map<string, MessageAudio> {
  const index = new Map<string, MessageAudio>()
  for (const clip of clips) {
    const anchor = clip.message_anchor
    if (!anchor) continue
    const kind = clip.content_mode
    const entry = index.get(anchor) || {}
    // Newest-first input, so an entry already present is the newer one. A failed or
    // in-flight newer row still supersedes an older ready one: it is the truth about
    // the most recent attempt, and that is what a marker reports.
    if (entry[kind]) continue
    entry[kind] = clip
    index.set(anchor, entry)
  }
  return index
}

/**
 * The marker for one (message, kind), given what the store holds and what this tab asked.
 *
 * A local request wins over `none` and over a stale `failed`, because the request is
 * newer than either; it does not win over a ready clip, since the clip that arrived is
 * the thing to play.
 */
export function transcriptAudioState(
  audio: MessageAudio | undefined,
  kind: VoiceContent,
  requested: boolean,
): TranscriptAudioState {
  const clip = audio?.[kind]
  if (clip?.status === 'ready') return 'ready'
  if (clip?.status === 'synthesizing' || requested) return 'generating'
  if (clip?.status === 'failed') return 'failed'
  return 'none'
}

/** The clip to play for one (message, kind), or `null` when there is nothing to play. */
export function transcriptAudioClip(
  audio: MessageAudio | undefined,
  kind: VoiceContent,
): VoiceClip | null {
  const clip = audio?.[kind]
  return clip && clip.status === 'ready' ? clip : null
}

/**
 * What pressing this marker would do, in one sentence, for the button's title.
 *
 * The spend is stated rather than implied: a summary is an LLM call against the daily
 * read-aloud budget, and verbatim never touches a model. A reader deciding between two
 * chips is exactly the moment that difference is worth knowing, and it is the only place
 * in the app where the choice is offered per message.
 */
export function transcriptAudioHint(state: TranscriptAudioState, kind: VoiceContent): string {
  const cost = kind === 'summary'
    ? 'a spoken summary (one model call, against the daily read-aloud budget)'
    : 'the reply read out verbatim (no model call)'
  switch (state) {
    case 'ready':
      return `Play ${kind === 'summary' ? 'the spoken summary' : 'the verbatim reading'} of this reply`
    case 'generating':
      return `Making ${cost}…`
    case 'failed':
      return `Making ${cost} failed. Click to try again.`
    case 'none':
      return `Speak this reply as ${cost}`
  }
}

/** The glyph a marker leads with. `▶` only when there is something to play. */
export const transcriptAudioMark = (state: TranscriptAudioState): string =>
  state === 'ready' ? '▶' : state === 'generating' ? '…' : state === 'failed' ? '!' : '♪'
