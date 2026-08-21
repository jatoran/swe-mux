// Client-side read-aloud playback.
//
// One module-level <audio> element plays every clip. Mobile browsers only allow
// programmatic play() on an element that has already played inside a user
// gesture, so any voice-UI gesture calls unlockPlayback() and later automatic
// clips reuse the same unlocked element.
//
// Read aloud is three layers, and this module owns the third. The daemon's
// `tts_enabled` master decides whether anything is generated or spoken at all;
// each session's `voice_mode` decides whether *that* session generates; and here
// the device autoplay toggle plus one global, focus-driven rule decide what
// actually comes out of this browser's speakers.

export type PlaybackOrigin = 'agent' | 'system'
export type PlaybackState = { clipId: string | null; playing: boolean; position: number; duration: number; origin: PlaybackOrigin | null }
/** An agent clip that was ready while its session was not the one being watched. */
export type HeldClip = { clipId: string; streamId: string | null; at: number }

const AUTOPLAY_KEY = 'mux:voice-autoplay'
// How many held clips one session keeps. A session that has been talking to
// itself for an hour has nothing useful to say in its ninth-oldest clip, and the
// operator asked for less noise, not a backlog to work through.
const HELD_PER_SESSION = 5
// How many finished/abandoned stream ids stay remembered as suppressed. Only
// large enough that a clip synthesized long after its stream was cut still
// finds it here; the ids themselves are UUIDs and cost nothing to keep.
const SUPPRESSED_STREAM_MEMORY = 256
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
// The silent gesture-time play only grants future playback permission. It is not
// audible playback and must never make capture classify the user's next words as
// speaker echo. Some mobile Chromium builds do not emit `ended` for a zero-frame
// WAV, so this cannot depend on that event to clear the public playback state.
let unlocking = false
let playbackDucked = false
let queue: QueueItem[] = []
const suppressedStreams = new Set<string>()
const requestedStreams = new Map<string,{sessionId:string|null;origin:PlaybackOrigin}>()
let state: PlaybackState = { clipId: null, playing: false, position: 0, duration: 0, origin: null }
const listeners = new Set<() => void>()

// ------------------------------------------------------------ playback focus
//
// Playback policy is global and focus-driven, not per session: the session the
// operator is watching plays its automatic clips here, and every other session
// generates its clip, keeps it (durably, in `voice_clips`) and offers it as
// ready-to-play. Three panes on `auto` used to talk over each other and over
// whatever the operator was actually reading, which is the overwhelm this rule
// exists to end.
//
// `focusKnown` separates "no session is focused" from "the app has not told us
// yet". Before the first report the legacy behaviour stands, so a page that
// never reports focus (or reports it a tick late) does not swallow the clip it
// would have played before this rule existed.
let focusKnown = false
let focusedSessionId: string | null = null
// Sessions that play here regardless of focus. Voice Comms pins one agent and
// converses with it hands-free; its replies are the point of the mode, so they
// cannot be held just because the operator's eyes are on another pane.
const pinnedPlaybackSessions = new Set<string>()
const heldClips = new Map<string, HeldClip[]>()

function notify() { for (const listener of [...listeners]) listener() }

function setState(next: Partial<PlaybackState>) {
  state = { ...state, ...next }
  notify()
}

