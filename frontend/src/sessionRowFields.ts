// The sidebar row's field engine: (session, config, fleet context) -> tokens.
//
// Pure and DOM-free so the notability rules are testable without rendering. The
// renderer decides what a token *looks* like; this decides whether it exists at
// all and what it says.
//
// One rule drives the whole module: the state indicator already carries the
// state, so the row must not print it again. `working`, `ready`, and `turn
// complete` are duplication, not information, and the space they cost is the
// space every other field is competing for.

import { activityBadges, awaitingLabel, type ActivityBadge } from './sessionStatus.ts'
import { hasHarnessMeasurement, isAgentBackend, isObservedHarness } from './harnessRegistry.ts'
import { displayModelName } from './modelDisplay.ts'
import type { Session } from './types'
import {
  ROW_FIELD_BY_ID, SEPARATORS,
  type RowAlign, type RowFieldId, type RowLine, type RowSlot, type SessionRowConfig,
} from './sessionRowConfig.ts'

/** Fleet-derived facts a single session cannot answer about itself. */
export interface SessionRowContext {
  /** Epoch seconds, quantized by the caller so rows do not re-render per frame. */
  now: number
  /** Most common branch per project id: the baseline "differs from" compares against. */
  defaultBranch: Record<string, string | undefined>
  /** Most common model per project id. */
  defaultModel: Record<string, string | undefined>
  /** True when more than one provider account is live across the fleet. */
  multiAccount: boolean
  /** Pending queue depth by target session id. */
  queueDepth: Record<string, number>
  /**
   * Live sessions per checkout root.
   *
   * Every Git measurement is a property of the working tree, not of the agent:
   * `git status` answers for the whole repository however it is invoked, so two
   * sessions in one checkout necessarily report identical numbers. This is what
   * lets a row say so instead of letting the reader assume the number is that
   * one agent's work.
   */
  checkoutSessions: Record<string, number>
  /**
   * How many low-priority tokens each section sheds for the current width.
   *
   * Shedding lives here rather than in a container query because the separator
   * invariant is a property of the *token list*: a CSS rule that hides a token
   * with `display:none` leaves the separator that JSX already emitted beside it,
   * so a narrowed row rendered as `· apply_patch` — a leading mark belonging to
   * a token that is no longer there.
   */
  shed: number
}

export function emptyRowContext(now = Date.now() / 1000): SessionRowContext {
  return {
    now, defaultBranch: {}, defaultModel: {}, multiAccount: false,
    queueDepth: {}, checkoutSessions: {}, shed: 0,
  }
}

/** Width thresholds, widest first; the index is how many tokens are shed. */
const SHED_STEPS = [270, 230, 195]

/** Tokens to shed at `width` (container inline size, CSS px). */
export function shedForWidth(width: number): number {
  if (!width) return 0
  let shed = 0
  for (const step of SHED_STEPS) if (width <= step) shed += 1
  return shed
}

export type RowTokenKind = 'text' | 'diff' | 'gauge' | 'count' | 'badges' | 'glyph' | 'title' | 'broadcast'
export type RowTone = 'default' | 'muted' | 'warn' | 'add' | 'del'

export interface RowToken {
  id: RowFieldId
  kind: RowTokenKind
  /** Rendered text for `text` tokens, and the accessible label for the rest. */
  text: string
  title?: string
  tone?: RowTone
  priority: number
  /**
   * Scope mark drawn before the value, for facts that would otherwise be
   * indistinguishable from a differently-scoped sibling: a branch-scoped
   * `+312 -48` beside a working-tree `+312 -48` is two numbers that mean
   * different things and look identical.
   */
  prefix?: string
  /**
   * The value describes a checkout more than one live session is working in, so
   * every one of those rows is printing this same number. Rendered as a mark on
   * the token rather than left to the tooltip, because the misreading it exists
   * to stop ("this is what *this* agent changed") happens while scanning.
   */
  shared?: boolean
  diff?: { added: number; removed: number }
  gauge?: { pct: number; peak: number }
  count?: number
  badges?: ActivityBadge[]
}

