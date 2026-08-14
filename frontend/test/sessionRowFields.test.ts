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
  buildSessionRowTokens, deriveRowContext, emptyRowContext, formatRowDuration,
  identityRowTokens, sessionContextArc, sessionStandingMark, shedForWidth,
} from '../src/sessionRowFields.ts'
import type { StandingActivity } from '../src/types.ts'
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

const withBottom = (config: SessionRowConfig, ids: RowFieldId[], mode: 'notable' | 'always' = 'always'): SessionRowConfig => ({
  ...config,
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

test('git glyphs are opt-in; branch names are bare by default', () => {
  const base = defaultSessionRowConfig()
  const item = session({ git: { branch: 'feat-x', dirty: 0, ahead: 0, behind: 0 } })
  assert.deepEqual(bottomText(item, withBottom(base, ['branch'])), ['feat-x'])
  assert.deepEqual(bottomText(item, withBottom({ ...base, gitGlyphs: true }, ['branch'])), ['⎇ feat-x'])
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

test('shedding drops whole tokens and leaves no dangling separator', () => {
  // Shedding used to be a container query hiding tokens with `display:none`,
  // which removed the token but not the separator JSX beside it — a narrowed
  // row rendered as `· apply_patch`, a mark belonging to a token that was gone.
  const config = withBottom(defaultSessionRowConfig(), ['branch', 'diff', 'queue', 'detail'])
  const item = session({
    state: 'working', state_detail: 'apply_patch',
    git: { branch: 'feat-x', dirty: 2, ahead: 0, behind: 0, added: 9, removed: 1 },
  })
  const full = context({ queueDepth: { s1: 2 } })
  assert.deepEqual(bottomText(item, config, full), ['feat-x', '+9 -1', '⋮2', 'apply_patch'])

  // Lowest priority sheds first — diff (55) then branch (60) — while the queue
  // depth (65) and what it is doing (70) survive, and the configured order of
  // the survivors is preserved.
  const tight = buildSessionRowTokens(item, config, { ...full, shed: 2 })
  assert.deepEqual(tight.bottom.left.tokens.map(token => token.id), ['queue', 'detail'])
  // The renderer emits a separator only between the tokens in this list, so a
  // shed token cannot leave one behind.
  assert.equal(tight.bottom.left.tokens[0].id, 'queue')
})

test('the width thresholds shed progressively and never below zero', () => {
  assert.equal(shedForWidth(0), 0, 'unmeasured width sheds nothing')
  assert.equal(shedForWidth(400), 0)
  assert.equal(shedForWidth(270), 1)
  assert.equal(shedForWidth(230), 2)
  assert.equal(shedForWidth(195), 3)
  assert.equal(shedForWidth(120), 3, 'shedding stops once the list is exhausted')
})

test('the title survives any amount of shedding', () => {
  const config = defaultSessionRowConfig()
  const tokens = buildSessionRowTokens(session(), config, { ...context(), shed: 99 })
  assert.ok(tokens.top.left.tokens.some(token => token.kind === 'title'))
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
  assert.deepEqual(base.top.right.map(slot => slot.id), ['broadcast', 'badges', 'draft'])
})

test('no amount of shedding removes a flag', () => {
  // Width shedding drops the lowest-priority token in *every* section. With the
  // strip alone in the top-right, that would delete the mark saying a subagent
  // is running at exactly the width that made the row hard to read.
  const config = defaultSessionRowConfig()
  const item = session({ broadcast: true, unsent_input: { since: NOW - 60 }, standing_activity: STANDING })
  const tokens = buildSessionRowTokens(item, config, { ...context(), shed: 99 })
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
  assert.equal(config.version, 2)
  assert.deepEqual(config.top.left.map(slot => slot.id), ['glyph', 'title'])
  assert.deepEqual(config.top.right.map(slot => slot.id), ['broadcast', 'badges', 'draft'])
})

test('migration relocates a choice without re-imposing one', () => {
  // A flag the user had removed is off, and stays off: `draft` arrives placed
  // because nobody could have declined a field that did not exist.
  const config = normalizeSessionRowConfig({
    version: 1,
    top: { left: [{ id: 'glyph', mode: 'always' }, { id: 'title', mode: 'always' }], right: [], separator: 'none' },
  })
  assert.deepEqual(config.top.right.map(slot => slot.id), ['draft'])
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

test('the shipped default is hexagon, arc, bare git, identity-only mobile', () => {
  const base = defaultSessionRowConfig()
  assert.equal(base.dotShape, 'hexagon')
  assert.equal(base.context, 'arc')
  assert.equal(base.gitGlyphs, false)
  assert.equal(base.mobileFields, false)
  assert.equal(base.diffStyle, 'numbers')
  assert.ok(!base.bottom.left.some(slot => slot.id === 'state'), 'the state word is off by default')
})

test('the indicator is larger on mobile than on desktop by default', () => {
  // A phone row is read at arm's length and never hovered, so the indicator is
  // the one element that must not step down with the smaller type around it.
  const base = defaultSessionRowConfig()
  assert.equal(base.dotSizeDesktop, DEFAULT_DOT_SIZE_DESKTOP)
  assert.equal(base.dotSizeMobile, DEFAULT_DOT_SIZE_MOBILE)
  assert.ok(base.dotSizeMobile > base.dotSizeDesktop)
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
  assert.equal(config.bottom.right.find(slot => slot.id === 'duration')?.mode, 'notable')
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
