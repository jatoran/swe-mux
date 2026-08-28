import assert from 'node:assert/strict'
import test from 'node:test'
import type { Session } from '../src/types.ts'
import {
  DEFAULT_DOT_SIZE_DESKTOP, DEFAULT_DOT_SIZE_MOBILE, DOT_SIZE_MAX, DOT_SIZE_MIN,
  ROW_FIELDS, defaultSessionRowConfig, normalizeDotSize, normalizeSessionRowConfig, placeField,
  presetConfig, removeField, setFieldMode, unplacedFields,
  type RowFieldId, type SessionRowConfig,
} from '../src/sessionRowConfig.ts'
import { sessionDotSize } from '../src/sessionRowPrefs.ts'
import {
  EMPTY_ROW_BUDGET, buildSessionRowTokens, deriveRowContext, emptyRowContext, formatRowDuration,
  identityRowTokens, rowBudget, sessionContextArc, sessionStandingMark,
} from '../src/sessionRowFields.ts'
import type { ApprovalPolicy, StandingActivity } from '../src/types.ts'
import { shapePath } from '../src/dotShapes.ts'

const NOW = 1_700_000_000

const session = (overrides: Partial<Session> = {}): Session => ({
  id: 's1', name: 's1', project_id: 'p1', backend: 'claude',
  state: 'idle', state_since: NOW - 10, created_at: NOW - 3600,
  context_pct: 0, context_peak_pct: 0, compaction_count: 0, cost_usd: 0,
  git: { branch: 'master', dirty: 0, ahead: 0, behind: 0 },
  ...overrides,
} as unknown as Session)

const context = (overrides: Partial<ReturnType<typeof emptyRowContext>> = {}) =>
  ({ ...emptyRowContext(NOW), ...overrides })

const STANDING: StandingActivity[] = [{
  kind: 'subagents', source: 'hook', evidence: 'hook:SubagentStart',
  since: NOW - 60, expires_at: null, count: 2, detail: null,
}]

const bottomText = (item: Session, config: SessionRowConfig, ctx = context()) => {
  const tokens = buildSessionRowTokens(item, config, ctx)
  return [...tokens.bottom.left.tokens, ...tokens.bottom.right.tokens].map(token => token.text)
}

// `gitGlyphs` is pinned off rather than inherited: every test built on this helper
// is about notability, degradation, or the width ladder, and the decoration would
// otherwise put two characters into token text and into character budgets that the
// test's own arithmetic does not account for. The setting's own behaviour, and the
// value the default ships, are asserted directly instead.
const withBottom = (config: SessionRowConfig, ids: RowFieldId[], mode: 'notable' | 'always' = 'always'): SessionRowConfig => ({
  ...config,
  gitGlyphs: false,
  bottom: { ...config.bottom, left: ids.map(id => ({ id, mode })), right: [] },
})

test('durations never exceed four characters and stay ordered', () => {
  const cases: Array<[number, string]> = [
    [0, '0s'], [9, '9s'], [59, '59s'], [60, '1m'], [72, '1m12'], [600, '10m'],
    [3599, '59m'], [3600, '1h'], [5400, '1h30'], [36_000, '10h'], [86_400, '1d'],
    [110_000, '1d6h'], [864_000 * 2, '20d'],
  ]
  for (const [seconds, expected] of cases) {
    assert.equal(formatRowDuration(seconds), expected, `${seconds}s`)
    assert.ok(formatRowDuration(seconds).length <= 4, `${seconds}s must fit the fixed slot`)
  }
})

test('a ready session reports its last turn, not how long it has been ready', () => {
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  const ready = session({ state: 'idle', state_since: NOW - 7200, last_turn_ms: 72_000 })
  assert.deepEqual(bottomText(ready, config), ['1m12'])
})

test('a ready session with running work reports the request, not the turn that dispatched it', () => {
  // The measured case (2026-08-19): three sessions 37-80 minutes into ultracode
  // runs, each reading ~10m. The harness ends its root turn to hand off to
  // background agents, so `turn_started_at` goes away and `last_turn_ms` freezes
  // at the length of the dispatching turn — a finished fragment standing in for
  // a request still in flight. Worse, it shrinks: every phase ends with a short
  // main-loop turn that overwrites the measurement.
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  const handedOff = session({
    state: 'idle', state_since: NOW - 1770, last_turn_ms: 416_000,
    turn_started_at: null, running_work_since: NOW - 2210,
    standing_activity: STANDING,
  })
  assert.deepEqual(bottomText(handedOff, config), ['36m'])
})

test('a ready session with running work and no anchor falls back to your prompt', () => {
  // Records written before the daemon latched the anchor, and sessions adopted
  // mid-flight. "Since you asked" is a slightly different question, which is why
  // it is second — but it is the right order of magnitude, and the alternative
  // is the finished fragment.
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  const adopted = session({
    state: 'idle', last_turn_ms: 416_000, turn_started_at: null,
    running_work_since: null, last_human_prompt_at: NOW - 2210,
    standing_activity: STANDING,
  })
  assert.deepEqual(bottomText(adopted, config), ['36m'])
})

test('running work with no anchor at all leaves the last turn in place', () => {
  // A stale-but-real number beats a fresh invented one, the same rule the rest
  // of this column runs on.
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  const anchorless = session({
    state: 'idle', last_turn_ms: 72_000, turn_started_at: null,
    running_work_since: null, last_human_prompt_at: null,
    standing_activity: STANDING,
  })
  assert.deepEqual(bottomText(anchorless, config), ['1m12'])
})

test('a merely scheduled engagement does not turn the last turn into a request clock', () => {
  // An armed loop is not work in flight: the turn genuinely ended and nothing is
  // running, so the ready session's static number is still the honest one. Same
  // split the blue ring and the notification suppression use.
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  const armed = session({
    state: 'idle', last_turn_ms: 72_000, turn_started_at: null,
    running_work_since: NOW - 3600, last_human_prompt_at: NOW - 3600,
    standing_activity: [{
      kind: 'loop', source: 'hook', evidence: 'hook:loop',
      since: NOW - 3600, expires_at: null, count: 1, detail: null,
    }] as StandingActivity[],
  })
  assert.deepEqual(bottomText(armed, config), ['1m12'])
})

test('a request in flight is notable on the working threshold, not the last-turn one', () => {
  const config = withBottom(defaultSessionRowConfig(), ['duration'], 'notable')
  const base = {
    state: 'idle' as const, turn_started_at: null, standing_activity: STANDING,
  }
  // 30s of live work is not yet worth marking; a finished 30s turn would be.
  assert.deepEqual(bottomText(session({ ...base, running_work_since: NOW - 30 }), config), [])
  assert.deepEqual(bottomText(session({ ...base, running_work_since: NOW - 90 }), config), ['1m30'])
})

test('since-your-prompt speaks on a handed-off session when it disagrees', () => {
  const config = withBottom(defaultSessionRowConfig(), ['duration', 'sincePrompt'], 'notable')
  // The work was dispatched long after you asked — an injected message, or a
  // first phase that ran before anything went to background.
  const drifted = session({
    state: 'idle', turn_started_at: null, standing_activity: STANDING,
    running_work_since: NOW - 300, last_human_prompt_at: NOW - 3600,
  })
  assert.deepEqual(bottomText(drifted, config), ['5m', '1h'])
  // Your prompt is what started the work, so the two numbers are one fact.
  const own = session({
    state: 'idle', turn_started_at: null, standing_activity: STANDING,
    running_work_since: NOW - 3600, last_human_prompt_at: NOW - 3603,
  })
  assert.deepEqual(bottomText(own, config), ['1h'])
})

