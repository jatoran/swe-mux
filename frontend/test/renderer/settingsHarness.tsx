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
import type { FirewallStatus, RemoteStatus } from '../../src/remoteConnection'
import type { WslBridgeStatus } from '../../src/wslBridge'
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
  provider: 'openrouter',
  providers: [{
    id:'openrouter',label:'OpenRouter',active:true,origin:'https://openrouter.ai/api/v1',
    model:'deepseek/deepseek-v4-flash',requires_verification:false,cache_policy:'by_model',
    secret:{configured:true,source:'stored',persistent:true},
    verification:{provider:'openrouter',verified:true,stale:false,verified_at:1_770_000_000,
      base_url:'https://openrouter.ai/api/v1',model:'deepseek/deepseek-v4-flash',
      resolved_model:'deepseek/deepseek-v4-flash',sample:'ok',latency_ms:100,
      capabilities:{catalog:'annotated',reports_cost:true,reports_cache:true}},
    readiness:{ready:true,provider:'openrouter',code:'ready',reason:'Ready.',reports_cost:true},
  }],
  llm:{ready:true,provider:'openrouter',code:'ready',reason:'Ready.',reports_cost:true},
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
//
// The three status payloads are typed against the real ones rather than sketched: all three
// had drifted onto invented field names (`urls`, `rules`, `distributions` for `tailnet_urls`,
// `firewall_rule`, `distros`), and the first of those threw on every visit to the Remote tab -
// `remote?.tailnet_urls.map` guards the fetch failing, not the payload lying. Nothing noticed,
// because no spec opened that tab until one started opening all seventeen.
const REMOTE: RemoteStatus = {
  mode: 'loopback', listen_url: 'http://127.0.0.1:8765', available: false,
  serve_configured: false, serve_url: null, funnel_detected: false,
  setup_command: 'tailscale serve', diagnostic: '',
  tailnet_enabled: false, tailnet_ip: null, tailnet_urls: [], direct_available: false,
  mobile_voice_configured: false, mobile_voice_url: null, mobile_voice_https_port: 443,
  connection_state: 'not_installed', device_name: null,
  connection_command: null, connection_detail: '',
}
const FIREWALL: FirewallStatus = { supported: false, detail: '' }
const WSL: WslBridgeStatus = { supported: false, enabled: false, distros: [] }
const EDGE_CATALOG = {status:'ready',fetched_at:1_770_000_000,error:null,
  package_version:'7.2.8',stale:false,selected:'en-US-JennyNeural',selected_present:true,
  voices:[
    {id:'en-GB-SoniaNeural',locale:'en-GB',gender:'Female',name:'Sonia',status:'GA',codec:'audio/mpeg',categories:['General'],personalities:['Friendly']},
    {id:'en-US-JennyNeural',locale:'en-US',gender:'Female',name:'Jenny',status:'GA',codec:'audio/mpeg',categories:['General'],personalities:['Friendly']},
  ]}
const EDGE_PROVIDER = {
  id:'edge',available:false,integration:'unknown',diagnostic:'check integration',python:'',
  package_version:null,tested_version:false,last_probe_at:null,risk_acknowledged:false,
  retry_after:null,using_managed:false,catalog:EDGE_CATALOG,
  managed:{status:'not_installed',phase:null,error:null,version:null,
    python:'C:/Users/test/.mux/integrations/edge-tts/current/Scripts/python.exe',
    requirement:'edge-tts==7.2.8',uv_available:true,installed_at:null,updated_at:null},
}

