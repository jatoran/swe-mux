/**
 * The demo's machine: processes, bandwidth, disk, and the durable telemetry the
 * Resources dialog and the Processes drawer tab read.
 *
 * Derived from the demo store rather than written out flat, so a session the visitor
 * spawns or kills changes the fleet the same way the rest of the UI changes - a
 * process table that kept naming a pane you just closed would undo the point of
 * showing it at all.
 *
 * Every figure is invented. No number here is measured from a real machine, and the
 * shapes are the daemon's so the real components render unmodified.
 */
import type { DaemonProcesses, FleetSnapshot, ProcessItem, SessionProcesses } from '../processFleet.ts'
import type { Session } from '../types.ts'
import { DEMO_PROJECT_ID } from './fixtures.ts'
import { nowSeconds, state } from './store.ts'

/** Deterministic per-session jitter, so a row does not jump on every poll. */
function seed(text: string): number {
  let value = 0
  for (let index = 0; index < text.length; index += 1) value = (value * 31 + text.charCodeAt(index)) | 0
  return Math.abs(value)
}

const MEGABYTE = 1024 * 1024

/**
 * A slow, bounded wobble around a session's baseline.
 *
 * Static numbers made the Processes tab read as a screenshot: the whole reason a
 * fleet view is worth opening is that it moves. The period is long enough (~40s)
 * that two frames sampling a second apart agree, which is what a real poll looks
 * like, and it is a function of the clock rather than of the poll so the desktop
 * and phone frames report the same reading.
 */
function wobble(key: string, amplitude: number): number {
  const phase = (Date.now() / 40_000) + seed(key) / 997
  return Math.sin(phase * Math.PI * 2) * amplitude
}

function agentProcesses(session: Session): ProcessItem[] {
  const base = seed(session.id)
  const working = session.state === 'working'
  const cli: ProcessItem = {
    pid: session.pid,
    executable: session.exe,
    command: `${session.exe} --demo`,
    started_at: session.created_at,
    cpu_pct: Math.max(0.2, (working ? 24 : 1.4) + wobble(session.id, working ? 6 : 0.9)),
    memory_bytes: (210 + (base % 90)) * MEGABYTE,
    memory_unique_bytes: (160 + (base % 70)) * MEGABYTE,
    listeners: [],
    connections: [
      { local_host: '127.0.0.1', local_port: 40000 + (base % 9000), remote_host: 'api.example.invalid', remote_port: 443 },
    ],
    conditions: working ? ['busy'] : [],
    job_assignment: 'assigned',
    evidence_state: 'active',
    confidence: 'high',
    attribution_source: 'session_root',
    first_seen: session.created_at,
    last_seen: nowSeconds(),
  }
  const child: ProcessItem = {
    pid: session.pid + 1,
    parent_pid: session.pid,
    executable: 'node',
    command: working ? 'node --test --runInBand' : 'node esbuild-service',
    started_at: session.created_at + 30,
    cpu_pct: Math.max(0, (working ? 61 : 0.4) + wobble(`${session.id}-child`, working ? 14 : 0.3)),
    memory_bytes: (90 + (base % 40)) * MEGABYTE,
    memory_unique_bytes: (64 + (base % 30)) * MEGABYTE,
    listeners: [],
    connections: [],
    conditions: [],
    job_assignment: 'assigned',
    evidence_state: 'active',
    confidence: 'high',
    attribution_source: 'job_membership',
    first_seen: session.created_at + 30,
    last_seen: nowSeconds(),
  }
  return [cli, child]
}

function shellProcesses(session: Session): ProcessItem[] {
  const base = seed(session.id)
  return [
    {
      pid: session.pid,
      executable: session.exe,
      command: session.exe,
      started_at: session.created_at,
      cpu_pct: 0.1 + Math.abs(wobble(session.id, 0.2)),
      memory_bytes: (18 + (base % 8)) * MEGABYTE,
      memory_unique_bytes: (11 + (base % 5)) * MEGABYTE,
      listeners: [],
      connections: [],
      conditions: [],
      job_assignment: 'assigned',
      evidence_state: 'active',
      confidence: 'high',
      attribution_source: 'session_root',
      first_seen: session.created_at,
      last_seen: nowSeconds(),
    },
    {
      // The dev server behind the demo's preview pane. It carries the listener, which
      // is what makes "register a preview" a real act on this surface rather than a
      // button with nothing behind it.
      pid: session.pid + 7,
      parent_pid: session.pid,
      executable: 'node',
      command: 'node scripts/dev-server.js --port 5173',
      started_at: session.created_at + 12,
      cpu_pct: 1.1 + Math.abs(wobble(`${session.id}-server`, 0.8)),
      memory_bytes: (140 + (base % 30)) * MEGABYTE,
      memory_unique_bytes: (96 + (base % 20)) * MEGABYTE,
      listeners: [{ host: '127.0.0.1', port: 5173, loopback: true, url: 'http://127.0.0.1:5173/' }],
      connections: [
        { local_host: '127.0.0.1', local_port: 5173, remote_host: '127.0.0.1', remote_port: 61122 },
      ],
      conditions: [],
      job_assignment: 'assigned',
      evidence_state: 'active',
      confidence: 'high',
      attribution_source: 'parent_walk',
      server_eligible: true,
      first_seen: session.created_at + 12,
      last_seen: nowSeconds(),
    },
  ]
}

