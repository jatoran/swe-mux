import assert from 'node:assert/strict'
import test from 'node:test'
import { harvestSettings, searchSettings, tabEntry } from '../src/settingsSearch.ts'
import { SETTINGS_SEARCH_ALIASES, applySettingsAliases, auditSettingsAliases, type SettingsAliasTarget } from '../src/settingsSearchAliases.ts'

// The half of the alias audit that needs no rendered panel. The other half - every alias
// resolves to exactly one real entry and shadows none - runs against the live index in
// `test/renderer/settings-search.spec.ts` (`npm run check:settings-aliases`).
test('the shipped alias table is consistent on its own', () => {
  const problems = auditSettingsAliases()
  assert.deepEqual(problems, [], problems.join('\n'))
})

const h = (type: string, props: Record<string, unknown>, ...children: unknown[]) =>
  ({ type, props: { ...props, children: children.length === 1 ? children[0] : children } })

const appearance = [
  tabEntry('appearance', 'Appearance', 11),
  ...harvestSettings(h('section', {},
    h('h3', {}, 'Theme'), h('button', {}, 'Dark'),
    h('h3', {}, 'Terminal font'), h('label', {}, 'Font family'),
    h('h3', {}, 'Session rows'), h('label', {}, 'Separator'),
    h('h3', {}, 'Session top bars'), h('label', {}, 'Separator')), 'appearance', 'Appearance', 11),
]

test('an alias attaches to the entry it names, without touching the shared harvest', () => {
  const table: SettingsAliasTarget[] = [{ tab: 'appearance', label: 'Theme', aliases: ['dark mode'] }]
  const aliased = applySettingsAliases(appearance, table)
  assert.deepEqual(aliased.find(entry => entry.label === 'Theme')?.aliases, ['dark mode'])
  assert.deepEqual(appearance.find(entry => entry.label === 'Theme')?.aliases, [], 'the input entries are not mutated')
  assert.equal(searchSettings(aliased, 'dark mode')[0].label, 'Theme')
})

test('the audit names every way an alias can go wrong', () => {
  const table: SettingsAliasTarget[] = [
    { tab: 'appearance', label: 'Theme', aliases: ['Dark  Mode', 'font family', 'theme'] },
    { tab: 'appearance', label: 'Font family', aliases: ['dark mode', 'voice'] },
    { tab: 'appearance', label: 'Separator', aliases: ['divider'] },
    { tab: 'appearance', label: 'Renamed long ago', aliases: ['ghost'] },
    { tab: 'appearance', label: 'Terminal font', aliases: ['diagnostics'] },
  ]
  const problems = auditSettingsAliases(table, appearance).join('\n')
  assert.match(problems, /"Dark {2}Mode" is not normalized/)
  assert.match(problems, /"font family" is the label of a real entry/)
  assert.match(problems, /"theme" is the entry's own label/)
  assert.match(problems, /"dark mode" is claimed by both/)
  assert.match(problems, /"voice" is the name of the voice tab/)
  assert.match(problems, /Separator: matches 2 places/)
  assert.match(problems, /Renamed long ago: matches no entry/)
  assert.match(problems, /"diagnostics" already names the maintenance tab as a deep link/)
})

test('a section disambiguates a label that repeats on its tab', () => {
  const table: SettingsAliasTarget[] = [{ tab: 'appearance', label: 'Separator', section: 'Session rows', aliases: ['divider'] }]
  assert.deepEqual(auditSettingsAliases(table, appearance), [])
  const hit = searchSettings(applySettingsAliases(appearance, table), 'divider')
  assert.deepEqual(hit.map(entry => entry.section), ['Session rows'])
})

test('every shipped target names a real tab and at least one alias', () => {
  for (const target of SETTINGS_SEARCH_ALIASES) assert.ok(target.aliases.length, target.label)
})
