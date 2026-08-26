import assert from 'node:assert/strict'
import test from 'node:test'

import type { VoiceClip } from '../src/types.ts'

// voice.ts owns one module-level <audio> element and reads localStorage, so both are
// stubbed before the module is imported. Tests share that singleton and therefore run
// in order, each leaving playback stopped.
class FakeAudio {
  static instances: FakeAudio[] = []
  preload = ''
  currentTime = 0
  duration = NaN
  paused = true
  ended = false
  muted = false
  private source = ''
  private handlers: Record<string, Array<() => void>> = {}
  constructor() { FakeAudio.instances.push(this) }
  get src() { return this.source }
  set src(value: string) { this.source = value; this.ended = false; this.paused = true }
  addEventListener(type: string, handler: () => void) { (this.handlers[type] ||= []).push(handler) }
  removeEventListener(type: string, handler: () => void) {
    this.handlers[type] = (this.handlers[type] || []).filter(item => item !== handler)
  }
  emit(type: string) { for (const handler of this.handlers[type] || []) handler() }
  async play() { this.paused = false; this.emit('play') }
  load() { /* setting src is enough for the fake; existence proves preload was requested */ }
  pause() { this.paused = true; this.emit('pause') }
  finish() { this.ended = true; this.paused = true; this.emit('ended') }
}

const store = new Map<string, string>()
const globals = globalThis as unknown as Record<string, unknown>
globals.Audio = FakeAudio
globals.localStorage = {
  getItem: (key: string) => store.get(key) ?? null,
  setItem: (key: string, value: string) => { store.set(key, value) },
  removeItem: (key: string) => { store.delete(key) },
  clear: () => store.clear(),
  key: () => null,
  length: 0,
}

const voice = await import('../src/voice.ts')
const element = () => FakeAudio.instances[0]
const settle = () => new Promise(resolve => setTimeout(resolve, 0))

test('gesture unlock never presents its silent media as active playback', async () => {
  voice.unlockPlayback()
  assert.equal(voice.getPlayback().playing, false, 'capture must not classify speech during unlock as playback echo')
  await settle()
  assert.equal(element().paused, true)
  assert.deepEqual(voice.getPlayback(), { clipId: null, playing: false, position: 0, duration: 0, origin: null })
})

test('turning read aloud off for a session cuts that session mid-clip', async () => {
  await voice.playClip('clip-a', 'session-a')
  assert.equal(voice.getPlayback().clipId, 'clip-a')
  assert.equal(voice.getPlayback().playing, true)

  voice.stopSessionPlayback('session-b')
  assert.equal(voice.getPlayback().clipId, 'clip-a', 'another pane going off must not silence this one')
  assert.equal(element().paused, false)

  voice.stopSessionPlayback('session-a')
  assert.equal(element().paused, true)
  assert.deepEqual(voice.getPlayback(), { clipId: null, playing: false, position: 0, duration: 0, origin: null })
})

test('stopping the speaking session advances to a queued clip from another session', async () => {
  voice.setAutoplayEnabled(true)
  await voice.playClip('clip-a', 'session-a')
  voice.enqueueAutoplay('clip-b', 'stream-b', 'session-b')
  assert.equal(voice.getPlayback().clipId, 'clip-a', 'a second session queues behind the one playing')

  voice.stopSessionPlayback('session-a')
  await settle()
  assert.equal(voice.getPlayback().clipId, 'clip-b')
  assert.equal(voice.getPlayback().playing, true)

  voice.stopAllPlayback()
  assert.equal(voice.getPlayback().clipId, null)
})

test('a stopped session leaves nothing of its own queued', async () => {
  voice.setAutoplayEnabled(true)
  await voice.playClip('clip-a', 'session-a')
  voice.enqueueAutoplay('clip-b', 'stream-b', 'session-b')
  voice.stopSessionPlayback('session-b')
  assert.equal(voice.getPlayback().clipId, 'clip-a')

  element().finish()
  await settle()
  assert.equal(voice.getPlayback().clipId, 'clip-a', 'the dropped clip must not play when the current one ends')
  assert.equal(voice.getPlayback().playing, false)
  voice.stopAllPlayback()
})

