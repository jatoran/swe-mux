// Client-side read-aloud playback and dictation.
//
// One module-level <audio> element plays every clip. Mobile browsers only allow
// programmatic play() on an element that has already played inside a user
// gesture, so any voice-UI gesture calls unlockPlayback() and later automatic
// clips reuse the same unlocked element.

export type PlaybackState = { clipId: string | null; playing: boolean; position: number; duration: number }

const AUTOPLAY_KEY = 'mux:voice-autoplay'
// Minimal valid silent wav used purely to unlock the element inside a gesture.
const SILENT_WAV = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA='

let audioElement: HTMLAudioElement | null = null
let currentClipId: string | null = null
let unlocked = false
let queue: string[] = []
let state: PlaybackState = { clipId: null, playing: false, position: 0, duration: 0 }
const listeners = new Set<() => void>()

function notify() { for (const listener of [...listeners]) listener() }

function setState(next: Partial<PlaybackState>) {
  state = { ...state, ...next }
  notify()
}

function ensureAudio(): HTMLAudioElement {
  if (audioElement) return audioElement
  const audio = new Audio()
  audio.preload = 'auto'
  audio.addEventListener('timeupdate', () => setState({ position: audio.currentTime, duration: Number.isFinite(audio.duration) ? audio.duration : state.duration }))
  audio.addEventListener('durationchange', () => { if (Number.isFinite(audio.duration)) setState({ duration: audio.duration }) })
  audio.addEventListener('play', () => setState({ playing: true }))
  audio.addEventListener('pause', () => setState({ playing: false }))
  audio.addEventListener('ended', () => {
    setState({ playing: false })
    const next = queue.shift()
    if (next) void playClip(next).catch(() => { /* autoplay chain stops on error */ })
  })
  audioElement = audio
  return audio
}

export function clipAudioUrl(clipId: string): string { return `/api/voice/clips/${clipId}/audio` }

export function getPlayback(): PlaybackState { return state }

export function subscribePlayback(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function unlockPlayback(): void {
  if (unlocked) return
  const audio = ensureAudio()
  unlocked = true
  audio.src = SILENT_WAV
  void audio.play().catch(() => { unlocked = false })
}

export async function playClip(clipId: string): Promise<void> {
  const audio = ensureAudio()
  if (currentClipId === clipId && audio.src && !audio.ended) {
    if (audio.paused) await audio.play()
    return
  }
  currentClipId = clipId
  unlocked = true
  audio.src = clipAudioUrl(clipId)
  state = { clipId, playing: false, position: 0, duration: 0 }
  notify()
  await audio.play()
}

export function pausePlayback(): void { audioElement?.pause() }

export function seekTo(seconds: number): void {
  if (!audioElement || !currentClipId) return
  audioElement.currentTime = Math.max(0, seconds)
}

export function autoplayEnabled(): boolean {
  try { return localStorage.getItem(AUTOPLAY_KEY) === '1' } catch { return false }
}

export function setAutoplayEnabled(value: boolean): void {
  try { localStorage.setItem(AUTOPLAY_KEY, value ? '1' : '0') } catch { /* private mode */ }
  if (value) unlockPlayback()
  notify()
}

export function enqueueAutoplay(clipId: string): void {
  if (!autoplayEnabled()) return
  if (state.playing && currentClipId && currentClipId !== clipId) { queue.push(clipId); return }
  queue = []
  void playClip(clipId).catch(() => { /* blocked until a gesture unlocks the element */ })
}

// ---- Dictation (Web Speech API) ----------------------------------------------------

type SpeechRecognitionLike = {
  continuous: boolean
  interimResults: boolean
  lang: string
  onresult: ((event: SpeechResultEventLike) => void) | null
  onerror: ((event: { error?: string }) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
}
type SpeechResultEventLike = {
  resultIndex: number
  results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>
}

function recognitionConstructor(): (new () => SpeechRecognitionLike) | null {
  const scope = window as unknown as Record<string, unknown>
  return (scope.SpeechRecognition || scope.webkitSpeechRecognition) as (new () => SpeechRecognitionLike) | null
}

export function sttAvailable(): boolean {
  return sttCapability().available
}

export type SttCapability={available:boolean;backend:'browser-web-speech';secureContext:boolean;recognizer:boolean;reason:string;contentTelemetry:false}
export function sttCapability():SttCapability{
  const secureContext=window.isSecureContext,recognizer=recognitionConstructor()!==null
  return {available:secureContext&&recognizer,backend:'browser-web-speech',secureContext,recognizer,
    reason:!secureContext?'A secure browser context is required.':!recognizer?'This browser does not expose Web Speech Recognition.':'Browser Web Speech Recognition is available.',contentTelemetry:false}
}

export interface DictationHandlers {
  onInterim(text: string): void
  onFinal(text: string): void
  onEnd(): void
  onError(message: string): void
}

export function startDictation(handlers: DictationHandlers): (() => void) | null {
  const Recognition = recognitionConstructor()
  if (!Recognition || !window.isSecureContext) return null
  const recognition = new Recognition()
  recognition.continuous = false
  recognition.interimResults = true
  recognition.lang = navigator.language || 'en-US'
  let finalText = ''
  recognition.onresult = event => {
    let interim = ''
    for (let index = event.resultIndex; index < event.results.length; index++) {
      const result = event.results[index]
      if (result.isFinal) finalText += result[0].transcript
      else interim += result[0].transcript
    }
    if (interim) handlers.onInterim(interim)
  }
  recognition.onerror = event => handlers.onError(String(event.error || 'speech recognition failed'))
  recognition.onend = () => {
    if (finalText.trim()) handlers.onFinal(finalText.trim())
    handlers.onEnd()
  }
  try { recognition.start() } catch { return null }
  return () => recognition.stop()
}
