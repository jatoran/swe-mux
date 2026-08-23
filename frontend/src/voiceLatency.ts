/**
 * Stage timing for the spoken-command path, end of speech to executed action.
 *
 * The browser is the only party that knows when speech actually stopped, and the
 * daemon is the only party that can separate queueing from decoding, so a sample is
 * assembled from both halves: the daemon returns its two stages on the transcribe
 * response and the client posts the merged record to `/api/voice/stt-latency`, which
 * stores it and writes it to `daemon.log`.
 *
 * Field names match `LATENCY_FIELDS` in `src/swe_mux/voice.py` — the object built
 * here *is* the stored shape, so a rename has to happen on both sides.
 */

/** What `PersistentVoiceCapture` hands over with each finished utterance. */
export type CaptureMarks = {
  utteranceId: string
  /** `performance.now()` of the last frame judged to be speech. */
  speechEndAt: number
  /** `performance.now()` when the endpoint fired. */
  finishedAt: number
  /** `performance.now()` when the WAV was ready to send. */
  encodedAt: number
  /** Captured audio length, milliseconds. */
  audioMs: number
  /** True while a decode started before the endpoint was certain. */
  speculative?: boolean
  /** Playback was active when speech began, activating the origin-specific echo policy. */
  playbackAtStart?: boolean
  /** Trusted app speech can accept read-only navigation; agent speech cannot. */
  playbackOriginAtStart?: 'agent' | 'system' | null
  /**
   * Capture proved this was a person and not the speaker, by muting playback and
   * then demanding clean speech frames against the silence.
   *
   * It travels with the utterance because the echo policy above is a *guess* made
   * from "audio was playing", and this is the measurement that settles the same
   * question. Without it the router refused a barge-in it had already confirmed:
   * the operator interrupted, spoke a full sentence, watched it transcribe, and
   * got "Playback command ignored" (2026-08-23).
   */
  bargeInConfirmed?: boolean
}

/** The daemon's half, echoed back on the transcribe response. */
export type ServerTimings = {
  audio_ms?: number
  queue_ms?: number
  decode_ms?: number
  server_ms?: number
  engine?: string
  model?: string
  beam_size?: number
}

export type LatencySample = {
  utterance_id: string
  audio_ms: number
  endpoint_ms: number
  encode_ms: number
  wait_ms: number
  upload_ms: number
  queue_ms: number
  decode_ms: number
  action_ms: number
  total_ms: number
  speculative: boolean
  /** The matched command, or '' for dictation. The exit criterion is about commands. */
  command: string
  engine: string
  model: string
}

export type LatencyStages = { to_post_ms: number; to_decode_ms: number; decode_ms: number; action_ms: number }

const clamp = (value: number): number => (Number.isFinite(value) ? Math.max(0, Math.round(value * 10) / 10) : 0)

export type SampleInput = {
  marks: CaptureMarks
  /** `performance.now()` when the POST was issued. */
  postAt: number
  /** `performance.now()` when the response body had been parsed. */
  responseAt: number
  /** `performance.now()` after the command ran or the draft updated. */
  actionAt: number
  server: ServerTimings
  command?: string
}

export function buildLatencySample(input: SampleInput): LatencySample {
  const { marks, postAt, responseAt, actionAt, server } = input
  const serverMs = clamp(server.server_ms || 0)
  const endpoint_ms = clamp(marks.finishedAt - marks.speechEndAt)
  const encode_ms = clamp(marks.encodedAt - marks.finishedAt)
  const wait_ms = clamp(postAt - marks.encodedAt)
  // Transport is what is left of the round trip once the daemon's own time is
  // removed. Clamped rather than allowed negative: the two clocks are different
  // and rounding alone can put a loopback request slightly under zero.
  const upload_ms = clamp(responseAt - postAt - serverMs)
  const queue_ms = clamp(server.queue_ms || 0)
  const decode_ms = clamp(server.decode_ms || 0)
  const total_ms = clamp(actionAt - marks.speechEndAt)
  return {
    utterance_id: marks.utteranceId,
    audio_ms: clamp(server.audio_ms ?? marks.audioMs),
    endpoint_ms,
    encode_ms,
    wait_ms,
    upload_ms,
    queue_ms,
    decode_ms,
    // The residual, not `actionAt - responseAt`. The text exists the moment decode
    // ends, so everything after that — the daemon serializing its response, the
    // reply crossing the network, the matcher, the command — belongs to this stage.
    // Taking it as the remainder is also what keeps the four stages summing to the
    // total, so a cost can never quietly fall out of the breakdown entirely.
    action_ms: clamp(total_ms - endpoint_ms - encode_ms - wait_ms - upload_ms - queue_ms - decode_ms),
    total_ms,
    speculative: !!marks.speculative,
    command: input.command || '',
    engine: server.engine || '',
    model: server.model || '',
  }
}

/** The four reported stages. Mirrors `LATENCY_STAGES` in `voice.py`. */
export function latencyStages(sample: LatencySample): LatencyStages {
  return {
    to_post_ms: clamp(sample.endpoint_ms + sample.encode_ms + sample.wait_ms),
    to_decode_ms: clamp(sample.upload_ms + sample.queue_ms),
    decode_ms: clamp(sample.decode_ms),
    action_ms: clamp(sample.action_ms),
  }
}

/** Order and labels for the four stages, so every surface names them identically. */
export const LATENCY_STAGE_LABELS: { key: keyof LatencyStages; label: string; hint: string }[] = [
  { key: 'to_post_ms', label: 'endpoint → sent', hint: 'Trailing-silence wait, WAV encode, and any queueing behind an earlier utterance.' },
  { key: 'to_decode_ms', label: 'sent → decoding', hint: 'Transport plus the daemon queueing before decode starts. A cold model load lands here.' },
  { key: 'decode_ms', label: 'decoding', hint: 'Whisper turning audio into text.' },
  { key: 'action_ms', label: 'text → action', hint: 'Matching the command and running it, or appending to the draft.' },
]

/** What `GET /api/voice/stt-latency` returns. Mirrors `latency_report` in `voice.py`. */
export type LatencyStat = { p50: number; p95: number; max: number }
export type LatencyReportPayload = {
  count: number
  stages: Record<keyof LatencyStages, LatencyStat>
  total_ms: LatencyStat
  command_total_ms: { count: number; p50: number; p95: number }
  recent: (LatencySample & { at: number; stages: LatencyStages })[]
}

/** One compact line for a status surface: the total and where it went. */
export function formatLatency(sample: LatencySample): string {
  const stages = latencyStages(sample)
  const round = (value: number) => Math.round(value)
  return `${round(sample.total_ms)} ms — endpoint ${round(stages.to_post_ms)} · send ${round(stages.to_decode_ms)} · decode ${round(stages.decode_ms)} · act ${round(stages.action_ms)}`
}