test('muting the device stops the clip already speaking', async () => {
  voice.setAutoplayEnabled(true)
  await voice.playClip('clip-c', 'session-c')
  assert.equal(voice.getPlayback().playing, true)

  voice.setAutoplayEnabled(false)
  assert.equal(element().paused, true)
  assert.equal(voice.getPlayback().clipId, null)
  assert.equal(voice.autoplayEnabled(), false)
})

test('a halted clip restarts from the beginning rather than resuming', async () => {
  voice.setAutoplayEnabled(true)
  await voice.playClip('clip-d', 'session-d')
  element().currentTime = 12
  voice.stopAllPlayback()

  await voice.playClip('clip-d', 'session-d')
  assert.equal(voice.getPlayback().position, 0)
  assert.equal(voice.getPlayback().playing, true)
  voice.stopAllPlayback()
})

test('playback explicitly distinguishes trusted application speech from agent text', async () => {
  await voice.playClip('clip-system', 'system', 'system')
  assert.equal(voice.getPlayback().origin, 'system')
  voice.stopAllPlayback()
  await voice.playClip('clip-agent', 'session-a')
  assert.equal(voice.getPlayback().origin, 'agent')
  voice.stopAllPlayback()
})

test('a capture probe ducks playback and a confirmed interruption abandons it', async () => {
  await voice.playClip('clip-probe','system','system')
  voice.setPlaybackDucked(true)
  assert.equal(element().muted,true)
  assert.equal(voice.getPlayback().playing,true,'ducking must keep playback state available to the probe')
  voice.bargeInPlayback()
  assert.equal(element().muted,false)
  assert.deepEqual(voice.getPlayback(),{clipId:null,playing:false,position:0,duration:0,origin:null})
})

test('requested segmented speech starts at the first clip and continues in order', async () => {
  const stream='11111111-1111-4111-8111-111111111111'
  voice.beginRequestedStream(stream,'system','system')
  voice.enqueueRequestedStreamClip('segment-1',stream,0,2)
  await settle()
  assert.equal(voice.getPlayback().clipId,'segment-1')
  assert.equal(voice.getPlayback().origin,'system')
  voice.enqueueRequestedStreamClip('segment-2',stream,1,2)
  assert.equal(
    FakeAudio.instances[1]?.src,
    voice.clipAudioUrl('segment-2'),
    'the next segment must be fetched before the current one ends',
  )
  element().finish()
  await settle()
  assert.equal(voice.getPlayback().clipId,'segment-2')
  voice.bargeInPlayback()
  assert.deepEqual(voice.getPlayback(),{clipId:null,playing:false,position:0,duration:0,origin:null})
})

test('an in-stream handoff reports readiness and preload separately', async () => {
  const reports: Array<Record<string, unknown>> = []
  const previousFetch = globalThis.fetch
  globalThis.fetch = (async (_input: string | URL | Request, init?: RequestInit) => {
    reports.push(JSON.parse(String(init?.body || '{}')) as Record<string, unknown>)
    return new Response('{}', { status: 200 })
  }) as typeof fetch
  try {
    const stream='12121212-1212-4212-8212-121212121212'
    voice.beginRequestedStream(stream,'system','system')
    voice.enqueueRequestedStreamClip('handoff-1',stream,0,0)
    await settle()
    voice.enqueueRequestedStreamClip('handoff-2',stream,1,2)
    element().finish()
    await settle()
    assert.equal(reports.length,1)
    assert.deepEqual(
      { ...reports[0], handoffMs: 0 },
      {
        event:'handoff',streamId:stream,previousClipId:'handoff-1',nextClipId:'handoff-2',
        handoffMs:0,queuedAtEnd:true,preloaded:true,
      },
    )
  } finally {
    voice.bargeInPlayback()
    globalThis.fetch = previousFetch
  }
})

