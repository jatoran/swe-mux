// Client-side read-aloud playback.
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
let currentStreamId: string | null = null
let unlocked = false
let queue: Array<{clipId:string;streamId:string|null}> = []
const suppressedStreams = new Set<string>()
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
    if (next) void playQueuedClip(next).catch(() => { /* autoplay chain stops on error */ })
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
  currentStreamId = null
  await playClipAudio(clipId)
}

async function playQueuedClip(item:{clipId:string;streamId:string|null}):Promise<void>{
  if(item.streamId&&suppressedStreams.has(item.streamId))return
  currentStreamId=item.streamId
  await playClipAudio(item.clipId)
}

async function playClipAudio(clipId:string):Promise<void>{
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

export function enqueueAutoplay(clipId: string, streamId: string | null = null): void {
  if (!autoplayEnabled()) return
  if(streamId&&suppressedStreams.has(streamId))return
  const item={clipId,streamId}
  if (state.playing && currentClipId && currentClipId !== clipId) { queue.push(item); return }
  queue = []
  void playQueuedClip(item).catch(() => { /* blocked until a gesture unlocks the element */ })
}

export function bargeInPlayback():void{
  if(currentStreamId){
    suppressedStreams.add(currentStreamId)
    if(suppressedStreams.size>64)suppressedStreams.delete(suppressedStreams.values().next().value as string)
  }
  queue=[]
  audioElement?.pause()
  setState({playing:false})
}
