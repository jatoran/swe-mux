/**
 * Correctly-shaped answers for the surfaces the demo does not populate.
 *
 * These exist because of a specific failure, and the failure is worth recording: the
 * fake daemon used to answer an unmatched GET with `{}`, and `{}` is not a safe empty
 * value. A view that renders `payload.items.map(...)` throws on it, a throw during
 * render tears the whole app down, and what the visitor saw was a frozen page with a
 * panel that would not close - reachable from seven of the app menu's own rows. An
 * empty *list* renders an empty state; a missing list is a crash.
 *
 * So every route a visitor can reach is answered here with the shape its reader
 * expects, even when the content is deliberately nothing. Anything genuinely absent
 * from the demo says so in its own diagnostic rather than by being malformed.
 */
import { DEMO_PROJECT_ID } from './fixtures.ts'
import { nowSeconds, state } from './store.ts'

const HOUR = 3600

// The prompt queue, the notification history and the land queue used to answer from here
// with a correct, permanently empty shape. They are state now (`controlPlane.ts`), because
// the scenario director needed them to move and a surface that cannot change cannot
// demonstrate the half of the product that is not a terminal. Everything remaining in this
// file is still genuinely nothing, and says so in its own diagnostic.

// ------------------------------------------------------- update and plugins

/**
 * `GET /api/update`.
 *
 * The demo is a published build of a released version and has no daemon to ask, so the
 * honest answer is "checked, nothing newer" - and `banner: false` is the load-bearing
 * field, because a marketing page must not grow an update banner for a product the
 * visitor has not installed. Answered explicitly rather than left to the unmatched-route
 * fallback for the usual reason: `{}` reaches `shouldShowUpdateBanner` and every reader
 * of `latest`, and the failure mode of a wrong shape is a torn-down render, not a gap.
 */
export function updatePayload(): unknown {
  return {
    enabled: false,
    status: 'disabled',
    current_version: 'demo',
    checked_at: null,
    next_check_at: null,
    update_available: false,
    latest: null,
    dismissed: [],
    banner: false,
  }
}

/**
 * `GET /api/plugins` and its development scan.
 *
 * Genuinely empty, like the automations: a demo that invented installed plugins would be
 * claiming something about a machine that does not exist. Execution is off, which is also
 * what the Settings page needs to explain itself rather than offer controls that cannot
 * do anything here.
 */
export function pluginsPayload(): unknown {
  return { execution_enabled: false, host_capabilities: [], development_root: '', plugins: [] }
}

export function pluginDevelopmentPayload(): unknown {
  return {
    root: '',
    exists: false,
    candidates: [],
    truncated: false,
    diagnostic: 'The demo has no filesystem to scan for plugins in development.',
  }
}

// ------------------------------------------------------------------- library

export function promptsPayload(): unknown {
  return {
    configured_scope: 'both',
    items: [
      // First and favourite, and the only one with no variables - which is what makes it
      // the phone's way to start a turn. A template with variables opens a field form,
      // and a form needs a keyboard the embed deliberately refuses (`main.tsx`), so
      // without one of these there is no keyboard-free way to say anything to an agent.
      {
        id: 'demo-blocked', key: 'blocked', scope: 'global', title: 'What is blocked?',
        body: 'What is blocked right now, and what is it waiting on?',
        tags: ['status'], variables: [], backends: [],
        created_at: nowSeconds() - 12 * 86400, updated_at: nowSeconds() - 86400,
        revision: '1', favorite: true, use_count: 31, conflict: false,
        project_id: null, project_name: null,
      },
      {
        id: 'demo-review', key: 'review', scope: 'global', title: 'Review this diff',
        body: 'Review the diff on {{branch}} for correctness bugs, then stop.',
        tags: ['review'], variables: ['branch'], backends: [],
        created_at: nowSeconds() - 9 * 86400, updated_at: nowSeconds() - 2 * 86400,
        revision: '1', favorite: true, use_count: 12, conflict: false,
        project_id: null, project_name: null,
      },
      {
        id: 'demo-repro', key: 'repro', scope: 'project', title: 'Minimal repro',
        body: 'Reduce {{failure}} to the smallest input that still fails.',
        tags: ['debug'], variables: ['failure'], backends: [],
        created_at: nowSeconds() - 4 * 86400, updated_at: nowSeconds() - 86400,
        revision: '1', favorite: false, use_count: 3, conflict: false,
        project_id: DEMO_PROJECT_ID, project_name: 'rocket-shop',
      },
    ],
    projects: state.projects.map(project => ({ id: project.id, name: project.name })),
    diagnostics: [],
  }
}

