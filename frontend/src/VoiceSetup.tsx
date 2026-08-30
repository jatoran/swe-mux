import type { ComponentChildren } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { conversationCapability } from './conversation.ts'
import { captureFailureNote, desktopMediaReport } from './desktopShell.ts'
import {
  KokoroModelPanel,
  WhisperModelPanel,
  type KokoroModelInfo,
  type VoiceRuntimeInfo,
  type WhisperModelInfo,
} from './Settings'
import { playClip, unlockPlayback } from './voice.ts'

/**
 * Guided voice setup: the most branching setup in the product, walked one
 * decision at a time.
 *
 * Every step reuses the surface that already owns its work rather than copying
 * it: the engine choice writes the same `tts_engine`/`tts_enabled` keys
 * Settings edits, the acquisition step mounts the same `KokoroModelPanel` (one
 * press, three progress lines - `voice.md` records why lines and not buttons),
 * the dictation step mounts `WhisperModelPanel`, and the confirmation speaks
 * through `POST /api/voice/speak`, the ordinary trusted application-speech
 * path. The wizard owns sequencing and nothing else, so it cannot drift from
 * the panels it borrows.
 *
 * The microphone check exists because every voice defect fixed on 2026-08-29
 * was a first-run defect, and the one this surfaces early is the grant nobody
 * answered: the meter asks for the microphone in a context where a refusal is
 * visible and explained (`captureFailureNote` names the WebView2 state), not
 * mid-conversation where it reads as a dead product.
 */

type VoiceStatusPayload = {
  kokoro_model?: KokoroModelInfo | null
  voice_runtime?: VoiceRuntimeInfo | null
  stt_models?: WhisperModelInfo[] | null
}

type Step = 'engine' | 'download' | 'mic' | 'confirm'

const CONFIRMATION_TEXT =
  'Voice is set up. swe-mux can read replies aloud, and this is what it sounds like.'

function MicMeter({ onNote }: { onNote: (note: string) => void }) {
  const [level, setLevel] = useState<number | null>(null)
  const stopRef = useRef<(() => void) | null>(null)
  useEffect(() => () => stopRef.current?.(), [])
  const start = async () => {
    onNote('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const context = new AudioContext()
      const analyser = context.createAnalyser()
      analyser.fftSize = 512
      context.createMediaStreamSource(stream).connect(analyser)
      const samples = new Uint8Array(analyser.fftSize)
      let live = true
      const tick = () => {
        if (!live) return
        analyser.getByteTimeDomainData(samples)
        let peak = 0
        for (const sample of samples) peak = Math.max(peak, Math.abs(sample - 128) / 128)
        setLevel(peak)
        requestAnimationFrame(tick)
      }
      tick()
      stopRef.current = () => {
        live = false
        for (const track of stream.getTracks()) track.stop()
        void context.close()
        stopRef.current = null
        setLevel(null)
      }
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause)
      onNote(captureFailureNote(message, desktopMediaReport()))
    }
  }
  if (level === null) {
    return <button type="button" onClick={() => void start()}>Test microphone</button>
  }
  return <div class="voice-setup-meter" role="img" aria-label="Microphone level">
    <div class="voice-setup-meter-fill" style={{ width: `${Math.min(100, Math.round(level * 140))}%` }} />
    <button type="button" onClick={() => stopRef.current?.()}>Stop</button>
    <small>{level > 0.02 ? 'Hearing you.' : 'Silent so far - say something.'}</small>
  </div>
}

