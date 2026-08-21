import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

import {
  indexTranscriptAudio, transcriptAudioClip, transcriptAudioHint, transcriptAudioMark,
  transcriptAudioState, TRANSCRIPT_AUDIO_KINDS,
} from '../src/transcriptAudio.ts'
import type { VoiceClip } from '../src/types.ts'

const dir = join(import.meta.dirname, '..', 'src')
const read = (name: string) => readFileSync(join(dir, name), 'utf8').replace(/\r\n/g, '\n')
const tab = read('TranscriptTab.tsx')
const css = read('style.css')

const clip = (overrides: Partial<VoiceClip> & Pick<VoiceClip, 'id'>): VoiceClip => ({
  session_id: 's1',
  agent_run_id: 'run-1',
  created_at: 100,
  trigger: 'auto',
  content_mode: 'verbatim',
  engine: 'sapi',
  voice: 'system default',
  text: 'hello',
  format: 'wav',
  size_bytes: 0,
  status: 'ready',
  ...overrides,
})

test('clips index by the message they speak, and only anchored ones can', () => {
  const index = indexTranscriptAudio([
    clip({ id: 'a', message_anchor: 'm1', content_mode: 'verbatim' }),
    clip({ id: 'b', message_anchor: 'm1', content_mode: 'summary' }),
    clip({ id: 'c', message_anchor: 'm2', content_mode: 'verbatim' }),
    // Application speech, and every clip written before the anchor existed. Neither
    // renders a reply, so neither can be offered as one reply's audio.
    clip({ id: 'd', message_anchor: null }),
    clip({ id: 'e' }),
  ])
  assert.deepEqual([...index.keys()], ['m1', 'm2'])
  assert.equal(index.get('m1')?.verbatim?.id, 'a')
  assert.equal(index.get('m1')?.summary?.id, 'b')
  assert.equal(index.get('m2')?.summary, undefined)
})

test('the newest clip for a kind wins, including a newer failure', () => {
  // `voice_clips` is returned newest-first, so the first row seen for a pair is the
  // newest. A regenerate supersedes what it replaced, and a failed retry is the truth
  // about the last attempt - reporting the older success would offer a play button for
  // audio the operator just told the app to remake.
  const index = indexTranscriptAudio([
    clip({ id: 'new', message_anchor: 'm1', created_at: 200 }),
    clip({ id: 'old', message_anchor: 'm1', created_at: 100 }),
  ])
  assert.equal(index.get('m1')?.verbatim?.id, 'new')

  const retried = indexTranscriptAudio([
    clip({ id: 'failed', message_anchor: 'm1', status: 'failed', created_at: 200 }),
    clip({ id: 'ready', message_anchor: 'm1', created_at: 100 }),
  ])
  assert.equal(transcriptAudioState(retried.get('m1'), 'verbatim', false), 'failed')
  assert.equal(transcriptAudioClip(retried.get('m1'), 'verbatim'), null)
})

test('a marker has four states and only one of them plays', () => {
  const ready = { verbatim: clip({ id: 'a', message_anchor: 'm1' }) }
  assert.equal(transcriptAudioState(ready, 'verbatim', false), 'ready')
  assert.equal(transcriptAudioClip(ready, 'verbatim')?.id, 'a')
  // The other kind of the same message is a separate question with a separate answer.
  assert.equal(transcriptAudioState(ready, 'summary', false), 'none')
  assert.equal(transcriptAudioClip(ready, 'summary'), null)

  // A clip the daemon is already making reads as generating even though this tab did
  // not ask for it: the automatic path may be making exactly this one, and offering a
  // generate button beside it would pay for the same audio twice.
  const inFlight = { summary: clip({ id: 'b', message_anchor: 'm1', content_mode: 'summary', status: 'synthesizing' as const }) }
  assert.equal(transcriptAudioState(inFlight, 'summary', false), 'generating')

  // A local request beats `none` and a stale failure, because it is newer than either.
  assert.equal(transcriptAudioState(undefined, 'summary', true), 'generating')
  const failed = { summary: clip({ id: 'c', message_anchor: 'm1', content_mode: 'summary', status: 'failed' as const }) }
  assert.equal(transcriptAudioState(failed, 'summary', true), 'generating')
  // It never beats a clip that has arrived: the thing to do with audio is play it.
  assert.equal(transcriptAudioState(ready, 'verbatim', true), 'ready')

  assert.equal(transcriptAudioMark('ready'), '▶')
  assert.notEqual(transcriptAudioMark('none'), '▶')
  assert.notEqual(transcriptAudioMark('generating'), '▶')
  assert.notEqual(transcriptAudioMark('failed'), '▶')
})

test('the two kinds state what they cost, because they cost different things', () => {
  assert.deepEqual([...TRANSCRIPT_AUDIO_KINDS], ['summary', 'verbatim'])
  // A summary is a model call against the daily read-aloud budget; verbatim never
  // touches a model. This is the only surface where the choice is offered per message,
  // so it is the one place the difference has to be readable before clicking.
  assert.match(transcriptAudioHint('none', 'summary'), /budget/)
  assert.match(transcriptAudioHint('none', 'verbatim'), /no model call/)
  assert.doesNotMatch(transcriptAudioHint('ready', 'verbatim'), /budget/)
})

test('the reader plays an existing clip and never regenerates it', () => {
  // The rule the anchor exists for: automatic read-aloud and this button produce
  // identical audio for the same reply, so pressing a ready marker is a play and the
  // daemon answers a repeat request from the store rather than by spending again.
  assert.match(tab, /onClick=\{\(\) => \(clip \? onPlay\(clip\) : onSpeak\(kind\)\)\}/)
  assert.match(tab, /message_id: message\.message_id,/)
  assert.match(tab, /content_mode: kind,/)
  // Its own stream: a per-message request must not join, or cut, whatever a pane's
  // automatic read-aloud happens to be speaking.
  assert.match(tab, /stream_id: newVoiceStreamId\(\),/)
  // Replies only. A prompt is something the operator wrote.
  assert.match(tab, /audioRequested && message\.role === 'assistant'/)
  // A per-item surface carries no gate: off means the markers are simply not drawn,
  // and the one gate for the master switch lives in the voice panel's `tts` tab.
  assert.match(tab, /const audioIndex = readAloud \? indexTranscriptAudio\(clips\) : null/)
  assert.doesNotMatch(tab, /GrantGate/)
})

test('only the ready marker is painted as a play button', () => {
  // A glance down the column has to answer "which of these replies has audio" without
  // reading four words per message, so green is reserved for the one state that plays.
  const ready = css.match(/\n\s*\.transcript-audio\.ready\{([^}]+)\}/)
  assert.ok(ready, 'missing ready rule')
  assert.match(ready[1], /var\(--green/)
  for (const state of ['generating', 'failed']) {
    const rule = css.match(new RegExp(`\\n\\s*\\.transcript-audio\\.${state}\\{([^}]+)\\}`))
    assert.ok(rule, `missing ${state} rule`)
    assert.doesNotMatch(rule[1], /--green/)
  }
  // Generating is disabled rather than merely styled: a second click would be a
  // duplicate request for audio already being made.
  assert.match(tab, /disabled=\{state === 'generating'\}/)
})