export interface RowSection { align: RowAlign; separator: string; tokens: RowToken[] }
export interface RowLineTokens { left: RowSection; right: RowSection }
export interface SessionRowTokens { top: RowLineTokens; bottom: RowLineTokens }

// Thresholds. Each exists to answer "would a human act on this?", and each is the
// difference between a row that speaks when it matters and one that always talks.
const WORKING_NOTABLE_SECONDS = 60
const AWAITING_NOTABLE_SECONDS = 20
const LAST_TURN_NOTABLE_SECONDS = 10
const IDLE_FOR_NOTABLE_SECONDS = 30 * 60
const CONTEXT_NOTABLE_PCT = 0.6
const COST_NOTABLE_USD = 1

/**
 * Shortest `last_turn_ms` that describes a turn rather than a boundary artifact.
 *
 * Mirrors `MIN_TURN_DURATION_SECONDS` in `observation.py`, which is where the
 * rule is enforced. This copy is not redundant: a daemon that predates that rule
 * wrote sub-millisecond durations into records that survive a restart, so the
 * values are already on disk and the row has to refuse them on the way out too.
 * Without it a session whose "last turn" was written as 2 ms reads `0s` — a
 * measurement of nothing, indistinguishable from a real one.
 */
const MIN_REPORTABLE_TURN_MS = 250

/**
 * How far a daemon timestamp may sit in this device's future before its age is
 * treated as unknowable rather than as zero.
 *
 * `serverClock.ts` removes real skew, but its estimate has a resolution of its
 * own, and a turn that genuinely started a moment ago is legitimately at zero.
 * Inside the band, "0s" is the truth; beyond it the correction did not hold and
 * the honest render is nothing at all, because the failure this replaces was a
 * row frozen at "0s" for ten minutes of visible work.
 */
const CLOCK_SKEW_TOLERANCE_SECONDS = 5

/**
 * How far the current turn's age may fall short of the time since you last
 * spoke before the gap is worth its own token.
 *
 * The two are the same number on a session nobody is feeding, and drawing both
 * there would be one fact twice. They come apart when something other than a
 * person keeps opening turns — auto-delivery, a teammate message injected the
 * instant the previous turn ends — and then the turn is a true answer to the
 * wrong question: measured live at "3m22" on a session thirteen minutes into
 * work its operator had asked for once.
 */
const PROMPT_GAP_NOTABLE_SECONDS = 90

/**
 * Mark on the since-your-prompt token.
 *
 * Load-bearing rather than decorative, on the same grounds as the branch scope
 * mark: two bare durations in one section are indistinguishable, and these two
 * are specifically the pair a reader is trying to tell apart.
 */
const HUMAN_PROMPT_MARK = '⌨'

/**
 * Compact duration, never wider than four characters.
 *
 * Fixed width is what lets the right section be a column instead of a ragged
 * edge: `1h30` and `72s` occupy the same slot, so the eye scans down rather
 * than re-finding the number on every row.
 */
export function formatRowDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) {
    // Sub-ten-minute values keep their seconds: a turn that took 72s and one
    // that took 110s are meaningfully different, and both read "1m" without it.
    const rest = total % 60
    return minutes < 10 && rest ? `${minutes}m${String(rest).padStart(2, '0')}` : `${minutes}m`
  }
  const hours = Math.floor(minutes / 60)
  if (hours < 24) {
    const rest = minutes % 60
    return hours < 10 && rest ? `${hours}h${String(rest).padStart(2, '0')}` : `${hours}h`
  }
  const days = Math.floor(hours / 24)
  const restHours = hours % 24
  if (days > 99) return '99d+'
  return days < 10 && restHours ? `${days}d${restHours}h` : `${days}d`
}

/**
 * Age of a daemon timestamp on this device's corrected clock, or `null` when the
 * two clocks disagree by enough that any number would be invented.
 *
 * The single way this module turns a daemon instant into an elapsed time. It
 * replaced a helper that clamped every disagreement to zero, which is what let a
 * skewed client render a lie in the shape of a measurement.
 */
