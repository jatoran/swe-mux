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

import { MODE_LABELS, effectiveApprovalMode } from './approvals.ts'
import {
  activityBadges, awaitingLabel, hasRunningActivity, type ActivityBadge,
} from './sessionStatus.ts'
import { hasHarnessMeasurement, isAgentBackend, isObservedHarness } from './harnessRegistry.ts'
import { displayModelName } from './modelDisplay.ts'
import type { Session, VoiceMode } from './types'
import { VOICE_MODE_OFF, resolveVoiceMode, voiceModeLabel, type VoiceModeDefaults } from './voiceMode.ts'
import {
  MAX_PIPS, ROW_FIELD_BY_ID, ROW_MIN_CHARS, SEPARATORS,
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
   * Sessions this device is holding an unsent composer draft for, as epoch
   * seconds, unioned with what the daemon reports.
   *
   * The mobile draft composer stages text in `localStorage` and never writes it
   * to the PTY, so the daemon cannot see it; the daemon's own ledger sees text
   * typed anywhere, including on another device. Neither is a superset of the
   * other, which is why the row asks both.
   */
  localDrafts: Record<string, number>
  /**
   * The global read-aloud switch and its per-session default.
   *
   * A session stores `voice_mode` only once somebody has chosen one, so what a
   * row must report is the *resolved* mode — which depends on two facts the
   * session record does not carry. They live here rather than on the session for
   * the same reason every other comparison does: they are one snapshot-wide
   * answer, not one per row.
   */
  voice: VoiceModeDefaults
  /**
   * Room each line has, in characters of that line's own type.
   *
   * Characters rather than pixels because the bottom line is monospace, so on
   * the line that carries almost every degradable field the unit is exact rather
   * than estimated. `rowBudget` derives it from a measured box; a zero budget
   * means "not measured yet" and degrades nothing.
   *
   * The fitting lives here rather than in a container query because the
   * separator invariant is a property of the *token list*: a CSS rule that hides
   * a token with `display:none` leaves the separator that JSX already emitted
   * beside it, so a narrowed row rendered as `· apply_patch` — a leading mark
   * belonging to a token that is no longer there.
   */
  budget: RowBudget
}

/** Room per line, in characters. Zero means unmeasured. */
export interface RowBudget { top: number; bottom: number }

export const EMPTY_ROW_BUDGET: RowBudget = { top: 0, bottom: 0 }

export function emptyRowContext(now = Date.now() / 1000): SessionRowContext {
  return {
    now, defaultBranch: {}, defaultModel: {}, multiAccount: false,
    queueDepth: {}, checkoutSessions: {}, localDrafts: {},
    voice: VOICE_MODE_OFF, budget: EMPTY_ROW_BUDGET,
  }
}

/**
 * Characters a line can hold, from the width of the row's *text column* and the
 * advance of that line's own font.
 *
 * Both numbers are measured (`sessionRowPrefs.useRowBudget`), never assumed. The
 * thresholds this replaced were compared against the width of the whole sidebar,
 * which overstates the room by the indicator gutter, the tree's padding, and the
 * scrollbar — between 49 and 63 px at the default width depending on a setting
 * (`--session-dot`) the thresholds could not see. The shipped default sidebar
 * therefore shed a token from every section before anybody dragged anything.
 */
export function rowBudget(textWidth: number, charPx: { top: number; bottom: number }): RowBudget {
  if (textWidth <= 0) return EMPTY_ROW_BUDGET
  return {
    top: charPx.top > 0 ? Math.floor(textWidth / charPx.top) : 0,
    bottom: charPx.bottom > 0 ? Math.floor(textWidth / charPx.bottom) : 0,
  }
}

