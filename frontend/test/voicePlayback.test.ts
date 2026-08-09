import assert from 'node:assert/strict'
import test from 'node:test'

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
