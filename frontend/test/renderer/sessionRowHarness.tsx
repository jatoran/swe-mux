// Real sidebar rows inside the real stack-cluster markup, so the geometry the
// spec measures is the geometry the app draws — including the tab thread, whose
// alignment with the state indicator is pure CSS and therefore invisible to
// every unit test.
//
// `?layout=worktree-model` swaps the bottom line for one worktree on the left and
// one model on the right: the configuration whose narrowing behaviour is pure CSS
// and, before the width ladder, clipped the model off the row's right edge while
// the worktree kept a fixed width and never truncated at all.
//
// `?layout=crowded` fills the left section, which is the only way to reach the
// ladder's icon rung: two tokens fit at any draggable width once each section's
// yielding token may ellipsize, so a mark only has to stand in for a value when
// several fields are competing.
import { render } from 'preact'
import { SessionRowBody } from '../../src/SessionRowBody'
import { StateIndicator } from '../../src/StateIndicator'
import { defaultSessionRowConfig, type DotShape, type SessionRowConfig } from '../../src/sessionRowConfig'
import {
  buildSessionRowTokens, deriveRowContext, rowBudget, sessionContextArc, sessionStandingMark,
} from '../../src/sessionRowFields'
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
    git: {
      branch: 'feat-tokenizer', worktree: 'feat-tokenizer-rewrite',
      dirty: 7, ahead: 2, behind: 0, added: 312, removed: 48,
    },
  }),
  sample({ id: 'ready', name: 'status detection v2', model: 'opus', last_turn_ms: 72_000, context_pct: 0.41 }),
  // Long enough to fill any sidebar this harness is measured at, and carrying
  // every flag: the strip has to survive exactly the row that cannot show its
  // own title, which is the row the marks used to disappear from.
  sample({
    id: 'standing',
    name: 'background work on the deterministic consumer pipeline and its detectors',
    model: 'opus', last_turn_ms: 9_000, context_pct: 0.2, broadcast: true,
    unsent_input: { since: NOW - 300 }, voice_mode: 'auto',
    standing_activity: [{ kind: 'subagents', source: 'hook', evidence: 'hook:SubagentStart', since: NOW, expires_at: null, count: 2, detail: null }],
  }),
]

const params = new URLSearchParams(location.search)
const base: SessionRowConfig = {
  ...defaultSessionRowConfig(),
  dotShape: (params.get('shape') || 'hexagon') as DotShape,
}
// Both width-ladder layouts turn `gitGlyphs` off, unlike the shipped default the
// harness otherwise renders. The decoration is two more characters on every git
// token, and these two layouts exist so a spec can assert exactly which rung a
// named value is on at a named sidebar width — arithmetic the mark would silently
// shift. Whether the glyph is drawn is asserted in the unit suite, where it costs
// nothing to state directly.
const LAYOUTS: Record<string, Partial<SessionRowConfig>> = {
  'worktree-model': {
    gitGlyphs: false,
    bottom: {
      ...base.bottom,
      left: [{ id: 'worktree', mode: 'always' }],
      right: [{ id: 'model', mode: 'always' }],
    },
  },
  crowded: {
    gitGlyphs: false,
    bottom: {
      ...base.bottom,
      left: [
        { id: 'worktree', mode: 'always' }, { id: 'branch', mode: 'always' },
        { id: 'diff', mode: 'always' }, { id: 'detail', mode: 'always' },
      ],
      right: [{ id: 'model', mode: 'always' }, { id: 'duration', mode: 'always' }],
    },
  },
}
const config: SessionRowConfig = { ...base, ...LAYOUTS[params.get('layout') || ''] }

// The budget the app measures off its own probe. The spec drives the sidebar's
// width directly, so the harness re-measures on every resize the same way — a
// fixed budget would let a spec assert on a ladder that never moved.
// Read aloud on with the global default off, so the `voice` mark is drawn for the one
// session that opted in rather than for all three. Its icon is an SVG sized in CSS, which
// is precisely the kind of mark that renders at zero width or swallows the row when the
// sizing is wrong, and neither failure is visible to tsc or the unit suite.
const fleet = deriveRowContext(
  SESSIONS, { working: 2 }, NOW, undefined, undefined, { enabled: true, default_mode: 'off' })
const readBudget = () => {
  const copy = document.querySelector<HTMLElement>('[data-metric="copy"]')
  const top = document.querySelector<HTMLElement>('[data-metric="top"]')
  const bottom = document.querySelector<HTMLElement>('[data-metric="bottom"]')
  if (!copy || !top || !bottom) return fleet.budget
  return rowBudget(copy.clientWidth, {
    top: top.getBoundingClientRect().width / 10,
    bottom: bottom.getBoundingClientRect().width / 10,
  })
}

let renders = 0

// `.layout-cluster.stack` + `.layout-branch` is what draws the tab thread; the
// first/last modifiers drop its outer segments exactly as App.tsx does.
const draw = () => {
  const context = { ...fleet, budget: readBudget() }
  // Published so a spec can wait for the ladder to have been recomputed rather
  // than for a guessed number of frames: the budget is measured in a
  // `ResizeObserver`, so a width set in one frame reaches the tokens in the next.
  document.documentElement.dataset.rowRender = String(++renders)
  document.documentElement.dataset.rowBudget = String(context.budget.bottom)
  render(
    <div class="sidebar">
      <div class="project-group active">
        <div class="session-list">
          {/* The measurement probe, exactly as App.tsx renders it. */}
          <div class="row-metric-probe" aria-hidden="true">
            <div class="row-metric">
              <span />
              <span class="session-copy" data-metric="copy">
                <span class="row-line top"><span data-metric="top">0000000000</span></span>
                <span class="row-line bottom"><span data-metric="bottom">0000000000</span></span>
              </span>
            </div>
          </div>
          <div class="layout-cluster stack">
            {SESSIONS.map((session, index) => (
              <div
                key={session.id}
                class={`layout-branch ${index === 0 ? 'first' : ''} ${index === SESSIONS.length - 1 ? 'last' : ''}`}
              >
                <div class="session-entry">
                  <button data-row={session.id} class={`session-row agent ${session.state}`}>
                    <StateIndicator
                      session={session}
                      shape={config.dotShape}
                      gauge={sessionContextArc(session, config)}
                      standing={sessionStandingMark(session, config)}
                    />
                    <SessionRowBody session={session} tokens={buildSessionRowTokens(session, config, context)} config={config} />
                    {/* The hover-revealed kill control, which is absolutely
                        positioned over the row's right edge — the lane the flag
                        strip has to be moved out of while it is showing. */}
                    <span class="row-actions"><button>×</button></span>
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
}

draw()
// Two passes on purpose: the first mounts the probe, the second draws with the
// budget the mounted probe reports. Thereafter the observer keeps them in step,
// which is what lets a spec set `.sidebar { width }` and measure the result.
draw()
new ResizeObserver(draw).observe(document.querySelector('[data-metric="copy"]')!)
