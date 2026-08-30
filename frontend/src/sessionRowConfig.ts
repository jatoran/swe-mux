// Configuration model for the sidebar session row.
//
// A row is two lines; each line has a left-aligned and a right-aligned section;
// each section is an ordered list of *slots*. A field that appears in no section
// is off — placement and visibility are one decision, not two, so a field can
// never be "enabled but nowhere".
//
// Every slot carries a `mode`:
//   - `always`  render whenever the field has a value
//   - `notable` render only when the value is worth reading (thresholds and
//               sibling comparisons live in sessionRowFields.ts)
// Most fields are noise on a healthy session, so `notable` is the interesting
// default: a sidebar where anything visible earned its place beats one where
// every row prints `master +0 -0 ⋮0`.
//
// The blob is stored server-side (deviceSettings `sessionRows`) in ONE canonical
// bucket rather than per device class, with `mobileFields` deciding whether the
// phone renders the configured sections or an identity-only row. The alternative
// — a second full configuration under the mobile profile — makes every edit a
// question ("which device did I just change?") for a layout the user
// overwhelmingly wants to keep in sync. There is consequently no such thing as a
// "mobile default" to set separately: a phone reads this same blob, and the two
// values that genuinely are per device class (`dotSizeMobile`, `mobileFields`)
// are fields *inside* it.

/** Every field the row can draw. Order here is the settings-UI catalog order. */
export type RowFieldId =
  | 'glyph' | 'title' | 'broadcast' | 'badges' | 'draft' | 'voice'
  | 'state' | 'detail' | 'duration' | 'sincePrompt' | 'idleFor' | 'worked' | 'context'
  | 'branch' | 'worktree' | 'diff' | 'dirty' | 'compareDiff' | 'compareFiles' | 'sync'
  | 'queue' | 'model' | 'account' | 'compactions' | 'cost' | 'cwd' | 'exit'
  | 'approvals'

export type RowFieldMode = 'notable' | 'always'
export type RowLine = 'top' | 'bottom'
export type RowAlign = 'left' | 'right'
export type DotShape = 'hexagon' | 'circle' | 'square'
/** How context pressure is drawn. One setting, because one fact must not render twice. */
export type ContextRender = 'off' | 'arc' | 'gauge' | 'percent'
/**
 * The band a context reading falls in, low to high.
 *
 * Named rather than numbered because every surface that colours context has to
 * agree on the *meaning* — the arc, the gauge cells and the percent text are one
 * scale drawn three ways, and a second copy of the comparison chain is how they
 * come to disagree at 59%.
 */
export type ContextBand = 'calm' | 'warn' | 'high' | 'crit'
/**
 * Where standing activity is drawn, on the same one-place rule as context.
 *
 * `row` spells it out as glyphs with counts; `indicator` collapses it to a pip on
 * the state indicator, which costs no row width at all and cannot be clipped, at
 * the price of saying only *that* something is standing (the kinds and counts
 * fall back to the tooltip).
 */
export type StandingRender = 'row' | 'indicator' | 'off'
export type DiffStyle = 'numbers' | 'bar'
export type CountStyle = 'numbers' | 'pips'

export interface RowSlot { id: RowFieldId; mode: RowFieldMode }

export interface RowLineConfig {
  left: RowSlot[]
  right: RowSlot[]
  separator: SeparatorId
}

/**
 * Bounds on the state indicator's box, in CSS pixels.
 *
 * The floor is where the hollow "standing" variant's stroke stops being legible
 * against the filled one; the ceiling is where a two-line row stops reading as a
 * list. The sidebar row's height is derived from the chosen size rather than
 * fixed, so anything inside these bounds fits without clipping.
 */
export const DOT_SIZE_MIN = 10
export const DOT_SIZE_MAX = 24
/**
 * Near the top of the range on purpose. The indicator is the row's only
 * always-drawn element and the only one carrying state, and everything else on
 * the row is expressed in terms of it — so the size that makes a fleet scannable
 * down a column of dots is larger than the size that merely fits the type.
 */
export const DEFAULT_DOT_SIZE_DESKTOP = 21
/**
 * Smaller than desktop, and the two are set independently because a physical
 * size is the one property a shared layout cannot express: the screens are held
 * at different distances. A phone row is read at arm's length and never hovered
 * for a tooltip, but it also has far less width to spend on a gutter, so it does
 * not simply inherit the desktop figure.
 */