test('a working session reports elapsed time in state', () => {
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  const working = session({ state: 'working', state_since: NOW - 1320 })
  assert.deepEqual(bottomText(working, config), ['22m'])
})

test('a working session is aged from its turn, so tool calls do not reset it', () => {
  // The defect this pins: every tool call and every auto-approved permission is
  // a state transition, so `state_since` restarted every few seconds and the
  // timer never once showed how long the work had been running.
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  const midTurn = session({ state: 'working', state_since: NOW - 3, turn_started_at: NOW - 1320 })
  assert.deepEqual(bottomText(midTurn, config), ['22m'])
})

test('an interrupt request freezes the visible turn duration', () => {
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  const pending = session({
    state: 'working',
    turn_started_at: NOW - 600,
    interrupt_pending_at: NOW - 300,
  })
  assert.deepEqual(bottomText(pending, config), ['5m'])
  assert.deepEqual(bottomText(pending, config, context({ now: NOW + 3600 })), ['5m'])
})

test('an awaiting session is aged from the turn, like every other live state', () => {
  // It used to report time in state here, so a permission prompt collapsed the
  // number to seconds and answering it sprang the number back to the turn
  // length. With several subagents raising prompts that reads as a timer
  // resetting at random, which is what this pins shut: one turn, one number.
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  const blocked = session({ state: 'awaiting', state_since: NOW - 300, turn_started_at: NOW - 3600 })
  assert.deepEqual(bottomText(blocked, config), ['1h'])
})

test('how long a block has stood moves into the detail, where it is labelled', () => {
  const config = withBottom(defaultSessionRowConfig(), ['detail'])
  const blocked = session({
    state: 'awaiting', awaiting_reason: 'approval', state_since: NOW - 300,
    turn_started_at: NOW - 3600,
  })
  assert.deepEqual(bottomText(blocked, config), ['awaiting approval 5m'])
})

test('a block too short to matter does not put a duration in the detail', () => {
  const config = withBottom(defaultSessionRowConfig(), ['detail'])
  const fresh = session({
    state: 'awaiting', awaiting_reason: 'approval', state_since: NOW - 3,
    turn_started_at: NOW - 3600,
  })
  assert.deepEqual(bottomText(fresh, config), ['awaiting approval'])
})

test('a working session with no open turn falls back to time in state', () => {
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  const working = session({ state: 'working', state_since: NOW - 600, turn_started_at: null })
  assert.deepEqual(bottomText(working, config), ['10m'])
})

test('since-your-prompt answers the question the turn cannot', () => {
  // The measured case: a session thirteen minutes into work its operator asked
  // for once, whose turn had been reopened by an injected teammate message and
  // so read "3m22". Both numbers are true; only together are they an answer.
  const config = withBottom(defaultSessionRowConfig(), ['duration', 'sincePrompt'])
  const fed = session({
    state: 'working', turn_started_at: NOW - 202, last_human_prompt_at: NOW - 824,
  })
  assert.deepEqual(bottomText(fed, config), ['3m22', '13m'])
})

test('since-your-prompt stays quiet when it would repeat the turn', () => {
  const config = withBottom(defaultSessionRowConfig(), ['sincePrompt'], 'notable')
  // Your own prompt opened this turn, so the two numbers are one fact.
  const own = session({
    state: 'working', turn_started_at: NOW - 600, last_human_prompt_at: NOW - 603,
  })
  assert.deepEqual(bottomText(own, config), [])
  // Nothing is running, so "you asked an hour ago" describes no outstanding work.
  const done = session({ state: 'idle', turn_started_at: null, last_human_prompt_at: NOW - 3600 })
  assert.deepEqual(bottomText(done, config), [])
})

test('a session no human has prompted on this run shows nothing', () => {
  const config = withBottom(defaultSessionRowConfig(), ['sincePrompt'])
  assert.deepEqual(bottomText(session({ state: 'working', last_human_prompt_at: null }), config), [])
})

test('a ready session with no completed turn shows no duration at all', () => {
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  assert.deepEqual(bottomText(session({ state: 'idle', last_turn_ms: null }), config), [])
})

test('a sub-second last turn is no measurement rather than a turn that took 0s', () => {
  // Daemons before the record-dated turn fix wrote the replay's own elapsed time
  // into `last_turn_ms`, and those records survive a restart. 0 ms already drew
  // nothing; 2 ms drew the literal `0s` — one defect rendering two ways.
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  assert.deepEqual(bottomText(session({ state: 'idle', last_turn_ms: 0 }), config), [])
  assert.deepEqual(bottomText(session({ state: 'idle', last_turn_ms: 2 }), config), [])
  assert.deepEqual(bottomText(session({ state: 'idle', last_turn_ms: 249 }), config), [])
  assert.deepEqual(bottomText(session({ state: 'idle', last_turn_ms: 1_500 }), config), ['1s'])
})

test('a turn that just began still reads 0s', () => {
  // The tolerance for clock disagreement must not swallow the honest zero: a
  // turn one tick old really has run for no whole seconds yet.
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  assert.deepEqual(bottomText(session({ state: 'working', turn_started_at: NOW }), config), ['0s'])
  assert.deepEqual(bottomText(session({ state: 'working', turn_started_at: NOW + 2 }), config), ['0s'])
})

test('a turn dated beyond the clock tolerance shows nothing, not a frozen 0s', () => {
  // A client whose clock trails the daemon's used to clamp every negative age to
  // zero, so a session that had been working ten minutes sat at `0s` for the
  // whole turn with nothing else on screen looking wrong. Saying nothing is the
  // honest render; `serverClock.ts` is what keeps it rare.
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  assert.deepEqual(bottomText(session({ state: 'working', turn_started_at: NOW + 600 }), config), [])
  assert.deepEqual(
    bottomText(session({ state: 'awaiting', state_since: NOW + 600, turn_started_at: null }), config),
    [],
  )
})

test('notable mode hides a short turn and a short working stretch', () => {
  const config = withBottom(defaultSessionRowConfig(), ['duration'], 'notable')
  assert.deepEqual(bottomText(session({ state: 'idle', last_turn_ms: 2_000 }), config), [])
  assert.deepEqual(bottomText(session({ state: 'working', state_since: NOW - 5 }), config), [])
  assert.deepEqual(bottomText(session({ state: 'working', state_since: NOW - 300 }), config), ['5m'])
})

test('the state word is never notable — the indicator already carries it', () => {
  const base = defaultSessionRowConfig()
  assert.deepEqual(bottomText(session({ state: 'working' }), withBottom(base, ['state'], 'notable')), [])
  assert.deepEqual(bottomText(session({ state: 'working' }), withBottom(base, ['state'], 'always')), ['working'])
})

test('"ready · turn complete" never renders; a background wait does', () => {
  const config = withBottom(defaultSessionRowConfig(), ['detail'], 'notable')
  assert.deepEqual(bottomText(session({ state: 'idle' }), config), [])
  assert.deepEqual(
    bottomText(session({ state: 'idle', idle_reason: 'waiting_on_background' }), config),
    ['background work running'],
  )
})