test('an open stream keeps accepting segments until its count is known',async()=>{
  // The assistant speaks a turn sentence by sentence and cannot know how many
  // clips it will produce, so segments arrive with count 0 until the closing
  // one. Treating 0 as "the last of one" would drop everything after sentence 1.
  const stream='22222222-2222-4222-8222-222222222222'
  voice.beginRequestedStream(stream,'system','system')
  voice.enqueueRequestedStreamClip('open-1',stream,0,0)
  await settle()
  assert.equal(voice.getPlayback().clipId,'open-1')
  voice.enqueueRequestedStreamClip('open-2',stream,1,0)
  element().finish()
  await settle()
  assert.equal(voice.getPlayback().clipId,'open-2','a later sentence must still join the open stream')
  voice.enqueueRequestedStreamClip('open-3',stream,2,3)
  element().finish()
  await settle()
  assert.equal(voice.getPlayback().clipId,'open-3')
  // The closing segment carried a real count, so the stream is no longer claimed.
  voice.enqueueRequestedStreamClip('open-4',stream,3,0)
  element().finish()
  await settle()
  assert.equal(voice.getPlayback().clipId,'open-3','a segment after the close must be refused')
  voice.bargeInPlayback()
})

test('an open stream reads as open through the event payload, not just the function',()=>{
  // The regression this pins cost ten of thirty-four assistant replies in the
  // field log, every one with `appends=0`: the daemon sends `segment_count: 0`
  // for "still open", `Number(payload.segment_count||1)` turned that into 1, the
  // claim was released after sentence one, and `assistantSpeech.ts` — which
  // gates every further sentence on that claim — stopped POSTing the rest of the
  // reply for synthesis at all. The test above proved the FUNCTION handled 0.
  // Nothing proved the one line that built its arguments did.
  assert.deepEqual(voice.segmentPosition({segment_index:0,segment_count:0}),{index:0,count:0})
  assert.deepEqual(voice.segmentPosition({segment_index:2,segment_count:4}),{index:2,count:4})
  // Absent, null, and junk all read as "open" and "first", which is the safe
  // direction: a claim held too long is released by `voice_stream_closed`, one
  // released too early silences the reply with nothing to say it went wrong.
  assert.deepEqual(voice.segmentPosition({}),{index:0,count:0})
  assert.deepEqual(voice.segmentPosition({segment_index:null,segment_count:undefined}),{index:0,count:0})
  assert.deepEqual(voice.segmentPosition({segment_index:'x',segment_count:Number.NaN}),{index:0,count:0})
  assert.deepEqual(voice.segmentPosition({segment_index:-3,segment_count:-1}),{index:0,count:0})
  assert.deepEqual(voice.segmentPosition({segment_index:'1',segment_count:'2'}),{index:1,count:2})
})

test('the closing segment releases the claim whichever arrives first',async()=>{
  // The event and the POST response race, and the response path plays the clip
  // itself. The release used to sit below the dedupe return, so a closing
  // segment the response had already started left the stream claimed while the
  // same segment arriving as an event released it.
  // Every id in this file is distinct on purpose: the playback module keeps its
  // claim and suppression sets across tests, so a reused stream arrives already
  // suppressed by whichever test barged in on it last.
  const stream='99999999-9999-4999-8999-999999999999'
  voice.beginRequestedStream(stream,'system','system')
  await voice.playRequestedStreamFirst('race-1',stream,'system','system')
  await settle()
  assert.equal(voice.getPlayback().clipId,'race-1')
  assert.equal(voice.requestedStreamActive(stream),true,'an opening segment must not end the stream')
  // The event for the clip already playing. It is the closing one, so the claim
  // has to go even though there is nothing left to enqueue.
  voice.enqueueRequestedStreamClip('race-1',stream,0,1)
  assert.equal(voice.requestedStreamActive(stream),false,'the closing segment releases the claim on the dedupe path too')
  voice.bargeInPlayback()
})