export function clipboardPayload(): unknown {
  const entry = (id: string, preview: string, minutesAgo: number, pinned = false) => ({
    id, preview,
    char_count: preview.length,
    line_count: preview.split('\n').length,
    source: 'terminal',
    session_id: state.sessions[0]?.id ?? null,
    project_id: DEMO_PROJECT_ID,
    device: 'demo desktop',
    pinned,
    created_at: nowSeconds() - minutesAgo * 60,
    updated_at: nowSeconds() - minutesAgo * 60,
  })
  return {
    enabled: true, persist: true, limit: 200, entry_max_chars: 100_000,
    retention_hours: 128, redact_secrets: true, count: 3,
    entries: [
      entry('clip-1', 'npm test -- --runInBand tests/checkout.spec.ts', 4),
      entry('clip-2', 'git worktree add ../.worktrees/coupon-table -b agent/coupon-table', 26, true),
      entry('clip-3', '92% of samples sit inside JSON.parse on the coupon table', 71),
    ],
  }
}

export function schedulesPayload(): unknown {
  return {
    schedules: [],
    status: { enabled: false, total: 0, armed: 0, live_sessions: 0, max_concurrent: 3 },
  }
}

// ------------------------------------------------------------------- alerts

export function attentionInboxPayload(): unknown {
  const now = nowSeconds()
  const rage = state.sessions.find(session => session.id === 's-rage')
  const working = state.sessions.find(session => session.id === 's-working')
  const item = (input: {
    id: string; channel: string; title: string; summary: string; action: string
    cls: string; score: number; confidence: number; session?: string; age: number
  }) => ({
    id: input.id,
    incident_key: `${input.cls}:${input.session || 'fleet'}`,
    project_id: DEMO_PROJECT_ID,
    session_id: input.session,
    agent_run_id: input.session ? `run-${input.session}` : undefined,
    incident_class: input.cls,
    kinds: [input.cls],
    title: input.title,
    summary: input.summary,
    action: input.action,
    channel: input.channel,
    cost_to_resolve: input.channel === 'interrupt_now' ? 'seconds' : 'minutes',
    score: input.score,
    confidence: input.confidence,
    evidence: [{ kind: 'demo', note: 'Invented for the demo; nothing was observed.' }],
    contributions: 2,
    narration_status: 'off',
    state: 'open',
    created_at: now - input.age,
    updated_at: now - input.age,
  })
  return {
    generated_at: now,
    channels: {
      interrupt_now: rage ? [item({
        id: 'att-1', channel: 'interrupt_now', cls: 'context_pressure',
        title: 'Context is nearly full on "prod is down (4h)"',
        summary: '86% of the window is used and the last three turns each added more than they resolved.',
        action: 'Compact the conversation or start a fresh one from a summary.',
        score: 0.91, confidence: 0.84, session: rage.id, age: 240,
      })] : [],
      next_breakpoint: working ? [item({
        id: 'att-2', channel: 'next_breakpoint', cls: 'possibly_stuck',
        title: '"refactor the coupon table" has run one command for 96 seconds',
        summary: 'A test run has not returned and no output has arrived since it started.',
        action: 'Look at the pane before it reaches the interrupt budget.',
        score: 0.62, confidence: 0.55, session: working.id, age: 96,
      })] : [],
      inbox: [item({
        id: 'att-3', channel: 'inbox', cls: 'cross_session_conflict',
        title: 'Two checkouts are editing src/cart.js',
        summary: 'agent/coupon-table and agent/cart-profile both have uncommitted changes to the same file.',
        action: 'Land one of them before the other rebases.',
        score: 0.44, confidence: 0.71, age: 1800,
      })],
      digest: [item({
        id: 'att-4', channel: 'digest', cls: 'record',
        title: 'agent/cart-profile landed',
        summary: 'Verification was skipped: the incoming diff was documentation only.',
        action: '',
        score: 0.1, confidence: 0.99, age: 2 * HOUR,
      })],
    },
    suppressed: { budget_exhausted: 1, low_confidence: 2 },
    suppressed_total: 3,
    budget: {
      day: new Date(now * 1000).toISOString().slice(0, 10),
      daily_budget: 4, used: 1, remaining: 3,
      hourly_cap: 2, burst_used: 1, burst_remaining: 1,
    },
    fanout: {
      status: 'ok', samples: 42, required: 20,
      interaction_seconds: 96, neglect_seconds: 640,
      sustainable_agents: 4, attended_now: 2,
    },
    resumption_lag: { samples: 12, mean_seconds: 214, max_seconds: 980 },
    rules: [],
    delivery: { push: false, surface: 'demo' },
  }
}

