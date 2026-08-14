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
// phone renders the configured sections or the identity-only row it has always
// had. The alternative — a second full configuration under the mobile profile —
// makes every edit a question ("which device did I just change?") for a layout
// the user overwhelmingly wants to keep in sync.

/** Every field the row can draw. Order here is the settings-UI catalog order. */
export type RowFieldId =
  | 'glyph' | 'title' | 'broadcast' | 'badges' | 'draft'
  | 'state' | 'detail' | 'duration' | 'sincePrompt' | 'idleFor' | 'context'
  | 'branch' | 'worktree' | 'diff' | 'dirty' | 'compareDiff' | 'compareFiles' | 'sync'
  | 'queue' | 'model' | 'account' | 'compactions' | 'cost' | 'cwd' | 'exit'

export type RowFieldMode = 'notable' | 'always'
export type RowLine = 'top' | 'bottom'
export type RowAlign = 'left' | 'right'
export type DotShape = 'hexagon' | 'circle' | 'square'
/** How context pressure is drawn. One setting, because one fact must not render twice. */
export type ContextRender = 'off' | 'arc' | 'gauge' | 'percent'
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
export const DEFAULT_DOT_SIZE_DESKTOP = 15
/**
 * Mobile runs larger than desktop, not smaller. The indicator is the one part of
 * a phone row that is read at arm's length and never hovered for a tooltip, and
 * it is competing with a touch target rather than with a dense scannable list.
 */
export const DEFAULT_DOT_SIZE_MOBILE = 17

export interface SessionRowConfig {
  version: 2
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

export interface RowFieldDescriptor {
  id: RowFieldId
  label: string
  /** What "notable" means for this field, shown beside the mode control. */
  notable: string
  /** Lower sheds first when the sidebar is too narrow to hold every token. */
  priority: number
  /** Identity fields the top line is built from; never available to the bottom line. */
  identity?: boolean
}

/**
 * The field catalog. `priority` is also the width-shedding order: a narrow
 * sidebar drops whole tokens from the lowest priority up, rather than
 * ellipsizing every token at once into a row of prefixes.
 */
export const ROW_FIELDS: RowFieldDescriptor[] = [
  { id: 'title', label: 'Session title', notable: 'always has a value', priority: 100, identity: true },
  { id: 'glyph', label: 'Provider mark', notable: 'agent sessions only', priority: 95, identity: true },
  // The flag strip, in the order it reads on the row.
  { id: 'broadcast', label: 'Broadcast flag', notable: 'session is in the broadcast set', priority: 88, identity: true },
  { id: 'badges', label: 'Standing activity', notable: 'a loop, cron, subagent, or background task is live', priority: 90, identity: true },
  { id: 'draft', label: 'Unsent input', notable: 'text is sitting unsent in this session’s composer', priority: 92, identity: true },
  { id: 'detail', label: 'What it is doing', notable: 'the harness reported a tool or a question', priority: 70 },
  { id: 'duration', label: 'Time', notable: 'past the per-state threshold', priority: 80 },
  {
    id: 'sincePrompt',
    label: 'Since your prompt',
    notable: 'the session is busy with something you asked for long ago',
    priority: 78,
  },
  { id: 'context', label: 'Context used', notable: 'past 60%', priority: 75 },
  { id: 'branch', label: 'Git branch', notable: 'differs from the project default', priority: 60 },
  { id: 'worktree', label: 'Worktree', notable: 'the checkout is a linked worktree', priority: 62 },
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
 * carries work facts left and session facts right, almost all conditional, with
 * context drawn on the indicator so it costs no row width.
 *
 * The top line's right section is the **flag strip**: presence-only marks that
 * are pinned to the row's right edge instead of queueing behind the title. Placed
 * after it they were clipped by exactly the rows that needed them — a title long
 * enough to fill the sidebar is a title long enough to hide everything following
 * it — and a flag whose whole content is "this is true" has nothing left to
 * ellipsize.
 */
export function defaultSessionRowConfig(): SessionRowConfig {
  return {
    version: 2,
    top: {
      left: [
        { id: 'glyph', mode: 'always' },
        { id: 'title', mode: 'always' },
      ],
      right: [
        { id: 'broadcast', mode: 'notable' },
        { id: 'badges', mode: 'notable' },
        { id: 'draft', mode: 'always' },
      ],
      separator: 'none',
    },
    bottom: {
      left: [
        { id: 'worktree', mode: 'notable' },
        { id: 'branch', mode: 'notable' },
        { id: 'diff', mode: 'notable' },
        { id: 'queue', mode: 'notable' },
        { id: 'detail', mode: 'notable' },
      ],
      right: [
        { id: 'model', mode: 'notable' },
        { id: 'account', mode: 'notable' },
        // Silent until the turn stops being a fair answer to "how long has this
        // been going" — see `sessionRowFields.ts`. A fleet nobody is feeding
        // never draws it.
        { id: 'sincePrompt', mode: 'notable' },
        { id: 'duration', mode: 'always' },
        // Placed but silent while `context` is drawn on the indicator, so
        // switching the rendering to a gauge or a percentage puts it where it
        // was always configured to go instead of nowhere.
        { id: 'context', mode: 'always' },
      ],
      separator: 'dot',
    },
    dotShape: 'hexagon',
    dotSizeDesktop: DEFAULT_DOT_SIZE_DESKTOP,
    dotSizeMobile: DEFAULT_DOT_SIZE_MOBILE,
    context: 'arc',
    standing: 'row',
    diffStyle: 'numbers',
    countStyle: 'numbers',
    gitGlyphs: false,
    mobileFields: false,
  }
}

export type RowPresetId = 'minimal' | 'default' | 'detailed'

export const ROW_PRESETS: Array<{ id: RowPresetId; label: string; description: string }> = [
  { id: 'minimal', label: 'Minimal', description: 'Title and time. Context on the indicator.' },
  { id: 'default', label: 'Standard', description: 'Git, queue, model, and time — each only when notable.' },
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

/** Flags in the order the strip reads them, ahead of any field placed by hand. */
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
    version: 2,
    top,
    bottom,
    dotShape: pick(raw.dotShape, ['hexagon', 'circle', 'square'] as const, base.dotShape),
    dotSizeDesktop: normalizeDotSize(raw.dotSizeDesktop, base.dotSizeDesktop),
    dotSizeMobile: normalizeDotSize(raw.dotSizeMobile, base.dotSizeMobile),
    context: pick(raw.context, ['off', 'arc', 'gauge', 'percent'] as const, base.context),
    standing: pick(raw.standing, ['row', 'indicator', 'off'] as const, base.standing),
    diffStyle: pick(raw.diffStyle, ['numbers', 'bar'] as const, base.diffStyle),
    countStyle: pick(raw.countStyle, ['numbers', 'pips'] as const, base.countStyle),
    gitGlyphs: typeof raw.gitGlyphs === 'boolean' ? raw.gitGlyphs : base.gitGlyphs,
    mobileFields: typeof raw.mobileFields === 'boolean' ? raw.mobileFields : base.mobileFields,
  }
  return raw.version === 2 ? config : migrateToFlagStrip(config)
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