test('closing a stream releases the claim without cutting queued audio',async()=>{
  const stream='33333333-3333-4333-8333-333333333333'
  voice.beginRequestedStream(stream,'system','system')
  voice.enqueueRequestedStreamClip('close-1',stream,0,0)
  await settle()
  voice.enqueueRequestedStreamClip('close-2',stream,1,0)
  voice.closeRequestedStream(stream)
  assert.equal(voice.getPlayback().clipId,'close-1','closing must not stop what is speaking')
  element().finish()
  await settle()
  assert.equal(voice.getPlayback().clipId,'close-2','already-queued clips still play')
  voice.enqueueRequestedStreamClip('close-3',stream,2,0)
  element().finish()
  await settle()
  assert.equal(voice.getPlayback().clipId,'close-2','a late clip cannot rejoin a closed stream')
  voice.bargeInPlayback()
})

test('barge-in silences streams whose audio has not arrived yet',async()=>{
  // A claim outlives the clip: synthesis runs behind the request, so a stream
  // with nothing playing is still going to speak. Suppressing only the audible
  // stream let a backlog keep talking for minutes after the operator had closed
  // the microphone and had no way left to say stop (2026-08-20).
  const speaking='55555555-5555-4555-8555-555555555555'
  const pending='66666666-6666-4666-8666-666666666666'
  voice.beginRequestedStream(speaking,'system','system')
  voice.claimRequestedStream(pending,'system','system')
  voice.enqueueRequestedStreamClip('loud-1',speaking,0,0)
  await settle()
  assert.equal(voice.getPlayback().clipId,'loud-1')

  voice.bargeInPlayback()
  assert.equal(voice.getPlayback().clipId,null)
  // The clip the daemon was still synthesizing when the user said stop.
  voice.enqueueRequestedStreamClip('late-1',pending,0,0)
  await settle()
  assert.equal(voice.getPlayback().clipId,null,'a stream cut before it spoke stays silent')
  assert.equal(voice.requestedStreamActive(pending),false)
})

test('a non-interrupting claim queues behind what is already speaking',async()=>{
  const first='77777777-7777-4777-8777-777777777777'
  const second='88888888-8888-4888-8888-888888888888'
  voice.beginRequestedStream(first,'system','system')
  voice.enqueueRequestedStreamClip('one',first,0,1)
  await settle()
  assert.equal(voice.getPlayback().clipId,'one')
  voice.claimRequestedStream(second,'system','system')
  voice.enqueueRequestedStreamClip('two',second,0,1)
  assert.equal(voice.getPlayback().clipId,'one','claiming must not take the floor')
  element().finish()
  await settle()
  assert.equal(voice.getPlayback().clipId,'two')
  voice.bargeInPlayback()
})

test('a segment arriving while the previous one is still loading queues behind it',async()=>{
  // Between assigning src and the `play` event the element reports not-playing
  // while being entirely occupied. Segments of one stream arrive close enough
  // together to hit that window, and starting there swallows a whole sentence.
  const stream='44444444-4444-4444-8444-444444444444'
  voice.beginRequestedStream(stream,'system','system')
  voice.enqueueRequestedStreamClip('race-1',stream,0,0)
  voice.enqueueRequestedStreamClip('race-2',stream,1,0)
  await settle()
  assert.equal(voice.getPlayback().clipId,'race-1','the first segment must not be replaced by the second')
  element().finish()
  await settle()
  assert.equal(voice.getPlayback().clipId,'race-2')
  voice.bargeInPlayback()
})

// ---------------------------------------------------------------------------
// Focus-driven playback. Everything above runs before focus has ever been
// reported, which is deliberate: that is the pre-policy state, and it must keep
// playing whatever it played before. From here on the module knows about focus,
// and it cannot be un-known — so a new test that wants the legacy behaviour
// belongs above this line, not below it.
// ---------------------------------------------------------------------------

