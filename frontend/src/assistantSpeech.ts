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
  beginRequestedStream, cancelRequestedStream, claimRequestedStream, newVoiceStreamId,
  playRequestedStreamFirst, requestedStreamActive, unlockPlayback,
} from './voice.ts'

type VoiceClip = { id: string; stream_id?: string }

type Speech = {
  /** The turn this stream belongs to, or null for speech outside any turn. */
  turnId: string | null
  streamId: string
  /** Serializes appends; also the handle a caller awaits to know speech landed. */
  chain: Promise<void>
  /** False until the opening `speak` has succeeded, which decides `continue_stream`. */
  opened: boolean
  /**
   * Whether this turn has text on the way. Set when an append is *queued*, not
   * when its synthesis returns, because its only reader (`endTurnSpeech`) asks
   * the moment the turn ends - seconds before a Kokoro post comes back.
   */
  spoke: boolean
  closed: boolean
  /** Pending idle-close for a loose stream, cancelled by the next append. */
  idle: ReturnType<typeof setTimeout> | null
}

// A loose stream stays open this long after its last append, so a second
// out-of-turn utterance joins it instead of opening a stream of its own — and
// opening one is what cuts the previous utterance off.
const LOOSE_IDLE_MS = 1_500

let active: Speech | null = null

/** Visible for tests: the stream currently being spoken into, if any. */
export function activeTurnSpeech(): { turnId: string | null; streamId: string; spoke: boolean } | null {
  return active ? { turnId: active.turnId, streamId: active.streamId, spoke: active.spoke } : null
}

/**
 * Claim a stream for a turn about to speak. Cheap and side-effect-light: no
 * request is made until there is text, so a turn that stays silent (a read-only
 * answer rendered on screen with Talk off) costs nothing.
 */
export function beginTurnSpeech(turnId: string): void {
  retire()
  const streamId = newVoiceStreamId()
  unlockPlayback()
  // A new turn takes the floor: the operator asked something else, so the reply
  // they moved on from stops.
  beginRequestedStream(streamId, 'system', 'system')
  active = open(turnId, streamId)
}

function open(turnId: string | null, streamId: string): Speech {
  return {
    turnId, streamId, chain: Promise.resolve(),
    opened: false, spoke: false, closed: false, idle: null,
  }
}

/**
 * The stream still worth appending to, or null.
 *
 * Playback is the authority: anything that stops the assistant talking cuts the
 * claim, and a stream whose claim is gone is a stream nobody will hear. Checked
 * *before* choosing between appending and opening, so an utterance arriving
 * after a barge-in opens a fresh stream instead of being appended into silence.
 */
function liveStream(): Speech | null {
  const current = active
  if (!current || current.closed) return null
  if (!requestedStreamActive(current.streamId)) { retire(); return null }
  return current
}

