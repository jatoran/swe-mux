// Real sidebar rows inside the real stack-cluster markup, so the geometry the
// spec measures is the geometry the app draws — including the tab thread, whose
// alignment with the state indicator is pure CSS and therefore invisible to
// every unit test.
import { render } from 'preact'
import { SessionRowBody } from '../../src/SessionRowBody'
import { StateIndicator } from '../../src/StateIndicator'
import { defaultSessionRowConfig, type DotShape } from '../../src/sessionRowConfig'
import { buildSessionRowTokens, deriveRowContext, sessionContextArc } from '../../src/sessionRowFields'
import type { Session } from '../../src/types'
import '../../src/style.css'

const NOW = 1_770_000_000

const sample = (overrides: Partial<Session>): Session => ({
  id: 'x', name: 'x', project_id: 'p1', backend: 'claude',
  state: 'idle', state_since: NOW - 30, created_at: NOW - 7200,
  context_pct: 0, context_peak_pct: 0, compaction_count: 0, cost_usd: 0,
  git: { branch: 'master', dirty: 0, ahead: 0, behind: 0 },
  ...overrides,
} as unknown as Session)

const SESSIONS: Session[] = [
  sample({
    id: 'working', name: 'refactor tokenizer', backend: 'codex', model: 'gpt-5-codex',
    state: 'working', state_detail: 'apply_patch', state_since: NOW - 1320,
    context_pct: 0.74, context_peak_pct: 0.8,
    git: { branch: 'feat-tokenizer', dirty: 7, ahead: 2, behind: 0, added: 312, removed: 48 },
  }),
  sample({ id: 'ready', name: 'status detection v2', model: 'opus', last_turn_ms: 72_000, context_pct: 0.41 }),
  sample({
    id: 'standing', name: 'background work', model: 'opus', last_turn_ms: 9_000, context_pct: 0.2,
    standing_activity: [{ kind: 'subagents', source: 'hook', evidence: 'hook:SubagentStart', since: NOW, expires_at: null, count: 2, detail: null }],
  }),
]

const config = { ...defaultSessionRowConfig(), dotShape: (new URLSearchParams(location.search).get('shape') || 'hexagon') as DotShape }
const context = deriveRowContext(SESSIONS, { working: 2 }, NOW)

// `.layout-cluster.stack` + `.layout-branch` is what draws the tab thread; the
// first/last modifiers drop its outer segments exactly as App.tsx does.
render(
  <div class="sidebar">
    <div class="project-group active">
      <div class="session-list">
        <div class="layout-cluster stack">
          {SESSIONS.map((session, index) => (
            <div
              key={session.id}
              class={`layout-branch ${index === 0 ? 'first' : ''} ${index === SESSIONS.length - 1 ? 'last' : ''}`}
            >
              <div class="session-entry">
                <button data-row={session.id} class={`session-row agent ${session.state}`}>
                  <StateIndicator session={session} shape={config.dotShape} gauge={sessionContextArc(session, config)} />
                  <SessionRowBody session={session} tokens={buildSessionRowTokens(session, config, context)} config={config} />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  </div>,
  document.querySelector('#root')!,
)