function groupFor(session: Session): SessionProcesses {
  return {
    session_id: session.id,
    project_id: session.project_id,
    processes: session.backend === 'shell' ? shellProcesses(session) : agentProcesses(session),
  }
}

function daemonBucket(): DaemonProcesses {
  const members: ProcessItem[] = [
    {
      pid: 4218, executable: 'swe-mux', command: 'swe-mux --serve',
      started_at: nowSeconds() - 26_400,
      cpu_pct: 0.8 + Math.abs(wobble('daemon', 0.5)),
      memory_bytes: 186 * MEGABYTE, memory_unique_bytes: 141 * MEGABYTE,
      listeners: [{ host: '127.0.0.1', port: 8765, loopback: true, url: 'http://127.0.0.1:8765/' }],
      connections: [], conditions: [], evidence_state: 'active', confidence: 'high',
    },
    {
      pid: 4219, parent_pid: 4218, executable: 'swe-mux-supervisor',
      command: 'swe-mux-supervisor --serve', started_at: nowSeconds() - 26_400,
      cpu_pct: 0.2, memory_bytes: 42 * MEGABYTE, memory_unique_bytes: 31 * MEGABYTE,
      listeners: [], connections: [], conditions: [], evidence_state: 'active', confidence: 'high',
    },
  ]
  return {
    pid: 4218,
    processes: members.length,
    cpu_pct: members.reduce((total, item) => total + item.cpu_pct, 0),
    memory_bytes: members.reduce((total, item) => total + item.memory_bytes, 0),
    memory_unique_bytes: members.reduce((total, item) => total + (item.memory_unique_bytes || 0), 0),
    listeners: 1,
    connections: 0,
    members,
  }
}

/** `GET /api/processes` (and its `?session=` / `?summary=1` / `?unique_memory=1` forms). */
export function processesPayload(sessionId?: string): FleetSnapshot {
  const sessions = state.sessions.filter(item => !sessionId || item.id === sessionId)
  const groups = sessions.map(groupFor)
  const processes = groups.flatMap(group => group.processes)
  const daemon = daemonBucket()
  return {
    available: true,
    sessions: groups,
    daemon,
    system_cpu_pct: Math.round((17 + wobble('system', 9)) * 10) / 10,
    ownership_diagnostics: [
      {
        ts: nowSeconds() - 640, kind: 'reattributed', pid: 41880,
        session_id: state.sessions[0]?.id, reason: 'job membership confirmed after a parent walk lost the trail',
      },
    ],
    totals: {
      processes: processes.length,
      cpu_pct: Math.round(processes.reduce((total, item) => total + item.cpu_pct, 0) * 10) / 10,
      memory_bytes: processes.reduce((total, item) => total + item.memory_bytes, 0),
      memory_unique_bytes: processes.reduce((total, item) => total + (item.memory_unique_bytes || 0), 0),
      listeners: processes.reduce((total, item) => total + item.listeners.length, 0),
      connections: processes.reduce((total, item) => total + item.connections.length, 0),
    },
  }
}

// ----------------------------------------------------------------- telemetry

