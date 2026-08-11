import assert from 'node:assert/strict'
import test from 'node:test'
import { resourceSaveIndicator } from '../src/resourceSaveIndicator.ts'

test('autosave indicator uses fixed semantic tones for stable and changing states', () => {
  assert.deepEqual(resourceSaveIndicator('ready'), { tone: 'saved', label: 'Saved' })
  assert.deepEqual(resourceSaveIndicator('saved'), { tone: 'saved', label: 'Saved' })
  assert.deepEqual(resourceSaveIndicator('modified'), { tone: 'modified', label: 'Modified' })
  assert.deepEqual(resourceSaveIndicator('saving'), { tone: 'modified', label: 'Saving' })
  assert.deepEqual(resourceSaveIndicator('conflict'), { tone: 'error', label: 'Save conflict' })
  assert.deepEqual(resourceSaveIndicator('read-only'), { tone: 'error', label: 'Read only' })
  assert.deepEqual(resourceSaveIndicator('loading'), { tone: 'pending', label: 'Loading' })
})