// -------------------------------------------------------------------- money

export function usagePayload(): unknown {
  const day = (offset: number) => new Date((nowSeconds() - offset * 86400) * 1000)
    .toISOString().slice(0, 10)
  const daily = Array.from({ length: 14 }, (_, index) => {
    const scale = 1 + Math.abs(Math.sin(index * 1.7))
    return {
      date: day(index),
      input_tokens: Math.round(41_000 * scale),
      output_tokens: Math.round(9_400 * scale),
      cache_read_tokens: Math.round(280_000 * scale),
      cache_creation_tokens: Math.round(31_000 * scale),
      total_tokens: Math.round(361_400 * scale),
      cost_usd: Math.round(410 * scale) / 100,
      month: day(index).slice(0, 7),
      source_id: 'demo',
    }
  })
  return {
    enabled: true,
    refreshing: false,
    refresh_minutes: 180,
    package: 'ccusage',
    install_command: 'npm i -g ccusage',
    collector: { id: 'demo', status: 'ok', refreshed_at: nowSeconds() - 1800 },
    cache: {
      version: 1,
      updated_at: nowSeconds() - 1800,
      sources: { demo: { id: 'demo', label: 'demo transcripts', daily } },
    },
  }
}

export function automationDashboardPayload(): unknown {
  const now = nowSeconds()
  return {
    controls: { automation_enabled: false, scan_timeline_enabled: true },
    engine: {
      enabled: false,
      diagnostic: 'Automation is switched off in the demo; nothing here can call a model.',
      rules: [],
      built_in_rules: [],
      queue: { size: 0, capacity: 256, dropped: 0, loop_rejections: 0 },
      capabilities: { triggers: [], observer_schemas: [] },
    },
    provider: {
      secret: { configured: false, source: 'none' },
      models: { models: [], stale: false },
      cheap_model: '', standard_model: '',
    },
    spend_today: { tokens: 0, cost_usd: 0 },
    observer_calls: {},
    annotations: {},
    unread_notifications: 0,
    recent_firings: [],
    recent_action_results: [],
    recent_observer_calls: [],
    spend_breakdown: {
      days: 7,
      today: new Date(now * 1000).toISOString().slice(0, 10),
      start_day: new Date((now - 7 * 86400) * 1000).toISOString().slice(0, 10),
      rules: [],
      totals: {
        calls: 0, tokens: 0, cost_usd: 0,
        today_calls: 0, today_tokens: 0, today_cost_usd: 0,
        unpriced_calls: 0,
      },
    },
  }
}

export function automationMatrixPayload(): unknown {
  return {
    automations: [],
    projects: state.projects.map(project => ({
      project_id: project.id,
      project_name: project.name,
      status: 'ready',
      revision: '1',
      requested: {},
      enabled: [],
      blocked: {},
      unverified: [],
      globally_disabled: [],
      llm: { ready: false, reason: 'No model provider is configured in the demo.' },
      scan_timeline_auto_enable: true,
      authority: {},
      authority_effective: {},
    })),
    global_allow: {},
    install_switches: {
      automation_enabled: false, scan_timeline_enabled: true,
      scheduled_runs_enabled: false, land_queue_enabled: false,
    },
    authority_fields: [],
    authority_default: {},
    authority_ceiling: {},
  }
}

