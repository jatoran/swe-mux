// The real Process fleet inspector over a stubbed daemon, so the spec measures the geometry the
// app draws. Row density and the collapsed/expanded split are pure CSS plus one class, which no
// unit test can see: `processRows.test.ts` proves the strings, this proves they fit on a line.
import { render } from 'preact'
import { ResourcesModal } from '../../src/ResourcesModal'
import type { ProcessItem } from '../../src/processFleet'
import type { Project, Session } from '../../src/types'
import '../../src/style.css'

const NOW = 1_770_000_000

const process = (overrides: Partial<ProcessItem> & { pid: number; executable: string }): ProcessItem => ({
  command: '', cpu_pct: 0, memory_bytes: 0, listeners: [], connections: [], conditions: [],
  started_at: NOW - 900, first_seen: NOW - 900, last_seen: NOW - 5, last_verified_at: NOW - 5,
  evidence_reason: 'live_descendant_fingerprint_match', confidence: 'high',
  attribution_source: 'session_root', attribution_version: 2,
  job_assignment: 'daemon_job_assigned;nested_session_job_assigned',
  ...overrides,
} as ProcessItem)

// One session that is a single process — the case where a rollup would only restate the row
// beneath it — and one deep enough to prove nesting, an abnormal state, and a listener survive.
const SNAPSHOT = {
  available: true,
  system_cpu_pct: 21.5,
  sessions: [
    {
      session_id: 'claude-d92695', project_id: 'p1',
      processes: [process({
        pid: 89460, parent_pid: 41280, executable: 'claude.exe', identity_id: 'i-1',
        cpu_pct: 2.5, memory_bytes: 598736896,
        command: 'claude.exe --session-id d9269550-6c7f-47e3-9bc3-a2665c3388db --mcp-config C:\\Users\\Jatora\\.mux\\claude-mcp.json --settings C:\\Users\\Jatora\\.mux\\sessions\\d9269550-6c7f-47e3-9bc3-a2665c3388db\\claude-hooks.json',
        connections: [{ local_host: '127.0.0.1', local_port: 51000, remote_host: '160.79.104.10', remote_port: 443 }],
      })],
    },
    {
      session_id: 'codex-a13f', project_id: 'p2',
      processes: [
        process({ pid: 700, parent_pid: 41280, executable: 'cmd.exe', identity_id: 'i-2', command: 'cmd.exe /c codex', cpu_pct: 0, memory_bytes: 4194304 }),
        process({ pid: 701, parent_pid: 700, executable: 'node.exe', identity_id: 'i-3', cpu_pct: 11.25, memory_bytes: 231735296, command: '"C:\\Program Files\\nodejs\\node.exe" C:\\tools\\codex\\bin\\codex.js --sandbox workspace-write' }),
        process({
          pid: 702, parent_pid: 701, executable: 'node.exe', identity_id: 'i-4', cpu_pct: 4, memory_bytes: 88080384,
          command: 'node.exe node_modules/vite/bin/vite.js --port 5173',
          listeners: [{ host: '127.0.0.1', port: 5173, loopback: true, url: 'http://127.0.0.1:5173/' }],
        }),
        process({
          pid: 703, parent_pid: 701, executable: 'esbuild.exe', identity_id: 'i-5', cpu_pct: 0.5, memory_bytes: 20971520,
          command: 'esbuild.exe --service=0.21.5 --ping', evidence_state: 'suspected_orphan', confidence: 'low',
          evidence_reason: 'parent_exited_without_reap', conditions: ['no owning job object'],
        }),
      ],
    },
  ],
  daemon: {
    pid: 78316, processes: 2, cpu_pct: 1.2, memory_bytes: 314572800, listeners: 1, connections: 3,
    members: [
      process({ pid: 78316, executable: 'swe-mux.exe', identity_id: 'd-1', cpu_pct: 1, memory_bytes: 262144000, command: 'D:\\PROJECTS\\swe-mux\\dist\\swe-mux\\swe-mux.exe', listeners: [{ host: '127.0.0.1', port: 8765, loopback: true, url: 'http://127.0.0.1:8765/' }] }),
      process({ pid: 41280, parent_pid: 78316, executable: 'swe-mux-supervisor.exe', identity_id: 'd-2', cpu_pct: 0.2, memory_bytes: 52428800, command: 'swe-mux-supervisor.exe --config C:\\Users\\Jatora\\.mux\\config.toml' }),
    ],
  },
  ownership_diagnostics: [],
  totals: { processes: 5, cpu_pct: 18.25, memory_bytes: 943718400, listeners: 1, connections: 1 },
}

window.fetch = (async (input: RequestInfo | URL) => {
  const path = String(typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url)
  const body = path.startsWith('/api/previews') ? { items: [] } : SNAPSHOT
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

const PROJECTS = [
  { id: 'p1', name: 'swe-mux', position: 0 },
  { id: 'p2', name: 'vaultspaces', position: 1 },
] as unknown as Project[]

// One titled and still auto-named, one renamed by hand: the inspector names both the way the
// sidebar does, so the two rows also cover the rule's only branch.
const SESSIONS = [
  { id: 'claude-d92695', name: 'claude-d92695', generated_title: 'Fix the parser', project_id: 'p1', state: 'working', pid: 89460 },
  { id: 'codex-a13f', name: 'release prep', generated_title: 'Land the migration', auto_named: false, project_id: 'p2', state: 'idle', pid: 701 },
] as unknown as Session[]

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')
render(
  <ResourcesModal sessions={SESSIONS} projects={PROJECTS} onClose={() => {}} onAttached={() => {}} onConfigureUsage={() => {}} />,
  document.querySelector('#root')!,
)