export const DEFAULT_DOT_SIZE_MOBILE = 17

/**
 * Where the context ramp changes colour by default: 40 / 60 / 80.
 *
 * Deliberately louder than the 70/90 two-step this replaces. That ramp was tuned
 * so a row only spoke when compaction was imminent, which is the right shape for
 * a warning and the wrong one for a gauge — by the time it moved, the decision it
 * informs (finish this thread, or start a fresh one) had already been taken for
 * you. Four bands starting at 40% make the sidebar a reading of how much room the
 * fleet has left rather than an alarm, which is what the arc is looked at for.
 *
 * The cost is real and worth stating: a busy fleet now shows colour on most rows,
 * so the signal is the *distribution* down the column rather than the presence of
 * any one non-neutral row. Anyone who preferred the quieter reading sets these
 * back to 0.7 / 0.9 (and anything for the third) in the settings panel.
 */
export const DEFAULT_CONTEXT_WARN = 0.4
export const DEFAULT_CONTEXT_HIGH = 0.6
export const DEFAULT_CONTEXT_CRIT = 0.8

/** The narrowest gap the ramp keeps between two thresholds, in fractions. */
const CONTEXT_THRESHOLD_GAP = 0.01
/**
 * Bounds on a threshold. Zero is excluded because a band no reading can fall
 * below is not a band, and one is excluded because a band nothing can reach is
 * a colour that never draws.
 */
const CONTEXT_THRESHOLD_MIN = 0.01
const CONTEXT_THRESHOLD_MAX = 0.99

/**
 * Which band a reading falls in, given a normalized ramp.
 *
 * The single comparison chain every context rendering shares. Thresholds are
 * inclusive lower bounds, so a ramp at 40/60/80 puts exactly 0.8 in `crit` — the
 * number a user typed is the number the colour changes at, which is the only
 * reading of "80% is red" that survives being checked against the row.
 */
export function contextBand(pct: number, config: SessionRowConfig): ContextBand {
  if (pct >= config.contextCrit) return 'crit'
  if (pct >= config.contextHigh) return 'high'
  if (pct >= config.contextWarn) return 'warn'
  return 'calm'
}

/**
 * Stored-layout version. Bumped whenever a *new* field must reach layouts that
 * already exist: a stored blob is authoritative and an unplaced field is off, so
 * adding one to `defaultSessionRowConfig` alone reaches nobody who has ever
 * opened the settings panel.
 *
 * Rewriting the *shipped default* is the opposite case and deliberately does not
 * bump it. That a stored blob is authoritative is exactly the guarantee wanted
 * there: a device that has configured its rows keeps what it configured, and only
 * a device with no stored blob sees the new layout.
 */
export const ROW_CONFIG_VERSION = 3

export interface SessionRowConfig {
  version: typeof ROW_CONFIG_VERSION
  top: RowLineConfig
  bottom: RowLineConfig
  /** Shape of the state indicator and of any gauge drawn around it. */
  dotShape: DotShape
  /**
   * Indicator box in CSS pixels, per device class.
   *
   * The one part of the row configuration that is genuinely split desktop from
   * mobile: the layout is shared because the same person wants the same facts in
   * the same order on both screens, but a size that reads correctly on a monitor
   * at desk distance does not read correctly on a phone in one hand. Everything
   * around the indicator — the gutter column, the context ring, the stack thread,
   * the row height — is expressed in terms of it, so one number moves all of them
   * together.
   */
  dotSizeDesktop: number
  dotSizeMobile: number
  context: ContextRender
  /**
   * Where the context ramp changes colour, as fractions in (0, 1).
   *
   * Three numbers rather than four: the lowest band starts at zero and needs no
   * threshold to enter. They are held sorted and separated by
   * `normalizeContextThresholds`, so the renderers can compare against them in
   * order without re-checking that the ramp makes sense.
   */
  contextWarn: number
  contextHigh: number
  contextCrit: number
  standing: StandingRender
  diffStyle: DiffStyle
  countStyle: CountStyle
  /** Prefix git tokens with their glyph (⎇ / ⌂). Off keeps branch names bare. */
  gitGlyphs: boolean
  /** When false the phone renders identity only: indicator, provider mark, title. */
  mobileFields: boolean
}

