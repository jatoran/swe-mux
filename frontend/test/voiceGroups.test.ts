import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

import type { VoiceClip } from '../src/types.ts'
import {
  clipPartIds, clipParts, groupPosition, partAtTime, partSpans, spansDuration,
} from '../src/voiceGroups.ts'

const dir = join(import.meta.dirname, '..', 'src')
const read = (name: string) => readFileSync(join(dir, name), 'utf8').replace(/\r\n/g, '\n')
const tab = read('VoiceReadTab.tsx')

const clip = (overrides: Partial<VoiceClip> & Pick<VoiceClip, 'id'>): VoiceClip => ({
  session_id: 's1',
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

const segmented = clip({
  id: 'a',
  stream_id: 'stream-1',
  duration_hint_s: 9,
  parts: [
    { id: 'a', segment_index: 0, status: 'ready', duration_hint_s: 2, size_bytes: 1 },
    { id: 'b', segment_index: 1, status: 'ready', duration_hint_s: 3, size_bytes: 1 },
    { id: 'c', segment_index: 2, status: 'ready', duration_hint_s: 4, size_bytes: 1 },
  ],
})

test('a clip with no parts is its own single part', () => {
  // A joined clip, and anything the daemon hands back without the breakdown. No
  // caller should have to ask whether a reply happens to be segmented right now.
  const joined = clip({ id: 'j', duration_hint_s: 5 })
  assert.deepEqual(clipPartIds(joined), ['j'])
  assert.equal(clipParts(joined).length, 1)
  assert.equal(spansDuration(partSpans(joined)), 5)
})

test('the reply is one timeline, whatever it is stored in', () => {
  const spans = partSpans(segmented)
  assert.deepEqual(spans.map(span => span.start), [0, 2, 5])
  assert.equal(spansDuration(spans), 9)
  // Position is stated in the reply's own terms: three seconds into the second
  // segment is five seconds into the answer.
  assert.equal(groupPosition(spans, 'b', 3), 5)
  assert.equal(groupPosition(spans, 'a', 0), 0)
  // A clip that is not part of this reply is how a row tells "I am the one
  // playing" from "I am a row".
  assert.equal(groupPosition(spans, 'elsewhere', 1), null)
})

test('the playing segment reports its real length rather than its estimate', () => {
  // The daemon's hint is rounded, and is a word-count guess when the measurement
  // failed. Using the element's own duration keeps the bar from jumping.
  const spans = partSpans(segmented, 'a', 2.4)
  assert.deepEqual(spans.map(span => span.start), [0, 2.4, 5.4])
  assert.equal(spansDuration(spans), 9.4)
})

test('a point on the reply resolves to the segment that covers it', () => {
  const spans = partSpans(segmented)
  assert.deepEqual(partAtTime(spans, 0), { id: 'a', offset: 0, index: 0 })
  assert.deepEqual(partAtTime(spans, 2), { id: 'b', offset: 0, index: 1 })
  assert.deepEqual(partAtTime(spans, 6), { id: 'c', offset: 1, index: 2 })
  // Clamped at both ends: a scrub bar hands over whatever the pointer landed on,
  // including exactly the duration, and the answer there is the end of the reply.
  assert.deepEqual(partAtTime(spans, -5), { id: 'a', offset: 0, index: 0 })
  assert.deepEqual(partAtTime(spans, 99), { id: 'c', offset: 4, index: 2 })
  assert.equal(partAtTime([], 1), null)
})

test('a reply with no durations yet still resolves to its first segment', () => {
  // Every segment reports 0 until synthesis measures it, so every span starts at
  // 0 and the last one wins the scan. Playing must still start at the beginning.
  const pending = clip({
    id: 'p',
    stream_id: 'stream-2',
    parts: [
      { id: 'p', segment_index: 0, status: 'ready', duration_hint_s: null, size_bytes: 0 },
      { id: 'q', segment_index: 1, status: 'synthesizing', duration_hint_s: null, size_bytes: 0 },
    ],
  })
  assert.deepEqual(partAtTime(partSpans(pending), 0), { id: 'p', offset: 0, index: 0 })
})

test('the tts tab addresses replies, never segments', () => {
  // The row, the transport and the row state are all in the reply's terms. Any
  // one of them slipping back to the clip id shows a segmented reply as several
  // rows, or drops the transport the moment a reply passes its first sentence.
  assert.match(tab, /clipPartIds\(clip\)\.includes\(playback\.clipId\)/)
  assert.match(tab, /const loadedSpans = loaded \? partSpans\(loaded, playback\.clipId, playback\.duration\) : \[\]/)
  assert.match(tab, /const loadedDuration = spansDuration\(loadedSpans\)/)
  assert.match(tab, /groupPosition\(loadedSpans, playback\.clipId, playback\.position\)/)
  assert.match(tab, /seekWithinGroup\(loaded, Number\(event\.currentTarget\.value\), loaded\.session_id\)/)
  assert.match(tab, /const device = clipGroupDeviceState\(clip\)/)
  assert.match(tab, /playClipGroup\(clip, clip\.session_id\)/)
  // Playable as soon as any of it exists: a reply still being spoken plays what
  // has been made, and one that failed part way still says what it managed to.
  assert.match(tab, /const playable = clipParts\(clip\)\.some\(part => part\.status === 'ready'\)/)
  assert.match(tab, /disabled=\{!playable\}/)
  // Pause resumes where it stopped rather than replaying the reply.
  assert.match(tab, /void resumePlayback\(\)/)
})
