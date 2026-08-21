import assert from 'node:assert/strict'
import test from 'node:test'
import { resourceSaveIndicator } from '../src/resourceSaveIndicator.ts'

test('autosave indicator uses fixed semantic tones for stable and changing states', () => {
  assert.deepEqual(resourceSaveIndicator('ready'), { tone: 'saved', label: 'Saved' })
  assert.deepEqual(resourceSaveIndicator('saved'), { tone: 'saved', label: 'Saved' })
  assert.deepEqual(resourceSaveIndicator('modified'), { tone: 'modified', label: 'Modified' })
  assert.deepEqual(resourceSaveIndicator('saving'), { tone: 'modified', label: 'Saving' })
  assert.deepEqual(resourceSaveIndicator('conflict'), { tone: 'error', label: 'Save conflict' })
  // A note that stopped autosaving because it was being written elsewhere reads as a fact the
  // user must see, not as the fallback "paused" pending state.
  assert.deepEqual(resourceSaveIndicator('paused'), { tone: 'error', label: 'Autosave paused' })
  assert.deepEqual(resourceSaveIndicator('read-only'), { tone: 'error', label: 'Read only' })
  assert.deepEqual(resourceSaveIndicator('loading'), { tone: 'pending', label: 'Loading' })
})
