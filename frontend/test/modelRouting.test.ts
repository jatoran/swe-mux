import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { MODEL_ROUTES, resolveRoute, resolveRoutes, type ModelRoutingConfig } from '../src/modelRouting.ts'
import { settingsTabs } from '../src/settingsTabs.ts'

const source = (name:string) => readFileSync(join(import.meta.dirname, '..', 'src', name), 'utf8')

/**
 * The JSX of the one `<label>` carrying a `data-setting` mark.
 *
 * Matched to the label's own close rather than by a character budget, so a control
 * that grows a help paragraph does not silently fall out of the window and turn
 * these assertions into passes that check nothing.
 */
const controlMarkup = (settings:string, setting:string):string => {
  const match = settings.match(new RegExp(`data-setting="${setting}"[\\s\\S]*?</label>`))
  assert.ok(match, `Settings.tsx has no labelled control marked data-setting="${setting}"`)
  return match[0]
}

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
  // `settingReveal.ts` finds the control by `[data-setting]`, so a renamed or
  // dropped mark strands the link silently. This is the same guard
  // `settingTargets.test.ts` applies to links from outside Settings.
  const settings = source('Settings.tsx')
  for (const route of MODEL_ROUTES) {
    if (route.target) controlMarkup(settings, route.target.setting)
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
  // of catalog entries and cannot show a price, and a text input accepts a typo the
  // daemon then rejects as an exact-id validation error.
  const settings = source('Settings.tsx')
  for (const route of MODEL_ROUTES) {
    if (!route.target) continue
    const markup = controlMarkup(settings, route.target.setting)
    assert.match(markup, /<ModelPicker\b/, `${route.target.setting} is not on a ModelPicker`)
    assert.doesNotMatch(markup, /<(select|input)\b/, `${route.target.setting} still has a raw form control`)
  }
})

test('a pinned model uses the picker that cannot clear itself', () => {
  // `required` suppresses the clear-the-setting row. Without it the control can
  // produce a blank the daemon rejects on Save, which reads as a broken form.
  const settings = source('Settings.tsx')
  for (const route of MODEL_ROUTES) {
    if (route.kind !== 'pinned' || !route.target) continue
    assert.match(controlMarkup(settings, route.target.setting), /\srequired\s/,
      `${route.target.setting} is pinned but its picker can be cleared`)
  }
})
