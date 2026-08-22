import assert from 'node:assert/strict'
import test from 'node:test'
import { isPreviewableDocument, STATIC_PREVIEW_SUFFIXES } from '../src/staticPreview.ts'
import { isStaticPreview, previewLabel, type Preview } from '../src/processFleet.ts'

const preview = (overrides: Partial<Preview>): Preview => ({
  id: 'p', session_id: '', project_id: 'default', url: 'http://127.0.0.1:5173/',
  host: '127.0.0.1', port: 5173, source: 'detected', viewport: 'responsive', ...overrides,
})

test('only pages are offered a preview', () => {
  assert.equal(isPreviewableDocument('site/index.html'), true)
  assert.equal(isPreviewableDocument('site/page.htm'), true)
  assert.equal(isPreviewableDocument('doc.xhtml'), true)
  assert.equal(isPreviewableDocument('style.css'), false)
  assert.equal(isPreviewableDocument('notes.md'), false)
  assert.equal(isPreviewableDocument('logo.svg'), false)
})

test('the suffix match is case-insensitive and reads the last dot', () => {
  assert.equal(isPreviewableDocument('PAGE.HTML'), true)
  assert.equal(isPreviewableDocument('a.html.bak'), false)
  assert.equal(isPreviewableDocument('archive.tar.html'), true)
})

test('a name with no suffix, or only a leading dot, is not a page', () => {
  assert.equal(isPreviewableDocument('README'), false)
  // A dotfile's leading dot does not make its name a suffix.
  assert.equal(isPreviewableDocument('.html'), false)
  assert.equal(isPreviewableDocument('dir.html/README'), false)
})

test('windows separators resolve to the same leaf as posix ones', () => {
  assert.equal(isPreviewableDocument('site\\index.html'), true)
  assert.equal(isPreviewableDocument('site\\notes.md'), false)
})

test('the allowlist is the one the daemon enforces', () => {
  assert.deepEqual([...STATIC_PREVIEW_SUFFIXES], ['.html', '.htm', '.xhtml'])
})

test('a loopback preview is labelled by its port and a static one by its file', () => {
  assert.equal(previewLabel(preview({})), ':5173')
  assert.equal(previewLabel(preview({ kind: 'static', label: 'index.html' })), 'index.html')
  // Restored from an older mirror that predates the label field.
  assert.equal(previewLabel(preview({ kind: 'static', entry: 'docs/page.html' })), 'docs/page.html')
  assert.equal(previewLabel(preview({ kind: 'static', id: 'abc' })), 'abc')
})

test('an older daemon reports no kind and is still a loopback preview', () => {
  assert.equal(isStaticPreview(preview({})), false)
  assert.equal(isStaticPreview(preview({ kind: 'loopback' })), false)
  assert.equal(isStaticPreview(preview({ kind: 'static' })), true)
})