function ageOf(stamp: number, now: number): number | null {
  const seconds = now - stamp
  if (seconds < -CLOCK_SKEW_TOLERANCE_SECONDS) return null
  return Math.max(0, seconds)
}

const isEnded = (session: Session) => session.state === 'exited' || session.state === 'crashed'

/**
 * The time this state has cost, which is a different question per state.
 *
 * A working session is aged from the **turn** it is in, not from the state it is
 * in. A turn survives every tool call and every approval inside it, while
 * `state_since` restarts on each of them — so a busy agent's timer reset every
 * few seconds and never once reported the length of the actual work.
 *
 * An awaiting session is aged from its turn too. It did not used to be: the
 * column switched to time-in-state, on the reasoning that the live question
 * there is "how long has it been blocked on me". That answer is worth having,
 * but not at the cost of the number changing what it measures underneath the
 * reader — a session with several subagents raising permission prompts made the
 * figure collapse to seconds and spring back to the turn length every time one
 * appeared and was answered, which reads as a timer resetting at random. The
 * blocked time now rides `detailText`, where it is labelled, and the number
 * stays one quantity for the whole turn.
 *
 * A ready session reports how long its last turn took rather than how long it
 * has been ready — that number is static, which also means a settled fleet
 * re-renders on no clock at all.
 *
 * Every branch renders nothing rather than a placeholder when it cannot answer.
 * A row that says nothing is read as "no measurement"; a row that says `0s` is
 * read as a measurement of zero, and the two failures this function has actually
 * produced — a replayed turn recorded as 2 ms, and a client clock behind the
 * daemon's — were both indistinguishable from a turn that had just begun.
 */
function durationToken(session: Session, context: SessionRowContext): { text: string; title: string; seconds: number } | null {
  if (isEnded(session)) {
    const lifetime = Math.max(0, (session.state_since || context.now) - (session.created_at || 0))
    return session.created_at
      ? { text: formatRowDuration(lifetime), title: `session lifetime ${formatRowDuration(lifetime)}`, seconds: lifetime }
      : null
  }
  if (session.state === 'idle') {
    if (typeof session.last_turn_ms !== 'number' || session.last_turn_ms < MIN_REPORTABLE_TURN_MS) return null
    const seconds = session.last_turn_ms / 1000
    return { text: formatRowDuration(seconds), title: `last turn took ${formatRowDuration(seconds)}`, seconds }
  }
  if (session.turn_started_at) {
    const seconds = ageOf(session.turn_started_at, context.now)
    if (seconds === null) return null
    return { text: formatRowDuration(seconds), title: `this turn has run ${formatRowDuration(seconds)}`, seconds }
  }
  if (!session.state_since) return null
  const seconds = ageOf(session.state_since, context.now)
  if (seconds === null) return null
  return { text: formatRowDuration(seconds), title: `${session.state} for ${formatRowDuration(seconds)}`, seconds }
}

function durationIsNotable(session: Session, seconds: number): boolean {
  if (isEnded(session)) return true
  if (session.state === 'idle') return seconds >= LAST_TURN_NOTABLE_SECONDS
  // `awaiting` shares the working threshold because it now reports the same
  // quantity — the turn. How long the block itself has stood is `detailText`'s.
  return seconds >= WORKING_NOTABLE_SECONDS
}

/** What the session is doing, minus the state word the indicator already carries. */
function detailText(session: Session, context: SessionRowContext): { text: string; tone: RowTone } | null {
  if (isAgentBackend(session.backend) && !isObservedHarness(session.backend)) {
    return { text: 'not observed by mux', tone: 'muted' }
  }
  if (session.state === 'awaiting') {
    // How long it has been blocked on you, which the duration column used to
    // carry by silently becoming a different measurement. Here it is labelled by
    // the thing it sits beside, so it can be read without being mistaken for the
    // turn length, and the two can disagree without either looking broken.
    const base = awaitingLabel(session)
    const blocked = session.state_since ? ageOf(session.state_since, context.now) : null
    const held = blocked !== null && blocked >= AWAITING_NOTABLE_SECONDS
      ? ` ${formatRowDuration(blocked)}`
      : ''
    return {
      text: session.state_detail ? `${base}: ${session.state_detail}${held}` : `${base}${held}`,
      tone: 'warn',
    }
  }
  if (session.state === 'working') {
    return session.state_detail ? { text: session.state_detail, tone: 'default' } : null
  }
  if (session.state === 'idle') {
    // "ready · turn complete" is the state said twice. Only the idle reason that
    // contradicts "finished" is worth the room.
    return session.idle_reason === 'waiting_on_background'
      ? { text: 'background work running', tone: 'default' }
      : null
  }
  if (session.state === 'starting') return { text: 'starting agent…', tone: 'muted' }
  return null
}