test('branch is notable only when it differs from the project default', () => {
  const config = withBottom(defaultSessionRowConfig(), ['branch'], 'notable')
  const fleet = [
    session({ id: 'a', git: { branch: 'master', dirty: 0, ahead: 0, behind: 0 } }),
    session({ id: 'b', git: { branch: 'master', dirty: 0, ahead: 0, behind: 0 } }),
    session({ id: 'c', git: { branch: 'feat-x', dirty: 0, ahead: 0, behind: 0 } }),
  ]
  const derived = deriveRowContext(fleet, {}, NOW)
  assert.deepEqual(bottomText(fleet[0], config, derived), [])
  assert.deepEqual(bottomText(fleet[2], config, derived), ['feat-x'])
})

test('a worktree suppresses a branch token that would repeat its name', () => {
  const config = withBottom(defaultSessionRowConfig(), ['worktree', 'branch'])
  const inWorktree = session({ git: { branch: 'wt-audit', worktree: 'wt-audit', dirty: 0, ahead: 0, behind: 0 } })
  assert.deepEqual(bottomText(inWorktree, config), ['wt-audit'])
  const divergent = session({ git: { branch: 'feat-y', worktree: 'wt-audit', dirty: 0, ahead: 0, behind: 0 } })
  assert.deepEqual(bottomText(divergent, config), ['wt-audit', 'feat-y'])
})

test('an unmeasured diffstat renders nothing rather than a false clean tree', () => {
  const config = withBottom(defaultSessionRowConfig(), ['diff'])
  assert.deepEqual(bottomText(session(), config), [])
  const measured = session({ git: { branch: 'master', dirty: 0, ahead: 0, behind: 0, added: 0, removed: 0 } })
  assert.deepEqual(bottomText(measured, config), ['+0 -0'])
})

test('the branch-scoped diff is a separate fact from the uncommitted one', () => {
  // The case the field exists for: everything is committed, so the working-tree
  // diff is honestly zero while the branch has changed a great deal. A row with
  // only `diff` on it reports a worktree session as having done nothing.
  const config = withBottom(defaultSessionRowConfig(), ['diff', 'compareDiff', 'compareFiles'])
  const committed = session({
    git: {
      branch: 'wt-audit', dirty: 0, ahead: 3, behind: 0, added: 0, removed: 0,
      root: 'D:/repo-wt/wt-audit', compare_ref: 'origin/main',
      compare_added: 486, compare_removed: 91, compare_files: 12,
    },
  })
  assert.deepEqual(bottomText(committed, config), ['+0 -0', '+486 -91', '12'])
  const tokens = buildSessionRowTokens(committed, config, context()).bottom.left.tokens
  assert.equal(tokens[0].prefix, undefined, 'the working-tree diff carries no scope mark')
  assert.equal(tokens[1].prefix, '⎇', 'the branch diff must be distinguishable from it')
  assert.match(tokens[1].title ?? '', /origin\/main/, 'the tooltip names the actual base')
})

test('an unmeasured comparison renders nothing rather than an identical branch', () => {
  const config = withBottom(defaultSessionRowConfig(), ['compareDiff', 'compareFiles'])
  // No base resolved: the fields are absent, not zero.
  assert.deepEqual(bottomText(session(), config), [])
  const measured = session({
    git: {
      branch: 'master', dirty: 0, ahead: 0, behind: 0,
      compare_ref: 'origin/main', compare_added: 0, compare_removed: 0, compare_files: 0,
    },
  })
  assert.deepEqual(bottomText(measured, config), ['+0 -0', '0'])
})

test('git quantities are marked shared when more than one live session reports them', () => {
  const config = withBottom(defaultSessionRowConfig(), ['diff', 'dirty', 'compareDiff', 'branch'])
  const git = {
    branch: 'master', dirty: 4, ahead: 0, behind: 0, added: 9, removed: 2,
    root: 'D:/repo', compare_ref: 'origin/main',
    compare_added: 9, compare_removed: 2, compare_files: 3,
  }
  const shared = deriveRowContext(
    [session({ id: 'a', git }), session({ id: 'b', git }), session({ id: 'c', git })], {}, NOW,
  )
  const marked = buildSessionRowTokens(session({ id: 'a', git }), config, shared)
  assert.deepEqual(
    marked.bottom.left.tokens.map(token => [token.id, Boolean(token.shared)]),
    [['diff', true], ['dirty', true], ['compareDiff', true], ['branch', false]],
    'the quantities are marked; the branch name is not a quantity anyone misreads',
  )
  assert.match(marked.bottom.left.tokens[0].title ?? '', /3 live sessions/)

  // One live session in the checkout is unambiguous, however many have exited.
  const alone = deriveRowContext(
    [session({ id: 'a', git }), session({ id: 'b', state: 'exited', git })], {}, NOW,
  )
  const plain = buildSessionRowTokens(session({ id: 'a', git }), config, alone)
  assert.ok(plain.bottom.left.tokens.every(token => !token.shared))
})

test('sessions in different checkouts are never marked as sharing one', () => {
  const config = withBottom(defaultSessionRowConfig(), ['diff'])
  const base = { branch: 'master', dirty: 1, ahead: 0, behind: 0, added: 5, removed: 1 }
  const ctx = deriveRowContext(
    [
      session({ id: 'a', git: { ...base, root: 'D:/repo' } }),
      session({ id: 'b', git: { ...base, root: 'D:/repo-wt/wt-audit' } }),
    ], {}, NOW,
  )
  const tokens = buildSessionRowTokens(session({ id: 'a', git: { ...base, root: 'D:/repo' } }), config, ctx)
  assert.equal(tokens.bottom.left.tokens[0].shared, false)
})

test('git glyphs decorate branch names by default, and can be turned off', () => {
  // The glyph tells a branch from a worktree from a model in one bottom line of
  // near-identical short tokens, so it ships on; a reader who reads the line by
  // position rather than by mark can have the width back.
  const base = defaultSessionRowConfig()
  const item = session({ git: { branch: 'feat-x', worktree: 'wt-x', dirty: 0, ahead: 0, behind: 0 } })
  assert.equal(base.gitGlyphs, true)
  const decorated = { ...base, bottom: { ...base.bottom, left: [{ id: 'worktree' as const, mode: 'always' as const }, { id: 'branch' as const, mode: 'always' as const }], right: [] } }
  assert.deepEqual(bottomText(item, decorated), ['⌂ wt-x', '⎇ feat-x'])
  assert.deepEqual(bottomText(item, { ...decorated, gitGlyphs: false }), ['wt-x', 'feat-x'])
})

test('context draws in exactly one place', () => {
  const base = withBottom(defaultSessionRowConfig(), ['context'])
  const item = session({ context_pct: 0.74, context_peak_pct: 0.8 })
  assert.deepEqual(bottomText(item, { ...base, context: 'arc' }), [], 'the arc owns it')
  assert.equal(sessionContextArc(item, { ...base, context: 'arc' })?.pct, 0.74)
  assert.deepEqual(bottomText(item, { ...base, context: 'percent' }), ['74%'])
  assert.equal(sessionContextArc(item, { ...base, context: 'percent' }), null)
  assert.deepEqual(bottomText(item, { ...base, context: 'off' }), [])
})

test('the queue depth comes from the fleet context, not the session', () => {
  const config = withBottom(defaultSessionRowConfig(), ['queue'], 'notable')
  assert.deepEqual(bottomText(session(), config), [])
  assert.deepEqual(bottomText(session(), config, context({ queueDepth: { s1: 3 } })), ['⋮3'])
})

