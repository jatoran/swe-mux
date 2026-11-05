import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
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
    () => buildVoiceMatcher(wakeWords.length ? wakeWords : DEFAULT_WAKE_WORDS, commands.length ? commands : DEFAULT_COMMANDS), [wakeWords, commands, uiLocale.value, catalogRevision.value])
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
    if (!available) { setStatus(diagnostic || t("WakeWordTester.c3629f50b0")); return }
    setTrials([])
    setStatus(t("WakeWordTester.619398e5a3"))
    const capture = new PersistentVoiceCapture({
      playbackActive: () => false,
      onSpeechStart: () => setStatus(t("WakeWordTester.bbb4106e81")),
      onSpeechEnd: () => setStatus(t("WakeWordTester.799ad5fa32")),
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
      setStatus(t("WakeWordTester.4bc74251ac"))
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
      setStatus(t("WakeWordTester.a57f0effd6"))
    }
  }, [trials.length, running])

  const report = summarizeTrials(trials)
  const suggestion = report.total === 0 ? ''
    : report.wakeHeard < report.total
      ? t("WakeWordTester.05ecf24d82", { value1: report.total - report.wakeHeard, value2: report.total })
      : report.matched < report.total
        ? t("WakeWordTester.f3435be87f")
        : t("WakeWordTester.b5d72a89d3")

  return <div class="wake-test">
    <div class="wake-test-actions">
      <label>{rich("WakeWordTester.9fefa18c48", { value1: <input type="number" min="3" max="50" value={target} disabled={running} onInput={event => setTarget(Math.max(3, Math.min(50, Number(event.currentTarget.value) || 10)))} /> })}</label>
      {running ? <button onClick={stop}>{rich("WakeWordTester.b60d5c2b58", { length: trials.length, target: target })}</button> : <button class="primary" onClick={() => void start()}>{t("WakeWordTester.664ffe9913")}</button>}
      {trials.length > 0 && !running && <button onClick={() => setTrials([])}>{t("WakeWordTester.83b12c2216")}</button>}
    </div>
    {status && <p aria-live="polite">{status}</p>}
    {trials.length > 0 && <>
      <table class="wake-test-table">
        <thead><tr><th>#</th><th>{t("WakeWordTester.0040953a08")}</th><th>{t("WakeWordTester.51391bf0ab")}</th><th>{t("WakeWordTester.eadc688cd5")}</th><th>{t("WakeWordTester.713a1417c7")}</th></tr></thead>
        <tbody>
          {trials.map((trial, index) => <tr key={index} class={trial.command ? '' : 'wake-test-miss'}>
            <td>{index + 1}</td>
            <td>{trial.text || <em>{t("WakeWordTester.c59708c4f2")}</em>}</td>
            <td>{trial.wake || '—'}</td>
            <td>{trial.command || '—'}</td>
            <td>{trial.decodeMs ? t("WakeWordTester.e060faa28a", { decodeMs: trial.decodeMs }) : '—'}</td>
          </tr>)}
        </tbody>
      </table>
      <p>{rich("WakeWordTester.c67c0ca7ae", { matched: report.matched, total: report.total, wakeHeard: report.wakeHeard, value4: report.total, value5: report.medianDecodeMs ? t("WakeWordTester.13f0cabaa4", { medianDecodeMs: report.medianDecodeMs }) : '', value6: report.byCommand.length ? t("WakeWordTester.f6b53f9c8a", { value1: report.byCommand.map(entry => `${entry.command} ×${entry.count}`).join(', ') }) : '' })}</p>
      {suggestion && <p class={report.matched < report.total ? 'settings-inline-error' : ''}>{suggestion}</p>}
    </>}
  </div>
}