function contextGauge(session: Session): { pct: number; peak: number } | null {
  if (!hasHarnessMeasurement(session.backend)) return null
  if (!(session.context_pct > 0)) return null
  return { pct: session.context_pct, peak: Math.max(session.context_pct, session.context_peak_pct || 0) }
}

/**
 * Context pressure for the indicator's arc. Separate from the token path because
 * the arc is drawn whether or not `context` is placed in a section.
 */
export function sessionContextArc(session: Session, config: SessionRowConfig): { pct: number; peak: number } | null {
  return config.context === 'arc' ? contextGauge(session) : null
}

function workingCwd(session: Session): string {
  return session.runtime_cwd || session.spawn_cwd || session.cwd || ''
}

const leafName = (path: string): string => path.split(/[\\/]/).filter(Boolean).pop() || path

function accountToken(session: Session): { text: string; title: string } | null {
  const entries = Object.entries(session.provider_account_hashes || {})
  if (!entries.length) return null
  const [provider, hash] = entries[0]
  if (!hash) return null
  // The daemon exposes the pseudonymous hash, not a friendly account name, so
  // this answers "is this the same account as that row?" and nothing more.
  return { text: hash.slice(0, 6), title: `${provider} account ${hash}` }
}

/**
 * Scope mark for branch-scoped Git tokens.
 *
 * Not gated on `gitGlyphs`, which is a preference about decorating branch names.
 * This one is load-bearing: without it a row carrying both diffs prints the same
 * `+312 -48` twice with no way to tell which is which.
 */
const BRANCH_SCOPE_MARK = '⎇'

/** Live sessions sharing this session's checkout, itself included; 0 when unknown. */
function checkoutShare(session: Session, context: SessionRowContext): number {
  const root = session.git?.root
  return root ? context.checkoutSessions[root] || 0 : 0
}

const sharedNote = (share: number): string =>
  share > 1 ? ` — shared checkout, ${share} live sessions report it` : ''

interface Candidate { token: RowToken; notable: boolean }

/**
 * Build one field's candidate token, plus whether it is currently notable.
 *
 * Returning both lets `notable` mode and `always` mode share one code path: a
 * field with no value renders under neither.
 */