test('the account token is notable only when more than one account is live', () => {
  const config = withBottom(defaultSessionRowConfig(), ['account'], 'notable')
  const one = [session({ id: 'a', provider_account_hashes: { anthropic: 'aaaaaaaa11' } })]
  assert.deepEqual(bottomText(one[0], config, deriveRowContext(one, {}, NOW)), [])
  const two = [...one, session({ id: 'b', provider_account_hashes: { anthropic: 'bbbbbbbb22' } })]
  assert.deepEqual(bottomText(two[0], config, deriveRowContext(two, {}, NOW)), ['aaaaaa'])
})

test('model labels compact only at render time', () => {
  const config = withBottom(defaultSessionRowConfig(), ['model'], 'notable')
  const item = session({ model: 'claude-opus-5' })
  assert.deepEqual(
    bottomText(item, config, context({ defaultModel: { p1: 'claude-opus-5' } })),
    [],
    'notability still compares the exact model id',
  )
  const tokens = buildSessionRowTokens(
    item,
    config,
    context({ defaultModel: { p1: 'claude-sonnet-5' } }),
  ).bottom.left.tokens
  assert.equal(tokens[0].text, 'opus-5')
  assert.equal(tokens[0].title, 'model claude-opus-5')
})

test('an ended session reports its lifetime and its exit reason', () => {
  const config = withBottom(defaultSessionRowConfig(), ['exit', 'duration'], 'notable')
  const ended = session({ state: 'crashed', state_detail: 'exit 1', created_at: NOW - 7200, state_since: NOW - 60 })
  assert.deepEqual(bottomText(ended, config), ['crashed · exit 1', '1h59'])
})

test('a session the daemon never dated shows no age rather than 1970', () => {
  const config = withBottom(defaultSessionRowConfig(), ['duration', 'idleFor'])
  assert.deepEqual(bottomText(session({ state: 'working', state_since: 0 }), config), [])
  assert.deepEqual(bottomText(session({ state: 'idle', state_since: 0, last_turn_ms: null }), config), [])
})

test('separators are carried, not baked, so hidden fields leave no dangling marks', () => {
  const config = withBottom(defaultSessionRowConfig(), ['branch', 'diff', 'queue'], 'notable')
  const quiet = session()
  const tokens = buildSessionRowTokens(quiet, config, deriveRowContext([quiet], {}, NOW))
  assert.equal(tokens.bottom.left.tokens.length, 0, 'a quiet session draws none of these')
  assert.equal(tokens.bottom.left.separator, ' · ', 'the separator survives an empty section')
})

test('with no fleet baseline every branch reads as divergent', () => {
  // An empty context is the boot state, before any snapshot has been derived.
  // Erring toward showing the branch is the safe direction: a branch shown
  // needlessly costs width, a branch hidden wrongly hides which tree you are in.
  const config = withBottom(defaultSessionRowConfig(), ['branch'], 'notable')
  assert.deepEqual(bottomText(session(), config, context()), ['master'])
})

test('a line that fits is left alone', () => {
  const config = withBottom(defaultSessionRowConfig(), ['branch', 'diff', 'queue', 'detail'])
  const item = session({
    state: 'working', state_detail: 'apply_patch',
    git: { branch: 'feat-x', dirty: 2, ahead: 0, behind: 0, added: 9, removed: 1 },
  })
  const roomy = context({ queueDepth: { s1: 2 }, budget: { top: 60, bottom: 80 } })
  assert.deepEqual(bottomText(item, config, roomy), ['feat-x', '+9 -1', '⋮2', 'apply_patch'])
})

test('a value long enough to overflow is left for CSS to ellipsize, not degraded', () => {
  // The reported case: one worktree on the left, one model on the right. At the
  // narrowest the sidebar can be dragged the bottom line holds 25 characters and
  // the two values want 31 — but each section's yielding token is the one CSS
  // ellipsizes, so six characters of the worktree plus six of the model plus the
  // gap is 14. Nothing is collapsed and nothing is dropped: the browser truncates
  // the worktree continuously, and the model the reader pinned to the row's edge
  // is untouched. Under the shedding this replaced the model was clipped off that
  // edge instead, mid-glyph and without an ellipsis.
  const config: SessionRowConfig = {
    ...defaultSessionRowConfig(),
    // Off, so the character arithmetic the comment above spells out is the
    // arithmetic the engine does; the glyph is two more characters per git token.
    gitGlyphs: false,
    bottom: {
      ...defaultSessionRowConfig().bottom,
      left: [{ id: 'worktree', mode: 'always' }],
      right: [{ id: 'model', mode: 'always' }],
    },
  }
  const item = session({
    model: 'gpt-5-codex',
    git: { branch: 'master', worktree: 'feat-tokenizer-rewrite', dirty: 0, ahead: 0, behind: 0 },
  })
  const tokens = buildSessionRowTokens(item, config, context({ budget: { top: 40, bottom: 25 } }))
  assert.deepEqual(tokens.bottom.left.tokens.map(token => token.display), ['full'])
  assert.equal(tokens.bottom.left.tokens[0].text, 'feat-tokenizer-rewrite')
  assert.deepEqual(tokens.bottom.right.tokens.map(token => token.text), ['5-codex'])

  // Below the floor the mark takes over, and the model still survives it.
  const crushed = buildSessionRowTokens(item, config, context({ budget: { top: 40, bottom: 12 } }))
  assert.equal(crushed.bottom.left.tokens[0].display, 'icon')
  assert.equal(crushed.bottom.left.tokens[0].glyph, '⌂')
  assert.deepEqual(crushed.bottom.right.tokens.map(token => token.text), ['5-codex'])
})

test('a narrow line collapses marks before it drops anything, lowest priority first', () => {
  // The ladder's whole point: an icon costs two characters against a value's ten,
  // so a field with a mark of its own keeps saying that it exists rather than
  // being deleted to make room for a field the reader ranked lower.
  const config = withBottom(defaultSessionRowConfig(), ['worktree', 'branch', 'diff', 'detail'])
  const item = session({
    state: 'working', state_detail: 'apply_patch',
    git: { branch: 'feat-tokenizer', worktree: 'feat-tokenizer-rewrite', dirty: 2, ahead: 0, behind: 0, added: 9, removed: 1 },
  })
  const tokens = (bottom: number) =>
    buildSessionRowTokens(item, config, context({ budget: { top: 60, bottom } })).bottom.left.tokens

  // The line's natural cost is 61 characters: 22 + 14 + 5 + 11 of value plus three
  // ` · ` separators. Branch (60) is the lowest-priority token that owns a mark, so
  // at a budget that one collapse satisfies it is the only one that loses its
  // value — and it is still on the row.
  const squeezed = tokens(50)
  assert.deepEqual(squeezed.map(token => token.id), ['worktree', 'branch', 'diff', 'detail'])
  assert.equal(squeezed.find(token => token.id === 'branch')?.display, 'icon')
  assert.equal(squeezed.find(token => token.id === 'worktree')?.display, 'full')

  // Tighter still: the worktree collapses too, and only then does the diff — which
  // has no mark of its own — get dropped.
  const tight = tokens(20)
  assert.equal(tight.find(token => token.id === 'worktree')?.display, 'icon')
  assert.ok(!tight.some(token => token.id === 'diff'), 'a markless low-priority field is dropped')
})