export type RowTokenKind =
  'text' | 'diff' | 'gauge' | 'count' | 'badges' | 'glyph' | 'title' | 'broadcast' | 'draft' | 'voice'
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
  /**
   * Which read-aloud participation the mark reports. Kept on the token rather
   * than re-derived at render, because the two live modes are drawn differently:
   * `auto` speaks without being asked and is the one that carries the accent.
   */
  voice?: VoiceMode
  /**
   * Which rung of the width ladder this token is drawn at.
   *
   * `full` prints the value and lets CSS ellipsize it down to `minChars`; `icon`
   * replaces it with the field's mark. There is no `truncated` rung, because
   * truncation is continuous and CSS already does it exactly — a JS step would
   * only quantise what the browser measures precisely, and would have to be
   * recomputed on every resize to do it worse.
   */
  display: 'full' | 'icon'
  /** The mark `icon` draws. Absent on a field with no unambiguous one. */
  glyph?: string
  /** Characters this value keeps before the `icon` rung is preferable. */
  minChars: number
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
 * How long the request in flight has been running, on a session whose root turn
 * has ended but whose work has not.
 *
 * A harness that dispatches background agents closes its turn to hand off. The
 * state is genuinely `idle` — you can type, delivery is safe, and the ring and
 * badges say an agent is engaged — but both of the row's usual clocks stop:
 * `turn_started_at` goes away, and `last_turn_ms` freezes at the length of the
 * *dispatching* turn. Reporting that as "the time" says a request 80 minutes in
 * has taken 10 minutes, and it can shrink as the run continues, because every
 * phase ends with a short main-loop turn that overwrites the measurement.
 *
 * `running_work_since` is the daemon's answer and the one to prefer.
 * `last_human_prompt_at` is the fallback for records written before it existed
 * and for sessions adopted mid-flight; it answers a slightly different question
 * ("since you asked" rather than "since the work started"), which is why it is
 * second and why the title says which one is being read.
 *
 * Null whenever the question does not apply, so the caller keeps the existing
 * behaviour rather than inventing a number: nothing is running, or no anchor
 * survived, or the clocks disagree by more than `ageOf` will correct.
 */
function runningWorkAge(session: Session, now: number): { seconds: number; anchor: 'work' | 'prompt' } | null {
  if (isEnded(session) || !hasRunningActivity(session)) return null
  const since = session.running_work_since
  if (typeof since === 'number' && since > 0) {
    const seconds = ageOf(since, now)
    return seconds === null ? null : { seconds, anchor: 'work' }
  }
  const prompted = session.last_human_prompt_at
  if (typeof prompted === 'number' && prompted > 0) {
    const seconds = ageOf(prompted, now)
    return seconds === null ? null : { seconds, anchor: 'prompt' }
  }
  return null
}

/**
 * The span the duration column is currently measuring, or null when it is
 * measuring nothing live.
 *
 * Exists so `sincePrompt` can decide whether it would be saying the same thing
 * twice without re-deriving which branch `durationToken` took. An idle session
 * with running work is now a live measurement, which it was not before.
 */
function liveDurationAge(session: Session, context: SessionRowContext): number | null {
  if (isEnded(session)) return null
  if (session.turn_started_at) {
    return ageOf(session.turn_started_at, session.interrupt_pending_at ?? context.now)
  }
  return runningWorkAge(session, context.now)?.seconds ?? null
}

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
 * re-renders on no clock at all. The exception is a ready session with *running*
 * work, which is not settled and whose last turn is a fragment of a request
 * still in flight; it is aged from `runningWorkAge` instead, and is the only
 * ready row whose token changes between ticks.
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
    // Running work outranks the finished turn. The turn really did end, but it
    // ended to hand off, and "last turn took 10m" on a request 80 minutes deep
    // is the finished fragment standing in for the whole.
    const running = runningWorkAge(session, context.now)
    if (running) {
      const label = formatRowDuration(running.seconds)
      const title = running.anchor === 'work'
        ? `work has been running ${label} — the turn ended, its agents did not`
        : `${label} since you prompted — the turn ended, its agents did not`
      return { text: label, title, seconds: running.seconds }
    }
    if (typeof session.last_turn_ms !== 'number' || session.last_turn_ms < MIN_REPORTABLE_TURN_MS) return null
    const seconds = session.last_turn_ms / 1000
    return { text: formatRowDuration(seconds), title: `last turn took ${formatRowDuration(seconds)}`, seconds }
  }
  if (session.turn_started_at) {
    const effectiveNow = session.interrupt_pending_at ?? context.now
    const seconds = ageOf(session.turn_started_at, effectiveNow)
    if (seconds === null) return null
    const title = session.interrupt_pending_at
      ? `turn ran ${formatRowDuration(seconds)} before interruption was requested`
      : `this turn has run ${formatRowDuration(seconds)}`
    return { text: formatRowDuration(seconds), title, seconds }
  }
  if (!session.state_since) return null
  const seconds = ageOf(session.state_since, context.now)
  if (seconds === null) return null
  return { text: formatRowDuration(seconds), title: `${session.state} for ${formatRowDuration(seconds)}`, seconds }
}