export function injectionSafetyPayload(): unknown {
  return {
    version: 1,
    research_only: true,
    authorizes_actuation: false,
    shadow_metrics: {
      evaluations: {}, reasons: {}, tracked_sessions: 0,
      unknown_duration_s: 0, transitions: 0,
    },
    parser_coverage: [],
    sessions: [],
  }
}

export function grantsPayload(): unknown {
  return {
    items: [],
    project_starting_sets: { sets: [] },
    llm: { ready: false, reason: 'No model provider is configured in the demo.' },
  }
}

/**
 * The two providers swe-mux manages accounts for, taken from the demo's own agent
 * sessions rather than named here.
 *
 * Reading them off the fleet keeps this module free of harness-name literals (the rule
 * `tests/test_harness_name_literals.py` enforces), and means the switcher can only ever
 * offer providers the demo actually demonstrates.
 */
export const demoProviders = (): string[] =>
  [...new Set(state.sessions.filter(session => session.backend !== 'shell').map(session => session.backend))]

/** Two saved accounts per provider, so the switcher has something to switch between. */
type DemoAccount = {
  suffix: string
  label: string
  email: string
  organization: string
  plan: string
  /** Percent used of the rolling five-hour window, then of the week. Whole percent,
   *  which is the scale `used_percent` carries - a fraction here rendered as "1%". */
  session: number
  weekly: number
  /** Minutes until the five-hour window rolls over, and until the weekly one does.
   *  Varied per account so the column does not read as one figure repeated. */
  resetsIn: number
  weeklyResetsIn: number
  verified: boolean
}

const ACCOUNTS: DemoAccount[][] = [
  [
    {
      suffix: 'personal', label: 'personal', email: 'demo@example.invalid',
      organization: 'Personal', plan: 'Max 20x',
      session: 62, weekly: 41, resetsIn: 94, weeklyResetsIn: 4 * 24 * 60, verified: true,
    },
    {
      suffix: 'work', label: 'rocket-shop work', email: 'demo@rocket-shop.invalid',
      organization: 'Rocket Shop', plan: 'Team',
      session: 18, weekly: 77, resetsIn: 212, weeklyResetsIn: 2 * 24 * 60 + 9 * 60, verified: true,
    },
  ],
  [
    {
      suffix: 'personal', label: 'personal', email: 'demo@example.invalid',
      organization: 'Personal', plan: 'Pro',
      session: 35, weekly: 22, resetsIn: 41, weeklyResetsIn: 6 * 24 * 60 + 2 * 60, verified: true,
    },
    {
      suffix: 'team', label: 'meme-garden team', email: 'bots@meme-garden.invalid',
      organization: 'Meme Garden', plan: 'Business',
      session: 88, weekly: 53, resetsIn: 17, weeklyResetsIn: 24 * 60 + 15 * 60, verified: false,
    },
  ],
]

const accountId = (provider: string, suffix: string): string => `acct-${provider}-${suffix}`

/** The account each provider is signed in as. The visitor's own choice wins. */
function selectedFor(provider: string, index: number): string {
  const chosen = state.providerSelection[provider]
  const rows = ACCOUNTS[index] || ACCOUNTS[0]
  if (chosen && rows.some(row => accountId(provider, row.suffix) === chosen)) return chosen
  // A different default per provider, so the demo shows a fleet that is genuinely
  // signed in to two different places at once rather than the first row twice.
  return accountId(provider, rows[index % rows.length].suffix)
}