export type SeparatorId =
  | 'none' | 'space' | 'dot' | 'bullet' | 'slash' | 'backslash'
  | 'pipe' | 'colon' | 'at' | 'dash' | 'tilde' | 'arrow'

/** Rendered text for each separator, including its surrounding spacing. */
export const SEPARATORS: Record<SeparatorId, { label: string; text: string }> = {
  none: { label: 'None', text: '' },
  space: { label: 'Space', text: ' ' },
  dot: { label: 'Middle dot ·', text: ' · ' },
  bullet: { label: 'Bullet •', text: ' • ' },
  slash: { label: 'Slash /', text: ' / ' },
  backslash: { label: 'Backslash \\', text: ' \\ ' },
  pipe: { label: 'Pipe |', text: ' | ' },
  colon: { label: 'Colon :', text: ' : ' },
  at: { label: 'At @', text: ' @ ' },
  dash: { label: 'Dash -', text: ' - ' },
  tilde: { label: 'Tilde ~', text: ' ~ ' },
  arrow: { label: 'Arrow ›', text: ' › ' },
}

export const SEPARATOR_IDS = Object.keys(SEPARATORS) as SeparatorId[]

/**
 * Characters a truncated value keeps before it stops being a value.
 *
 * Six is the floor rather than a target: below it a worktree reads `feat-t`,
 * which two sibling checkouts in the same fleet will share, so the token is
 * spending width to say something no longer distinguishing. A field with a mark
 * of its own collapses to it at that point instead; one without is dropped.
 */
export const ROW_MIN_CHARS = 6

/**
 * Gauge cells. Four is enough to compare rows at a glance and cheap to scan.
 *
 * Shared with the width ladder rather than owned by the renderer: the engine has
 * to know what a gauge costs to decide whether the line fits, and two copies of
 * the number would let the estimate drift away from the thing being drawn.
 */
export const GAUGE_CELLS = 4
/** Counts at or below this render as pips; above it, as a numeral. */
export const MAX_PIPS = 4

export interface RowFieldDescriptor {
  id: RowFieldId
  label: string
  /** What "notable" means for this field, shown beside the mode control. */
  notable: string
  /** Lower degrades first when the line is too narrow to hold every token. */
  priority: number
  /** Identity fields the top line is built from; never available to the bottom line. */
  identity?: boolean
  /**
   * The mark this field collapses to when its truncated value would stop being
   * one. Present only where the mark is unambiguous *within a row*: `model` has
   * no honest icon, because the provider mark is already the `glyph` field and
   * cannot tell opus from sonnet, so a model collapses to nothing and is dropped
   * instead. A field earns this the day it earns a mark, not before.
   *
   * Deliberately independent of the `gitGlyphs` setting: that decides whether
   * the mark is drawn *beside* the full value, which is decoration. This is the
   * field's identity at the width where its value no longer fits, which is
   * layout.
   */
  glyph?: string
  /** Truncation floor, in characters. Defaults to `ROW_MIN_CHARS`. */
  minChars?: number
}

/**
 * The field catalog. `priority` is also the degradation order: a line too narrow
 * for every token collapses the lowest-priority ones to their marks, and only
 * then starts dropping — rather than ellipsizing every token at once into a row
 * of prefixes.
 */
