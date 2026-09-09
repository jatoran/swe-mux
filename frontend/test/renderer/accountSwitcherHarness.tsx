// The real `AccountSwitcher` over a stubbed daemon, in the shell it actually lives in.
//
// The switcher's sign-in path is only true at runtime and no unit test reaches any of it:
// that a first account can be added from the popover at all, that the request goes to the
// login route rather than to a capture, that a run started on the daemon keeps reporting
// itself after the popover is dismissed and reopened, and that a failure carries its reason
// instead of vanishing with the request that started it.
//
// `fetch` is stubbed rather than mocked at the module boundary, so what runs is the real
// `useProviderAccounts` poll against the real component - including the fact that the
// login state arrives on an ordinary accounts read rather than on the login's own response.
import { render } from 'preact'
import { useState } from 'preact/hooks'
import { AccountSwitcher } from '../../src/ProviderAccounts'
import '../../src/deviceMode'
import '../../src/style.css'

type Call = { method: string; url: string }

const calls: Call[] = []
const params = new URLSearchParams(location.search)
// `empty` is the state a new install is in and the one the popover had no answer for.
const saved = params.get('saved') === '1'
const many = params.get('accounts') === 'multi'
// A Codex switch that left sessions behind: the one scenario that draws a notice at all.
const stranded = params.get('accounts') === 'stranded'
const outcome = params.get('outcome') || 'succeeded'

const NOW = Math.floor(Date.now() / 1000)
type Login = { provider: string; state: string; started_at: number; finished_at?: number; account_id?: string | null; label?: string | null; error?: string | null } | null

const ACCOUNTS = [
  {
    id: 'account-claude', provider: 'claude', label: 'work', created_at: NOW, updated_at: NOW,
    identity_source: 'token', email: 'work@example.com',
    quota: { status: 'ok', session: { used_percent: 12, window_minutes: 300 }, weekly: { used_percent: 40, window_minutes: 10080 }, refreshed_at: NOW },
  },
]

// Three accounts whose every figure is a different width - `5%` against `100%`, `22m`
// against `6d23h` - plus one with no Fable reading at all and one whose poll failed. Read
// as sentences and stacked, no two of these percentages land in the same place, which is
// the thing `account-switcher.spec.ts` measures. One account could never show it.
const MANY = [
  {
    id: 'account-claude', provider: 'claude', label: 'work', created_at: NOW, updated_at: NOW,
    identity_source: 'token', email: 'work@example.com',
    quota: {
      status: 'ok', refreshed_at: NOW,
      session: { used_percent: 5, window_minutes: 300, resets_at: NOW + 4 * 3600 + 3 * 60 },
      weekly: { used_percent: 63, window_minutes: 10080, resets_at: NOW + 3 * 86400 + 3600 },
      fable: { used_percent: 30, window_minutes: 10080 },
    },
  },
  {
    id: 'account-claude-2', provider: 'claude', label: 'personal', created_at: NOW, updated_at: NOW,
    identity_source: 'token', email: 'me@example.com',
    quota: {
      status: 'ok', refreshed_at: NOW - 7200,
      session: { used_percent: 100, window_minutes: 300, resets_at: NOW + 22 * 60 },
      weekly: { used_percent: 7, window_minutes: 10080, resets_at: NOW + 6 * 86400 + 23 * 3600 },
    },
  },
  {
    id: 'account-claude-3', provider: 'claude', label: 'client', created_at: NOW, updated_at: NOW,
    identity_source: 'cli', email: 'client@example.com',
    quota: { status: 'error', error: 'claude usage endpoint returned 500', refreshed_at: NOW - 60 },
  },
]

// Two Codex accounts, the second just selected, with seventeen sessions still on the
// first and one on a login mux never saved. The email is long on purpose: the collapsed
// line has to ellipsize it, not wrap into the paragraph it replaced.
const STRANDED = [
  ...ACCOUNTS,
  {
    id: 'account-codex', provider: 'codex', label: 'gorskovich.tony@example.com', created_at: NOW, updated_at: NOW,
    identity_source: 'token', email: 'gorskovich.tony@example.com',
    quota: { status: 'ok', session: { used_percent: 41, window_minutes: 300 }, weekly: { used_percent: 12, window_minutes: 10080 }, refreshed_at: NOW },
  },
  {
    id: 'account-codex-2', provider: 'codex', label: 'adamb@example.com', created_at: NOW, updated_at: NOW,
    identity_source: 'token', email: 'adamb@example.com',
    quota: { status: 'ok', session: { used_percent: 3, window_minutes: 300 }, weekly: { used_percent: 1, window_minutes: 10080 }, refreshed_at: NOW },
  },
]