export function providerAccountsPayload(): unknown {
  const now = nowSeconds()
  const providers = demoProviders()
  const window = (usedPercent: number, minutes: number, resetsIn: number | null) => ({
    used_percent: usedPercent,
    window_minutes: minutes,
    resets_at: resetsIn === null ? null : now + resetsIn * 60,
  })
  const accounts = providers.flatMap((provider, index) =>
    (ACCOUNTS[index] || ACCOUNTS[0]).map(row => ({
      id: accountId(provider, row.suffix),
      provider,
      label: row.label,
      email: row.email,
      organization: row.organization,
      provider_account_id: `${provider}-${row.suffix}-0000`,
      identity_source: row.verified ? 'token' : 'cli',
      identity_verified_at: row.verified ? now - 3 * HOUR : null,
      created_at: now - 30 * 86400,
      updated_at: now - HOUR,
      quota: {
        session: window(row.session, 300, row.resetsIn),
        weekly: window(row.weekly, 10_080, row.weeklyResetsIn),
        status: 'ok',
        error: null,
        refreshed_at: now - 240,
        attempted_at: now - 240,
        source: 'demo',
        plan: row.plan,
      },
      conflict: null,
    })))
  const selected = Object.fromEntries(providers.map((provider, index) => [provider, selectedFor(provider, index)]))
  return {
    providers,
    selected,
    current: Object.fromEntries(providers.map(provider => [provider, {
      state: 'saved',
      account_id: selected[provider],
      email: accounts.find(item => item.id === selected[provider])?.email ?? null,
      organization: accounts.find(item => item.id === selected[provider])?.organization ?? null,
      provider_account_id: accounts.find(item => item.id === selected[provider])?.provider_account_id ?? null,
      identity_source: 'token',
      match_hint: null,
    }])),
    accounts,
    poll_minutes: 15,
    stale_minutes: 60,
    refreshing: false,
    reset_alert: { count: 0, items: [] },
    login: Object.fromEntries(providers.map(provider => [provider, null])),
    login_commands: Object.fromEntries(providers.map(provider => [provider, `${provider} login`])),
    // Indexed like `ACCOUNTS`: the first fixture provider's CLI follows a switch on its
    // next request, the second keeps the login it started with until restarted.
    switch_reaches_live: Object.fromEntries(providers.map((provider, index) => [provider, index === 0])),
    // What each account was spawned under, which is what the daemon can honestly count:
    // it stamps the selection at spawn and cannot see a `/login` typed inside a pane.
    sessions: {
      by_account: Object.fromEntries(providers.map((provider, index) => [
        selectedFor(provider, index),
        state.sessions.filter(session => session.backend === provider).length,
      ])),
      unsaved: {},
      unattributed: {},
    },
  }
}

// ------------------------------------------------------------------ project

export function projectConfigPayload(projectId: string): unknown {
  const project = state.projects.find(item => item.id === projectId) || state.projects[0]
  return {
    project: { id: project?.id ?? projectId, label: project?.name ?? 'project', root: project?.root ?? '' },
    path: `${project?.root ?? ''}/.swe-mux/config.toml`,
    status: 'ready',
    revision: '1',
    values: {
      prompt_library_scope: 'both',
      notification_sounds_enabled: false,
      ignore_patterns: [],
      worktree: { verify_command: '.worktree-verify' },
    },
  }
}

export function projectAutomationsPayload(): unknown {
  return {
    revision: '1',
    // The two the demo actually demonstrates. Both are free (the timeline's records are
    // fixtures and the provenance ledger reads what swe-mux already has), so opting them
    // in here shows the surface rather than the grant gate in front of it. Everything
    // that could cost money stays out.
    requested: { scan_timeline: true, provenance_graph: true },
    enabled: ['scan_timeline', 'provenance_graph'],
    blocked: {},
    unverified: [],
    globally_disabled: [],
    llm: { ready: false, reason: 'No model provider is configured in the demo.' },
    automations: [],
    scan_timeline_auto_enable: true,
  }
}

// -------------------------------------------------------------------- agent