test('a field with no honest mark is dropped rather than iconised', () => {
  // `model` deliberately carries no glyph: the provider mark is already the
  // `glyph` field and cannot tell opus from sonnet, so an icon here would claim
  // to identify something it does not.
  const config = withBottom(defaultSessionRowConfig(), ['model', 'detail'])
  const item = session({ model: 'claude-opus-5', state: 'working', state_detail: 'apply_patch' })
  const tokens = buildSessionRowTokens(item, config, context({ budget: { top: 60, bottom: 12 } }))
  const ids = tokens.bottom.left.tokens.map(token => token.id)
  assert.deepEqual(ids, ['detail'], 'the lower-priority markless field leaves entirely')
})

test('a section is never emptied while it still holds a token', () => {
  // The regression this pins: the count-based shedding this replaced had no floor
  // beyond "more than one token to begin with", so a two-token section at shed 2
  // lost both. A sidebar dragged to 230px rendered a blank bottom line and
  // deleted an `always`-mode field to do it.
  const config: SessionRowConfig = {
    ...defaultSessionRowConfig(),
    bottom: {
      ...defaultSessionRowConfig().bottom,
      left: [{ id: 'worktree', mode: 'always' }, { id: 'detail', mode: 'always' }],
      right: [{ id: 'model', mode: 'always' }, { id: 'duration', mode: 'always' }],
    },
  }
  const item = session({
    state: 'working', state_detail: 'apply_patch', turn_started_at: NOW - 1320,
    model: 'claude-opus-5',
    git: { branch: 'master', worktree: 'feat-tokenizer-rewrite', dirty: 0, ahead: 0, behind: 0 },
  })
  const tokens = buildSessionRowTokens(item, config, context({ budget: { top: 60, bottom: 1 } }))
  assert.equal(tokens.bottom.left.tokens.length, 1, 'the left section keeps one token')
  assert.equal(tokens.bottom.right.tokens.length, 1, 'the right section keeps one token')
  // The survivors are the highest-priority members of each section.
  assert.equal(tokens.bottom.left.tokens[0].id, 'detail')
  assert.equal(tokens.bottom.right.tokens[0].id, 'duration')
})

test('an unmeasured budget degrades nothing', () => {
  const config = withBottom(defaultSessionRowConfig(), ['worktree', 'branch', 'diff', 'detail'])
  const item = session({
    state: 'working', state_detail: 'apply_patch',
    git: { branch: 'feat-tokenizer', worktree: 'feat-tokenizer-rewrite', dirty: 2, ahead: 0, behind: 0, added: 9, removed: 1 },
  })
  const tokens = buildSessionRowTokens(item, config, context())
  assert.equal(tokens.bottom.left.tokens.length, 4)
  assert.ok(tokens.bottom.left.tokens.every(token => token.display === 'full'))
})

test('the character budget is the text column divided by that line’s own advance', () => {
  // Measured, not assumed. The thresholds this replaced were compared against the
  // width of the whole sidebar, which overstates a row's room by the indicator
  // gutter, the tree's padding, and the scrollbar — 49 to 63px at the default
  // width depending on `--session-dot`, a setting they could not see.
  assert.deepEqual(rowBudget(199, { top: 7, bottom: 5.27 }), { top: 28, bottom: 37 })
  assert.deepEqual(rowBudget(135, { top: 7, bottom: 5.27 }), { top: 19, bottom: 25 })
  assert.deepEqual(rowBudget(0, { top: 7, bottom: 5.27 }), EMPTY_ROW_BUDGET, 'unmeasured stays unmeasured')
  assert.deepEqual(rowBudget(199, { top: 0, bottom: 0 }), { top: 0, bottom: 0 }, 'no advance, no budget')
})

test('identity survives any amount of width pressure', () => {
  const config = defaultSessionRowConfig()
  const tokens = buildSessionRowTokens(session(), config, context({ budget: { top: 1, bottom: 1 } }))
  assert.ok(tokens.top.left.tokens.some(token => token.kind === 'title'))
  assert.ok(tokens.top.left.tokens.every(token => token.display === 'full'))
})

test('the identity projection drops every non-identity field', () => {
  const config = defaultSessionRowConfig()
  const item = session({ state: 'working', state_since: NOW - 900, git: { branch: 'feat-x', dirty: 2, ahead: 0, behind: 0, added: 4, removed: 1 } })
  const tokens = identityRowTokens(item, config)
  assert.deepEqual(tokens.bottom.left.tokens, [])
  assert.deepEqual(tokens.bottom.right.tokens, [])
  assert.deepEqual(tokens.top.left.tokens.map(token => token.id), ['glyph', 'title'])
})

// --- the flag strip ----------------------------------------------------------

test('the flag strip is pinned to the top line’s right, away from the title', () => {
  // The defect this pins: the marks used to follow the title inside the section
  // that clips, so a title long enough to fill the sidebar hid every one of them.
  const base = defaultSessionRowConfig()
  assert.deepEqual(base.top.left.map(slot => slot.id), ['glyph', 'title'])
  assert.deepEqual(base.top.right.map(slot => slot.id), ['approvals', 'voice', 'broadcast', 'badges', 'draft'])
})

test('the approval badge shows only while a grant is actually in force', () => {
  // The mode's whole effect is removing the notification an approval would
  // raise, so the fleet list is the only place a grant nobody remembers setting
  // can still be seen — and a badge for a grant the daemon has already dropped
  // would be the sidebar asserting authority that no longer exists.
  const config = defaultSessionRowConfig()
  const granted: ApprovalPolicy = { mode: 'allow_all', run_id: 'run-1', expires_at: NOW + 600,
    granted_at: NOW, set_by: 'pane', rules: [], auto_approved: 4, max_auto: 200,
    last_decision_at: NOW, last_request: 'Read(/x)', floor_deferred: 0 }
  const flags = (item: Session) =>
    buildSessionRowTokens(item, config, context()).top.right.tokens.map(token => token.text)

  assert.deepEqual(flags(session()), [])
  assert.deepEqual(
    flags(session({ agent_run_id: 'run-1', approval_policy: { ...granted } })),
    ['auto ALL'],
  )
  // Expired, and made for a conversation that has since been replaced.
  assert.deepEqual(
    flags(session({ agent_run_id: 'run-1', approval_policy: { ...granted, expires_at: NOW - 1 } })),
    [],
  )
  assert.deepEqual(
    flags(session({ agent_run_id: 'run-2', approval_policy: { ...granted } })),
    [],
  )
  assert.deepEqual(
    flags(session({ agent_run_id: 'run-1', approval_policy: { ...granted, mode: 'allowlisted', rules: ['Read'] } })),
    ['auto'],
  )
})

test('no amount of shedding removes a flag', () => {
  // Width shedding drops the lowest-priority token in *every* section. With the
  // strip alone in the top-right, that would delete the mark saying a subagent
  // is running at exactly the width that made the row hard to read.
  const config = defaultSessionRowConfig()
  const item = session({ broadcast: true, unsent_input: { since: NOW - 60 }, standing_activity: STANDING })
  // Through `budget`, which is what actually drives shedding. This used to pass a `shed`
  // key the context has never had, so the line was fitted against an unmeasured (zero)
  // budget and nothing was ever shed — the assertion held for the wrong reason.
  const tokens = buildSessionRowTokens(item, config, { ...context(), budget: { top: 1, bottom: 1 } })
  assert.deepEqual(tokens.top.left.tokens.map(token => token.id), ['glyph', 'title'])
  assert.deepEqual(tokens.top.right.tokens.map(token => token.id), ['broadcast', 'badges', 'draft'])
})

