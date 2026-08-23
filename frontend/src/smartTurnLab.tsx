import { render } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'

import { FrameAssembler, joinFrames, StreamingDownsampler, VAD_FRAME_MS, VAD_SAMPLE_RATE } from './audioFrames.ts'
import { encodeWav } from './conversation.ts'
import { SILERO_GATE, SpeechGate } from './speechGate.ts'
import { SileroVad } from './sileroVad.ts'
import { SmartTurn, SMART_TURN_THRESHOLD } from './smartTurn.ts'
import type { TurnVerdict } from './smartTurn.ts'
import { SMART_TURN_SAMPLE_RATE, SMART_TURN_SAMPLES } from './smartTurnFeatures.ts'
import { utteranceCompleteness } from './utteranceCompleteness.ts'
import { captureWorkletUrl, CAPTURE_WORKLET_NAME } from './voiceCaptureWorklet.ts'

/**
 * A bench for Smart Turn v3, wired to the SAME segmentation the real app uses.
 *
 * That reuse is the point. Silero and `SpeechGate` decide when an utterance
 * starts and ends here exactly as they do in `conversation.ts`, so what the lab
 * measures is what production would get - not a parallel pipeline that happens
 * to agree on a good day.
 *
 * Two readings, answering two different questions:
 *
 *   - the LIVE trace, rescored every ~250 ms while you speak, answers "does the
 *     probability actually stay low mid-thought and rise when I finish", which
 *     is the only question that decides whether the model is useful here;
 *   - the ENDPOINT rows, one per utterance, carry the latency split (feature
 *     extraction is pure JS and is often the larger half) and, optionally, the
 *     transcript and what the current word heuristic said about it - so the
 *     cases where the two disagree are visible rather than argued about.
 */

/** How often the live trace rescores while speech is arriving. */
const LIVE_INTERVAL_MS = 250

/** Nothing shorter than this is worth scoring; it is breath, not a turn. */
const MIN_SCORABLE_MS = 320

type Row = {
  id: number
  at: string
  seconds: number
  verdict: TurnVerdict
  reason: string
  transcript: string | null
  heuristic: string | null
}

type LivePoint = { atMs: number; probability: number }

const percentile = (values: number[], fraction: number): number => {
  if (!values.length) return 0
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))]
}

const fixed = (value: number, places = 1) => value.toFixed(places)

