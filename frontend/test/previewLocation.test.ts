import assert from 'node:assert/strict'
import test from 'node:test'
import {
  PREVIEW_REVISION_MAX_PATHS, previewPagePath, readPreviewLocation, revisionPaths,
} from '../src/previewLocation.ts'

const ID = '40bd0a53490ec8e7'

test('a page path under the preview route is accepted as written', () => {
  assert.equal(previewPagePath(ID, ''), '')
  assert.equal(previewPagePath(ID, 'cart-drawings.html'), 'cart-drawings.html')
  assert.equal(previewPagePath(ID, 'JAR%20Systems/campaigns/'), 'JAR%20Systems/campaigns/')
  assert.equal(previewPagePath(ID, 'app/?tab=2#top'), 'app/?tab=2#top')
})

test('a path that would leave the preview route is refused rather than repaired', () => {
  // The value is written by the previewed page, which is untrusted code.
  for (const value of [
    '/api/sessions', '//evil.example/x', 'https://evil.example/', 'javascript:alert(1)',
    '../../api/sessions', 'a/../../../api', '\\\\evil', 'line\nbreak', 42, null, undefined,
    'x'.repeat(2049),
  ]) {
    assert.equal(previewPagePath(ID, value), null, `accepted ${String(value).slice(0, 40)}`)
  }
})

test('only the bridge location message is read, and its paths are validated one by one', () => {
  assert.equal(readPreviewLocation(ID, { type: 'location', path: 'a.html' }), null)
  assert.equal(readPreviewLocation(ID, { source: 'swe-mux-preview', type: 'other', path: '' }), null)
  assert.equal(readPreviewLocation(ID, { source: 'swe-mux-preview', type: 'location', path: '/x' }), null)
  assert.deepEqual(
    readPreviewLocation(ID, {
      source: 'swe-mux-preview', type: 'location', path: 'page.html',
      resources: ['ref/a.jpg', '/escape.js', 'ref/a.jpg', 'page.html', 'style.css', 7],
    }),
    { path: 'page.html', resources: ['ref/a.jpg', 'style.css'] },
  )
})

test('a location report cannot name more paths than one revision check allows', () => {
  const resources = Array.from({ length: 100 }, (_, index) => `ref/${index}.jpg`)
  const location = readPreviewLocation(ID, { source: 'swe-mux-preview', type: 'location', path: 'p.html', resources })
  assert.ok(location)
  assert.equal(revisionPaths(location).length, PREVIEW_REVISION_MAX_PATHS)
})

test('the revision check fingerprints the page without its fragment, page first', () => {
  assert.deepEqual(
    revisionPaths({ path: 'page.html#section', resources: ['page.html', 'app.js'] }),
    ['page.html', 'app.js'],
  )
})
