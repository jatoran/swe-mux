import assert from 'node:assert/strict'
import test from 'node:test'

// assistantSpeech drives voice.ts, which owns one module-level <audio> element
// and reads localStorage, so both are stubbed before either module is imported.
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
  emit(type: string) { for (const handler of this.handlers[type] || []) handler() }
  async play() { this.paused = false; this.emit('play') }
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
const speech = await import('../src/assistantSpeech.ts')

type SpeakCall = { text: string; stream_id?: string; continue_stream?: boolean; final?: boolean }

let calls: SpeakCall[] = []
let clipCounter = 0

const realFetch = globalThis.fetch
globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
  const url = String(input)
  const body = (init?.body ? JSON.parse(String(init.body)) : { text: '' }) as SpeakCall
  if (url !== '/api/voice/speak') return new Response('{}', { status: 200 })
  calls.push(body)
  clipCounter += 1
  return new Response(
    JSON.stringify({ id: `clip-${clipCounter}`, stream_id: body.stream_id }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  )
}) as typeof fetch

// Module state is a singleton, so each test starts from a closed stream and a
// silent element; the close each retirement posts is dropped with the log.
const reset = () => { speech.cancelAssistantSpeech(); voice.stopAllPlayback(); calls = [] }
const openedStreams = () => new Set(calls.map(call => call.stream_id))

test('a card announcement joins the turn already speaking', async () => {
  // The card and the sentences before it are one utterance. A second stream for
  // the card would take the floor and cut the sentence mid-word.
  reset()
  speech.beginTurnSpeech('turn-1')
  await speech.speakTurnText('turn-1', 'Working on it.')
  await speech.speakAnnouncement('Spawn a claude session in swe-mux. Say cancel to stop it.')
  assert.equal(openedStreams().size, 1, 'one stream carries the whole turn')
  assert.deepEqual(calls.map(call => !!call.continue_stream), [false, true])
  assert.deepEqual(calls.map(call => call.final), [false, false])
})

test('a second announcement joins the first instead of restarting it', async () => {
  // The regression this pins: an announcement that opened its own interrupting
  // stream made every repeat cut the previous one, so the operator heard the
  // same sentence begin over and over rather than once.
  reset()
  await speech.speakAnnouncement('First card. Confirm or cancel?')
  const playing = voice.getPlayback().clipId
  await speech.speakAnnouncement('Second card. Confirm or cancel?')
  assert.equal(openedStreams().size, 1, 'both announcements share one stream')
  assert.deepEqual(calls.map(call => !!call.continue_stream), [false, true])
  assert.equal(voice.getPlayback().clipId, playing, 'the first must still be speaking')
})

test('the spoken verdict does take the floor', async () => {
  // Opposite intent, deliberately: the operator has just answered the card being
  // read out, so finishing the question is worse than cutting it.
  reset()
  await speech.speakAnnouncement('Spawn a claude session in swe-mux. Say cancel to stop it.')
  const announcement = voice.getPlayback().clipId
  await speech.speakOnce('Cancelled: spawn a claude session in swe-mux.')
  assert.equal(openedStreams().size, 2)
  assert.notEqual(voice.getPlayback().clipId, announcement)
  assert.equal(voice.getPlayback().playing, true)
})

test('barge-in stops the turn from posting any more of itself', async () => {
  // Playback is the authority on whether a stream still matters. Posting text
  // into a stream nobody will play only spends synthesis on silence — and it is
  // what let a backlog keep talking after the microphone was closed.
  reset()
  speech.beginTurnSpeech('turn-2')
  await speech.speakTurnText('turn-2', 'First sentence.')
  const spoken = () => calls.filter(call => call.text.trim()).length
  const posted = spoken()
  voice.bargeInPlayback()
  await speech.speakTurnText('turn-2', 'Second sentence.')
  await speech.speakTurnText('turn-2', 'Third sentence.')
  assert.equal(spoken(), posted, 'no further TEXT may be sent for synthesis')
  // Counted on text rather than on calls, because the cut stream still has to be
  // sealed: a stream is only joined when it closes, so abandoning it would leave
  // the sentences already synthesized scattered across loose segments instead of
  // one clip the operator can replay from the talk tab.
  assert.ok(
    calls.some(call => !call.text.trim() && call.final && call.stream_id),
    'the abandoned stream must be closed, not left open on the daemon',
  )
})

test('a new turn supersedes the reply the operator moved on from', async () => {
  reset()
  speech.beginTurnSpeech('turn-3')
  await speech.speakTurnText('turn-3', 'An answer nobody waited for.')
  const stale = voice.getPlayback().clipId
  speech.beginTurnSpeech('turn-4')
  assert.equal(voice.getPlayback().clipId, null, 'the previous turn is cut')
  await speech.speakTurnText('turn-4', 'The answer to the new question.')
  assert.notEqual(voice.getPlayback().clipId, stale)
  assert.equal(openedStreams().size, 2)
})

test('a turn that never spoke says its completion fallback once', async () => {
  reset()
  speech.beginTurnSpeech('turn-5')
  await speech.endTurnSpeech('turn-5', 'Done.')
  assert.deepEqual(calls.map(call => call.text), ['Done.', ''])
  assert.equal(calls[1].final, true, 'and closes the stream it opened')
})

test('a turn that already spoke does not repeat itself at the end', async () => {
  reset()
  speech.beginTurnSpeech('turn-6')
  await speech.speakTurnText('turn-6', 'Three sessions are working.')
  await speech.endTurnSpeech('turn-6', 'Three sessions are working.')
  assert.deepEqual(calls.map(call => call.text), ['Three sessions are working.', ''])
})

test.after(() => { globalThis.fetch = realFetch })