export const ROW_FIELDS: RowFieldDescriptor[] = [
  { id: 'title', label: 'Session title', notable: 'always has a value', priority: 100, identity: true },
  { id: 'glyph', label: 'Provider mark', notable: 'agent sessions only', priority: 95, identity: true },
  // The flag strip, in the order it reads on the row.
  { id: 'broadcast', label: 'Broadcast flag', notable: 'session is in the broadcast set', priority: 88, identity: true },
  { id: 'badges', label: 'Standing activity', notable: 'a loop, cron, subagent, or background task is live', priority: 90, identity: true },
  { id: 'draft', label: 'Unsent input', notable: 'text is sitting unsent in this session’s composer', priority: 92, identity: true },
  // Read aloud is the one setting in the strip whose effect you hear rather than
  // see, and its controls live in the voice panel rather than on the pane — so
  // without this mark the only way to know which sessions speak is to focus each
  // of them in turn and read the panel.
  { id: 'voice', label: 'Read aloud', notable: 'this session turns replies into audio', priority: 94, identity: true },
  // Sits in the flag strip and is on by default, unlike almost every other
  // field. The mode's entire effect is *removing* the notification an approval
  // would raise, so the fleet list is the only place a grant nobody remembers
  // setting can still be seen. A shed or opt-in badge would defeat the point.
  { id: 'approvals', label: 'Approval mode', notable: 'mux is answering approvals here', priority: 96, identity: true },
  { id: 'detail', label: 'What it is doing', notable: 'the harness reported a tool or a question', priority: 70 },
  { id: 'duration', label: 'Time', notable: 'past the per-state threshold', priority: 80 },
  {
    id: 'sincePrompt',
    label: 'Since your prompt',
    notable: 'the session is busy with something you asked for long ago',
    priority: 78,
  },
  {
    id: 'worked',
    label: 'Total working time',
    notable: 'the session has worked more than 10 minutes',
    priority: 76,
  },
  { id: 'context', label: 'Context used', notable: 'past the high threshold', priority: 75 },
  { id: 'branch', label: 'Git branch', notable: 'differs from the project default', priority: 60, glyph: '⎇' },
  { id: 'worktree', label: 'Worktree', notable: 'the checkout is a linked worktree', priority: 62, glyph: '⌂' },
  { id: 'diff', label: 'Lines changed (uncommitted)', notable: 'the working tree has changes', priority: 55 },
  { id: 'dirty', label: 'Changed files (uncommitted)', notable: 'at least one file is dirty', priority: 50 },
  { id: 'compareDiff', label: 'Lines changed (branch)', notable: 'the branch differs from its base', priority: 56 },
  { id: 'compareFiles', label: 'Changed files (branch)', notable: 'the branch differs from its base', priority: 51 },
  { id: 'sync', label: 'Ahead / behind', notable: 'diverged from upstream', priority: 45 },
  { id: 'queue', label: 'Queue depth', notable: 'something is queued', priority: 65 },
  { id: 'model', label: 'Model', notable: 'differs from the project default', priority: 40 },
  { id: 'account', label: 'Provider account', notable: 'more than one account is live', priority: 35 },
  { id: 'state', label: 'State word', notable: 'never — the indicator already says it', priority: 30 },
  { id: 'idleFor', label: 'Idle for', notable: 'idle longer than 30 minutes', priority: 25 },
  { id: 'compactions', label: 'Compactions', notable: 'the conversation has compacted', priority: 20 },
  { id: 'cost', label: 'Cost', notable: 'past $1', priority: 15 },
  { id: 'cwd', label: 'Working directory', notable: 'differs from the project root', priority: 10 },
  { id: 'exit', label: 'Exit reason', notable: 'the session ended', priority: 85 },
]

export const ROW_FIELD_BY_ID: Record<RowFieldId, RowFieldDescriptor> =
  Object.fromEntries(ROW_FIELDS.map(field => [field.id, field])) as Record<RowFieldId, RowFieldDescriptor>

const ALL_FIELD_IDS = new Set(ROW_FIELDS.map(field => field.id))

/**
 * Shipped default. Identity on the top line and nothing else; the bottom line
 * carries work facts left and the model right, with context drawn on the
 * indicator so it costs no row width.
 *
 * This is the layout swe-mux is actually operated with rather than a conservative
 * guess at one, transcribed from the primary install's stored blob. Everything
 * that changed from the earlier guess changed in the same direction: it turned
 * out that a row is read as *one shape per session* rather than as a sentence, so
 * a wide always-on time on the left and a wide always-on model on the right form
 * two columns down the sidebar, and the conditional fields between them read as
 * deviations from that shape. The earlier default made every bottom-line field
 * conditional, which is the opposite: nothing lines up, and a row's width changes
 * as the session works.
 *
 * Two consequences of transcribing a real layout, recorded so they read as
 * decisions rather than as omissions:
 *
 *   - `state` is placed in `notable` mode, which for this field means it never
 *     draws (the indicator already says the state). Placed-but-silent is the
 *     useful position: switching it to `always` is one click in the settings
 *     panel, where re-adding an unplaced field is a drag.
 *   - `context` is *not* placed, unlike the earlier default. It is drawn on the
 *     indicator as an arc, so it costs no row width — but a reader who then
 *     switches the context rendering to `gauge` or `percent` has to place the
 *     field themselves before anything appears.
 *
 * The top line's right section is the **flag strip**: presence-only marks that
 * are pinned to the row's right edge instead of queueing behind the title. Placed
 * after it they were clipped by exactly the rows that needed them — a title long
 * enough to fill the sidebar is a title long enough to hide everything following
 * it — and a flag whose whole content is "this is true" has nothing left to
 * ellipsize.
 *
 * `approvals` and `voice` lead that strip even though the transcribed blob holds
 * neither. That blob is at version 2 and predates both fields, so their absence
 * is this file's own "a field nobody could have configured yet is new rather than
 * declined" case — `placeVoiceFlag` re-adds `voice` to that very blob on every
 * read, and `approvals` reaches it through no migration at all, which is why it
 * is missing rather than declined. Both are also the marks that report a standing
 * mode whose entire effect is to make something *not* happen, so the fleet list
 * is the only place either can still be seen.
 */