const RESPONSES: Record<string, unknown> = {
  '/api/settings/bundle': BUNDLE,
  '/api/projects': [
    {id:'p3',name:'Zulu'},
    {id:'p1',name:'Alpha'},
    {id:'p2',name:'Beta'},
  ],
  '/api/plugins': {
    execution_enabled:true,
    host_capabilities:['plugin.actions.v1','plugin.panes.v1'],
    plugins:[
      {
        id:'example.switchboard',name:'Session Switchboard',version:'0.2.0',enabled:true,
        lifecycle:'enabled',source_kind:'link',source_ref:'C:/plugins/switchboard',resolved_ref:'',
        diagnostic:'',approval_current:true,config_dir:'C:/config/switchboard',state_dir:'C:/state/switchboard',
        manifest:{id:'example.switchboard',name:'Session Switchboard',version:'0.2.0',
          description:'Compact Project session navigator.',author:'Example',license:'MIT',homepage:'',
          permissions:['sessions.read'],requires:['plugin.panes.v1'],runtime_requirements:['python>=3.10'],
          actions:[],events:[],startup:[],link_handlers:[],panes:[{id:'switchboard',title:'Session switchboard',
            description:'Open session switchboard',placement:'split',contexts:['project'],
            command:{command:['python','switchboard.py'],cwd:'.',timeout_seconds:60}}]},
      },
      {
        id:'example.health',name:'Worktree Health',version:'0.2.0',enabled:false,
        lifecycle:'disabled',source_kind:'managed',source_ref:'owner/health',resolved_ref:'abc',
        diagnostic:'',approval_current:true,config_dir:'C:/config/health',state_dir:'C:/state/health',
        manifest:{id:'example.health',name:'Worktree Health',version:'0.2.0',description:'Project worktree health.',
          author:'Example',license:'MIT',homepage:'',permissions:['projects.read'],requires:['plugin.panes.v1'],
          runtime_requirements:['python>=3.10'],actions:[],events:[],startup:[],link_handlers:[],panes:[]},
      },
    ],
  },
  '/api/remote/status': REMOTE,
  '/api/remote/firewall': FIREWALL,
  '/api/wsl/bridge': WSL,
  '/api/diagnostics/prerequisites': { prerequisites: [] },
  '/api/voice': null,
  '/api/voice/providers/edge': EDGE_PROVIDER,
  '/api/voice/providers/edge/install': {...EDGE_PROVIDER,started:true,
    managed:{...EDGE_PROVIDER.managed,status:'installing',phase:'creating_environment'}},
  '/api/voice/stt-latency': null,
  // Save is one request now, and its answer carries both halves plus what committed.
  '/api/settings/apply': {
    config: { ...BUNDLE.config, hot_applied: [], restart_required: [] },
    keybindings: KEYBINDINGS,
    committed: ['config', 'keybindings'],
  },
  '/api/config/reset': SETTINGS_CONFIG_FIXTURE,
}

/**
 * Failures a spec can ask for, by query string: `?fail=reset` or `?fail=apply`.
 *
 * Both are destructive-path failures the panel used to swallow — a rejected reset was an
 * unhandled rejection with no visible trace, and a rejected save claimed nothing had been
 * changed regardless of what the daemon said. A spec cannot assert on either without the
 * daemon being able to say no.
 */
const FAILURES: Record<string, { path: string; status: number; body: unknown }> = {
  reset: {
    path: '/api/config/reset',
    status: 500,
    body: { error: 'the configuration directory is read-only' },
  },
  apply: {
    path: '/api/settings/apply',
    status: 409,
    body: { error: 'configuration changed externally', revision: 99, committed: [] },
  },
}

declare global {
  interface Window {
    /** Every request the panel made, in order, for asserting what a click did not send. */
    settingsCalls: Array<{ method: string; path: string; body?:unknown; gesture?:string|null }>
  }
}
window.settingsCalls = []

window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const raw = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
  const path = raw.split('?')[0]
  let requestBody:unknown
  if(typeof init?.body==='string')try{requestBody=JSON.parse(init.body)}catch{requestBody=init.body}
  const gesture=new Headers(init?.headers).get('X-Mux-User-Gesture')
  window.settingsCalls.push({ method: (init?.method || 'GET').toUpperCase(), path, body:requestBody, gesture })
  const failure = FAILURES[new URLSearchParams(location.search).get('fail') || '']
  if (failure && failure.path === path) {
    return new Response(JSON.stringify(failure.body), {
      status: failure.status,
      headers: { 'Content-Type': 'application/json' },
    })
  }
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
    focusedProjectId="p2"
    navOpen={navOpen}
    onNavOpenChange={setNavOpen}
    onLaunchConfigurator={() => {}}
    onClose={() => {}}
  />
}

render(<Host />, document.querySelector('#root')!)
