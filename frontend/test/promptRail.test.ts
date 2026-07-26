import assert from 'node:assert/strict'
import test from 'node:test'
import { findPromptTemplate, promptItemSummary, splitPromptKey } from '../src/promptRail.ts'
import { mergeRail, railPayload, type RailItem } from '../src/commandRail.ts'

// The library's own identity is `scope:id`; template ids are UUIDs, so the first
// colon is the seam and the rest is the id verbatim.
test('a library key splits into scope and id', () => {
  assert.deepEqual(splitPromptKey('global:9f0e-1234'), { scope: 'global', id: '9f0e-1234' })
  assert.deepEqual(splitPromptKey('project:abc'), { scope: 'project', id: 'abc' })
  assert.equal(splitPromptKey('nocolon'), null)
  assert.equal(splitPromptKey(':leading'), null)
  assert.equal(splitPromptKey('trailing:'), null)
})

const template = (over: Record<string, unknown> = {}) => ({
  id: 'abc', key: 'global:abc', scope: 'global', title: 'Ship it', body: 'ship {{what}}', tags: [],
  variables: ['what'], backends: ['claude'], created_at: 0, updated_at: 0, revision: 'r',
  favorite: false, use_count: 0, conflict: false, ...over,
}) as never

test('a rail item resolves its template by key, not by bare id', () => {
  // A global and a Project template may share a stable id (the library keeps both,
  // flagged as a conflict); the key is what disambiguates them.
  const items = [template(), template({ key: 'project:abc', scope: 'project', title: 'Ship it (project)' })]
  assert.equal(findPromptTemplate(items, 'project:abc')?.title, 'Ship it (project)')
  assert.equal(findPromptTemplate(items, 'global:abc')?.title, 'Ship it')
  assert.equal(findPromptTemplate(items, 'global:gone'), null)
})

test('a dangling prompt item is described as dangling, not as blank', () => {
  const item: RailItem = { id: 'custom:prompt:abc:0', type: 'prompt', label: 'Ship it', promptKey: 'global:abc' }
  assert.equal(promptItemSummary(item, [template()]), 'Ship it')
  assert.equal(promptItemSummary(item, []), 'missing template')
  // Templates not loaded yet is not the same as deleted.
  assert.equal(promptItemSummary(item, null), 'global:abc')
  assert.equal(promptItemSummary({ ...item, promptKey: undefined }, []), 'no template attached')
})

test('prompt items survive a save round-trip and carry no local payload', () => {
  const saved: RailItem[] = [{ id: 'custom:prompt:abc:0', type: 'prompt', label: 'Ship it', promptKey: 'global:abc', placement: 'both' }]
  const merged = mergeRail(saved).find(item => item.id === 'custom:prompt:abc:0')
  assert.equal(merged?.promptKey, 'global:abc')
  assert.equal(merged?.placement, 'both')
  // The body lives in the library and is fetched on click, so there is nothing to
  // inject synchronously — a caller that ignored the type would inject nothing.
  assert.equal(railPayload(saved[0], 'claude'), '')
})