function candidateFor(
  id: RowFieldId, session: Session, config: SessionRowConfig, context: SessionRowContext,
): Candidate | null {
  const priority = ROW_FIELD_BY_ID[id].priority
  const make = (partial: Omit<RowToken, 'id' | 'priority'>, notable: boolean): Candidate =>
    ({ token: { id, priority, ...partial }, notable })

  switch (id) {
    case 'title':
      return make({ kind: 'title', text: '' }, true)
    case 'glyph':
      return isAgentBackend(session.backend) ? make({ kind: 'glyph', text: session.backend }, true) : null
    case 'broadcast':
      return session.broadcast
        ? make({ kind: 'broadcast', text: '⇶', title: 'In the broadcast set — keystrokes mirror here while broadcast input is on' }, true)
        : null
    case 'badges': {
      const badges = session.pending ? [] : activityBadges(session)
      return badges.length
        ? make({ kind: 'badges', text: badges.map(badge => badge.label).join(', '), badges }, true)
        : null
    }
    case 'state':
      // Never notable on purpose: the indicator says it. Reachable only in `always`.
      return make({ kind: 'text', text: session.state, tone: 'muted' }, false)
    case 'detail': {
      const detail = detailText(session, context)
      return detail ? make({ kind: 'text', text: detail.text, tone: detail.tone, title: detail.text }, true) : null
    }
    case 'duration': {
      const duration = durationToken(session, context)
      return duration
        ? make({ kind: 'text', text: duration.text, title: duration.title }, durationIsNotable(session, duration.seconds))
        : null
    }
    case 'sincePrompt': {
      // Ended sessions are excluded: the question is about work in flight, and
      // an exited row already reports its lifetime.
      if (isEnded(session) || !session.last_human_prompt_at) return null
      const seconds = ageOf(session.last_human_prompt_at, context.now)
      if (seconds === null) return null
      const label = formatRowDuration(seconds)
      // Notable only when it disagrees with the turn — on a session whose turns
      // you open yourself, the two numbers track each other and the second one
      // is noise. An idle session is never notable here: nothing is running, so
      // "you asked an hour ago" describes no outstanding work.
      const turn = session.turn_started_at ? ageOf(session.turn_started_at, context.now) : null
      const notable = session.state !== 'idle'
        && turn !== null
        && seconds - turn >= PROMPT_GAP_NOTABLE_SECONDS
      return make(
        {
          kind: 'text',
          text: label,
          prefix: HUMAN_PROMPT_MARK,
          tone: 'muted',
          title: `you last prompted this session ${label} ago`,
        },
        notable,
      )
    }
    case 'idleFor': {
      if (session.state !== 'idle') return null
      if (!session.state_since) return null
      const seconds = ageOf(session.state_since, context.now)
      if (seconds === null) return null
      return make(
        { kind: 'text', text: formatRowDuration(seconds), tone: 'muted', title: `idle for ${formatRowDuration(seconds)}` },
        seconds >= IDLE_FOR_NOTABLE_SECONDS,
      )
    }
    case 'context': {
      // `arc` and `off` draw nothing here; the indicator owns the fact instead.
      if (config.context !== 'gauge' && config.context !== 'percent') return null
      const gauge = contextGauge(session)
      if (!gauge) return null
      const label = `${Math.round(gauge.pct * 100)}%`
      const notable = gauge.pct >= CONTEXT_NOTABLE_PCT
      return config.context === 'percent'
        ? make({ kind: 'text', text: label, title: `context ${label}`, tone: notable ? 'warn' : 'default' }, notable)
        : make({ kind: 'gauge', text: label, title: `context ${label}`, gauge }, notable)
    }
    case 'branch': {
      const branch = session.git?.branch
      if (!branch) return null
      // A worktree token already names the checkout; repeating a branch that
      // matches it spends the row's width saying the same word twice.
      const worktree = session.git?.worktree
      if (worktree && worktree === branch) return null
      const text = config.gitGlyphs ? `⎇ ${branch}` : branch
      const notable = branch !== context.defaultBranch[session.project_id]
      return make({ kind: 'text', text, title: `branch ${branch}` }, notable)
    }
    case 'worktree': {
      const worktree = session.git?.worktree
      if (!worktree) return null
      const text = config.gitGlyphs ? `⌂ ${worktree}` : worktree
      return make({ kind: 'text', text, title: `linked worktree ${worktree}` }, true)
    }
    case 'diff': {
      const added = session.git?.added, removed = session.git?.removed
      if (typeof added !== 'number' || typeof removed !== 'number') return null
      const share = checkoutShare(session, context)
      const title = `+${added} -${removed} uncommitted lines against HEAD${sharedNote(share)}`
      return make(
        { kind: 'diff', text: `+${added} -${removed}`, title, diff: { added, removed }, shared: share > 1 },
        added + removed > 0,
      )
    }
    case 'dirty': {
      const dirty = session.git?.dirty || 0
      const share = checkoutShare(session, context)
      return make(
        {
          kind: 'count', text: String(dirty), count: dirty, shared: share > 1,
          title: `${dirty} changed file${dirty === 1 ? '' : 's'} in the working tree${sharedNote(share)}`,
        },
        dirty > 0,
      )
    }
    // The branch-scoped pair. They answer "what has this work changed" where
    // `diff`/`dirty` answer "what is uncommitted", and the two diverge the
    // moment anything is committed — which is why a worktree-per-branch fleet
    // that commits as it goes reads +0 -0 on the working-tree fields alone.
    case 'compareDiff': {
      const added = session.git?.compare_added, removed = session.git?.compare_removed
      if (typeof added !== 'number' || typeof removed !== 'number') return null
      const share = checkoutShare(session, context)
      const base = session.git?.compare_ref || 'its base'
      const title = `+${added} -${removed} lines vs ${base}, committed and uncommitted${sharedNote(share)}`
      return make(
        {
          kind: 'diff', text: `+${added} -${removed}`, title, prefix: BRANCH_SCOPE_MARK,
          diff: { added, removed }, shared: share > 1,
        },
        added + removed > 0,
      )
    }
    case 'compareFiles': {
      const files = session.git?.compare_files
      if (typeof files !== 'number') return null
      const share = checkoutShare(session, context)
      const base = session.git?.compare_ref || 'its base'
      return make(
        {
          kind: 'count', text: String(files), count: files,
          prefix: BRANCH_SCOPE_MARK, shared: share > 1,
          title: `${files} file${files === 1 ? '' : 's'} changed vs ${base}${sharedNote(share)}`,
        },
        files > 0,
      )
    }
    case 'sync': {
      const ahead = session.git?.ahead || 0, behind = session.git?.behind || 0
      if (!ahead && !behind) return make({ kind: 'text', text: '↕0', tone: 'muted', title: 'in sync with upstream' }, false)
      const text = `${ahead ? `↑${ahead}` : ''}${behind ? `↓${behind}` : ''}`
      return make({ kind: 'text', text, title: `${ahead} ahead, ${behind} behind upstream` }, true)
    }
    case 'queue': {
      const depth = context.queueDepth[session.id] || 0
      return make(
        { kind: 'count', text: `⋮${depth}`, count: depth, title: `${depth} queued message${depth === 1 ? '' : 's'}` },
        depth > 0,
      )
    }
    case 'model': {
      const model = session.model
      if (!model) return null
      return make({ kind: 'text', text: displayModelName(model), title: `model ${model}` }, model !== context.defaultModel[session.project_id])
    }
    case 'account': {
      const account = accountToken(session)
      return account ? make({ kind: 'text', text: account.text, title: account.title, tone: 'muted' }, context.multiAccount) : null
    }
    case 'compactions': {
      const count = session.compaction_count || 0
      return make({ kind: 'text', text: `×${count}`, tone: 'muted', title: `compacted ${count} time${count === 1 ? '' : 's'}` }, count > 0)
    }
    case 'cost': {
      const cost = session.cost_usd || 0
      return make({ kind: 'text', text: `$${cost < 10 ? cost.toFixed(2) : Math.round(cost)}`, tone: 'muted', title: `spent $${cost.toFixed(4)}` }, cost >= COST_NOTABLE_USD)
    }
    case 'cwd': {
      const cwd = workingCwd(session)
      if (!cwd) return null
      const leaf = leafName(cwd)
      const root = session.project_root ? leafName(session.project_root) : undefined
      return make({ kind: 'text', text: leaf, tone: 'muted', title: cwd }, leaf !== root)
    }
    case 'exit': {
      if (!isEnded(session)) return null
      const text = session.state_detail ? `${session.state} · ${session.state_detail}` : session.state
      return make({ kind: 'text', text, tone: session.state === 'crashed' ? 'warn' : 'muted', title: text }, true)
    }
    default:
      return null
  }
}

