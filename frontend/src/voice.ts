// Client-side read-aloud playback.
//
// One module-level <audio> element plays every clip. Mobile browsers only allow
// programmatic play() on an element that has already played inside a user
// gesture, so any voice-UI gesture calls unlockPlayback() and later automatic
// clips reuse the same unlocked element.

export type PlaybackOrigin = 'agent' | 'system'
export type PlaybackState = { clipId: string | null; playing: boolean; position: number; duration: number; origin: PlaybackOrigin | null }

const AUTOPLAY_KEY = 'mux:voice-autoplay'
// Minimal valid silent wav used purely to unlock the element inside a gesture.
const SILENT_WAV = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA='

// Clips carry the session they belong to so turning read aloud off for one pane
// can cut that pane's audio without silencing another pane that is still on.
type QueueItem = { clipId: string; streamId: string | null; sessionId: string | null; origin: PlaybackOrigin }

let audioElement: HTMLAudioElement | null = null
let currentClipId: string | null = null
let currentStreamId: string | null = null
let currentSessionId: string | null = null
let unlocked = false
let queue: QueueItem[] = []
const suppressedStreams = new Set<string>()
let state: PlaybackState = { clipId: null, playing: false, position: 0, duration: 0, origin: null }
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

export async function playClip(clipId: string, sessionId: string | null = null, origin: PlaybackOrigin = 'agent'): Promise<void> {
  currentStreamId = null
  currentSessionId = sessionId
  await playClipAudio(clipId, origin)
}

async function playQueuedClip(item:QueueItem):Promise<void>{
  if(item.streamId&&suppressedStreams.has(item.streamId))return
  currentStreamId=item.streamId
  currentSessionId=item.sessionId
  await playClipAudio(item.clipId,item.origin)
}

async function playClipAudio(clipId:string,origin:PlaybackOrigin):Promise<void>{
  const audio = ensureAudio()
  if (currentClipId === clipId && audio.src && !audio.ended) {
    if (audio.paused) await audio.play()
    return
  }
  currentClipId = clipId
  unlocked = true
  audio.src = clipAudioUrl(clipId)
  state = { clipId, playing: false, position: 0, duration: 0, origin }
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
  // Muting the device while a clip is mid-sentence has to be immediate; leaving it
  // to finish contradicts the 🔇 the button now shows.
  else stopAllPlayback()
  notify()
}

export function enqueueAutoplay(clipId: string, streamId: string | null = null, sessionId: string | null = null): void {
  if (!autoplayEnabled()) return
  if(streamId&&suppressedStreams.has(streamId))return
  const item:QueueItem={clipId,streamId,sessionId,origin:'agent'}
  if (state.playing && currentClipId && currentClipId !== clipId) { queue.push(item); return }
  queue = []
  void playQueuedClip(item).catch(() => { /* blocked until a gesture unlocks the element */ })
}

function suppressCurrentStream():void{
  if(!currentStreamId)return
  suppressedStreams.add(currentStreamId)
  if(suppressedStreams.size>64)suppressedStreams.delete(suppressedStreams.values().next().value as string)
}

export function bargeInPlayback():void{
  suppressCurrentStream()
  queue=[]
  audioElement?.pause()
  setState({playing:false})
}

// Hard stop: unlike pausePlayback (which keeps the clip loaded so the play button
// resumes it), this abandons the clip so the strip shows nothing playing and a
// later play starts from the beginning.
function haltCurrentClip():void{
  audioElement?.pause()
  currentClipId=null
  currentStreamId=null
  currentSessionId=null
  state={clipId:null,playing:false,position:0,duration:0,origin:null}
  notify()
}

// Read aloud was turned off for one session. Cut that session's audio and drop its
// queued clips; other sessions keep playing, and anything already queued for them
// advances rather than being stranded behind the halted clip.
export function stopSessionPlayback(sessionId: string): void {
  const hitCurrent = currentSessionId === sessionId
  if (hitCurrent) suppressCurrentStream()
  queue = queue.filter(item => item.sessionId !== sessionId)
  if (!hitCurrent) return
  haltCurrentClip()
  const next = queue.shift()
  if (next) void playQueuedClip(next).catch(() => { /* autoplay chain stops on error */ })
}

// Read aloud was turned off globally (Settings) or for this device (autoplay
// toggle): nothing should keep playing anywhere.
export function stopAllPlayback(): void {
  suppressCurrentStream()
  queue = []
  haltCurrentClip()
}