/** Close whatever stream is open, without cutting audio that is still playing. */
function retire(): void {
  const previous = active
  active = null
  if (!previous || previous.closed) return
  previous.closed = true
  if (previous.idle) clearTimeout(previous.idle)
  if (previous.opened) void closeStream(previous.streamId)
  else cancelRequestedStream(previous.streamId)
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
 * Speak a confirmation card's announcement.
 *
 * **This never takes the floor.** It joins the turn that opened it when there is
 * one — the card and the sentences before it are one utterance. A card arriving
 * with no turn speaking (one whose turn already ended, a replayed view, a second
 * device) joins the loose stream, opening one only if none is live, and that
 * stream is claimed without interrupting. An announcement that cut whatever was
 * speaking is how the same sentence came to restart over and over instead of
 * being said once.
 */
export function speakAnnouncement(text: string): Promise<void> {
  const speech = text.trim()
  if (!speech) return Promise.resolve()
  // Any live stream will do, whichever turn it belongs to: the point is that
  // the operator hears both, in order, rather than the second cutting the first.
  // A card is new information, so unlike a turn's own sentences it is spoken on
  // a fresh stream when the previous one was cut rather than dropped with it.
  const current = liveStream()
  if (current) return append(current, speech)
  const streamId = newVoiceStreamId()
  unlockPlayback()
  claimRequestedStream(streamId, 'system', 'system')
  active = open(null, streamId)
  return append(active, speech)
}

/**
 * The turn is over. `fallback` is spoken only if the turn never said anything
 * else — the daemon's terse "Done." for a turn that was all tool calls, which
 * arrives on the completion event rather than as a sentence.
 */
export async function endTurnSpeech(turnId: string, fallback = ''): Promise<void> {
  const turn = active
  if (!turn || turn.turnId !== turnId || turn.closed) return
  if (!turn.spoke && fallback.trim()) await append(turn, fallback.trim()).catch(() => {})
  // Drained before closing, so a sentence still in flight is not cut off by the
  // close it should have preceded.
  await turn.chain.catch(() => {})
  if (active === turn) retire()
}

/** Abandon the running turn's speech without closing it gracefully. */
export function cancelTurnSpeech(turnId: string): void {
  if (!active || active.turnId !== turnId) return
  retire()
}

/**
 * Stop feeding whatever the assistant is currently saying.
 *
 * Playback already self-heals through the claim check, so this exists for the
 * cases that know sooner than playback does — a surface going away, a test
 * starting from a clean slate. It closes the stream rather than cutting audio;
 * silencing what is already playing is `bargeInPlayback`'s job.
 */
export function cancelAssistantSpeech(): void {
  retire()
}

/**
 * One self-contained utterance that supersedes whatever is being said: the
 * deterministic spoken verdict on a confirmation card, which is not a model turn
 * at all. Taking the floor is the point here — the operator has just answered
 * the card being read out, so finishing the question is worse than cutting it.
 */
export async function speakOnce(text: string): Promise<void> {
  const speech = text.trim()
  if (!speech) return
  retire()
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

/**
 * A loose stream closes shortly after its last append so the daemon is not left
 * holding it, but not immediately: a sibling utterance arriving right behind it
 * should join rather than open a stream of its own.
 */
function scheduleIdleClose(turn: Speech): void {
  if (turn.turnId !== null || turn.closed) return
  if (turn.idle) clearTimeout(turn.idle)
  turn.idle = setTimeout(() => {
    turn.idle = null
    if (turn.closed || active !== turn) return
    retire()
  }, LOOSE_IDLE_MS)
}

function append(turn: Speech, speech: string): Promise<void> {
  if (turn.idle) { clearTimeout(turn.idle); turn.idle = null }
  // Marked here, synchronously, rather than when the post returns. `spoke` is
  // read by `endTurnSpeech` to decide whether the completion fallback is still
  // needed, and that decision is made the moment the turn ends - which for a
  // one-sentence reply is milliseconds after the sentence was queued and
  // seconds before its synthesis comes back. Setting it after the await made
  // the guard read state the operation it guards against had not written yet,
  // and the turn said the whole reply twice (measured 2026-08-23: one sentence,
  // no card, two segments, 11.8s of audio for a 95-character sentence).
  //
  // "Has text on the way" is also what the flag means to its only reader. A
  // queued segment that never synthesizes is not a reason to speak the fallback:
  // if the stream died, the fallback would be dropped with it.
  turn.spoke = true
  const next = turn.chain.then(async () => {
    if (turn.closed && turn.opened) return
    // Playback is the authority on whether this stream still matters. Anything
    // that stops the assistant talking — barge-in, releasing the microphone,
    // read aloud switched off — cuts the claim, and continuing to post text
    // into a stream nobody will play just spends synthesis on silence.
    if (!requestedStreamActive(turn.streamId)) {
      turn.closed = true
      // Seal it rather than abandoning it. `retire` short-circuits on a turn
      // that is already closed, so without this the daemon keeps an open stream
      // with no producer until its idle drop, and — because a stream is only
      // joined when it closes — the sentences it DID synthesize never become one
      // clip. That is the difference between "cut off, but replayable from the
      // talk tab" and "cut off, and the audio is scattered across loose
      // segments". Fire-and-forget: `closeStream` swallows its own failures, and
      // nothing here is waiting on the answer.
      if (turn.opened) void closeStream(turn.streamId)
      return
    }
    if (!turn.opened) {
      const clip = await api<VoiceClip>('POST', '/api/voice/speak', {
        text: speech, stream_id: turn.streamId, final: false,
      })
      turn.opened = true
      // The response is the fallback for a `voice_clip_ready` that was lost or
      // delayed; the live event usually starts playback first and this dedupes.
      await playRequestedStreamFirst(clip.id, clip.stream_id || turn.streamId, 'system', 'system')
      scheduleIdleClose(turn)
      return
    }
    await api('POST', '/api/voice/speak', {
      text: speech, stream_id: turn.streamId, continue_stream: true, final: false,
    })
    scheduleIdleClose(turn)
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