export function agentEnvironmentPayload(sessionId: string): unknown {
  const session = state.sessions.find(item => item.id === sessionId)
  const now = nowSeconds()
  const item = (id: string, kind: string, name: string, description: string, scope: string, origin: string) => ({
    id, kind, name, description, scope, origin, state: 'active',
    source_id: null, source_label: origin, changed_after_start: false, meta: [],
  })
  return {
    schema_version: 2,
    backend: session?.backend ?? '',
    cwd: session?.runtime_cwd ?? session?.cwd ?? '',
    generated_at: now,
    config_baseline: 'captured',
    runtime: {
      executable: session?.exe ?? '',
      version: '0.0.0-demo',
      model: session?.model ?? null,
      loaded_at: session?.created_at ?? now,
      run_started_at: session?.agent_run_started_at ?? null,
      options: [{ label: 'demo', value: 'nothing here is real' }],
      modes: ['demo'],
    },
    sources: [
      { id: 'src-user', label: '~/.config/agent/config.toml', scope: 'user', format: 'toml', status: 'read', mtime: now - 86400, changed_after_start: false },
      { id: 'src-project', label: '.swe-mux/config.toml', scope: 'project', format: 'toml', status: 'read', mtime: now - 5400, changed_after_start: false },
    ],
    sections: [
      {
        id: 'tools', label: 'Tools', completeness: 'complete', total: 3, truncated: false,
        note: 'Invented for the demo.',
        items: [
          item('t-read', 'tool', 'Read', 'Read a file from the checkout', 'built_in', 'built in'),
          item('t-edit', 'tool', 'Edit', 'Apply an exact string replacement', 'built_in', 'built in'),
          item('t-bash', 'tool', 'Bash', 'Run a command in the session shell', 'built_in', 'built in'),
        ],
      },
      {
        id: 'mcp', label: 'MCP servers', completeness: 'complete', total: 1, truncated: false,
        note: 'The demo ships one invented server.',
        items: [item('m-mux', 'mcp', 'mux', 'Fleet visibility across sibling sessions', 'managed', 'swe-mux')],
      },
      {
        id: 'instructions', label: 'Instructions', completeness: 'complete', total: 1, truncated: false,
        note: 'Instruction files this agent reads.',
        items: [item('i-claude', 'instruction', 'CLAUDE.md', 'Project instructions', 'project', 'checkout')],
      },
    ],
    diagnostics: [],
  }
}

export function skillsPayload(sessionId: string): unknown {
  const session = state.sessions.find(item => item.id === sessionId)
  const now = nowSeconds()
  const skill = (name: string, description: string, scope: string, origin: string) => ({
    name, description, path: `/skills/${name}/SKILL.md`, scope, origin,
    kind: 'skill', invocation: `/${name}`, mtime: now - 86400, implicit: false,
    display_name: null, short_description: description, shadowed_by: null,
    added_after_start: false,
  })
  return {
    backend: session?.backend ?? '',
    cwd: session?.runtime_cwd ?? session?.cwd ?? '',
    generated_at: now,
    agent_loaded_at: session?.created_at ?? now,
    agent_run_started_at: session?.agent_run_started_at ?? now,
    roots: [
      { path: '~/.agent/skills', scope: 'user', kind: 'skill', origin: 'user skills', exists: true, count: 2 },
      { path: '.agent/skills', scope: 'project', kind: 'skill', origin: 'this project', exists: true, count: 1 },
    ],
    skills: [
      skill('code-review', 'Review the diff for correctness bugs', 'user', 'user skills'),
      skill('documentation', 'Create or update project documentation', 'user', 'user skills'),
      skill('release', 'Cut a release from the checklist', 'project', 'this project'),
    ],
    errors: [],
    truncated: false,
    skipped_plugins: [],
    builtin_skills_hidden: true,
  }
}

// --------------------------------------------------------------- odds + ends

export function historyProjectsPayload(): unknown {
  return {
    items: state.projects.map(project => ({
      project_id: project.id, label: project.name, root: project.root, runs: project.history_count,
    })),
  }
}

export function filesTreePayload(projectId: string): unknown {
  const project = state.projects.find(item => item.id === projectId)
  const entry = (path: string, directory: boolean) => ({
    name: path.split('/').pop() || path,
    path,
    is_dir: directory,
    size: directory ? 0 : 2_400,
    mtime: nowSeconds() - 3600,
  })
  return {
    root: project?.root ?? '',
    path: '',
    truncated: false,
    entries: [
      entry('src', true), entry('tests', true), entry('site', true),
      entry('README.md', false), entry('package.json', false),
    ],
  }
}

export function lastReplyPayload(): unknown {
  return { text: '', ts: null, available: false }
}