export function defaultSessionRowConfig(): SessionRowConfig {
  return {
    version: ROW_CONFIG_VERSION,
    top: {
      left: [
        { id: 'glyph', mode: 'always' },
        { id: 'title', mode: 'always' },
      ],
      right: [
        { id: 'approvals', mode: 'notable' },
        { id: 'voice', mode: 'notable' },
        { id: 'broadcast', mode: 'notable' },
        { id: 'badges', mode: 'notable' },
        { id: 'draft', mode: 'always' },
      ],
      separator: 'none',
    },
    bottom: {
      left: [
        { id: 'duration', mode: 'always' },
        { id: 'worktree', mode: 'notable' },
        // Never notable by definition; see the note above.
        { id: 'state', mode: 'notable' },
        { id: 'queue', mode: 'notable' },
      ],
      right: [
        { id: 'model', mode: 'always' },
      ],
      separator: 'dot',
    },
    dotShape: 'hexagon',
    dotSizeDesktop: DEFAULT_DOT_SIZE_DESKTOP,
    dotSizeMobile: DEFAULT_DOT_SIZE_MOBILE,
    context: 'arc',
    contextWarn: DEFAULT_CONTEXT_WARN,
    contextHigh: DEFAULT_CONTEXT_HIGH,
    contextCrit: DEFAULT_CONTEXT_CRIT,
    standing: 'row',
    diffStyle: 'numbers',
    countStyle: 'numbers',
    gitGlyphs: true,
    mobileFields: true,
  }
}

export type RowPresetId = 'minimal' | 'default' | 'detailed'

export const ROW_PRESETS: Array<{ id: RowPresetId; label: string; description: string }> = [
  { id: 'minimal', label: 'Minimal', description: 'Title and time. Context on the indicator.' },
  { id: 'default', label: 'Standard', description: 'Time and model always; worktree and queue when notable.' },
  { id: 'detailed', label: 'Detailed', description: 'Everything the row can say, always.' },
]

export function presetConfig(id: RowPresetId): SessionRowConfig {
  const base = defaultSessionRowConfig()
  if (id === 'minimal') {
    return {
      ...base,
      bottom: { left: [{ id: 'detail', mode: 'notable' }], right: [{ id: 'duration', mode: 'always' }], separator: 'dot' },
    }
  }
  if (id === 'detailed') {
    return {
      ...base,
      bottom: {
        left: [
          { id: 'state', mode: 'always' }, { id: 'detail', mode: 'always' },
          { id: 'worktree', mode: 'always' }, { id: 'branch', mode: 'always' },
          { id: 'diff', mode: 'always' }, { id: 'dirty', mode: 'always' },
          { id: 'compareDiff', mode: 'always' }, { id: 'compareFiles', mode: 'notable' },
          { id: 'sync', mode: 'notable' }, { id: 'queue', mode: 'always' },
          { id: 'compactions', mode: 'notable' },
        ],
        right: [
          { id: 'model', mode: 'always' }, { id: 'account', mode: 'always' },
          { id: 'sincePrompt', mode: 'always' }, { id: 'duration', mode: 'always' },
        ],
        separator: 'dot',
      },
      context: 'gauge',
      gitGlyphs: true,
    }
  }
  return base
}

