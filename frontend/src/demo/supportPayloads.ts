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

// ---------------------------------------------------------------- prompt queue

export function queueSummaryPayload(): unknown {
  return { targets: [] }
}

export function queueAutoPayload(): unknown {
  return {
    master_enabled: false,
    paused: false,
    quiet_hours: { start: '', end: '', active: false },
    stable_seconds: 8,
    max_consecutive: 10,
    session_ttl_minutes: 120,
    reply_window_minutes: 60,
    sessions: [],
    counters: {},
    promotion: {
      criteria: {}, met: false, auto_sends: 0, unsafe_reports: 0,
      proving_days: 0, required_sends: 50, required_days: 7, fixture_classes: [],
    },
    last_error: '',
  }
}

export function queueMessagesPayload(sessionId: string): unknown {
  const session = state.sessions.find(item => item.id === sessionId)
  return {
    target: {
      session_id: sessionId,
      live: Boolean(session),
      agent_run_id: session?.agent_run_id ?? null,
      label: session?.name ?? null,
      state: session?.state ?? null,
      delivery_readiness: session?.delivery_readiness ?? null,
    },
    messages: [],
    pending: 0,
  }
}

export function queueMailboxPayload(author: string): unknown {
  return {
    author,
    messages: [],
    spawn_requests: [],
    spawn_request_errors: [],
    control_requests: [],
    targets: state.sessions.map(session => ({
      target_session_id: session.id,
      label: session.name,
      project_id: session.project_id,
    })),
  }
}

// ------------------------------------------------------------------- library

export function promptsPayload(): unknown {
  return {
    configured_scope: 'both',
    items: [
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

export function providerAccountsPayload(): unknown {
  return {
    providers: [],
    selected: {},
    current: {},
    accounts: [],
    poll_minutes: 15,
    stale_minutes: 60,
    refreshing: false,
    login: {},
    login_commands: {},
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