test('an unfocused session holds its clip instead of speaking over the focused one',async()=>{
  voice.setAutoplayEnabled(true)
  voice.setPlaybackFocus('session-focus')
  voice.enqueueAutoplay('clip-focus','stream-focus','session-focus')
  await settle()
  assert.equal(voice.getPlayback().clipId,'clip-focus')

  voice.enqueueAutoplay('clip-held','stream-held','session-other')
  await settle()
  assert.equal(voice.getPlayback().clipId,'clip-focus','an unfocused session must not even queue behind the focused one')
  assert.deepEqual(voice.heldClipsFor('session-other').map(item=>item.clipId),['clip-held'])
  assert.equal(voice.heldClipTotal(),1)

  element().finish()
  await settle()
  assert.equal(voice.getPlayback().playing,false,'the held clip must not follow on when the focused one ends')
  voice.stopAllPlayback()
  assert.equal(voice.heldClipTotal(),0,'a global stop clears the backlog it would otherwise still offer')
})

test('moving focus never plays a held clip, and asking for it plays them in order',async()=>{
  voice.setAutoplayEnabled(true)
  voice.setPlaybackFocus('session-a')
  voice.enqueueAutoplay('held-1','stream-h1','session-b')
  voice.enqueueAutoplay('held-2','stream-h2','session-b')
  assert.equal(voice.heldClipsFor('session-b').length,2)

  voice.setPlaybackFocus('session-b')
  await settle()
  assert.equal(voice.getPlayback().clipId,null,'arriving at a pane is not a request to be talked at')
  assert.equal(voice.heldClipsFor('session-b').length,2,'the backlog survives the focus move')

  voice.playHeldClips('session-b')
  await settle()
  assert.equal(voice.getPlayback().clipId,'held-1')
  assert.equal(voice.heldClipTotal(),0,'played clips leave the backlog')
  element().finish()
  await settle()
  assert.equal(voice.getPlayback().clipId,'held-2','the backlog plays oldest first')
  voice.stopAllPlayback()
})

test('a pinned session speaks while another pane has focus, and stops when unpinned',async()=>{
  // Voice Comms is the one mode where focus is the wrong question: the operator is
  // talking to that agent hands-free, so its replies are the point.
  voice.setAutoplayEnabled(true)
  voice.setPlaybackFocus('session-a')
  voice.setPinnedPlaybackSession('session-comms',true)
  voice.enqueueAutoplay('comms-1','stream-c1','session-comms')
  await settle()
  assert.equal(voice.getPlayback().clipId,'comms-1')
  assert.equal(voice.heldClipTotal(),0)

  voice.setPinnedPlaybackSession('session-comms',false)
  voice.stopAllPlayback()
  voice.enqueueAutoplay('comms-2','stream-c2','session-comms')
  await settle()
  assert.equal(voice.getPlayback().clipId,null)
  assert.equal(voice.heldClipsFor('session-comms').length,1,'releasing the pin returns the session to the focus rule')
  voice.stopAllPlayback()
})

test('turning a session off, or dismissing it, drops the clips it was holding',async()=>{
  voice.setAutoplayEnabled(true)
  voice.setPlaybackFocus('session-a')
  voice.enqueueAutoplay('drop-1','stream-d1','session-b')
  assert.equal(voice.heldClipsFor('session-b').length,1)
  voice.stopSessionPlayback('session-b')
  assert.equal(voice.heldClipsFor('session-b').length,0,'"off" that leaves a play-me button behind is not off')
  voice.playHeldClips('session-b')
  await settle()
  assert.equal(voice.getPlayback().clipId,null)

  voice.enqueueAutoplay('drop-2','stream-d2','session-b')
  voice.dismissHeldClips('session-b')
  assert.equal(voice.heldClipTotal(),0)
  voice.stopAllPlayback()
})

test('the device toggle clears the backlog rather than leaving it on offer',async()=>{
  voice.setAutoplayEnabled(true)
  voice.setPlaybackFocus('session-a')
  voice.enqueueAutoplay('mute-1','stream-m1','session-b')
  assert.equal(voice.heldClipTotal(),1)
  voice.setAutoplayEnabled(false)
  assert.equal(voice.heldClipTotal(),0)
  voice.enqueueAutoplay('mute-2','stream-m2','session-b')
  assert.equal(voice.heldClipTotal(),0,'a muted device generates no backlog either')
  voice.setAutoplayEnabled(true)
})

