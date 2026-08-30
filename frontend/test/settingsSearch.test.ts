import assert from 'node:assert/strict'
import test from 'node:test'
import { Fragment, h as preactNode } from 'preact'
import { domVNode, harvestHeadings, harvestSettings, matchIndex, normalizeSearchText, searchSettings, tabEntry } from '../src/settingsSearch.ts'

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

test('a Dropdown keeps its choices searchable now that they are a prop, not children', () => {
  // The regression the app-wide `<select>` sweep would otherwise have shipped: with the rows
  // moved from `<option>` children into an `options` prop, "Tokyo Night" stopped being text
  // anywhere in the tree and the setting that offers it became unfindable by the word a
  // person would actually search for.
  const [, theme] = tab(
    preactNode('h3', {}, 'Appearance'),
    preactNode('label', {}, 'Theme', preactNode(
      function Dropdown() { return null },
      { value: 'dark', options: [{ value: 'dark', label: 'Dark' }, { value: 'tokyo', label: 'Tokyo Night' }] },
    )),
  )
  assert.equal(theme.label, 'Theme', 'the option labels are keywords, never the label')
  assert.match(theme.keywords, /tokyo night/)
  assert.doesNotMatch(theme.keywords, /\btokyo\b(?! night)/, 'the value id is not indexed')
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

test('a control under an h4 group names the h3 block above it too', () => {
  // The whole point of a path: "Input · view" told a reader nothing about where the
  // row lives, because the category heading had overwritten the section heading.
  const [, , row] = tab(
    h('h3', {}, 'Keyboard shortcuts'),
    h('section', {}, h('h4', {}, 'View'), h('button', {}, 'Open global Scratchpad')))
  assert.deepEqual(row.path, ['Keyboard shortcuts', 'View'])
  assert.equal(row.section, 'Keyboard shortcuts · View')
})

test('a group heading closes with its section, so what follows keeps the block', () => {
  // The disclosure after the shortcut table used to file itself under whichever
  // category rendered last.
  const entries = tab(
    h('h3', {}, 'Keyboard shortcuts'),
    h('div', {},
      h('section', {}, h('h4', {}, 'View'), h('button', {}, 'Open Settings')),
      h('section', {}, h('h4', {}, 'History'), h('button', {}, 'Browse history'))),
    h('details', {}, h('summary', {}, 'Reserved shortcut policy')))
  const policy = entries.find(entry => entry.label === 'Reserved shortcut policy')
  assert.deepEqual(policy?.path, ['Keyboard shortcuts'])
  assert.deepEqual(entries.find(entry => entry.label === 'Browse history')?.path,
    ['Keyboard shortcuts', 'History'], 'a sibling group replaces the previous one')
})

test('a new h3 drops the h4 under the block it closed', () => {
  const [, , , field] = tab(
    h('h3', {}, 'Touch gestures'), h('h4', {}, 'Swipes'),
    h('h3', {}, 'Keyboard shortcuts'), h('label', {}, 'Enabled'))
  assert.deepEqual(field.path, ['Keyboard shortcuts'])
})

test('a heading is filed under its block rather than under itself', () => {
  const [, group] = tab(h('h3', {}, 'Keyboard shortcuts'), h('h4', {}, 'View'))
  assert.equal(group.label, 'View')
  assert.deepEqual(group.path, ['Keyboard shortcuts'])
})

test('a labelled block inside a section does not claim the section', () => {
  // `<strong>` marks a sub-block ("EDITOR::CHORDS") without opening one; letting it
  // take a level would file the controls after it under a shouted phrase.
  const [, , field] = tab(
    h('h3', {}, 'Editor shortcuts'), h('strong', {}, 'EDITOR::CHORDS'), h('label', {}, 'Policy'))
  assert.deepEqual(field.path, ['Editor shortcuts'])
})

test('the enclosing headings are keywords, so a block name narrows a search', () => {
  const entries = tab(
    h('h3', {}, 'Keyboard shortcuts'),
    h('section', {}, h('h4', {}, 'View'), h('button', {}, 'Open global Scratchpad')))
  assert.equal(searchSettings(entries, 'keyboard scratchpad')[0]?.label, 'Open global Scratchpad')
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

test('a tab s headings are readable from its vnodes, before it has mounted', () => {
  // What lets the sidebar disclose a tab's sections on the first visit rather than the
  // second: only the mounted tab has a DOM to read.
  const Opaque = () => null
  const tree = h('div', {},
    h('section', {}, h('h3', {}, 'Rendering'), h('label', {}, 'Renderer')),
    h('section', {}, h('h3', {}, 'Scrollback'), h('h4', {}, 'Limits')),
    { type: Opaque, props: {} })
  assert.deepEqual(harvestHeadings(tree), ['Rendering', 'Scrollback'])
})

test('a heading split across runs reads as one section, the way textContent does', () => {
  // The live read is `textContent`; a preview that kept only the first run would name a
  // different section and its id would not match the one the rail settles on.
  assert.deepEqual(harvestHeadings(h('section', {}, h('h3', {}, 'Talk', ' & ', 'dictation'))),
    ['Talk & dictation'])
})

test('an empty heading is not a section', () => {
  assert.deepEqual(harvestHeadings(h('section', {}, h('h3', {}, ' '), h('h3', {}, 'Real'))), ['Real'])
})

test('a tab is findable by name even when its body is an opaque component', () => {
  const opaque = [tabEntry('accounts', 'Accounts', 5)]
  assert.equal(searchSettings(opaque, 'accounts')[0].tab, 'accounts')
})

test('normalizeSearchText collapses case and whitespace', () => {
  assert.equal(normalizeSearchText('  Read   aloud\n(TTS) '), 'read aloud (tts)')
})
