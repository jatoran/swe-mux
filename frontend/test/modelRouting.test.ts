import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { MODEL_ROUTES, resolveRoute, resolveRoutes, type ModelRoutingConfig } from '../src/modelRouting.ts'
import { settingsTabs } from '../src/settingsTabs.ts'

const source = (name:string) => readFileSync(join(import.meta.dirname, '..', 'src', name), 'utf8')

const config = (overrides:Partial<ModelRoutingConfig> = {}):ModelRoutingConfig => ({
  openrouter_cheap_model: 'deepseek/deepseek-v4-flash',
  openrouter_standard_model: '',
  scan_timeline_model: 'deepseek/deepseek-v4-flash',
  attention_narration_model: '',
  tts_summary_model: '',
  assistant_model: 'openai/gpt-5.6-terra',
  project_card_model: '',
  ...overrides,
})

test('an unset override reports what it inherits rather than nothing', () => {
  const narration = MODEL_ROUTES.find(route => route.key === 'attention_narration_model')!
  const resolved = resolveRoute(narration, config())
  assert.equal(resolved.model, 'deepseek/deepseek-v4-flash')
  assert.equal(resolved.inherited, true)
})

test('a set override stops inheriting', () => {
  const narration = MODEL_ROUTES.find(route => route.key === 'attention_narration_model')!
  const resolved = resolveRoute(narration, config({attention_narration_model: 'openai/gpt-5.6-luna'}))
  assert.equal(resolved.model, 'openai/gpt-5.6-luna')
  assert.equal(resolved.inherited, false)
})

test('whitespace is not a configured model', () => {
  const summary = MODEL_ROUTES.find(route => route.key === 'tts_summary_model')!
  const resolved = resolveRoute(summary, config({tts_summary_model: '   '}))
  assert.equal(resolved.model, 'deepseek/deepseek-v4-flash')
  assert.equal(resolved.inherited, true)
})

test('a pin has nothing to inherit, so an unset pin resolves to nothing', () => {
  // The distinction the summary exists to show: a blank override is fine, a blank
  // pin is a feature that cannot run, and both must not read the same.
  const assistant = MODEL_ROUTES.find(route => route.key === 'assistant_model')!
  assert.equal(assistant.fallback, undefined)
  const resolved = resolveRoute(assistant, config({assistant_model: ''}))
  assert.equal(resolved.model, '')
  assert.equal(resolved.inherited, false)
})

test('every route resolves against a fully unset install without throwing', () => {
  const blank = config({
    openrouter_cheap_model: '', scan_timeline_model: '', assistant_model: '',
  })
  const resolved = resolveRoutes(blank)
  assert.equal(resolved.length, MODEL_ROUTES.length)
  assert.ok(resolved.every(item => item.model === '' && !item.inherited))
})

test('a fallback only ever points at a routed default, never at another override', () => {
  // A chain of overrides would make "inherited" ambiguous about *from what*, and the
  // daemon resolves exactly one level (`or config.openrouter_cheap_model`).
  const routed = new Set(MODEL_ROUTES.filter(route => route.kind === 'routed').map(route => route.key))
  for (const route of MODEL_ROUTES) {
    if (route.fallback) assert.ok(routed.has(route.fallback), `${route.key} falls back outside the routed pair`)
  }
})

test('every route names a real settings tab', () => {
  const tabs = new Set(settingsTabs.map(tab => tab.id))
  for (const route of MODEL_ROUTES) {
    if (route.target) assert.ok(tabs.has(route.target.tab), `${route.key} points at an unknown tab`)
  }
})

test('every linked control carries the data-setting the link reveals', () => {
  // `settingReveal.ts` finds the control by `[data-setting]`, so a dropped mark
  // strands the link silently. The mark is now one dynamic attribute on the row the
  // table renders per route, which is why this asserts the loop rather than seven
  // static attributes: with the rows generated, a single route cannot drift out of
  // the marked set without every one of them going with it.
  const table = source('ModelRoutingSummary.tsx')
  assert.match(table, /<li key=\{route\.key\} data-setting=\{route\.key\}>/,
    'each routing row must carry its own data-setting mark')
  // And every model that used to be edited on a feature tab still has a row there
  // linking back, so the reader standing on Voice can still find out what it calls.
  // The two routed defaults are exempt because they never lived on a feature tab -
  // they have always been the thing the others fall back to, edited here.
  const settings = source('Settings.tsx')
  const relocated = MODEL_ROUTES.filter(route => route.kind !== 'routed')
  assert.equal(relocated.length, 5)
  for (const route of relocated) {
    assert.match(settings, new RegExp(`model-routing-elsewhere[\\s\\S]{0,400}?${route.key}`),
      `${route.key} has no read-only row linking back from its feature tab`)
  }
})

test('the routing table covers every OpenRouter model key the panel knows about', () => {
  // A new model setting that never reaches this table is invisible in the one place
  // that claims to list them all, which is worse than not claiming it.
  const settings = source('Settings.tsx')
  const declared = new Set(MODEL_ROUTES.map(route => route.key as string))
  for (const key of ['openrouter_cheap_model', 'openrouter_standard_model', 'scan_timeline_model',
    'attention_narration_model', 'tts_summary_model', 'assistant_model', 'project_card_model']) {
    assert.ok(declared.has(key), `${key} is missing from MODEL_ROUTES`)
    assert.ok(settings.includes(key), `${key} is no longer read by Settings.tsx`)
  }
})

test('no model setting is still rendered as a native select or a bare text input', () => {
  // The three-widget drift this table replaced: a `<select>` cannot filter hundreds
  // of catalog entries and cannot show a price, and a text input commits a typo
  // without ever saying it was not in the catalog.
  //
  // One loop renders all seven now, so the drift this guards against would have to
  // be a second control somewhere else rather than one row diverging - which is what
  // the `ModelPicker`-free assertion over `Settings.tsx` in `llmProvider.test.ts`
  // covers. Here the table itself has to hold exactly one picker and no raw control.
  const table = source('ModelRoutingSummary.tsx')
  assert.match(table, /<ModelPicker\b/, 'the routing table must render the picker')
  assert.doesNotMatch(table, /<(select|input)\b/, 'the routing table must hold no raw form control')
})

test('a pinned model uses the picker that cannot clear itself', () => {
  // `required` suppresses the clear-the-setting row. Without it the control can
  // produce a blank the daemon rejects on Save, which reads as a broken form.
  //
  // Asserted on the expression rather than on seven rendered controls, because one
  // loop renders them all: the table has to decide `required` *from* the route kind,
  // and a table that hardcoded it either way would be wrong for four rows or three.
  const table = source('ModelRoutingSummary.tsx')
  assert.match(table, /required=\{route\.kind === 'pinned'\}/,
    'the picker must take `required` from the route kind')
  assert.ok(MODEL_ROUTES.some(route => route.kind === 'pinned'),
    'a table deriving `required` from a kind nothing uses would assert nothing')
})
