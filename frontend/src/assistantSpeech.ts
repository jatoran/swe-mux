/**
 * Speaking one assistant turn as it is written.
 *
 * The daemon publishes a turn as `assistant_sentence` events and finishes with
 * `assistant_turn_done`. Speaking only at the end meant the operator waited for
 * the model to stop *and* for the whole reply to be synthesized — measured at
 * three to fourteen seconds of silence — before hearing a word. Here each
 * sentence is posted into one open speech stream as it lands, so the first
 * sentence is already playing while the model writes the second.
 *
 * Two invariants hold the design together:
 *
 * - **One stream per turn.** Everything a turn says (its sentences and any
 *   confirmation card it opens) is appended to the same stream, so nothing a
 *   turn says can cut off something else the same turn said. Starting a stream
 *   halts the previous turn's audio, which is correct: a new turn supersedes
 *   the reply the operator has moved on from.
 * - **Posts are serialized.** Segment order on the daemon is the order its
 *   `speak` calls arrive, so two concurrent posts could invert two sentences.
 *   Every append is chained behind the previous one.
 */
import { api } from './api.ts'
import {
  beginRequestedStream, cancelRequestedStream, newVoiceStreamId,
  playRequestedStreamFirst, unlockPlayback,
} from './voice'

type VoiceClip = { id: string; stream_id?: string }

type TurnSpeech = {
  turnId: string
  streamId: string
  /** Serializes appends; also the handle a caller awaits to know speech landed. */
  chain: Promise<void>
  /** False until the opening `speak` has succeeded, which decides `continue_stream`. */
  opened: boolean
  spoke: boolean
  closed: boolean
}

let active: TurnSpeech | null = null

/** Visible for tests: the turn currently being spoken, if any. */
export function activeTurnSpeech(): { turnId: string; streamId: string; spoke: boolean } | null {
  return active ? { turnId: active.turnId, streamId: active.streamId, spoke: active.spoke } : null
}

/**
 * Claim a stream for a turn about to speak. Cheap and side-effect-light: no
 * request is made until there is text, so a turn that stays silent (a read-only
 * answer rendered on screen with Talk off) costs nothing.
 */
export function beginTurnSpeech(turnId: string): void {
  if (active && !active.closed) void closeStream(active.streamId)
  const streamId = newVoiceStreamId()
  unlockPlayback()
  beginRequestedStream(streamId, 'system', 'system')
  active = { turnId, streamId, chain: Promise.resolve(), opened: false, spoke: false, closed: false }
}

/** Append one sentence of the running turn. Ignored once the turn has closed. */
export function speakTurnText(turnId: string, text: string): Promise<void> {
  const speech = text.trim()
  if (!speech) return Promise.resolve()
  const turn = active
  if (!turn || turn.turnId !== turnId || turn.closed) return Promise.resolve()
  return append(turn, speech)
}

/**
 * Speak a confirmation card's announcement. It joins the turn that opened it
 * when there is one — the card and the sentences before it are one utterance,
 * and a separate stream would cut the sentences off mid-word. A card that
 * arrives with no turn speaking (a replayed view, a second device) opens its
 * own one-shot stream.
 */
export function speakAnnouncement(turnId: string, text: string): Promise<void> {
  const speech = text.trim()
  if (!speech) return Promise.resolve()
  const turn = active
  if (turn && turn.turnId === turnId && !turn.closed) return append(turn, speech)
  return speakOnce(speech)
}

/**
 * The turn is over. `fallback` is spoken only if the turn never said anything
 * else — the daemon's terse "Done." for a turn that was all tool calls, which
 * arrives on the completion event rather than as a sentence.
 */
export async function endTurnSpeech(turnId: string, fallback = ''): Promise<void> {
  const turn = active
  if (!turn || turn.turnId !== turnId || turn.closed) return
  if (!turn.spoke && fallback.trim()) await append(turn, fallback.trim())
  turn.closed = true
  const streamId = turn.streamId
  const chain = turn.chain
  if (active === turn) active = null
  await chain.catch(() => {})
  if (!turn.opened) {
    // Nothing was ever spoken, so the daemon has no stream to close; only this
    // tab's claim needs releasing, which `beginRequestedStream` will do for the
    // next turn anyway.
    cancelRequestedStream(streamId)
    return
  }
  await closeStream(streamId)
}

/** Abandon the running turn's speech without closing it gracefully. */
export function cancelTurnSpeech(turnId: string): void {
  const turn = active
  if (!turn || turn.turnId !== turnId) return
  turn.closed = true
  active = null
  if (turn.opened) void closeStream(turn.streamId)
  else cancelRequestedStream(turn.streamId)
}

/**
 * One self-contained utterance outside any turn: the deterministic spoken
 * verdict on a confirmation card, which is not a model turn at all.
 */
export async function speakOnce(text: string): Promise<void> {
  const speech = text.trim()
  if (!speech) return
  const streamId = newVoiceStreamId()
  unlockPlayback()
  beginRequestedStream(streamId, 'system', 'system')
  try {
    const clip = await api<VoiceClip>('POST', '/api/voice/speak', { text: speech, stream_id: streamId })
    await playRequestedStreamFirst(clip.id, clip.stream_id || streamId, 'system', 'system')
  } catch (cause) {
    cancelRequestedStream(streamId)
    throw cause
  }
}

function append(turn: TurnSpeech, speech: string): Promise<void> {
  const next = turn.chain.then(async () => {
    if (turn.closed && turn.opened) return
    if (!turn.opened) {
      const clip = await api<VoiceClip>('POST', '/api/voice/speak', {
        text: speech, stream_id: turn.streamId, final: false,
      })
      turn.opened = true
      turn.spoke = true
      // The response is the fallback for a `voice_clip_ready` that was lost or
      // delayed; the live event usually starts playback first and this dedupes.
      await playRequestedStreamFirst(clip.id, clip.stream_id || turn.streamId, 'system', 'system')
      return
    }
    await api('POST', '/api/voice/speak', {
      text: speech, stream_id: turn.streamId, continue_stream: true, final: false,
    })
    turn.spoke = true
  })
  // The chain must survive a failed segment: one clip that could not be
  // synthesized should not silence the rest of the reply.
  turn.chain = next.catch(() => {})
  return next
}

async function closeStream(streamId: string): Promise<void> {
  try {
    await api('POST', '/api/voice/speak', { text: '', stream_id: streamId, final: true })
  } catch {
    /* the stream expires on its own; nothing is left waiting on it */
  }
}
