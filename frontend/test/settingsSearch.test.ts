import assert from 'node:assert/strict'
import test from 'node:test'
import { Fragment, h as preactNode } from 'preact'
import { domVNode, harvestSettings, matchIndex, normalizeSearchText, searchSettings, tabEntry } from '../src/settingsSearch.ts'

// Stand-in for what the JSX transform produces: type plus props.children.
const h = (type: string, props: Record<string, unknown>, ...children: unknown[]) =>
  ({ type, props: { ...props, children: children.length === 1 ? children[0] : children } })

const tab = (...children: unknown[]) => harvestSettings(h('section', {}, ...children), 'general', 'General', 0)

test('a label is indexed by its own text, with its control values as keywords', () => {
  const [heading, theme] = tab(
    h('h3', {}, 'Appearance'),
    h('label', {}, 'Theme', h('select', {}, h('option', {}, 'Dark'), h('option', {}, 'Tokyo Night'))),
  )
  assert.equal(heading.kind, 'section')
  assert.equal(theme.label, 'Theme')
  assert.equal(theme.section, 'Appearance')
  assert.equal(theme.kind, 'field')
  assert.match(theme.keywords, /tokyo night/)
})

test('help text following a control folds into that control keywords', () => {
  const [, listen] = tab(
    h('h3', {}, 'Remote'),
    h('label', { class: 'check' }, h('span', {}, 'Listen on Tailscale IPv4'), h('input', { type: 'checkbox' })),
    h('p', {}, 'Changing the listener requires a daemon restart.'),
  )
  assert.equal(listen.label, 'Listen on Tailscale IPv4')
  assert.match(listen.keywords, /daemon restart/)
})

test('placeholder and title text are searchable without being the label', () => {
  const [entry] = tab(h('label', {}, 'Startup directory', h('input', { placeholder: 'D:\\projects' })))
  assert.equal(entry.label, 'Startup directory')
  assert.match(entry.keywords, /d:\\projects/)
})

test('an icon-prefixed button indexes by its word text, not the glyph', () => {
  const [entry] = tab(h('button', {}, h('span', {}, '▶'), h('strong', {}, 'Claude')))
  assert.equal(entry.kind, 'action')
  assert.equal(entry.label, 'Claude')
})

test('emphasis inside prose is not indexed as its own entry', () => {
  const entries = tab(h('p', {}, 'Trust applies to ', h('strong', {}, 'the current contents'), ' only.'))
  assert.deepEqual(entries, [])
})

test('component vnodes are walked for children but never invoked', () => {
  let invoked = false
  const Child = () => { invoked = true; return null }
  const entries = harvestSettings(
    { type: Child, props: { children: h('label', {}, 'Passed through') } }, 'notifications', 'Notifications', 5)
  assert.equal(invoked, false)
  assert.equal(entries.length, 1)
  assert.equal(entries[0].tabLabel, 'Notifications')
  assert.equal(entries[0].tabIndex, 5)
})

test('repeated labels within a tab are distinguished by occurrence', () => {
  const [first, , second] = tab(
    h('label', {}, 'Enabled'), h('h4', {}, 'Second block'), h('label', {}, 'Enabled'))
  assert.equal(first.occurrence, 0)
  assert.equal(second.occurrence, 1)
  assert.equal(second.section, 'Second block')
})

const entries = [
  ...tab(h('h3', {}, 'General'), h('label', {}, 'Scrollback bytes'), h('label', {}, 'History limit')),
  ...harvestSettings(
    h('section', {}, h('h3', {}, 'Voice'), h('label', {}, 'Wake word'), h('p', {}, 'Scrollback is unrelated here.')),
    'voice', 'Voice', 11),
]

test('a label match outranks the same word buried in help text', () => {
  const [top] = searchSettings(entries, 'scrollback')
  assert.equal(top.label, 'Scrollback bytes')
})

test('every term must match, so extra words narrow the result', () => {
  assert.equal(searchSettings(entries, 'history limit')[0].label, 'History limit')
  assert.deepEqual(searchSettings(entries, 'history nonsense'), [])
})

