import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { capabilitySummary, llmGateHeading, type EndpointCapabilities, type LlmReadiness } from '../src/llmProvider.ts'
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

// A *catalog-less* custom endpoint invalidates every row at once: one server, one
// model, seven settings that name ids it has never heard of. A table still listing
// those ids would be the most misleading surface in the panel.
test('a catalog-less custom endpoint overrides every route, pin included', () => {
  const draft = { llm_provider: 'custom', custom_llm_model: 'qwen2.5-coder:7b' }
  const override = customProviderOverride(draft, false)
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

// The other half, and the one that decides whether switching provider is safe. An
// endpoint that publishes a catalog collapses nothing: the daemon stops redirecting,
// every setting reaches the wire as written, and reporting a collapse anyway would say
// the models you chose had been silently replaced when they had not - which is a reason
// not to switch provider at all.
test('a custom endpoint with a catalog overrides nothing', () => {
  const draft = { llm_provider: 'custom', custom_llm_model: 'qwen2.5-coder:7b' }
  assert.equal(customProviderOverride(draft, true), null)
  const resolved = resolveRoutes(config(), customProviderOverride(draft, true))
  assert.equal(resolved.find(item => item.route.key === 'assistant_model')!.model,
    'openai/gpt-5.6-terra')
  assert.equal(resolved.find(item => item.route.key === 'scan_timeline_model')!.model,
    'deepseek/deepseek-v4-flash')
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
  const override = customProviderOverride({ llm_provider: 'custom', custom_llm_model: '  ' }, false)
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
  for (const file of ['GrantGate.tsx', 'AutomationMatrix.tsx']) {
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

test('the policy matrix states the reason rather than paraphrasing it', () => {
  // The daemon's sentence distinguishes never-verified from edited-since-verified, and
  // a surface that wrote its own would collapse the two.
  const matrix = source('AutomationMatrix.tsx')
  assert.match(matrix, /project\?\.llm\?\.reason/)
  assert.match(matrix, /unverified\.has\(item\.id\)/)
})

test('the Accounts panel marks the controls the links reveal', () => {
  const settings = source('Settings.tsx') + source('ProviderSetup.tsx')
  for (const setting of ['llm_provider', 'custom_llm_base_url', 'custom_llm_model']) {
    assert.ok(settings.includes(`data-setting="${setting}"`),
      `Settings.tsx does not mark ${setting}`)
  }
})

// --- capabilities: what the endpoint turned out to be ------------------------

const capabilities = (
  overrides: Partial<EndpointCapabilities> = {},
): EndpointCapabilities => ({
  catalog: 'none', reports_cost: false, reports_cache: false, ...overrides,
})

// Silence before anyone has looked is the whole point. The fixed provider table this
// replaced stated "no catalog" about every custom endpoint as though it were a finding,
// and that claim is exactly what was wrong for an OpenRouter-shaped proxy.
test('an unproven endpoint claims nothing about what it can do', () => {
  assert.equal(capabilitySummary(capabilities({ catalog: 'annotated' }), false), '')
  assert.equal(capabilitySummary(undefined, true), '')
})

test('each catalog shape gets its own sentence, because each means a different UI', () => {
  const summaries = (['none', 'bare', 'annotated'] as const)
    .map(catalog => capabilitySummary(capabilities({ catalog }), true))
  assert.equal(new Set(summaries).size, 3)
  assert.match(summaries[2], /pricing/)
  // The `none` case has to say what the reader gets instead of a picker, or an absent
  // control reads as a bug rather than as the single-model endpoint it describes.
  assert.match(summaries[0], /the one model named above/)
})

test('cost reporting is stated either way rather than only when present', () => {
  // A dollar budget cannot bind against an endpoint that reports nothing, so the
  // absence is a fact the reader needs, not merely a missing line.
  assert.match(capabilitySummary(capabilities({ reports_cost: false }), true), /reports no cost/)
  assert.match(capabilitySummary(capabilities({ reports_cost: true }), true), /reports cost per call/)
})

test('cache reporting appears only when it was actually observed', () => {
  // Unlike cost, a zero here is ambiguous by construction - "no hit" and "this provider
  // does not report caching" are the same number - so the summary says it or says nothing.
  assert.doesNotMatch(capabilitySummary(capabilities(), true), /caching/)
  assert.match(capabilitySummary(capabilities({ reports_cache: true }), true), /caching/)
})

test('the daemon and the browser agree on the catalog vocabulary', () => {
  // Three values, spelled the same on both sides. A fourth added in Python and not here
  // would fall through to the `none` branch and quietly report a capable endpoint as a
  // single-model one, which is the direction that hides a working picker.
  const python = daemon('llm_endpoint.py')
  const shapes = python.match(/CatalogShape = Literal\[([^\]]+)\]/)
  assert.ok(shapes, 'llm_endpoint.py must declare CatalogShape')
  const declared = [...shapes[1].matchAll(/"([a-z]+)"/g)].map(match => match[1]).sort()
  assert.deepEqual(declared, ['annotated', 'bare', 'none'])
  const rendered = source('llmProvider.ts')
  for (const value of declared) assert.match(rendered, new RegExp(`'${value}'`))
})

// --- centralisation ----------------------------------------------------------

test('every model route is edited in one place', () => {
  // The rule this replaced put each control beside the feature it configured, which
  // optimised for setting one feature up and against the operation that touches all
  // seven at once. If a row drifts back to another tab, the Accounts table stops being
  // the whole answer and the hunting starts again.
  for (const route of MODEL_ROUTES) {
    assert.equal(route.target?.tab, 'accounts', `${route.key} must be edited in Accounts`)
    assert.match(route.where, /Accounts/, `${route.key} must say where it lives`)
  }
})

test('the panel holds no second control for a routed model', () => {
  // Two controls writing one config key is how a panel starts disagreeing with itself.
  // The feature tabs show these read-only and link back, so a `ModelPicker` bound to one
  // of these keys anywhere but the routing table is the regression this catches.
  const settings = source('Settings.tsx') + source('ProviderSetup.tsx')
  assert.ok(!settings.includes('ModelPicker'),
    'Settings.tsx must render model pickers only through the routing table')
  const table = source('ModelRoutingSummary.tsx')
  assert.ok(table.includes('ModelPicker'), 'the routing table is where the pickers live')
})

test('a model absent from the endpoint catalog is flagged rather than left to fail', () => {
  // A model configured against one endpoint and missing from the next fails at call
  // time as a provider error. The table has the catalog in hand, so it can say so first.
  const table = source('ModelRoutingSummary.tsx')
  assert.match(table, /model-routing-missing/)
  assert.match(table, /catalogKnown/)
})