function buildSection(
  slots: RowSlot[], align: RowAlign, separator: string,
  session: Session, config: SessionRowConfig, context: SessionRowContext,
): RowSection {
  const tokens: RowToken[] = []
  for (const slot of slots) {
    const candidate = candidateFor(slot.id, session, config, context)
    if (!candidate) continue
    if (slot.mode === 'notable' && !candidate.notable) continue
    tokens.push(candidate.token)
  }
  return { align, separator, tokens: shedLowestPriority(tokens, context.shed) }
}

/**
 * Drop the `count` lowest-priority tokens, preserving the configured order of
 * those that survive. The title is never shed — a row without one is not
 * identifiable — and shedding happens before the renderer sees the list, so
 * separators are still emitted only between tokens that actually draw.
 */
function shedLowestPriority(tokens: RowToken[], count: number): RowToken[] {
  if (count <= 0 || tokens.length <= 1) return tokens
  const sheddable = tokens.filter(token => token.kind !== 'title')
  if (!sheddable.length) return tokens
  const doomed = new Set(
    [...sheddable].sort((a, b) => a.priority - b.priority).slice(0, count),
  )
  return tokens.filter(token => !doomed.has(token))
}

/**
 * Every token the row should draw, grouped by line and alignment.
 *
 * The separator is returned rather than baked between tokens so the renderer can
 * emit it *between drawn tokens only* — a conditional field that hides must not
 * leave a dangling ` · ` behind it.
 */