test('a held backlog is bounded and keeps the newest clips',async()=>{
  voice.setAutoplayEnabled(true)
  voice.setPlaybackFocus('session-a')
  for(let index=0;index<8;index+=1)voice.enqueueAutoplay(`bound-${index}`,`stream-b${index}`,'session-b')
  const held=voice.heldClipsFor('session-b').map(item=>item.clipId)
  assert.equal(held.length,5,'an hour of talking to itself is not a backlog worth working through')
  assert.deepEqual(held,['bound-3','bound-4','bound-5','bound-6','bound-7'])
  voice.stopAllPlayback()
})

test('with no session focused everything holds, but an unattributed clip still plays',async()=>{
  voice.setAutoplayEnabled(true)
  voice.setPlaybackFocus(null)
  voice.enqueueAutoplay('none-1','stream-n1','session-a')
  await settle()
  assert.equal(voice.getPlayback().clipId,null,'a note or shell in focus means nothing speaks over the operator')
  assert.deepEqual(voice.heldClipSessions(),['session-a'])

  // A clip nobody can attribute to a session cannot be held against one either.
  voice.enqueueAutoplay('loose-1','stream-l1',null)
  await settle()
  assert.equal(voice.getPlayback().clipId,'loose-1')
  voice.stopAllPlayback()

  voice.setPlaybackFocus('session-a')
  voice.enqueueAutoplay('none-2','stream-n2','session-b')
  voice.playAllHeldClips()
  await settle()
  assert.equal(voice.getPlayback().clipId,'none-2')
  assert.equal(voice.heldClipTotal(),0)
  voice.stopAllPlayback()
})

// ------------------------------------------------------------- whole replies
//
// A long reply is synthesized in segments so its first sentence can play while
// the rest is made. Every control here addresses the reply, never a segment.

const reply = (id: string, parts: Array<[string, number]>, overrides: Partial<VoiceClip> = {}): VoiceClip => ({
  id, session_id: 'session-a', created_at: 1, trigger: 'auto', content_mode: 'verbatim',
  engine: 'sapi', voice: 'system default', text: 'hello', format: 'wav',
  size_bytes: 0, status: 'ready', stream_id: `stream-${id}`,
  duration_hint_s: parts.reduce((total, [, duration]) => total + duration, 0),
  parts: parts.map(([partId, duration], index) => ({
    id: partId, segment_index: index, status: 'ready' as const,
    duration_hint_s: duration, size_bytes: 0,
  })),
  ...overrides,
})

test('playing a reply plays every segment of it, in order',async()=>{
  const clip = reply('r1', [['r1-a', 2], ['r1-b', 3]])
  await voice.playClipGroup(clip, 'session-a')
  await settle()
  assert.equal(voice.getPlayback().clipId,'r1-a')
  element().finish()
  await settle()
  // The whole answer, not its opening sentence. Playing the row used to play the
  // first file and stop there.
  assert.equal(voice.getPlayback().clipId,'r1-b')
  assert.equal(voice.clipGroupDeviceState(clip),'playing')
  element().finish()
  await settle()
  assert.equal(voice.clipGroupDeviceState(clip),'played')
  voice.stopAllPlayback()
})

test('a reply already speaking is not restarted by its own response fallback',async()=>{
  const clip = reply('r2', [['r2-a', 2], ['r2-b', 3]])
  await voice.playClipGroup(clip, 'session-a')
  await settle()
  // The live event usually starts the reply before the HTTP response lands, and
  // the response calls this too. Restarting here would clear the segments the
  // events queued and speak the opening again.
  await voice.playClipGroup(clip, 'session-a')
  await settle()
  assert.equal(voice.getPlayback().clipId,'r2-a')
  element().finish()
  await settle()
  assert.equal(voice.getPlayback().clipId,'r2-b','the queued continuation must survive the fallback')
  voice.stopAllPlayback()
})