test('the identity projection keeps the strip, because a phone is where drafts are left', () => {
  const config = defaultSessionRowConfig()
  const item = session({ unsent_input: { since: NOW - 60 } })
  const tokens = identityRowTokens(item, config, context())
  assert.deepEqual(tokens.top.left.tokens.map(token => token.id), ['glyph', 'title'])
  assert.deepEqual(tokens.top.right.tokens.map(token => token.id), ['draft'])
})

// --- read aloud --------------------------------------------------------------

test('the read-aloud mark reports the resolved mode, not the stored one', () => {
  // The fact worth marking is "this session speaks", and a session stores `voice_mode`
  // only once somebody has chosen one — so a fleet on a global default of `auto` is a
  // fleet that all speaks, however empty its session records are.
  const config = defaultSessionRowConfig()
  const marks = (item: Session, ctx = context()) =>
    buildSessionRowTokens(item, config, ctx).top.right.tokens
      .filter(token => token.id === 'voice')
      .map(token => token.voice)

  const on = context({ voice: { enabled: true, default_mode: 'off' } })
  assert.deepEqual(marks(session(), on), [], 'the global default is off, so nothing is marked')
  assert.deepEqual(marks(session({ voice_mode: 'auto' }), on), ['auto'])
  assert.deepEqual(marks(session({ voice_mode: 'on_demand' }), on), ['on_demand'])
  assert.deepEqual(
    marks(session(), context({ voice: { enabled: true, default_mode: 'auto' } })),
    ['auto'],
    'an unset session inherits the global default and speaks',
  )
  assert.deepEqual(
    marks(session({ voice_mode: 'off' }), context({ voice: { enabled: true, default_mode: 'auto' } })),
    [],
    'an explicit off overrides the default',
  )
})

test('the read-aloud mark is off whenever the master switch is', () => {
  // A stored `auto` on a session is a preference the daemon is currently ignoring, and a
  // speaker mark on a fleet that cannot make a sound is worse than no mark at all.
  const config = defaultSessionRowConfig()
  const item = session({ voice_mode: 'auto' })
  const marks = (ctx: ReturnType<typeof context>) =>
    buildSessionRowTokens(item, config, ctx).top.right.tokens.filter(token => token.id === 'voice')
  assert.equal(marks(context()).length, 0, 'the default context has read aloud off')
  assert.equal(marks(context({ voice: { enabled: false, default_mode: 'auto' } })).length, 0)
  assert.equal(marks(context({ voice: { enabled: true, default_mode: 'off' } })).length, 1)
})

test('read aloud is an agent fact, so a shell and a pending pane carry no mark', () => {
  const config = defaultSessionRowConfig()
  const on = context({ voice: { enabled: true, default_mode: 'auto' } })
  const marks = (item: Session) =>
    buildSessionRowTokens(item, config, on).top.right.tokens.filter(token => token.id === 'voice')
  assert.equal(marks(session({ voice_mode: 'auto' })).length, 1)
  assert.equal(marks(session({ backend: 'shell', voice_mode: 'auto' })).length, 0)
  assert.equal(marks(session({ pending: true, voice_mode: 'auto' })).length, 0)
})

test('the read-aloud mark survives shedding and the phone’s identity row', () => {
  // Same rule as the rest of the flag strip: a mark whose whole content is "this is true"
  // has nothing to ellipsize, and dropping it at the width that made the row hard to read
  // deletes the one report that this session is talking.
  const config = defaultSessionRowConfig()
  const item = session({ voice_mode: 'auto' })
  const on = context({ voice: { enabled: true, default_mode: 'off' } })
  // A one-character budget, which is the narrowest the line can be asked to fit into.
  const squeezed = { ...on, budget: { top: 1, bottom: 1 } }
  assert.ok(buildSessionRowTokens(item, config, squeezed)
    .top.right.tokens.some(token => token.id === 'voice'))
  assert.ok(identityRowTokens(item, config, on).top.right.tokens.some(token => token.id === 'voice'))
})

test('a pre-strip layout relocates its flags once and gains the new one', () => {
  const config = normalizeSessionRowConfig({
    version: 1,
    top: {
      left: [
        { id: 'glyph', mode: 'always' }, { id: 'title', mode: 'always' },
        { id: 'broadcast', mode: 'notable' }, { id: 'badges', mode: 'notable' },
      ],
      right: [],
      separator: 'none',
    },
  })
  assert.equal(config.version, 3)
  assert.deepEqual(config.top.left.map(slot => slot.id), ['glyph', 'title'])
  assert.deepEqual(config.top.right.map(slot => slot.id), ['voice', 'broadcast', 'badges', 'draft'])
})

test('migration relocates a choice without re-imposing one', () => {
  // A flag the user had removed is off, and stays off: `draft` and `voice` arrive placed
  // because nobody could have declined a field that did not exist yet.
  const config = normalizeSessionRowConfig({
    version: 1,
    top: { left: [{ id: 'glyph', mode: 'always' }, { id: 'title', mode: 'always' }], right: [], separator: 'none' },
  })
  assert.deepEqual(config.top.right.map(slot => slot.id), ['voice', 'draft'])
})

test('a version 2 layout gains the read-aloud flag beside the approval one', () => {
  // Changing the shipped default reaches nobody who has ever opened the settings panel: a
  // stored blob is authoritative and an unplaced field is off, so without this step the
  // mark would ship invisible to exactly the users who configured their rows.
  const config = normalizeSessionRowConfig({
    version: 2,
    top: {
      left: [{ id: 'glyph', mode: 'always' }, { id: 'title', mode: 'always' }],
      right: [{ id: 'approvals', mode: 'notable' }, { id: 'draft', mode: 'always' }],
      separator: 'none',
    },
  })
  assert.equal(config.version, 3)
  assert.deepEqual(config.top.right.map(slot => slot.id), ['approvals', 'voice', 'draft'])
  // And version 2's own relocation does not run again on a layout that has had it.
  assert.deepEqual(config.top.left.map(slot => slot.id), ['glyph', 'title'])
})

test('a layout that already places read aloud is not given a second copy', () => {
  const config = normalizeSessionRowConfig({
    version: 2,
    top: {
      left: [{ id: 'title', mode: 'always' }],
      right: [{ id: 'voice', mode: 'always' }],
      separator: 'none',
    },
  })
  assert.deepEqual(config.top.right.map(slot => slot.id), ['voice'])
  assert.equal(config.top.right[0].mode, 'always', 'the stored mode is kept')
})

test('an already-migrated layout is left exactly as it is', () => {
  const stored = {
    ...defaultSessionRowConfig(),
    top: {
      left: [{ id: 'title', mode: 'always' }],
      right: [{ id: 'badges', mode: 'always' }],
      separator: 'none' as const,
    },
  }
  const config = normalizeSessionRowConfig(stored)
  assert.deepEqual(config.top.right.map(slot => slot.id), ['badges'], 'no field is re-added')
  assert.deepEqual(config.top.left.map(slot => slot.id), ['title'])
})

// --- unsent input ------------------------------------------------------------

test('unsent composer text marks the row, from either device or the daemon', () => {
  const config = defaultSessionRowConfig()
  const flags = (item: Session, ctx = context()) =>
    buildSessionRowTokens(item, config, ctx).top.right.tokens.map(token => token.id)
  assert.deepEqual(flags(session()), [], 'a quiet session is unmarked')
  assert.deepEqual(flags(session({ unsent_input: { since: NOW - 60 } })), ['draft'])
  assert.deepEqual(
    flags(session(), context({ localDrafts: { s1: NOW - 60 } })),
    ['draft'],
    'a device-local draft never reaches the PTY, so only this client can report it',
  )
})

