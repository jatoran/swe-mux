// One reply's audio as a single timeline, however many segments it is stored in.
//
// A reply is synthesized in segments so its first sentence can play while the rest
// is still being made, and once the stream completes the daemon joins them into one
// file. Between those two moments a clip is several files, and every player control
// still has to behave as though it were one: one duration, one position, one scrub
// bar that crosses a segment boundary without the operator ever learning there was
// one.
//
// Everything here is pure arithmetic over the part list, deliberately separate from
// `voice.ts`. The audio element makes this hard to test and the arithmetic is where
// the off-by-one lives, so the arithmetic is tested on its own.

import type { VoiceClip, VoiceClipPart } from './types'

/** One part placed on the group's timeline. */
export type PartSpan = {
  id: string
  /** Seconds from the start of the reply to the start of this part. */
  start: number
  /** This part's own length, 0 when nothing has reported one yet. */
  duration: number
}

const partDuration = (part: VoiceClipPart): number =>
  typeof part.duration_hint_s === 'number' && part.duration_hint_s > 0 ? part.duration_hint_s : 0

/**
 * The parts of a clip, in spoken order.
 *
 * A clip with no `parts` is its own single part - a joined clip, a clip made before
 * streaming, anything the daemon hands back without the breakdown - so callers never
 * branch on whether a reply happens to be segmented right now.
 */
export function clipParts(clip: VoiceClip): VoiceClipPart[] {
  if (clip.parts && clip.parts.length) return clip.parts
  return [{
    id: clip.id,
    segment_index: 0,
    status: clip.status,
    duration_hint_s: clip.duration_hint_s ?? null,
    size_bytes: clip.size_bytes,
    error: clip.error ?? null,
  }]
}

export const clipPartIds = (clip: VoiceClip): string[] => clipParts(clip).map(part => part.id)

/** The parts laid end to end. `liveDuration` overrides the playing part's hint. */
export function partSpans(
  clip: VoiceClip,
  playingId: string | null = null,
  liveDuration = 0,
): PartSpan[] {
  const spans: PartSpan[] = []
  let start = 0
  for (const part of clipParts(clip)) {
    // The element knows the playing part's real length; the daemon's hint is
    // rounded and is only an estimate at all for a failed measurement. Using the
    // live value keeps the bar from jumping when a part starts.
    const duration = part.id === playingId && liveDuration > 0 ? liveDuration : partDuration(part)
    spans.push({ id: part.id, start, duration })
    start += duration
  }
  return spans
}

export const spansDuration = (spans: readonly PartSpan[]): number =>
  spans.reduce((total, span) => total + span.duration, 0)

/**
 * Where the reply is, given which part is playing and how far into it.
 *
 * Returns null when the playing clip is not part of this group at all, which is
 * how a caller tells "this row is the one being played" from "this row is a row".
 */
export function groupPosition(
  spans: readonly PartSpan[],
  playingId: string | null,
  positionInPart: number,
): number | null {
  const span = spans.find(item => item.id === playingId)
  if (!span) return null
  return span.start + Math.max(0, positionInPart)
}

/**
 * Which part covers this point on the reply's timeline, and how far into it.
 *
 * Clamped at both ends rather than returning null: a scrub bar hands over whatever
 * value the pointer landed on, including exactly the duration, and the answer there
 * is the end of the last part - not "nothing".
 */
export function partAtTime(
  spans: readonly PartSpan[],
  seconds: number,
): { id: string; offset: number; index: number } | null {
  if (!spans.length) return null
  // Nothing has reported a length yet, so every span sits at zero and the scan
  // below would fall through to the last one - starting a reply at its ending.
  // A reply nobody can place is played from its beginning.
  if (spansDuration(spans) <= 0) return { id: spans[0].id, offset: 0, index: 0 }
  const target = Math.max(0, seconds)
  for (const [index, span] of spans.entries()) {
    const end = span.start + span.duration
    if (target < end || index === spans.length - 1) {
      return { id: span.id, offset: Math.max(0, Math.min(target - span.start, span.duration)), index }
    }
  }
  return null
}