test('a row labels itself by what is visible, with aria-label only as a fallback', () => {
  const [row, icon] = tab(
    h('button', { 'aria-label': 'Set shortcut for Open command palette' },
      h('span', {}, 'Open command palette'), h('small', {}, 'view')),
    h('button', { 'aria-label': 'Clear shortcut' }, '×'),
  )
  assert.equal(row.label, 'Open command palette')
  assert.match(row.keywords, /set shortcut/)
  assert.equal(icon.label, 'Clear shortcut')
})

test('fuzzy matching accepts an abbreviation but not letters strewn across a phrase', () => {
  const entries = tab(h('label', {}, 'Scrollback bytes'), h('button', {}, 'Set shortcut for Open command palette'))
  assert.equal(searchSettings(entries, 'scrlbck')[0].label, 'Scrollback bytes')
  assert.deepEqual(searchSettings(entries, 'sound'), [])
})

test('a sentence-long label does not fuzzy-match unrelated queries', () => {
  const wordy = tab(h('label', { class: 'check' }, h('span', {},
    'Swipe-away closes an open panel: swiping back toward the edge it slid in from dismisses it')))
  assert.deepEqual(searchSettings(wordy, 'renderer'), [])
  assert.equal(searchSettings(wordy, 'swipe')[0].kind, 'field')
})

test('an empty query matches nothing rather than everything', () => {
  assert.deepEqual(searchSettings(entries, '   '), [])
})

test('results are capped at the requested limit', () => {
  assert.equal(searchSettings(entries, 'e', 2).length, 2)
})

test('matchIndex finds the nth element whose text starts with the label', () => {
  const texts = ['Theme Dark Light Custom', 'Enabled', 'Enabled', 'History limit']
  assert.equal(matchIndex(texts, 'theme', 0), 0)
  assert.equal(matchIndex(texts, 'enabled', 1), 2)
  assert.equal(matchIndex(texts, 'enabled', 9), 2) // clamps rather than missing
  assert.equal(matchIndex(texts, 'missing', 0), -1)
})

test('matchIndex falls back to containment when nothing starts with the label', () => {
  assert.equal(matchIndex(['▶ Claude', 'Codex'], 'claude', 0), 0)
})

// The walk reads `props.children` off real vnodes, so lock that shape against a
// preact upgrade rather than trusting the hand-built ones above.
test('real preact vnodes harvest, fragments and unrendered branches included', () => {
  const tree = preactNode(Fragment, null,
    false,
    preactNode('section', null,
      preactNode('h3', null, 'General'),
      preactNode('label', null, 'Scrollback bytes', preactNode('input', { type: 'number' })),
      preactNode('p', null, 'Bytes of output kept per session.')))
  const field = harvestSettings(tree, 'general', 'General', 0).find(entry => entry.kind === 'field')
  assert.equal(field?.label, 'Scrollback bytes')
  assert.equal(field?.section, 'General')
  assert.match(field?.keywords || '', /bytes of output/)
})

test('a mounted tab can be indexed from its DOM by the same rules', () => {
  // Minimal stand-in for the live elements: tag, text-bearing attributes, children.
  const text = (value: string) => ({ nodeType: 3, nodeValue: value })
  const el = (tagName: string, attrs: Record<string, string>, ...childNodes: unknown[]) =>
    ({ nodeType: 1, tagName, childNodes, getAttribute: (name: string) => attrs[name] ?? null })
  const section = el('SECTION', {},
    el('H3', {}, text('Session notification sounds')),
    el('LABEL', {}, text('Quiet from'), el('INPUT', { placeholder: '22:00' })))
  const entries = harvestSettings(
    domVNode(section as unknown as Element), 'notifications', 'Notifications', 10)
  assert.deepEqual(entries.map(entry => entry.label), ['Session notification sounds', 'Quiet from'])
  assert.equal(entries[1].section, 'Session notification sounds')
  assert.match(entries[1].keywords, /22:00/)
})

test('a tab is findable by name even when its body is an opaque component', () => {
  const opaque = [tabEntry('accounts', 'Accounts', 5)]
  assert.equal(searchSettings(opaque, 'accounts')[0].tab, 'accounts')
})

test('normalizeSearchText collapses case and whitespace', () => {
  assert.equal(normalizeSearchText('  Read   aloud\n(TTS) '), 'read aloud (tts)')
})