test('the mark reports the oldest thing sitting there', () => {
  // Two sources, two clocks: a phone draft from an hour ago is not made recent
  // by a keystroke on the desktop a minute ago, and the question the row answers
  // is how long something has been waiting.
  const config = defaultSessionRowConfig()
  const item = session({ unsent_input: { since: NOW - 60 } })
  const tokens = buildSessionRowTokens(item, config, context({ localDrafts: { s1: NOW - 3600 } }))
  assert.match(tokens.top.right.tokens[0].title || '', /1h ago/)
})

test('an ended session has no composer to report', () => {
  const config = defaultSessionRowConfig()
  const item = session({ state: 'exited', unsent_input: { since: NOW - 60 } })
  assert.deepEqual(buildSessionRowTokens(item, config, context()).top.right.tokens, [])
})

// --- standing activity, in exactly one place ---------------------------------

test('standing activity renders in the row, on the indicator, or nowhere', () => {
  const base = defaultSessionRowConfig()
  const item = session({ standing_activity: STANDING })
  const inRow = (config: SessionRowConfig) =>
    buildSessionRowTokens(item, config, context()).top.right.tokens.map(token => token.id)

  assert.equal(base.standing, 'row', 'the glyphs carry the kinds and counts, so they are the default')
  assert.deepEqual(inRow(base), ['badges'])
  assert.equal(sessionStandingMark(item, base), null)

  const onIndicator: SessionRowConfig = { ...base, standing: 'indicator' }
  assert.deepEqual(inRow(onIndicator), [], 'one fact must not render twice')
  assert.equal(sessionStandingMark(item, onIndicator)?.label, '2 subagents')

  const off: SessionRowConfig = { ...base, standing: 'off' }
  assert.deepEqual(inRow(off), [])
  assert.equal(sessionStandingMark(item, off), null)
})

test('the indicator pip is drawn only for a session that has something standing', () => {
  const config: SessionRowConfig = { ...defaultSessionRowConfig(), standing: 'indicator' }
  assert.equal(sessionStandingMark(session(), config), null)
  assert.equal(sessionStandingMark(undefined, config), null)
})

test('normalization keeps the title, deduplicates, and rejects cross-line fields', () => {
  const config = normalizeSessionRowConfig({
    version: 1,
    top: { left: [{ id: 'branch', mode: 'always' }], right: [], separator: 'none' },
    bottom: { left: [{ id: 'title', mode: 'always' }, { id: 'diff', mode: 'x' }, { id: 'diff', mode: 'always' }], right: [], separator: 'nope' },
  })
  const topIds = [...config.top.left, ...config.top.right].map(slot => slot.id)
  const bottomIds = [...config.bottom.left, ...config.bottom.right].map(slot => slot.id)
  assert.ok(topIds.includes('title'), 'the title is re-placed when a blob drops it')
  assert.ok(!topIds.includes('branch'), 'a non-identity field cannot sit on the top line')
  assert.ok(!bottomIds.includes('title'), 'an identity field cannot sit on the bottom line')
  assert.deepEqual(bottomIds.filter(id => id === 'diff'), ['diff'], 'a field is placed at most once')
  assert.equal(config.bottom.separator, 'dot', 'an unknown separator falls back')
  assert.equal(config.bottom.left.find(slot => slot.id === 'diff')?.mode, 'notable', 'an unknown mode falls back')
})

test('garbage and absent blobs both yield the shipped default', () => {
  const base = defaultSessionRowConfig()
  assert.deepEqual(normalizeSessionRowConfig(undefined), base)
  assert.deepEqual(normalizeSessionRowConfig('nonsense'), base)
  assert.equal(normalizeSessionRowConfig({ dotShape: 'triangle' }).dotShape, 'hexagon')
})

test('moving the shipped default repaints no device that has stored a layout', () => {
  // The blob a build before this one wrote, verbatim: the previous default's
  // bottom line, its bare git tokens, its identity-only phone, its 15px
  // indicator. Every one of those differs from what ships now, which is what
  // makes it a usable witness — a device that saved a layout must keep it, and a
  // default change is not a migration.
  //
  // It works because a save writes the whole `SessionRowConfig`, so a stored blob
  // has every key and `normalizeSessionRowConfig` never reaches its fallbacks.
  // If a future field is ever added without that being true, this fails here
  // rather than on somebody's sidebar.
  const stored = {
    version: 3,
    top: {
      left: [{ id: 'glyph', mode: 'always' }, { id: 'title', mode: 'always' }],
      right: [
        { id: 'approvals', mode: 'notable' }, { id: 'voice', mode: 'notable' },
        { id: 'broadcast', mode: 'notable' }, { id: 'badges', mode: 'notable' },
        { id: 'draft', mode: 'always' },
      ],
      separator: 'none',
    },
    bottom: {
      left: [
        { id: 'worktree', mode: 'notable' }, { id: 'branch', mode: 'notable' },
        { id: 'diff', mode: 'notable' }, { id: 'queue', mode: 'notable' },
        { id: 'detail', mode: 'notable' },
      ],
      right: [
        { id: 'model', mode: 'notable' }, { id: 'account', mode: 'notable' },
        { id: 'sincePrompt', mode: 'notable' }, { id: 'duration', mode: 'always' },
        { id: 'context', mode: 'always' },
      ],
      separator: 'dot',
    },
    dotShape: 'circle',
    dotSizeDesktop: 15,
    dotSizeMobile: 17,
    context: 'arc',
    standing: 'row',
    diffStyle: 'numbers',
    countStyle: 'numbers',
    gitGlyphs: false,
    mobileFields: false,
  }
  assert.deepEqual(normalizeSessionRowConfig(stored), stored)
})

test('a version-2 blob gains only the flag it predates, never the new default', () => {
  // The shape the primary install is actually on. Its own bottom line and
  // scalars survive untouched; the one thing normalization adds is `voice`,
  // which version 3 places because nobody could have declined a field that did
  // not exist. That is the only sanctioned way a default change reaches a stored
  // layout, and it is a field placement rather than a repaint.
  const stored = {
    version: 2,
    top: {
      left: [{ id: 'glyph', mode: 'always' }, { id: 'title', mode: 'always' }],
      right: [
        { id: 'broadcast', mode: 'notable' }, { id: 'badges', mode: 'notable' },
        { id: 'draft', mode: 'always' },
      ],
      separator: 'none',
    },
    bottom: {
      left: [{ id: 'duration', mode: 'always' }, { id: 'compactions', mode: 'notable' }],
      right: [{ id: 'cost', mode: 'notable' }],
      separator: 'space',
    },
    dotShape: 'square',
    dotSizeDesktop: 12,
    dotSizeMobile: 22,
    context: 'percent',
    standing: 'indicator',
    diffStyle: 'bar',
    countStyle: 'pips',
    gitGlyphs: false,
    mobileFields: false,
  }
  const config = normalizeSessionRowConfig(stored)
  assert.equal(config.version, 3)
  assert.deepEqual(config.top.right.map(slot => slot.id), ['voice', 'broadcast', 'badges', 'draft'])
  assert.deepEqual(config.bottom, stored.bottom, 'the bottom line is the device’s own, still')
  for (const key of ['dotShape', 'dotSizeDesktop', 'dotSizeMobile', 'context', 'standing', 'diffStyle', 'countStyle', 'gitGlyphs', 'mobileFields'] as const) {
    assert.equal(config[key], stored[key], `${key} must not adopt the new default`)
  }
})

