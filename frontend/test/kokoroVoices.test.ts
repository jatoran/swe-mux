import assert from 'node:assert/strict'
import test from 'node:test'

import { kokoroVoiceLabel, sortKokoroVoices } from '../src/kokoroVoices.ts'

test('voice ids decode into readable labels', () => {
  assert.deepEqual(kokoroVoiceLabel('af_heart'), { id: 'af_heart', name: 'Heart', flavor: 'American female' })
  assert.deepEqual(kokoroVoiceLabel('bm_fable'), { id: 'bm_fable', name: 'Fable', flavor: 'British male' })
  // An unknown scheme degrades to the raw id rather than inventing a flavor.
  const odd = kokoroVoiceLabel('zz_thing')
  assert.equal(odd.name, 'Thing')
  assert.equal(odd.flavor, '')
})

test('picker order groups US female, US male, UK female, UK male', () => {
  const sorted = sortKokoroVoices(['bm_daniel', 'af_sky', 'bf_emma', 'am_adam', 'af_heart'])
  assert.deepEqual(sorted, ['af_heart', 'af_sky', 'am_adam', 'bf_emma', 'bm_daniel'])
})