function ensureAudio(): HTMLAudioElement {
  if (audioElement) return audioElement
  const audio = new Audio()
  audio.preload = 'auto'
  audio.addEventListener('timeupdate', () => { if (!unlocking) setState({ position: audio.currentTime, duration: Number.isFinite(audio.duration) ? audio.duration : state.duration }) })
  audio.addEventListener('durationchange', () => { if (!unlocking && Number.isFinite(audio.duration)) setState({ duration: audio.duration }) })
  audio.addEventListener('play', () => { if (!unlocking) setState({ playing: true }) })
  audio.addEventListener('pause', () => { if (!unlocking) setState({ playing: false }) })
  audio.addEventListener('ended', () => {
    if (unlocking) { unlocking = false; return }
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
  unlocking = true
  unlocked = true
  audio.src = SILENT_WAV
  void audio.play().then(() => {
    if (!unlocking) return
    audio.pause()
    unlocking = false
  }).catch(() => {
    if (!unlocking) return
    unlocking = false
    unlocked = false
  })
}

export async function playClip(clipId: string, sessionId: string | null = null, origin: PlaybackOrigin = 'agent'): Promise<void> {
  currentStreamId = null
  currentSessionId = sessionId
  await playClipAudio(clipId, origin)
}

export function newVoiceStreamId():string{
  if(globalThis.crypto?.randomUUID)return globalThis.crypto.randomUUID()
  const hex=()=>Math.floor(Math.random()*0x10000).toString(16).padStart(4,'0')
  return `${hex()}${hex()}-${hex()}-4${hex().slice(1)}-${(8+Math.floor(Math.random()*4)).toString(16)}${hex().slice(1)}-${hex()}${hex()}${hex()}`
}

/**
 * Claim a user-requested stream before its first server event can arrive, and
 * take the floor: whatever is speaking now is cut. That is right for a stream
 * the user just asked for — a new question supersedes the answer to the old one.
 */
export function beginRequestedStream(streamId:string,sessionId:string|null,origin:PlaybackOrigin):void{
  suppressCurrentStream()
  queue=[]
  haltCurrentClip()
  requestedStreams.set(streamId,{sessionId,origin})
}

/**
 * Claim a stream without taking the floor: its clips queue behind whatever is
 * already speaking. For app-initiated speech that is *additional* rather than
 * superseding — a confirmation card announcing itself while the reply that
 * produced it is still being read — where interrupting would cut a sentence the
 * operator is mid-way through hearing.
 */
export function claimRequestedStream(streamId:string,sessionId:string|null,origin:PlaybackOrigin):void{
  requestedStreams.set(streamId,{sessionId,origin})
}

/** Whether this tab still intends to play a stream, or has cut it. */
export function requestedStreamActive(streamId:string):boolean{
  return requestedStreams.has(streamId)&&!suppressedStreams.has(streamId)
}

export function cancelRequestedStream(streamId:string):void{
  requestedStreams.delete(streamId)
  suppress(streamId)
}

/**
 * Accept live segment events only for a stream explicitly requested by this tab.
 *
 * `count <= 0` marks a stream that is still open: the assistant speaks a turn
 * sentence by sentence and does not know how many clips it will produce until
 * the model stops, so the stream stays claimed until its closing segment
 * carries a real count (or `closeRequestedStream` arrives).
 */
export function enqueueRequestedStreamClip(clipId:string,streamId:string,index:number,count:number):void{
  const request=requestedStreams.get(streamId)
  if(!request||suppressedStreams.has(streamId))return
  const item:QueueItem={clipId,streamId,sessionId:request.sessionId,origin:request.origin}
  if(currentClipId===clipId)return
  // Busy means "a clip is loaded and has not finished", not "audio is audible":
  // between assigning src and the `play` event, `state.playing` is false while
  // the element is very much occupied, and a clip started there would replace
  // the one about to speak. A stream's segments arrive close together, so that
  // window is hit often enough to swallow whole sentences.
  if(playbackBusy())queue.push(item)
  else void playQueuedClip(item).catch(()=>{})
  if(count>0&&index>=count-1)requestedStreams.delete(streamId)
}

function playbackBusy():boolean{
  if(!audioElement||!currentClipId)return false
  return !audioElement.ended
}

/**
 * The producer finished this stream. Queued clips still play; only the claim
 * that lets new segments join it is released, so a late clip from an abandoned
 * turn cannot append itself to whatever is speaking now.
 */
export function closeRequestedStream(streamId:string):void{
  requestedStreams.delete(streamId)
}

/** Response fallback for a lost/delayed event. Dedupe against current and queued clips. */
export async function playRequestedStreamFirst(clipId:string,streamId:string,sessionId:string|null,origin:PlaybackOrigin):Promise<void>{
  if(currentClipId===clipId||queue.some(item=>item.clipId===clipId))return
  requestedStreams.set(streamId,{sessionId,origin})
  await playQueuedClip({clipId,streamId,sessionId,origin})
}

async function playQueuedClip(item:QueueItem):Promise<void>{
  if(item.streamId&&suppressedStreams.has(item.streamId))return
  currentStreamId=item.streamId
  currentSessionId=item.sessionId
  await playClipAudio(item.clipId,item.origin)
}

async function playClipAudio(clipId:string,origin:PlaybackOrigin):Promise<void>{
  const audio = ensureAudio()
  // A real clip supersedes any still-pending silent unlock. Its media events are
  // public playback state even if the unlock promise settles afterwards.
  unlocking = false
  if (currentClipId === clipId && audio.src && !audio.ended) {
    if (audio.paused) await audio.play()
    return
  }
  currentClipId = clipId
  unlocked = true
  audio.src = clipAudioUrl(clipId)
  audio.muted = playbackDucked
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

/**
 * Report which agent session the operator is watching, or `null` for none.
 *
 * Called by the app whenever focus settles. Moving focus never plays anything:
 * a held clip stays held until it is asked for, because arriving at a pane is
 * not a request to be talked at.
 */
export function setPlaybackFocus(sessionId: string | null): void {
  const changed = !focusKnown || focusedSessionId !== sessionId
  focusKnown = true
  focusedSessionId = sessionId
  if (changed) notify()
}

export function playbackFocus(): string | null { return focusKnown ? focusedSessionId : null }

/** Pin (or release) a session that plays here whatever has focus — Voice Comms. */
export function setPinnedPlaybackSession(sessionId: string, pinned: boolean): void {
  if (pinned) pinnedPlaybackSessions.add(sessionId)
  else pinnedPlaybackSessions.delete(sessionId)
  notify()
}

/** Does an automatic clip from this session play on this device right now? */
export function sessionPlaysHere(sessionId: string | null): boolean {
  // A clip nobody can attribute to a session cannot be held against one either,
  // so it keeps the pre-policy behaviour and plays.
  if (!sessionId) return true
  if (pinnedPlaybackSessions.has(sessionId)) return true
  if (!focusKnown) return true
  return focusedSessionId === sessionId
}

export function heldClipsFor(sessionId: string): HeldClip[] { return heldClips.get(sessionId) || [] }

export function heldClipTotal(): number {
  let total = 0
  for (const items of heldClips.values()) total += items.length
  return total
}

export function heldClipSessions(): string[] {
  return [...heldClips.entries()].filter(([, items]) => items.length).map(([sessionId]) => sessionId)
}

function holdClip(sessionId: string, clipId: string, streamId: string | null): void {
  const items = heldClips.get(sessionId) || []
  if (items.some(item => item.clipId === clipId)) return
  items.push({ clipId, streamId, at: Date.now() })
  while (items.length > HELD_PER_SESSION) items.shift()
  heldClips.set(sessionId, items)
  notify()
}

/**
 * Play what this session held, oldest first, behind whatever is speaking now.
 *
 * Deliberately not a floor-taking play: the operator asked to hear a backlog,
 * not to cut off the sentence they are listening to. Clips whose stream was
 * suppressed in the meantime (barge-in, a pane switched off) stay dead.
 */
export function playHeldClips(sessionId: string): void {
  const items = heldClips.get(sessionId)
  if (!items || !items.length) return
  heldClips.delete(sessionId)
  notify()
  const playable: QueueItem[] = items
    .filter(item => !(item.streamId && suppressedStreams.has(item.streamId)))
    .map(item => ({ clipId: item.clipId, streamId: item.streamId, sessionId, origin: 'agent' as const }))
  if (!playable.length) return
  if (playbackBusy()) { queue.push(...playable); return }
  const first = playable.shift() as QueueItem
  queue.push(...playable)
  void playQueuedClip(first).catch(() => { /* blocked until a gesture unlocks the element */ })
}

/** Play every held clip on this device, oldest session first. */
export function playAllHeldClips(): void { for (const sessionId of heldClipSessions()) playHeldClips(sessionId) }

export function dismissHeldClips(sessionId: string): void {
  if (heldClips.delete(sessionId)) notify()
}

export function enqueueAutoplay(clipId: string, streamId: string | null = null, sessionId: string | null = null): void {
  if (!autoplayEnabled()) return
  if(streamId&&suppressedStreams.has(streamId))return
  // The unfocused half of the policy. The clip is already durable on the daemon;
  // holding it here is what turns "every pane speaks at once" into "the pane you
  // are watching speaks, and the rest have something ready for you".
  if (sessionId && !sessionPlaysHere(sessionId)) { holdClip(sessionId, clipId, streamId); return }
  const item:QueueItem={clipId,streamId,sessionId,origin:'agent'}
  if (state.playing && currentClipId && currentClipId !== clipId) { queue.push(item); return }
  queue = []
  void playQueuedClip(item).catch(() => { /* blocked until a gesture unlocks the element */ })
}

function suppressCurrentStream():void{
  if(!currentStreamId)return
  suppress(currentStreamId)
  requestedStreams.delete(currentStreamId)
}

// Bounded, and trimmed on every insert rather than only on the single-stream
// path: barge-in suppresses a whole claim map at once, and a trim that only ran
// for one of them would let the set grow without limit.
function suppress(streamId:string):void{
  suppressedStreams.add(streamId)
  while(suppressedStreams.size>SUPPRESSED_STREAM_MEMORY){
    suppressedStreams.delete(suppressedStreams.values().next().value as string)
  }
}

/**
 * Stop talking, now — the user interrupted, or released the microphone.
 *
 * Every *claimed* stream is suppressed, not only the one making noise. A claim
 * outlives the clip: synthesis runs behind the request, so a stream whose audio
 * has not arrived yet is still going to speak. Suppressing one stream let a
 * backlog keep talking for minutes after the operator had closed the microphone
 * and had no way left to say "stop" (2026-08-20). Autoplayed agent read-aloud is
 * deliberately untouched — that is a different switch, and a pane's own chip or
 * the device toggle turns it off.
 */
export function bargeInPlayback():void{
  suppressCurrentStream()
  suppressClaimedStreams()
  queue=[]
  haltCurrentClip()
}

/** Suppress every claimed stream, including ones whose first clip has not arrived. */
function suppressClaimedStreams():void{
  for(const streamId of requestedStreams.keys())suppress(streamId)
  requestedStreams.clear()
}

/** Sidechain mute used only while capture verifies a possible human interruption. */
export function setPlaybackDucked(active:boolean):void{
  playbackDucked=active&&!!currentClipId
  if(audioElement)audioElement.muted=playbackDucked
}

// Hard stop: unlike pausePlayback (which keeps the clip loaded so the play button
// resumes it), this abandons the clip so the strip shows nothing playing and a
// later play starts from the beginning.
function haltCurrentClip():void{
  audioElement?.pause()
  playbackDucked=false
  if(audioElement)audioElement.muted=false
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
  // Held clips go with it: they are this session's queued speech, and "off"
  // that leaves a play-me button behind is not off.
  heldClips.delete(sessionId)
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
  // The master switch and the device toggle are both "nothing plays here", and a
  // backlog offering itself afterwards contradicts the switch that was thrown.
  heldClips.clear()
  suppressCurrentStream()
  // Suppressed, not merely unclaimed: a `speak` request in flight when the
  // switch was thrown still has its HTTP response as a playback fallback, and
  // that fallback re-claims the stream it is about to play.
  suppressClaimedStreams()
  queue = []
  haltCurrentClip()
}