function normalizeSlots(value: unknown, seen: Set<RowFieldId>, identityOnly: boolean): RowSlot[] {
  if (!Array.isArray(value)) return []
  const slots: RowSlot[] = []
  for (const raw of value) {
    if (!raw || typeof raw !== 'object') continue
    const id = (raw as { id?: unknown }).id
    if (typeof id !== 'string' || !ALL_FIELD_IDS.has(id as RowFieldId)) continue
    const fieldId = id as RowFieldId
    // A field placed twice would render twice; the first placement wins so the
    // rest of the layout survives a hand-edited or half-migrated blob.
    if (seen.has(fieldId)) continue
    if (identityOnly && !ROW_FIELD_BY_ID[fieldId].identity) continue
    if (!identityOnly && ROW_FIELD_BY_ID[fieldId].identity) continue
    const mode = (raw as { mode?: unknown }).mode
    seen.add(fieldId)
    slots.push({ id: fieldId, mode: mode === 'always' ? 'always' : 'notable' })
  }
  return slots
}

function normalizeLine(value: unknown, seen: Set<RowFieldId>, identityOnly: boolean, fallback: RowLineConfig): RowLineConfig {
  if (!value || typeof value !== 'object') {
    for (const slot of [...fallback.left, ...fallback.right]) seen.add(slot.id)
    return { ...fallback, left: [...fallback.left], right: [...fallback.right] }
  }
  const raw = value as Record<string, unknown>
  const separator = typeof raw.separator === 'string' && raw.separator in SEPARATORS
    ? raw.separator as SeparatorId
    : fallback.separator
  return {
    left: normalizeSlots(raw.left, seen, identityOnly),
    right: normalizeSlots(raw.right, seen, identityOnly),
    separator,
  }
}

function pick<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value) ? value as T : fallback
}

/**
 * A stored ramp as a usable one: three fractions, in order, none touching.
 *
 * Clamped and reordered rather than rejected, on the same reasoning as
 * `normalizeDotSize`: a hand-edited or half-typed blob should render at the
 * nearest ramp this build can draw instead of silently snapping back to the
 * default and looking like a lost setting. Separation is enforced upward from
 * the low end because that is the direction the user was editing — dragging the
 * warn threshold past `high` means "warn later", not "abandon the edit".
 *
 * A missing value takes the default rather than the neighbour, so a blob written
 * before this setting existed adopts the shipped ramp whole.
 */
export function normalizeContextThresholds(
  raw: Record<string, unknown>, base: SessionRowConfig,
): Pick<SessionRowConfig, 'contextWarn' | 'contextHigh' | 'contextCrit'> {
  // Each threshold's own ceiling leaves room for the ones above it. Without
  // that, a `warn` pinned to the top pushes `high` and `crit` past 1 and the
  // clamp collapses them together — a band no reading can enter, which is
  // exactly what `CONTEXT_THRESHOLD_MAX` exists to prevent and what a push that
  // ignored it would reintroduce from the other direction.
  const read = (value: unknown, fallback: number, above: number): number => {
    const ceiling = CONTEXT_THRESHOLD_MAX - above * CONTEXT_THRESHOLD_GAP
    const usable = typeof value === 'number' && Number.isFinite(value) ? value : fallback
    return Math.max(CONTEXT_THRESHOLD_MIN, Math.min(ceiling, usable))
  }
  const warn = read(raw.contextWarn, base.contextWarn, 2)
  const high = Math.max(read(raw.contextHigh, base.contextHigh, 1), warn + CONTEXT_THRESHOLD_GAP)
  const crit = Math.max(read(raw.contextCrit, base.contextCrit, 0), high + CONTEXT_THRESHOLD_GAP)
  // Rounded so the stored numbers stay readable rather than 0.6000000000000001.
  const round = (value: number): number => Math.round(value * 1000) / 1000
  return { contextWarn: round(warn), contextHigh: round(high), contextCrit: round(crit) }
}

/**
 * A stored indicator size as a usable one: whole pixels, inside the bounds.
 *
 * Clamped rather than rejected, so a blob written by a future build with wider
 * bounds still renders at the nearest size this build can draw instead of
 * silently snapping back to the default and looking like a lost setting.
 */
export function normalizeDotSize(value: unknown, fallback: number): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return fallback
  return Math.max(DOT_SIZE_MIN, Math.min(DOT_SIZE_MAX, Math.round(value)))
}