/** `GET /api/telemetry/workloads`: observed behaviour per backend and model. */
export function workloadsPayload(): unknown {
  const dimensions = new Map<string, {
    backend: string; model: string; runs: number; ended_runs: number
    tokens_in: number; tokens_out: number; final: number; peak: number
    turns: number; worked: number
  }>()
  for (const session of state.sessions) {
    if (session.backend === 'shell') continue
    const key = `${session.backend}:${session.model || session.backend}`
    const row = dimensions.get(key) || {
      backend: session.backend, model: session.model || session.backend,
      runs: 0, ended_runs: 0, tokens_in: 0, tokens_out: 0, final: 0, peak: 0, turns: 0, worked: 0,
    }
    row.runs += 1
    row.ended_runs += session.state === 'working' ? 0 : 1
    row.tokens_in += session.tokens_in
    row.tokens_out += session.tokens_out
    row.final += session.context_pct
    row.peak += session.context_peak_pct ?? session.context_pct
    row.turns += session.turn_seq ?? 0
    row.worked += (session.worked_ms ?? 0) / 1000
    dimensions.set(key, row)
  }
  return {
    since: nowSeconds() - 7 * 86400,
    interpretation: 'Seven days of invented runs, shown so the shape of this table is legible.',
    dimensions: [...dimensions.values()].map(row => ({
      backend: row.backend,
      model: row.model,
      runs: row.runs,
      ended_runs: row.ended_runs,
      average_duration_s: row.runs ? Math.round(row.worked / row.runs) : 0,
      tokens_in: row.tokens_in,
      tokens_out: row.tokens_out,
      average_final_context_pct: row.runs ? row.final / row.runs : 0,
      average_peak_context_pct: row.runs ? row.peak / row.runs : 0,
      turns_per_run: row.runs ? row.turns / row.runs : 0,
      stalls_per_run: row.backend === 'shell' ? 0 : 0.18,
      approvals_per_run: 0.42,
      completion_evidence_count: row.turns,
      completion_evidence_runs: row.ended_runs,
    })),
  }
}

/** `GET /api/telemetry/operational`: the durable evidence store, bounded. */
export function operationalPayload(): unknown {
  const agents = state.sessions.filter(session => session.backend !== 'shell')
  const tools = ['Read', 'Update', 'Bash', 'Grep']
  return {
    schema_version: 4,
    interpretation: 'Invented evidence for the demo. Descriptive only; nothing here ranks an agent.',
    quota: { samples: [], resets: [], attributions: [], rollups: [] },
    tools: {
      metrics: agents.flatMap((session, index) => tools.slice(0, 2 + (index % 3)).map((tool, position) => ({
        backend: session.backend,
        model: session.model || session.backend,
        project_id: session.project_id,
        session_id: session.id,
        taxonomy: position === 0 ? 'file.read' : position === 1 ? 'file.write' : 'process.run',
        raw_tool: tool,
        events: 6 + ((seed(session.id + tool) % 40)),
        uses: 4 + ((seed(tool + session.id) % 30)),
        errors: seed(session.id) % 3,
        average_duration_ms: 120 + (seed(tool) % 900),
      }))),
      skills: [
        {
          explicit_skill: 'code-review', backend: agents[0]?.backend || 'agent',
          project_id: DEMO_PROJECT_ID, uses: 3, last_used_at: nowSeconds() - 5400,
        },
        {
          explicit_skill: 'documentation', backend: agents[1]?.backend || 'agent',
          project_id: DEMO_PROJECT_ID, uses: 1, last_used_at: nowSeconds() - 86_400,
        },
      ],
      unknown_or_unmapped: 2,
      parser_version: 'demo-3',
      parser_versions: Object.fromEntries(
        [...new Set(agents.map(session => session.backend))].map(backend => [backend, 'demo-3']),
      ),
      coverage: agents.map(session => ({
        session_id: session.id,
        backend: session.backend,
        parser_version: 'demo-3',
        status: 'reconciled',
        recognized_records: 40 + (seed(session.id) % 200),
        unknown_records: seed(session.id) % 4,
        tool_events: 12 + (seed(session.id) % 60),
        skill_events: seed(session.id) % 3,
        compaction_events: session.compaction_count ?? 0,
        reconciled_at: nowSeconds() - 900,
      })),
    },
    compactions: agents
      .filter(session => (session.context_peak_pct ?? 0) > 0.5)
      .map(session => ({
        session_id: session.id,
        backend: session.backend,
        project_id: session.project_id,
        count: 1 + (seed(session.id) % 3),
        last_compaction_at: nowSeconds() - 3600 - (seed(session.id) % 7200),
        capability: 'native',
        confidence: 'high',
      })),
  }
}

// ---------------------------------------------------------------- bandwidth

const http = (requests: number, up: number, down: number, compressed = 0) => ({
  requests, request_bytes: up, response_bytes: down,
  compressed_responses: compressed, unknown_request_bodies: 0, unknown_response_bodies: 0,
})

const socket = (connections: number, active: number, rx: number, rxBytes: number, tx: number, txBytes: number) => ({
  connections, active_connections: active,
  received_frames: rx, received_bytes: rxBytes, sent_frames: tx, sent_bytes: txBytes,
})