export function buildSessionRowTokens(
  session: Session, config: SessionRowConfig, context: SessionRowContext,
): SessionRowTokens {
  const line = (which: RowLine): RowLineTokens => {
    const source = which === 'top' ? config.top : config.bottom
    const separator = SEPARATORS[source.separator].text
    return {
      left: buildSection(source.left, 'left', separator, session, config, context),
      right: buildSection(source.right, 'right', separator, session, config, context),
    }
  }
  return { top: line('top'), bottom: line('bottom') }
}

/** Identity-only projection: what a phone renders when parity is off. */
export function identityRowTokens(session: Session, config: SessionRowConfig): SessionRowTokens {
  const identityOnly = {
    ...config,
    top: { ...config.top, left: config.top.left.filter(slot => slot.id === 'glyph' || slot.id === 'title'), right: [] },
    bottom: { ...config.bottom, left: [], right: [] },
  }
  return buildSessionRowTokens(session, identityOnly, emptyRowContext(0))
}

const mostCommon = (values: Array<string | undefined | null>): string | undefined => {
  const counts = new Map<string, number>()
  for (const value of values) if (value) counts.set(value, (counts.get(value) || 0) + 1)
  let best: string | undefined
  let bestCount = 0
  for (const [value, count] of counts) if (count > bestCount) { best = value; bestCount = count }
  return best
}

/**
 * Derive the fleet comparisons the notability rules need, once per snapshot
 * rather than once per row: "differs from the project default" is a question
 * about the other rows, and asking it per row is quadratic.
 */
export function deriveRowContext(
  sessions: readonly Session[],
  queueDepth: Record<string, number>,
  now: number,
  shed = 0,
): SessionRowContext {
  const byProject = new Map<string, Session[]>()
  for (const session of sessions) {
    const list = byProject.get(session.project_id)
    if (list) list.push(session)
    else byProject.set(session.project_id, [session])
  }
  const defaultBranch: Record<string, string | undefined> = {}
  const defaultModel: Record<string, string | undefined> = {}
  for (const [projectId, list] of byProject) {
    defaultBranch[projectId] = mostCommon(list.map(session => session.git?.branch))
    defaultModel[projectId] = mostCommon(list.map(session => session.model))
  }
  const accounts = new Set<string>()
  // Ended sessions are excluded: the question a shared-checkout mark answers is
  // "how many rows in front of me are quoting this same number", and an exited
  // session is not competing for the attribution. A repository whose only live
  // session is this one is unambiguous however many corpses sit beside it.
  const checkoutSessions: Record<string, number> = {}
  for (const session of sessions) {
    for (const hash of Object.values(session.provider_account_hashes || {})) if (hash) accounts.add(hash)
    const root = session.git?.root
    if (root && !isEnded(session)) checkoutSessions[root] = (checkoutSessions[root] || 0) + 1
  }
  return {
    now, defaultBranch, defaultModel,
    multiAccount: accounts.size > 1, queueDepth, checkoutSessions, shed,
  }
}