export function VoiceSetup({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState<Step>('engine')
  const [engine, setEngine] = useState<'sapi' | 'kokoro'>('kokoro')
  const [dictation, setDictation] = useState(true)
  const [status, setStatus] = useState<VoiceStatusPayload | null>(null)
  const [kokoroReady, setKokoroReady] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void api<VoiceStatusPayload>('GET', '/api/voice').then(setStatus).catch(() => setStatus({}))
  }, [])

  // The panel owns the press; the wizard only needs to know when Continue is
  // honest, so it polls the same cheap status endpoint the panel refreshes from.
  useEffect(() => {
    if (step !== 'download') return
    const probe = () => void api<KokoroModelInfo>('GET', '/api/voice/models/kokoro')
      .then(model => setKokoroReady(
        model.status === 'ready'
        && (model.g2p?.status ?? 'ready') === 'ready'
        && (model.runtime ? !model.runtime.supported || model.runtime.status === 'ready' : true),
      ))
      .catch(() => {})
    probe()
    const timer = setInterval(probe, 2000)
    return () => clearInterval(timer)
  }, [step])

  const capability = conversationCapability()

  const chooseEngine = async () => {
    setBusy(true)
    setNote('')
    try {
      await api('PATCH', '/api/config', { tts_enabled: true, tts_engine: engine })
      setStep(engine === 'kokoro' ? 'download' : 'mic')
    } catch (cause) {
      setNote(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  const chooseDictation = async () => {
    setBusy(true)
    setNote('')
    try {
      if (dictation) await api('PATCH', '/api/config', { stt_enabled: true })
      setStep('confirm')
    } catch (cause) {
      setNote(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  const speak = async () => {
    setBusy(true)
    setNote('')
    try {
      unlockPlayback()
      const clip = await api<{ id: string }>('POST', '/api/voice/speak', { text: CONFIRMATION_TEXT })
      await playClip(clip.id, null, 'system')
      setNote('If you heard that, read aloud is working.')
    } catch (cause) {
      setNote(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  const footer = (main: ComponentChildren) => <footer>
    <button type="button" class="link" disabled={busy} onClick={onClose}>Close</button>
    <span class="harness-setup-spacer" />
    {step !== 'engine' && <button type="button" disabled={busy} onClick={() => setStep(step === 'confirm' ? 'mic' : step === 'mic' && engine === 'kokoro' ? 'download' : 'engine')}>Back</button>}
    {main}
  </footer>

  return <div class="harness-setup-backdrop" role="dialog" aria-modal="true" aria-label="Set up voice">
    <section class="harness-setup">
      <header><strong>SET UP::VOICE</strong></header>
      <div class="harness-setup-body">
        {step === 'engine' && <div>
          <p>Pick the speech engine for reading replies aloud. Both stay switchable later under Settings → Voice; Edge TTS (experimental, online) lives there too.</p>
          <label class="harness-setup-row check harness-setup-tier">
            <span><strong>Local neural voice (Kokoro)</strong><small>Natural speech, fully on this machine. One press downloads everything it needs; the exact size is quoted before anything transfers.</small></span>
            <input type="radio" name="voice-engine" checked={engine === 'kokoro'} onChange={() => setEngine('kokoro')} />
          </label>
          <label class="harness-setup-row check harness-setup-tier">
            <span><strong>OS voice</strong><small>Your system's built-in voice. Instant, offline, no download - and noticeably more robotic.</small></span>
            <input type="radio" name="voice-engine" checked={engine === 'sapi'} onChange={() => setEngine('sapi')} />
          </label>
        </div>}
        {step === 'download' && <div>
          <p>One press downloads the three parts Kokoro needs; each reports its own line because they can fail independently, and the button retries exactly what failed.</p>
          <KokoroModelPanel initial={status?.kokoro_model || null} />
          {kokoroReady && <p class="harness-setup-note">Everything Kokoro needs is ready.</p>}
        </div>}
        {step === 'mic' && <div>
          <p>Talking to swe-mux (dictation, wake words, hands-free) needs the microphone. Test it now so a permission prompt happens here, where a refusal is visible, rather than mid-conversation.</p>
          {!capability.available && <p class="harness-setup-note">{capability.reason}{!capability.secureContext ? ' On a phone, use the HTTPS address from Settings → Remote.' : ''}</p>}
          {capability.available && <MicMeter onNote={setNote} />}
          <label class="harness-setup-scan check">
            <span><strong>Enable dictation and voice commands</strong><small>Turns on speech-to-text. The transcription model downloads below on its own press; nothing is fetched until you ask.</small></span>
            <input type="checkbox" checked={dictation} onChange={event => setDictation(event.currentTarget.checked)} />
          </label>
          {dictation && <WhisperModelPanel initial={status?.stt_models || null} runtime={status?.voice_runtime || null} />}
        </div>}
        {step === 'confirm' && <div>
          <p>Last step: hear it. This speaks one sentence through the engine you chose.</p>
          <button type="button" disabled={busy} onClick={() => void speak()}>Speak a test sentence</button>
          <p class="harness-setup-note">Read aloud per session is controlled from each pane's voice controls; the master switches live under Settings → Voice.</p>
        </div>}
        {note && <p class="harness-setup-note" role="status">{note}</p>}
      </div>
      {step === 'engine' && footer(<button type="button" class="primary" disabled={busy} onClick={() => void chooseEngine()}>Continue</button>)}
      {step === 'download' && footer(<button type="button" class="primary" disabled={busy || !kokoroReady} onClick={() => setStep('mic')}>Continue</button>)}
      {step === 'mic' && footer(<button type="button" class="primary" disabled={busy} onClick={() => void chooseDictation()}>Continue</button>)}
      {step === 'confirm' && footer(<button type="button" class="primary" disabled={busy} onClick={onClose}>Done</button>)}
    </section>
  </div>
}