/**
 * The flags version 2 relocated, in the order the strip read them then.
 *
 * Deliberately frozen rather than kept current: it is the *input to a
 * migration*, so a later flag added here would rewrite what version 2 meant for
 * a blob that has not run it yet. New flags reach stored layouts through their
 * own version step (`placeVoiceFlag`).
 */
const FLAG_STRIP: RowFieldId[] = ['broadcast', 'badges', 'draft']

/**
 * Bring a pre-flag-strip layout onto version 2.
 *
 * Changing `defaultSessionRowConfig` reaches nobody who has ever opened the
 * settings: a stored blob is authoritative, and an unplaced field is off, so a
 * new field would arrive invisible and a relocated one would stay where it was.
 * The two moves are made once, on the stored blob:
 *
 *   - flags already placed move to the top line's right section, in strip order.
 *     A flag the user had removed stays removed — this relocates a choice, it
 *     does not re-impose one.
 *   - `draft` is placed, because a field nobody could have configured yet is new
 *     rather than declined.
 */
function migrateToFlagStrip(config: SessionRowConfig): SessionRowConfig {
  const placed = new Map<RowFieldId, RowSlot>()
  for (const slot of [...config.top.left, ...config.top.right]) {
    if (FLAG_STRIP.includes(slot.id)) placed.set(slot.id, slot)
  }
  const strip: RowSlot[] = FLAG_STRIP.flatMap(id => {
    const existing = placed.get(id)
    if (existing) return [existing]
    return id === 'draft' ? [{ id, mode: 'always' as RowFieldMode }] : []
  })
  const keep = (slots: RowSlot[]) => slots.filter(slot => !FLAG_STRIP.includes(slot.id))
  return {
    ...config,
    top: {
      ...config.top,
      left: keep(config.top.left),
      right: [...strip, ...keep(config.top.right)],
    },
  }
}

/**
 * Bring a pre-read-aloud layout onto version 3 by placing the `voice` flag.
 *
 * A field nobody could have configured yet is new rather than declined, so it is
 * placed rather than left off — the same rule version 2 applied to `draft`. It
 * goes immediately after `approvals` when that is in the strip, because the two
 * report the same shape of fact: a standing mode this session was deliberately
 * put into, whose effect is otherwise invisible from the list.
 */
function placeVoiceFlag(config: SessionRowConfig): SessionRowConfig {
  const placed = [...config.top.left, ...config.top.right, ...config.bottom.left, ...config.bottom.right]
  if (placed.some(slot => slot.id === 'voice')) return config
  const slot: RowSlot = { id: 'voice', mode: 'notable' }
  const right = [...config.top.right]
  const after = right.findIndex(item => item.id === 'approvals')
  right.splice(after >= 0 ? after + 1 : 0, 0, slot)
  return { ...config, top: { ...config.top, right } }
}

/**
 * Coerce any stored blob into a usable configuration.
 *
 * Invariants enforced here rather than in the renderer, so the row never has to
 * ask whether its configuration makes sense:
 *   - the title is always placed (a row with no title is not a row)
 *   - identity fields live on the top line only, and non-identity fields never do
 *   - no field appears twice
 */
export function normalizeSessionRowConfig(value: unknown): SessionRowConfig {
  const base = defaultSessionRowConfig()
  if (!value || typeof value !== 'object') return base
  const raw = value as Record<string, unknown>
  const seen = new Set<RowFieldId>()
  const top = normalizeLine(raw.top, seen, true, base.top)
  const bottom = normalizeLine(raw.bottom, seen, false, base.bottom)
  if (!seen.has('title')) top.left.push({ id: 'title', mode: 'always' })
  const config: SessionRowConfig = {
    version: ROW_CONFIG_VERSION,
    top,
    bottom,
    dotShape: pick(raw.dotShape, ['hexagon', 'circle', 'square'] as const, base.dotShape),
    dotSizeDesktop: normalizeDotSize(raw.dotSizeDesktop, base.dotSizeDesktop),
    dotSizeMobile: normalizeDotSize(raw.dotSizeMobile, base.dotSizeMobile),
    context: pick(raw.context, ['off', 'arc', 'gauge', 'percent'] as const, base.context),
    ...normalizeContextThresholds(raw, base),
    standing: pick(raw.standing, ['row', 'indicator', 'off'] as const, base.standing),
    diffStyle: pick(raw.diffStyle, ['numbers', 'bar'] as const, base.diffStyle),
    countStyle: pick(raw.countStyle, ['numbers', 'pips'] as const, base.countStyle),
    gitGlyphs: typeof raw.gitGlyphs === 'boolean' ? raw.gitGlyphs : base.gitGlyphs,
    mobileFields: typeof raw.mobileFields === 'boolean' ? raw.mobileFields : base.mobileFields,
  }
  // Each step runs only for a blob written before it, and a blob from a *later*
  // build runs none of them: re-imposing an old migration on a layout that has
  // already moved past it is how a relocation becomes a loop.
  const stored = typeof raw.version === 'number' ? raw.version : 0
  const migrated = stored < 2 ? migrateToFlagStrip(config) : config
  return stored < 3 ? placeVoiceFlag(migrated) : migrated
}

