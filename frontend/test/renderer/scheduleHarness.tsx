// The real Schedule tab over a stubbed daemon, inside a column the width of the drawer.
//
// The unit tests prove the strings; this proves they fit. The drawer column is the
// narrowest surface in the app, and this tab puts a label, a cron expression, a
// countdown, a verdict and four buttons in it - the exact shape that reads fine at
// 1200 px and overflows at 300.
import { render } from 'preact'
import { ScheduleTab } from '../../src/ScheduleTab'
import type { Schedule } from '../../src/schedules'
import type { LaunchProfile, Project } from '../../src/types'
import '../../src/style.css'

const NOW = Math.floor(Date.now() / 1000)

const schedule = (overrides: Partial<Schedule>): Schedule => ({
  id: 'sch_1',
  project_id: 'p1',
  project_name: 'swe-mux',
  label: 'Nightly health check',
  enabled: true,
  trigger_kind: 'cron',
  cron: '0 3 * * *',
  interval_seconds: null,
  run_at: null,
  timezone: '',
  catch_up: false,
  overlap: 'skip',
  backend: 'claude',
  profile_id: '',
  cwd: '',
  session_name: '',
  prompt: 'Check every deployed surface for errors, then report PASS or the findings.',
  follow_ups: [],
  daily_run_cap: 0,
  next_fire_at: NOW + 7_200,
  last_fire_at: NOW - 79_200,
  last_session_id: 'claude-d92695',
  last_outcome: 'spawned',
  last_reason: '',
  disabled_reason: '',
  blocked: '',
  runs: [],
  revision: 1,
  created_at: NOW - 800_000,
  updated_at: NOW - 79_200,
  ...overrides,
})

const SCHEDULES: Schedule[] = [
  schedule({
    id: 'sch_1',
    runs: [
      {
        id: 'run_1', schedule_id: 'sch_1', project_id: 'p1', fire_key: '1', due_at: NOW - 79_200,
        started_at: NOW - 79_200, finished_at: NOW - 79_100, outcome: 'spawned', reason: '',
        session_id: 'claude-d92695', origin: 'timer', queued_messages: 1,
      },
      {
        id: 'run_2', schedule_id: 'sch_1', project_id: 'p1', fire_key: '2', due_at: NOW - 165_600,
        started_at: NOW - 165_600, finished_at: NOW - 165_590, outcome: 'skipped',
        reason: "the previous run's session is still live", session_id: null,
        origin: 'timer', queued_messages: 0,
      },
    ],
  }),
  // The long-everything row: a label and a cron expression that have no business fitting.
  schedule({
    id: 'sch_2',
    label: 'Weekly dependency and vulnerability sweep across every workspace package',
    cron: '30 4 1,15 * mon-fri',
    timezone: 'America/New_York',
    project_name: 'vaultspaces',
    project_id: 'p2',
    blocked: 'automation_disabled',
    last_outcome: 'failed',
    last_reason: 'ValueError: unknown launch profile claude-opus-nightly',
    next_fire_at: NOW + 400_000,
  }),
  schedule({
    id: 'sch_3',
    label: 'Hourly smoke test',
    trigger_kind: 'interval',
    cron: '',
    interval_seconds: 3_600,
    enabled: false,
    next_fire_at: null,
    last_outcome: 'missed',
    last_session_id: null,
  }),
]

window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const path = String(typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url)
  const body = path.startsWith('/api/schedules/preview')
    ? { next_fires: [NOW + 7_200, NOW + 93_600, NOW + 180_000], now: NOW }
    : { schedules: SCHEDULES, status: { enabled: true, total: 3, armed: 2, live_sessions: 1, max_concurrent: 3 } }
  void init
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

const PROJECTS = [
  { id: 'p1', name: 'swe-mux', position: 0 },
  { id: 'p2', name: 'vaultspaces', position: 1 },
] as unknown as Project[]

const PROFILES = [
  { id: 'claude-plan', label: 'Claude (plan)', backend: 'claude', args: ['--permission-mode', 'plan'] },
] as unknown as LaunchProfile[]

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')
// The drawer column, not the viewport: the tab never gets more room than this on desktop.
const column = document.createElement('div')
column.id = 'drawer-column'
column.className = 'drawer-body drawer-body-schedule'
column.style.cssText = 'width:380px;height:100vh;display:flex;flex-direction:column;overflow:hidden'
document.querySelector('#root')!.append(column)

render(
  <ScheduleTab
    project={PROJECTS[0]}
    projects={PROJECTS}
    profiles={PROFILES}
    scope=""
    onScope={() => {}}
    onOpenSession={() => {}}
    onOpenProjectSettings={() => {}}
    onDone={() => {}}
  />,
  column,
)
