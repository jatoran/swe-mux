import assert from 'node:assert/strict'
import test from 'node:test'
import { localPreviewUrl } from '../src/previewLinks.ts'

test('terminal localhost links normalize into preview-safe literal loopback URLs',()=>{
  assert.equal(localPreviewUrl('http://localhost:5173/app?debug=1#top'),'http://127.0.0.1:5173/app')
  assert.equal(localPreviewUrl('http://0.0.0.0:8000/'),'http://127.0.0.1:8000/')
  assert.equal(localPreviewUrl('https://example.com/'),null)
  assert.equal(localPreviewUrl('file:///tmp/index.html'),null)
})