function Lab() {
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const [running, setRunning] = useState(false)
  const [rows, setRows] = useState<Row[]>([])
  const [live, setLive] = useState<LivePoint[]>([])
  const [speaking, setSpeaking] = useState(false)
  const [transcribe, setTranscribe] = useState(false)
  const [modelBytes, setModelBytes] = useState(0)
  const [liveScoring, setLiveScoring] = useState(true)

  const detectorRef = useRef<SmartTurn | null>(null)
  const stopRef = useRef<(() => void) | null>(null)
  const transcribeRef = useRef(transcribe); transcribeRef.current = transcribe
  const liveScoringRef = useRef(liveScoring); liveScoringRef.current = liveScoring
  const rowId = useRef(0)

  const inference = rows.map(row => row.verdict.inferenceMs)
  const features = rows.map(row => row.verdict.featuresMs)

  const start = async () => {
    setError('')
    try {
      setStatus('loading Smart Turn weights…')
      const detector = detectorRef.current ?? new SmartTurn()
      detectorRef.current = detector
      if (!detector.ready) {
        // `?url` rather than a public asset: see `tools/fetch_smart_turn.py`.
        const modelUrl = (await import('../models/smart-turn-v3.2-cpu.onnx?url')).default
        await detector.load(modelUrl)
      }
      setModelBytes(detector.modelBytes)

      setStatus('loading Silero…')
      const vad = new SileroVad()
      await vad.load()

      setStatus('opening the microphone…')
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      const context = new AudioContext()
      await context.audioWorklet.addModule(captureWorkletUrl())
      const source = context.createMediaStreamSource(stream)
      const worklet = new AudioWorkletNode(context, CAPTURE_WORKLET_NAME)
      // Connected through a silent gain because the graph is pulled from the
      // destination backwards; a node with nothing downstream may never render.
      const mute = context.createGain()
      mute.gain.value = 0
      source.connect(worklet)
      worklet.connect(mute)
      mute.connect(context.destination)

      const downsampler = new StreamingDownsampler(context.sampleRate, VAD_SAMPLE_RATE)
      const assembler = new FrameAssembler()
      let gate = new SpeechGate(SILERO_GATE)
      let utterance: Float32Array[] = []
      let busy = false
      let lastLiveAt = 0
      let startedAt = 0

      const score = async (frames: Float32Array[], reason: string, commit: boolean) => {
        if (busy) return
        const audio = joinFrames(frames)
        if ((audio.length / SMART_TURN_SAMPLE_RATE) * 1000 < MIN_SCORABLE_MS) return
        busy = true
        try {
          const verdict = await detector.predict(audio)
          if (!commit) {
            setLive(current => [...current.slice(-119), {
              atMs: performance.now() - startedAt,
              probability: verdict.probability,
            }])
            return
          }
          let transcript: string | null = null
          let heuristic: string | null = null
          if (transcribeRef.current) {
            try {
              const response = await fetch('/api/voice/transcribe', {
                method: 'POST',
                headers: { 'Content-Type': 'audio/wav' },
                body: encodeWav(audio, SMART_TURN_SAMPLE_RATE),
              })
              const payload = await response.json()
              transcript = String(payload.text ?? payload.transcript ?? '').trim() || '(nothing decoded)'
              const rule = utteranceCompleteness(transcript)
              heuristic = rule.complete ? 'complete' : `held on “${rule.trigger}” (${fixed(rule.completion, 2)})`
            } catch (cause) {
              transcript = `transcription failed: ${cause instanceof Error ? cause.message : String(cause)}`
            }
          }
          rowId.current += 1
          setRows(current => [{
            id: rowId.current,
            at: new Date().toLocaleTimeString(),
            seconds: audio.length / SMART_TURN_SAMPLE_RATE,
            verdict, reason, transcript, heuristic,
          }, ...current].slice(0, 60))
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : String(cause))
        } finally {
          busy = false
        }
      }

      const ingest = async (block: Float32Array) => {
        for (const frame of assembler.push(downsampler.push(block))) {
          const probability = await vad.probability(frame)
          for (const event of gate.push(probability)) {
            if (event.type === 'speech-start') {
              utterance = []
              startedAt = performance.now()
              lastLiveAt = 0
              setLive([])
              setSpeaking(true)
            }
            if (event.type === 'endpoint') {
              setSpeaking(false)
              const captured = utterance
              utterance = []
              gate = new SpeechGate(SILERO_GATE)
              void score(captured, event.reason, true)
            }
          }
          if (gate.speaking) {
            utterance.push(frame)
            // Bounded: the model only ever sees the last 8 s, so retaining more
            // is pure memory. Frames are 32 ms, so this is a 10 s ring.
            const cap = Math.ceil((SMART_TURN_SAMPLES / VAD_SAMPLE_RATE) * 1000 * 1.25 / VAD_FRAME_MS)
            if (utterance.length > cap) utterance = utterance.slice(-cap)
            const now = performance.now()
            if (liveScoringRef.current && now - lastLiveAt >= LIVE_INTERVAL_MS) {
              lastLiveAt = now
              void score(utterance.slice(), 'live', false)
            }
          }
        }
      }

      worklet.port.onmessage = event => { void ingest(event.data as Float32Array) }
      setRunning(true)
      setStatus('listening — speak, and pause mid-sentence to see what it does')

      stopRef.current = () => {
        worklet.port.onmessage = null
        worklet.disconnect(); source.disconnect(); mute.disconnect()
        for (const track of stream.getTracks()) track.stop()
        void context.close()
        setRunning(false); setSpeaking(false); setStatus('stopped')
        stopRef.current = null
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setStatus('failed')
      setRunning(false)
    }
  }

  useEffect(() => () => stopRef.current?.(), [])

  const latest = live.length ? live[live.length - 1].probability : null

  return (
    <main class="lab">
      <h1>Smart Turn v3 lab</h1>
      <p class="sub">
        pipecat-ai/smart-turn-v3, BSD-2-Clause, 8M params, int8 ONNX, single-threaded WASM.
        Segmentation is Silero plus the app’s own <code>SpeechGate</code>, so what you see here is what
        capture would get. Nothing on this page touches your sessions.
      </p>

      <div class="controls">
        <button onClick={() => (running ? stopRef.current?.() : void start())}>
          {running ? 'Stop' : 'Start listening'}
        </button>
        <label><input type="checkbox" checked={liveScoring} onChange={event => setLiveScoring((event.target as HTMLInputElement).checked)} /> live trace while speaking</label>
        <label><input type="checkbox" checked={transcribe} onChange={event => setTranscribe((event.target as HTMLInputElement).checked)} /> transcribe + compare with the word heuristic (needs the daemon)</label>
        <span class={`pill ${speaking ? 'on' : ''}`}>{speaking ? 'speech' : 'silence'}</span>
      </div>

      <p class="status">{status}{modelBytes ? ` · weights ${(modelBytes / 1e6).toFixed(2)} MB` : ''}</p>
      {error && <p class="error">{error}</p>}

      <section>
        <h2>Live probability {latest !== null && <em>{fixed(latest, 3)}</em>}</h2>
        <p class="hint">
          P(the speaker finished). Rescored every {LIVE_INTERVAL_MS} ms on everything said so far.
          The useful thing to watch: say “now I want you to add” and stop. If the bar stays low, the
          model knows you are mid-thought without any transcript at all.
        </p>
        <div class="trace">
          {live.map((point, index) => (
            <span
              key={index}
              class={point.probability >= SMART_TURN_THRESHOLD ? 'bar done' : 'bar'}
              style={{ height: `${Math.max(2, point.probability * 100)}%` }}
              title={`${fixed(point.atMs / 1000, 2)}s → ${fixed(point.probability, 3)}`}
            />
          ))}
          <div class="threshold" style={{ bottom: `${SMART_TURN_THRESHOLD * 100}%` }} />
        </div>
      </section>

      <section>
        <h2>Endpoints</h2>
        {rows.length > 0 && (
          <p class="hint">
            inference p50 {fixed(percentile(inference, 0.5))} ms · p95 {fixed(percentile(inference, 0.95))} ms ·
            features p50 {fixed(percentile(features, 0.5))} ms ·
            worst total {fixed(Math.max(...rows.map(row => row.verdict.featuresMs + row.verdict.inferenceMs)))} ms
            over {rows.length} utterances
          </p>
        )}
        <table>
          <thead>
            <tr>
              <th>time</th><th>p</th><th>verdict</th><th>audio</th>
              <th>features</th><th>infer</th><th>why</th>
              {transcribe && <><th>transcript</th><th>word rule</th></>}
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.id}>
                <td>{row.at}</td>
                <td class="num">{fixed(row.verdict.probability, 3)}</td>
                <td class={row.verdict.complete ? 'done' : 'holding'}>
                  {row.verdict.complete ? 'finished' : 'mid-thought'}
                </td>
                <td class="num">{fixed(row.seconds, 2)}s</td>
                <td class="num">{fixed(row.verdict.featuresMs)} ms</td>
                <td class="num">{fixed(row.verdict.inferenceMs)} ms</td>
                <td>{row.reason}</td>
                {transcribe && <><td>{row.transcript}</td><td>{row.heuristic}</td></>}
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={transcribe ? 9 : 7} class="empty">nothing scored yet</td></tr>}
          </tbody>
        </table>
      </section>

      <style>{`
        :root { color-scheme: dark light; }
        body { margin: 0; font: 14px/1.5 ui-sans-serif, system-ui, sans-serif; background: #0e1116; color: #d7dde5; }
        .lab { max-width: 1080px; margin: 0 auto; padding: 24px 20px 64px; }
        h1 { font-size: 20px; margin: 0 0 4px; }
        h2 { font-size: 15px; margin: 28px 0 6px; font-weight: 600; }
        h2 em { font-style: normal; font-variant-numeric: tabular-nums; color: #7fd1a4; margin-left: 8px; }
        .sub, .hint { color: #8b96a5; margin: 0 0 12px; }
        .hint { font-size: 12.5px; }
        code { background: #1a2029; padding: 1px 5px; border-radius: 4px; }
        .controls { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; margin: 16px 0 8px; }
        button { background: #2b6cb0; color: #fff; border: 0; border-radius: 6px; padding: 8px 16px; font: inherit; cursor: pointer; }
        label { display: flex; gap: 6px; align-items: center; font-size: 13px; color: #a9b4c2; }
        .pill { font-size: 12px; padding: 2px 10px; border-radius: 999px; background: #1a2029; color: #6b7686; }
        .pill.on { background: #1f4d33; color: #7fd1a4; }
        .status { color: #8b96a5; font-size: 12.5px; margin: 4px 0; }
        .error { color: #ff9b9b; background: #2a1618; padding: 8px 12px; border-radius: 6px; white-space: pre-wrap; }
        .trace { position: relative; display: flex; align-items: flex-end; gap: 2px; height: 140px;
                 background: #141922; border-radius: 6px; padding: 6px; overflow: hidden; }
        .bar { flex: 1 0 4px; background: #d98b5f; border-radius: 2px 2px 0 0; min-width: 3px; }
        .bar.done { background: #7fd1a4; }
        .threshold { position: absolute; left: 0; right: 0; border-top: 1px dashed #4a5666; }
        table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
        th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #1e252f; vertical-align: top; }
        th { color: #7a8698; font-weight: 500; }
        .num { font-variant-numeric: tabular-nums; text-align: right; }
        .done { color: #7fd1a4; } .holding { color: #d98b5f; }
        .empty { color: #6b7686; }
      `}</style>
    </main>
  )
}

const host = document.getElementById('lab')
if (host) render(<Lab />, host)