test('playing a different reply takes the floor from the one speaking',async()=>{
  const first = reply('r3', [['r3-a', 2], ['r3-b', 2]])
  const second = reply('r4', [['r4-a', 2]])
  await voice.playClipGroup(first, 'session-a')
  await settle()
  await voice.playClipGroup(second, 'session-a')
  await settle()
  assert.equal(voice.getPlayback().clipId,'r4-a')
  element().finish()
  await settle()
  // The abandoned reply's own segments must not resume: its element was
  // re-pointed, so nothing will ever fire `ended` for the segment they followed.
  assert.equal(voice.getPlayback().playing,false)
  assert.equal(voice.getPlayback().clipId,'r4-a')
  voice.stopAllPlayback()
})

test('scrubbing past a segment boundary continues the reply from there',async()=>{
  const clip = reply('r5', [['r5-a', 2], ['r5-b', 3], ['r5-c', 4]])
  await voice.playClipGroup(clip, 'session-a')
  await settle()
  // Six seconds in is the third segment, one second into it. The operator scrubbed
  // the reply, not a file, and has no way of knowing there was a boundary there.
  element().currentTime = -1
  voice.seekWithinGroup(clip, 6, 'session-a')
  await settle()
  assert.equal(voice.getPlayback().clipId,'r5-c')
  // Deferred until the element has metadata: `currentTime` is not writable before
  // that, and a silently dropped seek restarts the segment being scrubbed.
  assert.equal(element().currentTime, -1)
  element().emit('loadedmetadata')
  assert.equal(element().currentTime, 1)
  voice.stopAllPlayback()
})

test('an unfocused reply is held once, not once per segment',async()=>{
  voice.setAutoplayEnabled(true)
  voice.setPlaybackFocus('session-focus')
  voice.enqueueAutoplay('seg-1','stream-seg','session-other')
  voice.enqueueAutoplay('seg-2','stream-seg','session-other')
  voice.enqueueAutoplay('seg-3','stream-seg','session-other')
  // One reply waiting, not three clips. Held per segment, a three-sentence answer
  // reported "3 clips waiting" and ate three of the five slots a session keeps.
  assert.equal(voice.heldClipTotal(),1)
  assert.deepEqual(voice.heldClipsFor('session-other')[0].partIds,['seg-1','seg-2','seg-3'])

  voice.playHeldClips('session-other')
  await settle()
  assert.equal(voice.getPlayback().clipId,'seg-1')
  element().finish()
  await settle()
  assert.equal(voice.getPlayback().clipId,'seg-2','a held reply plays through, not just its opening')
  voice.stopAllPlayback()
})

test('a reply that has finished plays again from its first segment',async()=>{
  const clip = reply('r7', [['r7-a', 2], ['r7-b', 2]])
  await voice.playClipGroup(clip, 'session-a')
  await settle()
  element().finish()
  await settle()
  element().finish()
  await settle()
  // The element keeps its clip id after it ends, so the "already in flight" guard
  // has to be about *busy*, not about which clip is loaded - otherwise pressing
  // play on a reply you just heard does nothing at all.
  await voice.playClipGroup(clip, 'session-a')
  await settle()
  assert.equal(voice.getPlayback().clipId,'r7-a')
  assert.equal(voice.getPlayback().playing,true)
  voice.stopAllPlayback()
})

test('a reply heard before the daemon joined it still reads as played',async()=>{
  voice.setAutoplayEnabled(true)
  voice.setPlaybackFocus('session-a')
  const clip = reply('r6', [['r6-a', 1], ['r6-b', 1]])
  await voice.playClipGroup(clip, 'session-a')
  await settle()
  element().finish()
  await settle()
  element().finish()
  await settle()
  // The daemon replaces the segments with one joined clip whose id nothing here
  // has ever seen. The reply was heard, and must not read as unplayed because of
  // that.
  const joined: VoiceClip = { ...clip, id: 'r6-joined', parts: undefined, duration_hint_s: 2 }
  assert.equal(voice.clipGroupDeviceState(joined),'played')
  voice.stopAllPlayback()
})
