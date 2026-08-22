import assert from 'node:assert/strict'
import test from 'node:test'
import { findPromptTemplate, promptItemSummary, railItemLabel, splitPromptKey } from '../src/promptRail.ts'
import { normalizeRailConfig, railPayload, type RailItem } from '../src/commandRail.ts'
import { orderPromptTemplates } from '../src/promptTemplates.ts'

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

// A configured button with no name of its own follows the template's name. The stored
// label is a snapshot taken when it was added, and a snapshot of a name is exactly the
// copy a `prompt` item exists to avoid — the body is a pointer, so the name is too.
test('an auto-labelled prompt button renders the template’s live title', () => {
  const auto: RailItem = { id: 'custom:prompt:abc', type: 'prompt', label: 'Ship it', autoLabel: true, promptKey: 'global:abc' }
  const renamed = [template({ title: 'Ship it properly' })]
  assert.equal(railItemLabel(auto, renamed), 'Ship it properly')
  // Until the library is read, and if the template is gone, the stored copy stands:
  // the dangling case is reported on the press, where there is room to say which.
  assert.equal(railItemLabel(auto, null), 'Ship it')
  assert.equal(railItemLabel(auto, []), 'Ship it')
})

test('a typed label is never overridden, and neither is one saved before the flag existed', () => {
  const renamed = [template({ title: 'Ship it properly' })]
  const typed: RailItem = { id: 'p', type: 'prompt', label: 'Ship', autoLabel: false, promptKey: 'global:abc' }
  assert.equal(railItemLabel(typed, renamed), 'Ship')
  // No flag at all is what every item saved before this rule looks like. Treating
  // those as auto would rename buttons somebody deliberately named.
  const legacy: RailItem = { id: 'p', type: 'prompt', label: 'Ship', promptKey: 'global:abc' }
  assert.equal(railItemLabel(legacy, renamed), 'Ship')
  // Nothing but a prompt item resolves a title, whatever the flag says.
  assert.equal(railItemLabel({ id: 's', type: 'skill', label: 'commit', text: 'commit', autoLabel: true }, renamed), 'commit')
})

test('the picker orders by favourite, then recency, then title', () => {
  const items = [
    template({ id: 'c', key: 'global:c', title: 'Cold b' }),
    template({ id: 'a', key: 'global:a', title: 'Cold a' }),
    template({ id: 'r', key: 'global:r', title: 'Recent', last_used_at: 500 }),
    template({ id: 'o', key: 'global:o', title: 'Older', last_used_at: 100 }),
    template({ id: 'f', key: 'global:f', title: 'Zeta', favorite: true }),
  ]
  assert.deepEqual(orderPromptTemplates(items).map(item => item.title), ['Zeta', 'Recent', 'Older', 'Cold a', 'Cold b'])
  // Pure: sorting for the picker must not reorder the caller's own list.
  assert.deepEqual(orderPromptTemplates(items).map(item => item.id).length, 5)
  assert.deepEqual(items.map((item: { id: string }) => item.id), ['c', 'a', 'r', 'o', 'f'])
})

test('prompt items survive a save round-trip and carry no local payload', () => {
  const saved: RailItem[] = [{ id: 'custom:prompt:abc:0', type: 'prompt', label: 'Ship it', promptKey: 'global:abc' }]
  const config = normalizeRailConfig({
    items: saved,
    layouts: { desktop: { strip: [{ id: 'r1', items: ['custom:prompt:abc:0'] }] } },
  })
  const merged = config.items.find(item => item.id === 'custom:prompt:abc:0')
  assert.equal(merged?.promptKey, 'global:abc')
  assert.equal(config.layouts.desktop.strip[0].items[0], 'custom:prompt:abc:0')
  // The body lives in the library and is fetched on click, so there is nothing to
  // inject synchronously — a caller that ignored the type would inject nothing.
  assert.equal(railPayload(saved[0], 'claude'), '')
})
