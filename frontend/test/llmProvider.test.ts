import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { llmGateHeading, type LlmReadiness } from '../src/llmProvider.ts'
import { customProviderOverride, MODEL_ROUTES, resolveRoute, resolveRoutes, type ModelRoutingConfig } from '../src/modelRouting.ts'
import { SETTING_TARGETS } from '../src/settingTargets.ts'

const root = join(import.meta.dirname, '..')
const source = (name: string) => readFileSync(join(root, 'src', name), 'utf8')
const daemon = (name: string) => readFileSync(join(root, '..', 'src', 'swe_mux', name), 'utf8')

const readiness = (overrides: Partial<LlmReadiness> = {}): LlmReadiness => ({
  ready: false, provider: 'custom', code: 'unverified', reason: 'not verified yet', ...overrides,
})

const config = (overrides: Partial<ModelRoutingConfig> = {}): ModelRoutingConfig => ({
  openrouter_cheap_model: 'deepseek/deepseek-v4-flash',
  openrouter_standard_model: '',
  scan_timeline_model: 'deepseek/deepseek-v4-flash',
  attention_narration_model: '',
  tts_summary_model: '',
  assistant_model: 'openai/gpt-5.6-terra',
  project_card_model: '',
  ...overrides,
})

// The four not-ready states need four different next actions - store a key, finish the
// endpoint, verify it, verify it again - so a gate that flattened them into one sentence
// would send the reader to the wrong control three times out of four.
test('each unready state gets its own heading', () => {
  const headings = new Set((['no_key', 'no_endpoint', 'unverified', 'endpoint_changed'] as const)
    .map(code => llmGateHeading(readiness({ code }))))
  assert.equal(headings.size, 4)
})

test('an incomplete endpoint and a missing model read the same, because they are', () => {
  assert.equal(llmGateHeading(readiness({ code: 'no_model' })),
    llmGateHeading(readiness({ code: 'no_endpoint' })))
})

test('an unrecognised code still produces a sentence rather than blank prose', () => {
  const heading = llmGateHeading(readiness({ code: 'unknown' }))
  assert.ok(heading.length > 0)
  assert.match(heading, /model provider/)
})

// The routing index is the thing Accounts owns, and a custom endpoint invalidates every
// row of it at once: one server, one model, seven settings that name OpenRouter ids it
// has never heard of. A table still listing those ids would be the most misleading
// surface in the panel.
test('a custom endpoint overrides every route, pin included', () => {
  const draft = { llm_provider: 'custom', custom_llm_model: 'qwen2.5-coder:7b' }
  const override = customProviderOverride(draft)
  assert.deepEqual(override, { provider: 'custom', model: 'qwen2.5-coder:7b' })
  const resolved = resolveRoutes(config(), override)
  assert.equal(resolved.length, MODEL_ROUTES.length)
  assert.ok(resolved.every(item => item.model === 'qwen2.5-coder:7b'))
  // Every row is inherited under an override: none of them chose this model.
  assert.ok(resolved.every(item => item.inherited))
})

test('an override beats a pin, because the pin is not what gets sent', () => {
  const assistant = MODEL_ROUTES.find(route => route.key === 'assistant_model')!
  assert.equal(assistant.kind, 'pinned')
  const resolved = resolveRoute(assistant, config(), { provider: 'custom', model: 'local-model' })
  assert.equal(resolved.model, 'local-model')
})

test('OpenRouter is not an override, so nothing about the existing table changes', () => {
  assert.equal(customProviderOverride({ llm_provider: 'openrouter', custom_llm_model: 'x' }), null)
  assert.equal(customProviderOverride({}), null)
  const narration = MODEL_ROUTES.find(route => route.key === 'attention_narration_model')!
  assert.equal(resolveRoute(narration, config(), null).model, 'deepseek/deepseek-v4-flash')
})

test('an empty custom model still overrides, so the summary reports the gap', () => {
  // The alternative - falling through to the OpenRouter ids - would show seven models
  // for an endpoint that cannot answer at all. The daemon refuses to save this state;
  // the browser can still be looking at an unsaved draft in it.
  const override = customProviderOverride({ llm_provider: 'custom', custom_llm_model: '  ' })
  assert.deepEqual(override, { provider: 'custom', model: '' })
  assert.equal(resolveRoutes(config(), override).every(item => item.model === ''), true)
})

// The provider is a *value*, not a switch: choosing one and typing a URL, a key, and a
// model id is not something one button can honestly offer. `setting-links.md` calls that
// out under "Deliberately not granted", and the enforcement is that no grant is keyed to
// this target.
test('the model provider is a link and never a grant', () => {
  const grants = source('grants.ts')
  assert.ok(!grants.includes("'accounts.llmProvider'"), 'the provider must not be grantable')
  assert.equal(SETTING_TARGETS['accounts.llmProvider'].surface, 'settings')
  assert.equal(SETTING_TARGETS['accounts.llmProvider'].setting, 'llm_provider')
})

test('the surfaces that go inert behind a provider link to the one that owns it', () => {
  for (const file of ['GrantGate.tsx', 'ProjectsManager.tsx']) {
    assert.match(source(file), /target="accounts\.llmProvider"/,
      `${file} names the provider without offering a way to reach it`)
  }
})

// `needs_llm` rides the registry payload for the same reason `spends` does: one fact,
// one source. A browser that recomputed "which of these call a model" would drift the
// day a fourth one is added.
test('the gate reads needs_llm from the registry rather than listing ids', () => {
  const gate = source('GrantGate.tsx')
  assert.match(gate, /needs_llm === true/)
  assert.match(daemon('automation_registry.py'), /needs_llm: bool = False/)
})

test('the Projects editor states the reason rather than paraphrasing it', () => {
  // The daemon's sentence distinguishes never-verified from edited-since-verified, and
  // a surface that wrote its own would collapse the two.
  const manager = source('ProjectsManager.tsx')
  assert.match(manager, /state\.llm\?\.reason/)
  assert.match(manager, /unverified\.has\('scan_timeline'\)/)
})

test('the Accounts panel marks the controls the links reveal', () => {
  const settings = source('Settings.tsx')
  for (const setting of ['llm_provider', 'custom_llm_base_url', 'custom_llm_model']) {
    assert.ok(settings.includes(`data-setting="${setting}"`),
      `Settings.tsx does not mark ${setting}`)
  }
})