function durationIsNotable(session: Session, seconds: number): boolean {
  if (isEnded(session)) return true
  // Idle-with-running-work is measuring live work, so it takes the working
  // threshold: the low last-turn one exists because a *finished* turn is worth
  // reporting sooner than a running one is worth interrupting for.
  if (session.state === 'idle' && !hasRunningActivity(session)) {
    return seconds >= LAST_TURN_NOTABLE_SECONDS
  }
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

/**
 * Standing activity for the indicator's pip, and the label that explains it.
 *
 * The counterpart of `sessionContextArc`: the same fact the `badges` field
 * draws, in the rendering that costs no row width. Returns null in every mode
 * but `indicator`, so the two renderings can never both be on.
 */
export function sessionStandingMark(
  session: Session | undefined, config: SessionRowConfig,
): { label: string } | null {
  if (!session || session.pending || config.standing !== 'indicator') return null
  const badges = activityBadges(session)
  return badges.length ? { label: badges.map(badge => badge.label).join(' · ') } : null
}

/**
 * When this session's composer was last known to hold unsent text, or null.
 *
 * Zero is a real answer here — a device-local draft with no usable timestamp —
 * and is distinct from null, which is "nothing is sitting there". An ended
 * session has no composer, whatever the last thing typed into it was.
 */
function unsentInputSince(session: Session, context: SessionRowContext): number | null {
  if (isEnded(session)) return null
  const local = context.localDrafts[session.id]
  const daemon = session.unsent_input?.since
  const stamps = [local, daemon].filter(
    (value): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0,
  )
  if (!stamps.length) return null
  // The oldest, because the question the row answers is "how long has something
  // been sitting here": a phone draft from yesterday is not made recent by a
  // keystroke on the desktop a minute ago.
  const dated = stamps.filter(value => value > 0)
  return dated.length ? Math.min(...dated) : 0
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
  const field = ROW_FIELD_BY_ID[id]
  // Every token starts on the top rung; `fitSection` is what moves it down. The
  // ladder's shape belongs to the field, so it is stamped on here rather than
  // looked up again at every step of the fit.
  const make = (
    partial: Omit<RowToken, 'id' | 'priority' | 'display' | 'glyph' | 'minChars'>,
    notable: boolean,
  ): Candidate => ({
    token: {
      id, priority: field.priority, display: 'full',
      glyph: field.glyph, minChars: field.minChars ?? ROW_MIN_CHARS,
      ...partial,
    },
    notable,
  })

  switch (id) {
    case 'title':
      return make({ kind: 'title', text: '' }, true)
    case 'glyph':
      return isAgentBackend(session.backend) ? make({ kind: 'glyph', text: session.backend }, true) : null
    case 'broadcast':
      return session.broadcast
        ? make({ kind: 'broadcast', text: '⇶', title: 'In the broadcast set — keystrokes mirror here while broadcast input is on' }, true)
        : null
    case 'voice': {
      // A pending terminal has no conversation to read from, and a shell has no
      // replies at all: read aloud is an agent-session fact, so a mark on either
      // would report a setting that cannot take effect.
      if (session.pending || !isAgentBackend(session.backend)) return null
      const mode = resolveVoiceMode(session, context.voice)
      if (mode === 'off') return null
      return make({
        kind: 'voice',
        text: `read aloud: ${voiceModeLabel(mode)}`,
        title: mode === 'auto'
          ? 'Read aloud · every completed reply becomes audio automatically · change it in the voice panel’s tts tab'
          : 'Read aloud · on demand: audio is made when you ask for it · change it in the voice panel’s tts tab',
        voice: mode,
      }, true)
    }
    case 'badges': {
      // `indicator` moves the same fact onto the state indicator and `off`
      // withdraws it; either way the row must not also print it.
      if (config.standing !== 'row') return null
      const badges = session.pending ? [] : activityBadges(session)
      return badges.length
        ? make({ kind: 'badges', text: badges.map(badge => badge.label).join(', '), badges }, true)
        : null
    }
    case 'approvals': {
      // Reads the mode that is actually in force, not the stored one: a grant
      // that expired or was made against a replaced conversation applies as
      // `wait`, and a badge claiming otherwise would be the sidebar asserting
      // authority the daemon has already dropped.
      if (session.pending) return null
      const mode = effectiveApprovalMode(session, context.now)
      if (mode === 'wait') return null
      const answered = session.approval_policy?.auto_approved ?? 0
      return make(
        {
          kind: 'text',
          text: mode === 'allow_all' ? 'auto ALL' : 'auto',
          tone: 'warn',
          title:
            `swe-mux is answering this conversation's approval requests (${MODE_LABELS[mode]})` +
            `; ${answered} answered so far`,
        },
        true,
      )
    }
    case 'draft': {
      const since = unsentInputSince(session, context)
      if (since === null) return null
      const age = since > 0 ? ageOf(since, context.now) : null
      return make(
        {
          kind: 'draft',
          text: 'unsent input',
          title: age === null
            ? 'unsent text is sitting in this session’s composer'
            : `unsent text is sitting in this session’s composer, from ${formatRowDuration(age)} ago`,
        },
        true,
      )
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
      // Notable only when it disagrees with whatever the duration column is
      // measuring — on a session whose turns you open yourself, the two numbers
      // track each other and the second one is noise. An idle session with
      // nothing running is never notable: "you asked an hour ago" describes no
      // outstanding work. An idle session with running work is, because there
      // the duration column is a live measurement and the two can genuinely part.
      const live = liveDurationAge(session, context)
      const notable = live !== null && seconds - live >= PROMPT_GAP_NOTABLE_SECONDS
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
  return { align, separator, tokens }
}

/**
 * Marks and cell strips, in characters of the line they sit on.
 *
 * Estimates, and only for the tokens that are not text: the bottom line is
 * monospace, so a text token's cost is its length exactly, and CSS is the
 * backstop for whatever these few miss.
 */
const MARK_COST = 2
const GAUGE_COST = 4
const DIFF_BAR_COST = 5
/** The mandatory space between the two sections (`.row-section.right` padding). */
const SECTION_GAP = 2

function tokenCost(token: RowToken, config: SessionRowConfig): number {
  const prefix = token.prefix ? token.prefix.length + 1 : 0
  if (token.display === 'icon') return prefix + (token.glyph?.length ?? 1)
  switch (token.kind) {
    case 'title':
      // The name is always costed at the floor it ellipsizes to. It is the top
      // line's yielding token by construction, and costing it in full would make
      // every long-named row look overfull and start deleting facts that fit.
      return token.minChars
    case 'glyph': case 'broadcast': case 'draft': case 'voice':
      return MARK_COST
    case 'badges':
      return Math.max(1, token.badges?.length ?? 1) * MARK_COST
    case 'gauge':
      return GAUGE_COST
    case 'diff':
      return prefix + (config.diffStyle === 'bar' ? DIFF_BAR_COST : token.text.length)
    case 'count':
      return prefix + (config.countStyle === 'pips' && (token.count ?? 0) <= MAX_PIPS
        ? Math.max(1, token.count ?? 0)
        : token.text.length)
    default:
      return prefix + token.text.length
  }
}

/**
 * What a section costs once CSS has taken everything it is allowed to take.
 *
 * Exactly ONE token per section ellipsizes, and which one is a CSS fact this has
 * to agree with: the left section's last token and the right section's first —
 * the ones furthest from the edge the section is anchored to. Its siblings are
 * `flex:none`, so costing them at a floor they can never reach would let the
 * engine believe a line fits that visibly does not.
 *
 * This is what puts a truncation rung *below* the full value and *above* the
 * mark: while the line still fits with its yielding token ellipsized, nothing is
 * collapsed or dropped at all, and the browser truncates continuously — exactly,
 * at every intermediate pixel, which no JS step can match.
 */
function sectionCost(
  tokens: readonly RowToken[], separator: string, config: SessionRowConfig, align: RowAlign,
): number {
  if (!tokens.length) return 0
  const yielding = align === 'left' ? tokens.length - 1 : 0
  let total = (tokens.length - 1) * separator.length
  for (const [index, token] of tokens.entries()) {
    const full = tokenCost(token, config)
    const prefix = token.prefix ? token.prefix.length + 1 : 0
    total += index === yielding && token.kind === 'text' && token.display === 'full'
      ? Math.min(full, prefix + token.minChars)
      : full
  }
  return total
}

/**
 * Degrade the line's lowest-priority tokens until it fits `budget` characters.
 *
 * The ladder has three rungs and they are tried in this order, which is the whole
 * design:
 *
 *  1. **truncated** — CSS ellipsizes the yielding token down to `ROW_MIN_CHARS`.
 *     Nothing here does anything; `sectionCost` merely accounts for it. The
 *     browser measures the available space exactly and truncates at every
 *     intermediate pixel, which a JS step could only quantise, and worse.
 *  2. **icon** — a token with a mark of its own collapses to it. An icon costs two
 *     characters against a value's ten or twenty, so collapsing the line is far
 *     cheaper than deleting from it and keeps every placed fact on screen, which
 *     is the point of having placed the field.
 *  3. **dropped** — for a field with no honest mark, and for a line so narrow that
 *     even the marks do not fit.
 *
 * Within rungs 2 and 3 the order is ascending priority, so the field the reader
 * ranked lowest is the first to lose its value and the first to leave.
 *
 * The line is fitted as a whole rather than section by section, because the two
 * sections share one line and a per-section budget would be a guess at how CSS
 * splits it. Which section *yields* is CSS's decision and is the opposite of what
 * it used to be: the right section is laid out first and the left one ellipsizes
 * into what remains, so a value the reader placed on the right can no longer be
 * pushed off the row's edge by a long worktree name on the left.
 *
 * Two invariants bound it:
 *  - identity tokens never degrade, so the title, the provider mark, and the flag
 *    strip survive every width;
 *  - a section is never emptied while it still holds a token. The count-based
 *    shedding this replaced had no such floor — a two-token section at shed 2
 *    lost both — so a sidebar dragged to 230 px rendered a blank bottom line, and
 *    deleted an `always`-mode field to do it.
 */
function fitLine(line: RowLineTokens, budget: number, config: SessionRowConfig): RowLineTokens {
  const separator = line.left.separator
  let entries: Array<{ align: RowAlign; token: RowToken }> = [
    ...line.left.tokens.map(token => ({ align: 'left' as RowAlign, token })),
    ...line.right.tokens.map(token => ({ align: 'right' as RowAlign, token })),
  ]
  if (budget <= 0 || entries.length <= 1) return line

  const overflow = () => {
    const left = entries.filter(entry => entry.align === 'left').map(entry => entry.token)
    const right = entries.filter(entry => entry.align === 'right').map(entry => entry.token)
    const gap = left.length && right.length ? SECTION_GAP : 0
    return sectionCost(left, separator, config, 'left')
      + sectionCost(right, separator, config, 'right')
      + gap - budget
  }
  const rebuild = (): RowLineTokens => ({
    left: { ...line.left, tokens: entries.filter(entry => entry.align === 'left').map(entry => entry.token) },
    right: { ...line.right, tokens: entries.filter(entry => entry.align === 'right').map(entry => entry.token) },
  })
  if (overflow() <= 0) return line

  // Ids, not token objects: the collapse pass replaces tokens rather than
  // mutating them, so an object captured here would be stale by the drop pass.
  // A field occupies at most one slot, so its id identifies it on the line.
  const order = [...line.left.tokens, ...line.right.tokens]
    .filter(token => !ROW_FIELD_BY_ID[token.id]?.identity)
    .sort((a, b) => a.priority - b.priority)
    .map(token => token.id)

  for (const id of order) {
    if (overflow() <= 0) return rebuild()
    const found = entries.find(entry => entry.token.id === id)
    if (!found || found.token.display === 'icon') continue
    const { glyph, minChars, kind } = found.token
    if (!glyph || kind !== 'text') continue
    // Against the truncation floor, not the full value: the rung below this one is
    // "ellipsized to `minChars`", so a mark no shorter than that floor buys the
    // line nothing and only costs the reader the value.
    if (glyph.length >= minChars) continue
    entries = entries.map(entry =>
      (entry.token.id === id ? { ...entry, token: { ...entry.token, display: 'icon' as const } } : entry))
  }
  for (const id of order) {
    if (overflow() <= 0) return rebuild()
    const align = entries.find(entry => entry.token.id === id)?.align
    if (!align) continue
    if (entries.filter(entry => entry.align === align).length <= 1) continue
    entries = entries.filter(entry => entry.token.id !== id)
  }
  return rebuild()
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
    return fitLine(
      {
        left: buildSection(source.left, 'left', separator, session, config, context),
        right: buildSection(source.right, 'right', separator, session, config, context),
      },
      which === 'top' ? context.budget.top : context.budget.bottom,
      config,
    )
  }
  return { top: line('top'), bottom: line('bottom') }
}

/**
 * Identity-only projection: what a phone renders when parity is off.
 *
 * The flag strip survives it. Identity means "which session is this and what is
 * true of it right now", and a phone is where an unsent draft is most likely to
 * have been left behind — a projection that dropped the marks would be silent on
 * exactly the device that stages text and walks away. The strip costs a few
 * pixels at the row's edge and nothing at all when nothing is flagged.
 */
export function identityRowTokens(
  session: Session, config: SessionRowConfig, context = emptyRowContext(0),
): SessionRowTokens {
  const identityOnly = {
    ...config,
    top: { ...config.top, left: config.top.left.filter(slot => slot.id === 'glyph' || slot.id === 'title') },
    bottom: { ...config.bottom, left: [], right: [] },
  }
  return buildSessionRowTokens(session, identityOnly, context)
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
  budget: RowBudget = EMPTY_ROW_BUDGET,
  localDrafts: Record<string, number> = {},
  voice: VoiceModeDefaults = VOICE_MODE_OFF,
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
    multiAccount: accounts.size > 1, queueDepth, checkoutSessions, localDrafts, voice, budget,
  }
}
