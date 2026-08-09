import assert from 'node:assert/strict'
import test from 'node:test'
import type { Session } from '../src/types.ts'
import {
  ROW_FIELDS, defaultSessionRowConfig, normalizeSessionRowConfig, placeField, presetConfig,
  removeField, setFieldMode, unplacedFields,
  type RowFieldId, type SessionRowConfig,
} from '../src/sessionRowConfig.ts'
import {
  buildSessionRowTokens, deriveRowContext, emptyRowContext, formatRowDuration,
  identityRowTokens, sessionContextArc, secondsInState,
} from '../src/sessionRowFields.ts'
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

test('a ready session with no completed turn shows no duration at all', () => {
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  assert.deepEqual(bottomText(session({ state: 'idle', last_turn_ms: null }), config), [])
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

test('an ended session reports its lifetime and its exit reason', () => {
  const config = withBottom(defaultSessionRowConfig(), ['exit', 'duration'], 'notable')
  const ended = session({ state: 'crashed', state_detail: 'exit 1', created_at: NOW - 7200, state_since: NOW - 60 })
  assert.deepEqual(bottomText(ended, config), ['crashed · exit 1', '1h59'])
})

test('a session the daemon never dated shows no age rather than 1970', () => {
  const config = withBottom(defaultSessionRowConfig(), ['duration'])
  assert.equal(secondsInState(session({ state_since: 0 }), NOW), 0)
  assert.deepEqual(bottomText(session({ state: 'working', state_since: 0 }), config), [])
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

test('the identity projection drops every non-identity field', () => {
  const config = defaultSessionRowConfig()
  const item = session({ state: 'working', state_since: NOW - 900, git: { branch: 'feat-x', dirty: 2, ahead: 0, behind: 0, added: 4, removed: 1 } })
  const tokens = identityRowTokens(item, config)
  assert.deepEqual(tokens.bottom.left.tokens, [])
  assert.deepEqual(tokens.bottom.right.tokens, [])
  assert.deepEqual(tokens.top.left.tokens.map(token => token.id), ['glyph', 'title'])
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
