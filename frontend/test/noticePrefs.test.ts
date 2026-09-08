import assert from 'node:assert/strict'
import test from 'node:test'
import { NOTICE_IDS, normalizeNoticePreferences } from '../src/noticePrefs.ts'

test('the stored document is the hidden id list, and nothing it does not recognise', () => {
  // An absent or malformed domain hides nothing: a device that has never loaded the
  // store shows every notice rather than guessing at what was dismissed.
  assert.deepEqual(normalizeNoticePreferences(undefined), { hidden: [] })
  assert.deepEqual(normalizeNoticePreferences(null), { hidden: [] })
  assert.deepEqual(normalizeNoticePreferences('hidden'), { hidden: [] })
  assert.deepEqual(normalizeNoticePreferences({ hidden: 'stranded-sessions' }), { hidden: [] })
  assert.deepEqual(normalizeNoticePreferences({ hidden: ['stranded-sessions'] }), { hidden: ['stranded-sessions'] })
  // Unknown ids are dropped on the way in, never re-saved as ours: they are a newer
  // frontend's vocabulary, which this build cannot draw, or a corrupted entry.
  assert.deepEqual(
    normalizeNoticePreferences({ hidden: ['stranded-sessions', 'from-a-newer-frontend', 7, null] }),
    { hidden: ['stranded-sessions'] },
  )
  // Deduplicated and sorted, so the same choice always serialises to the same bytes
  // and the configurator's digest guard does not refuse a no-op rewrite.
  assert.deepEqual(
    normalizeNoticePreferences({ hidden: ['stranded-sessions', 'stranded-sessions'] }),
    { hidden: ['stranded-sessions'] },
  )
})

test('every notice id is one the vocabulary declares', () => {
  // The union is the whole point of the module: a producer and the Settings control
  // that undoes it must name the same thing, and a string typo is a notice that can
  // never be un-hidden.
  for (const id of NOTICE_IDS) assert.deepEqual(normalizeNoticePreferences({ hidden: [id] }), { hidden: [id] })
})