test('the shipped default is the layout swe-mux is operated with', () => {
  // Pinned field by field rather than by spot-checking a flag, because this
  // object is a transcription of a real install's stored blob and the whole
  // point of shipping it is that a new user sees that layout and not a drift of
  // it. Anything here that changes should change because somebody decided to
  // move the default, which is a diff worth reading.
  const base = defaultSessionRowConfig()
  assert.equal(base.version, 3)
  assert.deepEqual(base.top.left, [
    { id: 'glyph', mode: 'always' },
    { id: 'title', mode: 'always' },
  ])
  assert.deepEqual(base.top.right, [
    { id: 'approvals', mode: 'notable' },
    { id: 'voice', mode: 'notable' },
    { id: 'broadcast', mode: 'notable' },
    { id: 'badges', mode: 'notable' },
    { id: 'draft', mode: 'always' },
  ])
  assert.equal(base.top.separator, 'none')
  assert.deepEqual(base.bottom.left, [
    { id: 'duration', mode: 'always' },
    { id: 'worktree', mode: 'notable' },
    { id: 'state', mode: 'notable' },
    { id: 'queue', mode: 'notable' },
  ])
  assert.deepEqual(base.bottom.right, [{ id: 'model', mode: 'always' }])
  assert.equal(base.bottom.separator, 'dot')
  assert.equal(base.dotShape, 'hexagon')
  assert.equal(base.context, 'arc')
  assert.equal(base.standing, 'row')
  assert.equal(base.diffStyle, 'numbers')
  assert.equal(base.countStyle, 'numbers')
  assert.equal(base.gitGlyphs, true)
  assert.equal(base.mobileFields, true)
  assert.equal(base.dotSizeDesktop, DEFAULT_DOT_SIZE_DESKTOP)
  assert.equal(base.dotSizeMobile, DEFAULT_DOT_SIZE_MOBILE)
})

test('the default places the state word but keeps it silent', () => {
  // `state` is never notable by definition — the indicator already says it — so a
  // `notable` slot draws nothing. Placed anyway, because promoting it to `always`
  // is one click where re-adding an unplaced field is a drag.
  const base = defaultSessionRowConfig()
  assert.equal(base.bottom.left.find(slot => slot.id === 'state')?.mode, 'notable')
  const item = session({ state: 'working', state_since: NOW - 30, turn_started_at: NOW - 30 })
  assert.ok(
    !bottomText(item, base).includes('working'),
    'a notable state slot must render nothing at all',
  )
  const shown = setFieldMode(base, 'state', 'always')
  assert.ok(bottomText(item, shown).includes('working'), 'always is what turns it on')
})

test('the default indicator size differs per device class, and mobile is not simply larger', () => {
  // Both are set explicitly rather than one deriving from the other: a physical
  // size is the one property the shared layout cannot express, because the
  // screens are held at different distances *and* a phone has far less width to
  // spend on the gutter the indicator sits in.
  const base = defaultSessionRowConfig()
  assert.equal(base.dotSizeDesktop, DEFAULT_DOT_SIZE_DESKTOP)
  assert.equal(base.dotSizeMobile, DEFAULT_DOT_SIZE_MOBILE)
  assert.notEqual(base.dotSizeMobile, base.dotSizeDesktop)
  for (const size of [base.dotSizeDesktop, base.dotSizeMobile]) {
    assert.ok(size >= DOT_SIZE_MIN && size <= DOT_SIZE_MAX, `${size} must be a size the panel can set`)
  }
})

test('a stored indicator size is clamped rather than discarded', () => {
  // Clamped, not rejected: a blob from a build with wider bounds should render at
  // the nearest size this one can draw, not silently reset and look like a lost
  // setting. Anything that is not a number at all does fall back.
  assert.equal(normalizeDotSize(40, 15), DOT_SIZE_MAX)
  assert.equal(normalizeDotSize(2, 15), DOT_SIZE_MIN)
  assert.equal(normalizeDotSize(16.4, 15), 16)
  assert.equal(normalizeDotSize('big', 15), 15)
  assert.equal(normalizeDotSize(Number.NaN, 15), 15)
  const config = normalizeSessionRowConfig({ dotSizeDesktop: 999, dotSizeMobile: 'x' })
  assert.equal(config.dotSizeDesktop, DOT_SIZE_MAX)
  assert.equal(config.dotSizeMobile, DEFAULT_DOT_SIZE_MOBILE)
})

test('the indicator size resolves per device class', () => {
  const config = { ...defaultSessionRowConfig(), dotSizeDesktop: 14, dotSizeMobile: 20 }
  assert.equal(sessionDotSize(config, 'desktop'), 14)
  assert.equal(sessionDotSize(config, 'mobile'), 20)
})

test('placement moves a field rather than duplicating it', () => {
  let config = defaultSessionRowConfig()
  config = placeField(config, 'dirty', 'bottom', 'left', 0)
  assert.equal(config.bottom.left[0].id, 'dirty')
  config = placeField(config, 'dirty', 'bottom', 'right', 0)
  assert.ok(!config.bottom.left.some(slot => slot.id === 'dirty'))
  assert.equal(config.bottom.right[0].id, 'dirty')
  assert.equal([...config.bottom.left, ...config.bottom.right].filter(slot => slot.id === 'dirty').length, 1)
})

test('the title cannot be removed and identity cannot cross lines', () => {
  const config = defaultSessionRowConfig()
  assert.deepEqual(removeField(config, 'title'), config)
  assert.deepEqual(placeField(config, 'title', 'bottom', 'left'), config)
  assert.deepEqual(placeField(config, 'branch', 'top', 'left'), config)
})

test('mode changes reach the field wherever it sits', () => {
  const config = setFieldMode(defaultSessionRowConfig(), 'duration', 'notable')
  assert.equal(config.bottom.left.find(slot => slot.id === 'duration')?.mode, 'notable')
})

test('every catalog field is either placed or offered by a preset', () => {
  for (const preset of ['minimal', 'default', 'detailed'] as const) {
    const config = presetConfig(preset)
    const placed = new Set([...config.top.left, ...config.top.right, ...config.bottom.left, ...config.bottom.right].map(slot => slot.id))
    const offered = new Set(unplacedFields(config).map(field => field.id))
    for (const field of ROW_FIELDS) {
      assert.ok(placed.has(field.id) || offered.has(field.id), `${field.id} must be reachable in ${preset}`)
    }
  }
})

test('every shape is a closed path starting at twelve o’clock', () => {
  for (const shape of ['hexagon', 'circle', 'square'] as const) {
    const path = shapePath(shape, 9)
    assert.ok(path.endsWith('Z'), `${shape} must close`)
    const start = /^M([\d.-]+),([\d.-]+)/.exec(path)
    if (!start) throw new Error(`${shape} must begin with a move`)
    assert.equal(Number(start[1]), 12, `${shape} must start horizontally centred`)
    assert.ok(Number(start[2]) < 12, `${shape} must start above centre so a partial fill reads as a clock`)
  }
})

test('the square is axis-aligned, not a diamond', () => {
  const path = shapePath('square', 9)
  assert.equal(path.split('L').length - 1, 4, 'four straight edges')
  assert.ok(!path.includes('A'), 'no arcs')
  const corners = [...path.matchAll(/([\d.-]+),([\d.-]+)/g)].map(match => Number(match[1]))
  assert.ok(corners.some(x => x > 12) && corners.some(x => x < 12), 'edges reach both sides of centre')
})
