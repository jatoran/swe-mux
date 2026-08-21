// The real Settings panel over a stubbed daemon, at whatever viewport the spec sets.
//
// What this exists to prove is geometry the unit tests structurally cannot see: at the
// mobile breakpoint the header has to stay one row (that is the vertical space the change
// was for), and the section list has to become a drawer that is genuinely off screen when
// closed and covers nothing but the content when open. Both are CSS, and both were
// previously a rail nobody could assert on.
//
// The panel's open state is owned here exactly as `App` owns it, so the harness exercises
// the same prop contract the shell does rather than a private one.
import { render } from 'preact'
import { useState } from 'preact/hooks'
import { Settings } from '../../src/Settings'
import { SETTINGS_CONFIG_FIXTURE } from './settingsConfigFixture'
import '../../src/style.css'

const KEYBINDINGS = {
  bindings: { 'ctrl+k': 'palette.open' },
  defaults: { 'ctrl+k': 'palette.open' },
  commands: [
    { id: 'palette.open', label: 'Open command palette', category: 'view' },
    { id: 'sidebar.toggle', label: 'Toggle navigation sidebar', category: 'view' },
  ],
  policy: { browser_reserved: [], desktop_only: [], application_reserved: [], terminal_reserved: [], rules: [] },
  rejected: {},
}

/**
 * A cached OpenRouter catalog, shaped exactly as `OpenRouterClient.models()` emits it.
 *
 * Every awkward row the real catalog contains is represented, because the picker's
 * layout is only interesting where the data is: a sub-cent price beside a
 * double-digit one (the columns have to line up), a free model, an entry whose
 * pricing OpenRouter did not report (which must not read as free), an auto-router
 * whose price is negative, and an id long enough to need the ellipsis.
 */
const MODEL_CATALOG = [
  { id: 'deepseek/deepseek-v4-flash', name: 'DeepSeek: DeepSeek V4 Flash', context_length: 1_000_000, prompt_price: 0.00000008, completion_price: 0.0000003 },
  { id: 'openai/gpt-5.6-luna', name: 'OpenAI: GPT-5.6 Luna', context_length: 400_000, prompt_price: 0.00000005, completion_price: 0.0000004 },
  { id: 'openai/gpt-5.6-terra', name: 'OpenAI: GPT-5.6 Terra', context_length: 400_000, prompt_price: 0.00000125, completion_price: 0.00001 },
  { id: 'anthropic/claude-sonnet-5', name: 'Anthropic: Claude Sonnet 5', context_length: 200_000, prompt_price: 0.000003, completion_price: 0.000015 },
  { id: 'meta-llama/llama-4-scout:free', name: 'Meta: Llama 4 Scout (free)', context_length: 128_000, prompt_price: 0, completion_price: 0 },
  { id: 'openrouter/auto', name: 'OpenRouter: Auto Router', context_length: 2_000_000, prompt_price: -1, completion_price: -1 },
  { id: 'somevendor/model-without-published-pricing', name: 'SomeVendor: Model Without Published Pricing', context_length: 0, prompt_price: null, completion_price: null },
  { id: 'averylongvendorname/an-extremely-long-model-identifier-preview-2026-08-01', name: 'AVeryLongVendorName: An Extremely Long Model Identifier Preview', context_length: 32_768, prompt_price: 0.000000015, completion_price: 0.00000009 },
]

const PROVIDER = {
  secret: { configured: true, source: 'stored', persistent: true },
  models: { models: MODEL_CATALOG, fetched_at: 1_770_000_000, stale: false },
  origin: 'https://openrouter.ai/api/v1',
  cheap_model: 'deepseek/deepseek-v4-flash',
  standard_model: 'anthropic/claude-sonnet-5',
}

const BUNDLE = {
  // The routed pair is set and the two overrides are not, so the routing summary has
  // both a configured row and an inherited one to distinguish.
  config: {
    ...SETTINGS_CONFIG_FIXTURE,
    openrouter_cheap_model: 'deepseek/deepseek-v4-flash',
    openrouter_standard_model: 'anthropic/claude-sonnet-5',
  },
  keybindings: KEYBINDINGS,
  profiles: { profiles: [], detected: [] },
  projects: [],
  automation: null,
  provider: PROVIDER,
  usage: null,
  errors: {},
}

// Everything the panel asks for on open. Unlisted paths answer `{}` rather than failing,
// so a new fetch added to a tab degrades to an empty section instead of a blank harness.
const RESPONSES: Record<string, unknown> = {
  '/api/settings/bundle': BUNDLE,
  '/api/remote/status': { tailnet_enabled: false, urls: [], diagnostic: '' },
  '/api/remote/firewall': { checked: false, rules: [], diagnostic: '' },
  '/api/wsl/bridge': { available: false, distributions: [], diagnostic: '' },
  '/api/diagnostics/prerequisites': { prerequisites: [] },
  '/api/voice': null,
  '/api/voice/stt-latency': null,
}

window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  void init
  const raw = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
  const path = raw.split('?')[0]
  const body = path in RESPONSES ? RESPONSES[path] : {}
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')

function Host() {
  const [navOpen, setNavOpen] = useState(false)
  return <Settings
    activeUiScale={1}
    onUiScalePreview={() => 1}
    navOpen={navOpen}
    onNavOpenChange={setNavOpen}
    onClose={() => {}}
  />
}

render(<Host />, document.querySelector('#root')!)