/** `GET /api/diagnostics/network`. */
export function networkPayload(): unknown {
  const uptime = 26_400
  const routes = [
    { method: 'GET', route: '/api/sessions', ...http(5280, 0, 41 * MEGABYTE, 5280) },
    { method: 'GET', route: '/api/git/worktrees', ...http(1140, 0, 9 * MEGABYTE, 1140) },
    { method: 'GET', route: '/api/processes', ...http(980, 0, 6 * MEGABYTE, 980) },
    { method: 'POST', route: '/api/sessions/{id}/read', ...http(412, 88_000, 96_000) },
  ]
  const channels = [
    { channel: '/pty/{id}', ...socket(14, 6, 8_420, 1_100_000, 96_400, 118 * MEGABYTE) },
    { channel: '/events', ...socket(4, 2, 210, 44_000, 12_800, 9 * MEGABYTE) },
  ]
  const totalHttp = routes.reduce((total, row) => ({
    requests: total.requests + row.requests,
    request_bytes: total.request_bytes + row.request_bytes,
    response_bytes: total.response_bytes + row.response_bytes,
    compressed_responses: total.compressed_responses + row.compressed_responses,
    unknown_request_bodies: 0, unknown_response_bodies: 0,
  }), http(0, 0, 0, 0))
  const totalSocket = channels.reduce((total, row) => socket(
    total.connections + row.connections,
    total.active_connections + row.active_connections,
    total.received_frames + row.received_frames,
    total.received_bytes + row.received_bytes,
    total.sent_frames + row.sent_frames,
    total.sent_bytes + row.sent_bytes,
  ), socket(0, 0, 0, 0, 0, 0))
  return {
    started_at: nowSeconds() - uptime,
    uptime_seconds: uptime,
    measurement: { http: 'after compression', websocket: 'before per-message compression' },
    totals: { http: totalHttp, websocket: totalSocket },
    peers: [
      { peer: '127.0.0.1', http: http(6900, 84_000, 52 * MEGABYTE, 6900), websocket: socket(12, 5, 8_100, 1_050_000, 92_000, 112 * MEGABYTE) },
      { peer: 'phone.tailnet', http: http(912, 4_000, 4 * MEGABYTE, 912), websocket: socket(6, 3, 530, 94_000, 17_200, 15 * MEGABYTE) },
    ],
    http_routes: routes,
    websocket_channels: channels,
    websocket_sent_payloads: [
      { peer: '127.0.0.1', channel: '/pty/{id}', kind: 'replay', frames: 42, bytes: 24 * MEGABYTE },
      { peer: '127.0.0.1', channel: '/pty/{id}', kind: 'live', frames: 88_400, bytes: 82 * MEGABYTE },
      { peer: 'phone.tailnet', channel: '/pty/{id}', kind: 'live', frames: 14_900, bytes: 12 * MEGABYTE },
    ],
  }
}

/** `GET /api/diagnostics/storage`. */
export function storagePayload(): unknown {
  const bucket = (name: string, bytes: number, files: number) => ({ name, bytes, files })
  const global = [
    bucket('database', 412 * MEGABYTE, 1),
    bucket('webview', 268 * MEGABYTE, 4_120),
    bucket('logs', 84 * MEGABYTE, 62),
    bucket('worktrees', 1_240 * MEGABYTE, 18_402),
    bucket('recovery', 46 * MEGABYTE, 340),
    bucket('media', 22 * MEGABYTE, 96),
    bucket('trash', 9 * MEGABYTE, 41),
  ]
  const projects = state.projects.map((project, index) => ({
    project_id: project.id,
    label: project.name,
    root: project.root,
    present: true,
    bytes: (index === 0 ? 96 : 12) * MEGABYTE,
    files: index === 0 ? 1_840 : 210,
    buckets: [
      bucket('database', (index === 0 ? 62 : 8) * MEGABYTE, 1),
      bucket('media', (index === 0 ? 26 : 3) * MEGABYTE, index === 0 ? 140 : 12),
      bucket('other', (index === 0 ? 8 : 1) * MEGABYTE, index === 0 ? 1_699 : 197),
    ],
  }))
  return {
    generated_at: nowSeconds() - 120,
    duration_ms: 940,
    cached: true,
    age_seconds: 120,
    data_dir: '/home/demo/.mux',
    global: {
      present: true,
      total_bytes: global.reduce((total, item) => total + item.bytes, 0),
      total_files: global.reduce((total, item) => total + item.files, 0),
      buckets: global,
    },
    projects: {
      total_bytes: projects.reduce((total, item) => total + item.bytes, 0),
      items: projects,
    },
  }
}