/**
 * Whether a field is drawn at all under this configuration.
 *
 * Placement and visibility are one decision here (a field in no section is off),
 * so this is the question any *other* surface has to ask before drawing the same
 * fact — the tab strip picks its marks by hand rather than running the token
 * engine, and must not print what the sidebar row is configured to omit.
 */
export function isFieldPlaced(config: SessionRowConfig, id: RowFieldId): boolean {
  return [...config.top.left, ...config.top.right, ...config.bottom.left, ...config.bottom.right]
    .some(slot => slot.id === id)
}

/** Fields not currently placed anywhere, in catalog order, for the settings pool. */
export function unplacedFields(config: SessionRowConfig): RowFieldDescriptor[] {
  const placed = new Set<RowFieldId>([
    ...config.top.left, ...config.top.right, ...config.bottom.left, ...config.bottom.right,
  ].map(slot => slot.id))
  return ROW_FIELDS.filter(field => !placed.has(field.id))
}

export function lineConfig(config: SessionRowConfig, line: RowLine): RowLineConfig {
  return line === 'top' ? config.top : config.bottom
}

/** Immutably place a field, removing it from wherever it currently sits. */
export function placeField(
  config: SessionRowConfig, id: RowFieldId, line: RowLine, align: RowAlign, index?: number,
): SessionRowConfig {
  const descriptor = ROW_FIELD_BY_ID[id]
  // Identity fields are what the top line *is*; letting one drop to the bottom
  // line would put the title under itself.
  if (Boolean(descriptor.identity) !== (line === 'top')) return config
  const existing = [
    ...config.top.left, ...config.top.right, ...config.bottom.left, ...config.bottom.right,
  ].find(slot => slot.id === id)
  const slot: RowSlot = existing ?? { id, mode: descriptor.identity ? 'always' : 'notable' }
  const strip = (slots: RowSlot[]) => slots.filter(item => item.id !== id)
  const next: SessionRowConfig = {
    ...config,
    top: { ...config.top, left: strip(config.top.left), right: strip(config.top.right) },
    bottom: { ...config.bottom, left: strip(config.bottom.left), right: strip(config.bottom.right) },
  }
  const target = line === 'top' ? next.top : next.bottom
  const slots = [...target[align]]
  slots.splice(index === undefined ? slots.length : Math.max(0, Math.min(index, slots.length)), 0, slot)
  target[align] = slots
  return next
}

/** Immutably remove a field from the row. The title cannot be removed. */
export function removeField(config: SessionRowConfig, id: RowFieldId): SessionRowConfig {
  if (id === 'title') return config
  const strip = (slots: RowSlot[]) => slots.filter(item => item.id !== id)
  return {
    ...config,
    top: { ...config.top, left: strip(config.top.left), right: strip(config.top.right) },
    bottom: { ...config.bottom, left: strip(config.bottom.left), right: strip(config.bottom.right) },
  }
}

/** Immutably set one placed field's visibility mode. */
export function setFieldMode(config: SessionRowConfig, id: RowFieldId, mode: RowFieldMode): SessionRowConfig {
  const apply = (slots: RowSlot[]) => slots.map(slot => slot.id === id ? { ...slot, mode } : slot)
  return {
    ...config,
    top: { ...config.top, left: apply(config.top.left), right: apply(config.top.right) },
    bottom: { ...config.bottom, left: apply(config.bottom.left), right: apply(config.bottom.right) },
  }
}
