import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { buildVoiceMatcher, conversationCapability, DEFAULT_COMMANDS, DEFAULT_WAKE_WORDS, PersistentVoiceCapture } from './conversation'
import type { CaptureMarks } from './voiceLatency'
import { evaluateTrial, summarizeTrials } from './wakeWordTest'
import type { WakeWordTrial } from './wakeWordTest'

/**
 * Speak a wake word N times and see what the recognizer heard.
 *
 * Deliberately not a simulation: it drives the same capture pipeline, posts to the
 * same transcribe endpoint on the same routing decoder the command path uses, and
 * scores with the matcher compiled from the live configuration. A tester that
 * approximated any of those would answer a question nobody asked.
 *
 * It exists before any trigger-word change, because "mux comes back as bucks" and
 * "mux is heard but the phrase after it is not" are different problems with
 * different fixes, and neither is visible from the configuration screen.
 */
export function WakeWordTester({ wakeWords, commands, available, diagnostic }: {
  wakeWords: string[]
  commands: { action: string; phrases: string[] }[]
  available: boolean
  diagnostic: string
}) {
  const [target, setTarget] = useState(10)
  const [trials, setTrials] = useState<WakeWordTrial[]>([])
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState('')
  const captureRef = useRef<PersistentVoiceCapture | null>(null)
  const runningRef = useRef(false)
  const targetRef = useRef(target)
  targetRef.current = target
  const matcher = useMemo(
    () => buildVoiceMatcher(wakeWords.length ? wakeWords : DEFAULT_WAKE_WORDS, commands.length ? commands : DEFAULT_COMMANDS),
    [wakeWords, commands],
  )
  const matcherRef = useRef(matcher)
  matcherRef.current = matcher
  const wakeRef = useRef(wakeWords)
  wakeRef.current = wakeWords.length ? wakeWords : DEFAULT_WAKE_WORDS

  const stop = () => {
    runningRef.current = false
    captureRef.current?.stop()
    captureRef.current = null
    setRunning(false)
  }
  const stopRef = useRef(stop)
  stopRef.current = stop
  // The microphone is a device resource; closing Settings mid-run must release it.
  useEffect(() => () => stopRef.current(), [])

  const record = async (audio: Blob, marks: CaptureMarks) => {
    if (!runningRef.current) return
    const response = await fetch('/api/voice/transcribe', {
      method: 'POST',
      headers: { 'Content-Type': 'audio/wav', 'X-Mux-Utterance-Id': marks.utteranceId, 'X-Mux-Decode-Profile': 'command' },
      body: audio,
    })
    const payload = await response.json() as { text?: string; error?: string; timings?: { decode_ms?: number } }
    if (!response.ok) {
      // "no speech was recognized" is a result, not a failure: it is the strongest
      // possible evidence against a trigger word, so it is recorded as an empty
      // transcript rather than thrown away as an error.
      setTrials(current => [...current, evaluateTrial('', matcherRef.current, wakeRef.current)])
    } else {
      setTrials(current => [
        ...current,
        evaluateTrial(String(payload.text || ''), matcherRef.current, wakeRef.current, payload.timings?.decode_ms || 0),
      ])
    }
  }

  const start = async () => {
    const capability = conversationCapability()
    if (!capability.available) { setStatus(capability.reason); return }
    if (!available) { setStatus(diagnostic || 'Daemon transcription is unavailable.'); return }
    setTrials([])
    setStatus('Requesting microphone…')
    const capture = new PersistentVoiceCapture({
      playbackActive: () => false,
      onSpeechStart: () => setStatus('Listening…'),
      onSpeechEnd: () => setStatus('Heard you.'),
      // Speculation is the command path's latency trick, not part of what is being
      // measured; the tester scores one decode per spoken utterance.
      onSpeculative: () => {},
      onSpeculativeAbort: () => {},
      onUtterance: (audio, marks) => { void record(audio, marks).catch(cause => setStatus(cause instanceof Error ? cause.message : String(cause))) },
      onDetector: (_detector, message) => setStatus(message),
      onError: message => setStatus(message),
    })
    try {
      await capture.start()
      captureRef.current = capture
      runningRef.current = true
      setRunning(true)
      setStatus('Say the wake word and a command, the way you normally would.')
    } catch (cause) {
      capture.stop()
      setStatus(cause instanceof Error ? cause.message : String(cause))
    }
  }

  // The run ends itself once enough utterances have landed, so the user never has
  // to look back at the screen to stop it.
  useEffect(() => {
    if (running && trials.length >= targetRef.current) {
      stop()
      setStatus('Done. Read the misses first — those are the variants worth adding.')
    }
  }, [trials.length, running])

  const report = summarizeTrials(trials)
  const suggestion = report.total === 0 ? ''
    : report.wakeHeard < report.total
      ? `The wake word was missed ${report.total - report.wakeHeard} of ${report.total} times. Add the spellings under "heard" to the wake-word list, or pick a trigger that is longer and more distinctive.`
      : report.matched < report.total
        ? 'The wake word was heard every time, so the trigger is fine; the command phrase after it is what did not match. Add the phrasings you actually used.'
        : 'Every utterance matched. This trigger word and phrase set survive the recognizer.'

  return <div class="wake-test">
    <div class="wake-test-actions">
      <label>Utterances<input type="number" min="3" max="50" value={target} disabled={running} onInput={event => setTarget(Math.max(3, Math.min(50, Number(event.currentTarget.value) || 10)))} /></label>
      {running
        ? <button onClick={stop}>Stop ({trials.length}/{target})</button>
        : <button class="primary" onClick={() => void start()}>Start test</button>}
      {trials.length > 0 && !running && <button onClick={() => setTrials([])}>Clear</button>}
    </div>
    {status && <p aria-live="polite">{status}</p>}
    {trials.length > 0 && <>
      <table class="wake-test-table">
        <thead><tr><th>#</th><th>heard</th><th>wake</th><th>matched</th><th>decode</th></tr></thead>
        <tbody>
          {trials.map((trial, index) => <tr key={index} class={trial.command ? '' : 'wake-test-miss'}>
            <td>{index + 1}</td>
            <td>{trial.text || <em>(nothing recognized)</em>}</td>
            <td>{trial.wake || '—'}</td>
            <td>{trial.command || '—'}</td>
            <td>{trial.decodeMs ? `${trial.decodeMs} ms` : '—'}</td>
          </tr>)}
        </tbody>
      </table>
      <p>
        Matched {report.matched}/{report.total} · wake word heard {report.wakeHeard}/{report.total}
        {report.medianDecodeMs ? ` · median decode ${report.medianDecodeMs} ms` : ''}
        {report.byCommand.length ? ` · ${report.byCommand.map(entry => `${entry.command} ×${entry.count}`).join(', ')}` : ''}
      </p>
      {suggestion && <p class={report.matched < report.total ? 'settings-inline-error' : ''}>{suggestion}</p>}
    </>}
  </div>
}