let login: Record<string, Login> = { claude: null, codex: null }

const snapshot = () => ({
  providers: ['claude', 'codex'],
  selected: { claude: saved || many || stranded ? 'account-claude' : null, codex: stranded ? 'account-codex-2' : null },
  current: {
    claude: { state: saved || many || stranded ? 'saved' : 'signed_out', account_id: saved || many || stranded ? 'account-claude' : null },
    codex: stranded ? { state: 'saved', account_id: 'account-codex-2' } : { state: 'signed_out', account_id: null },
  },
  accounts: many ? MANY : stranded ? STRANDED : saved ? ACCOUNTS : [],
  poll_minutes: 5,
  stale_minutes: 30,
  refreshing: false,
  login,
  login_commands: { claude: 'claude auth login --claudeai', codex: 'codex login' },
  // What the daemon declares per provider: Claude Code re-reads its credential file and
  // follows a switch, Codex keeps the login it started with.
  switch_reaches_live: { claude: true, codex: false },
  // Live sessions by the account they were spawned under. `account-claude` is the
  // selected one; the other two rows are what a switch left behind, which is the
  // whole reason the count is drawn.
  sessions: many
    ? {
        by_account: { 'account-claude': 5, 'account-claude-2': 2 },
        unsaved: { claude: 1 },
        unattributed: {},
      }
    : stranded
      ? {
          by_account: { 'account-claude': 1, 'account-codex': 17, 'account-codex-2': 1 },
          unsaved: { codex: 1 },
          unattributed: {},
        }
      : { by_account: {}, unsaved: {}, unattributed: {} },
})

window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url)
  const method = init?.method || 'GET'
  calls.push({ method, url })
  if (url.includes('/login/dismiss')) {
    login = { ...login, claude: null }
  } else if (url.endsWith('/claude/login')) {
    // Exactly what the daemon does: the response says a run is *running*, and the outcome
    // lands on a later read. A harness that answered with the finished state here would
    // prove the opposite of the thing under test.
    login = { ...login, claude: { provider: 'claude', state: 'running', started_at: NOW } }
    window.setTimeout(() => {
      login = {
        ...login,
        claude: outcome === 'failed'
          ? { provider: 'claude', state: 'failed', started_at: NOW, finished_at: NOW, error: 'claude command failed: browser login was cancelled' }
          : { provider: 'claude', state: 'succeeded', started_at: NOW, finished_at: NOW, account_id: 'account-claude', label: 'work@example.com' },
      }
    }, 60)
  }
  return new Response(JSON.stringify(snapshot()), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

document.body.style.margin = '0'
document.body.style.width = '220px'
document.documentElement.style.setProperty('--ui-scale', '1')
// The status block sits at the *bottom* of the sidebar in the real shell, and the full
// switcher's popover opens upward from wherever its trigger is. Rendered against the top of
// the viewport it would be placed off-screen, so the column is given its real height.
// The invitation's dismissal is machine config in the real shell, so the host owns it
// here too - which is exactly the property the spec presses: `hide` has to remove the
// block through the host rather than by the component remembering anything.
function Harness() {
  const [dismissed, setDismissed] = useState(params.get('prompt') === 'hidden')
  return <div class="app-shell" style="height:100vh">
    <div class="sidebar" style="height:100vh;display:flex;flex-direction:column">
      <div class="sidebar-status">
        <AccountSwitcher
          onManage={() => { calls.push({ method: 'UI', url: 'manage' }) }}
          onViewUsage={() => { calls.push({ method: 'UI', url: 'view-usage' }) }}
          promptDismissed={dismissed}
          onDismissPrompt={() => { calls.push({ method: 'UI', url: 'dismiss-prompt' }); setDismissed(true) }}
        />
      </div>
    </div>
  </div>
}

render(<Harness />, document.querySelector('#root')!)

Object.assign(window as unknown as Record<string, unknown>, { __calls: calls })
