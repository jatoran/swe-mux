import type { ShortcutPolicy } from '@continuity-editor/editor'

/**
 * Note-editor preferences: the configurable surface of the Continuity
 * editor, resolved from the daemon config into the two shapes it accepts.
 *
 * Continuity takes its configuration two ways and this module keeps that split
 * explicit: element properties/attributes (spellcheck, syntax, tab behavior,
 * shortcut policy and overrides, command rail) travel as props through the
 * React adapter, while typography and rail geometry are `--continuity-*` custom
 * properties set once on the document root and inherited by every editor.
 *
 * Colours are deliberately not here. `style.css` already maps the app palette
 * onto the editor's colour variables, so a second source for them would fight
 * the theme.
 */

export const NOTE_EDITOR_EVENT = 'mux:note-editor'

export type NoteEditorSettings = {
  spellcheck: boolean
  syntax: 'markdown' | 'plain'
  tabBehavior: 'indent' | 'focus'
  shortcutPolicy: ShortcutPolicy
  /** `auto` shows the rail on touch-primary devices, which is Continuity's own default. */
  commandRail: 'auto' | 'on' | 'off'
  /** Vertical rules at each enclosing indent level. Continuity defaults this off so an
   *  upgrade changes no host's appearance; we default it on, matching its desktop app. */
  indentGuides: 'on' | 'off'
  /** Chord → command, or null to release the chord to the browser (the library's unbind). */
  shortcutBindings: Readonly<Record<string, string | null>>
}

/** The two chords Continuity binds but flags non-browser-safe, reclaimed by default. */
export const DEFAULT_NOTE_SHORTCUT_OVERRIDES: Readonly<Record<string, string>> = {
  'mod+r': 'editor.toggle_bullet_at_line_start',
  'mod+e': 'markdown.toggle_task',
}

export const DEFAULT_NOTE_EDITOR_SETTINGS: NoteEditorSettings = {
  spellcheck: false,
  syntax: 'markdown',
  tabBehavior: 'indent',
  shortcutPolicy: 'browser-safe',
  commandRail: 'auto',
  indentGuides: 'on',
  shortcutBindings: { ...DEFAULT_NOTE_SHORTCUT_OVERRIDES },
}

// Mirrors the daemon's shapes in config.py. The chord grammar is Continuity's
// normalized form; assigning an unparseable chord throws inside the element's
// `shortcutBindings` setter, so anything malformed is dropped here rather than
// taking the editor down with it.
const CHORD = /^[a-z0-9+`\-=[\]\\;',./]{1,40}$/
const COMMAND = /^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/

/** Custom properties this module owns; anything else on the root is left alone. */
export const NOTE_EDITOR_CSS_VARS = [
  '--continuity-font-family',
  '--continuity-font-size',
  '--continuity-line-height',
  '--continuity-rail-height',
  '--continuity-rail-button-size',
] as const

const pick = <T extends string>(value: unknown, allowed: readonly T[], fallback: T): T =>
  typeof value === 'string' && (allowed as readonly string[]).includes(value) ? (value as T) : fallback

const bounded = (value: unknown, min: number, max: number): number =>
  typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max ? value : 0

export function noteEditorSettingsFrom(config: Record<string, unknown>): NoteEditorSettings {
  return {
    spellcheck: config.note_spellcheck === true,
    syntax: pick(config.note_syntax, ['markdown', 'plain'] as const, 'markdown'),
    tabBehavior: pick(config.note_tab_behavior, ['indent', 'focus'] as const, 'indent'),
    shortcutPolicy: pick(
      config.note_shortcut_policy,
      ['browser-safe', 'editor-first', 'none'] as const,
      'browser-safe',
    ),
    commandRail: pick(config.note_command_rail, ['auto', 'on', 'off'] as const, 'auto'),
    // Absent means a daemon older than this build, which should still show the guides
    // rather than silently landing on Continuity's off-by-default.
    indentGuides: config.note_indent_guides === false ? 'off' : 'on',
    shortcutBindings: noteShortcutBindings(config.note_shortcut_overrides),
  }
}

/**
 * Config carries the released state as `''` because TOML has no null; the
 * element wants an actual null. An absent/!object overlay means the daemon is
 * older than this build, which is the default overlay rather than none.
 */
export function noteShortcutBindings(overrides: unknown): Record<string, string | null> {
  if (overrides === undefined || overrides === null) return { ...DEFAULT_NOTE_SHORTCUT_OVERRIDES }
  if (typeof overrides !== 'object' || Array.isArray(overrides)) return {}
  const bindings: Record<string, string | null> = {}
  for (const [chord, command] of Object.entries(overrides as Record<string, unknown>)) {
    if (!CHORD.test(chord) || typeof command !== 'string') continue
    if (command !== '' && !COMMAND.test(command)) continue
    bindings[chord] = command === '' ? null : command
  }
  return bindings
}

/**
 * Typography and rail geometry, as the custom properties Continuity reads. A
 * value the user left at zero/blank is absent from the result: the editor's own
 * default then applies, so upgrading the editor can still move it.
 */
export function noteEditorCssVars(config: Record<string, unknown>): Record<string, string> {
  const vars: Record<string, string> = {}
  const family = typeof config.note_font_family === 'string' ? config.note_font_family.trim() : ''
  if (family) vars['--continuity-font-family'] = family
  const fontSize = bounded(config.note_font_size_px, 8, 48)
  if (fontSize) vars['--continuity-font-size'] = `${fontSize}px`
  const lineHeight = bounded(config.note_line_height, 1, 3)
  if (lineHeight) vars['--continuity-line-height'] = String(lineHeight)
  const button = bounded(config.note_rail_button_size_px, 32, 96)
  if (button) {
    vars['--continuity-rail-button-size'] = `${button}px`
    // The rail is its buttons plus Continuity's own 8px of chrome (48 → 56 by
    // default), and the same variable insets the content above it, so the two
    // move together or the last line hides behind the rail.
    vars['--continuity-rail-height'] = `${button + 8}px`
  }
  return vars
}

let current: NoteEditorSettings = DEFAULT_NOTE_EDITOR_SETTINGS

export function currentNoteEditorSettings(): NoteEditorSettings {
  return current
}

/**
 * Apply a freshly fetched config: write the custom properties, publish the
 * element-prop half to mounted editors, and make live editors re-measure.
 */
export function applyNoteEditorConfig(config: Record<string, unknown>): void {
  const vars = noteEditorCssVars(config)
  const root = document.documentElement.style
  let typographyChanged = false
  for (const name of NOTE_EDITOR_CSS_VARS) {
    const next = vars[name] ?? ''
    if (root.getPropertyValue(name) === next) continue
    typographyChanged = true
    if (next) root.setProperty(name, next)
    else root.removeProperty(name)
  }
  const settings = noteEditorSettingsFrom(config)
  if (JSON.stringify(settings) !== JSON.stringify(current)) {
    current = settings
    window.dispatchEvent(new CustomEvent<NoteEditorSettings>(NOTE_EDITOR_EVENT, { detail: settings }))
  }
  if (typographyChanged) remeasureLiveEditors()
}

/**
 * An editor pins its measured typography (`--continuity-character-width` and a
 * px line height) on its own frame from a ResizeObserver watching its host box.
 * Changing a font variable underneath it never moves that box, so the pinned
 * metrics — which caret placement and hanging indents are computed from — would
 * stay stale until the next reflow. Perturbing the box by a sub-pixel and
 * restoring it re-runs the measurement now.
 */
function remeasureLiveEditors(): void {
  for (const editor of document.querySelectorAll<HTMLElement>('continuity-editor')) {
    editor.style.paddingRight = '0.01px'
    requestAnimationFrame(() => editor.style.removeProperty('padding-right'))
  }
}

/** Storage key Continuity persists the command rail's arrangement under. */
export const RAIL_STORAGE_KEY = 'continuity-editor.command-rail'
const RAIL_LEADING_ORDER_MIGRATION_KEY = 'mux.note-rail-leading-order.v1'

/** The editing actions swe-mux keeps at the rail's leading edge. Remaining built-in and
 *  host actions retain their existing relative order after this block. */
export const NOTE_RAIL_LEADING_ORDER = [
  'mux:copy',
  'mux:paste',
  'indent',
  'outdent',
  'bullet',
  'checkbox',
  'move-line-up',
  'move-line-down',
] as const

/**
 * Install the swe-mux rail baseline once per browser profile.
 *
 * Continuity owns the arrangement format and every edit made in its gear panel. This migration
 * only lifts the requested core actions to the front, forces them visible, and leaves every
 * other known or future id in its existing relative order. The marker then gets out of the way
 * so later user reordering remains authoritative.
 */
export function ensureNoteRailArrangement(): void {
  try {
    if (localStorage.getItem(RAIL_LEADING_ORDER_MIGRATION_KEY) === '1') return
    const parsed = JSON.parse(localStorage.getItem(RAIL_STORAGE_KEY) || 'null')
    const stored = parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as { order?: unknown; disabled?: unknown }
      : {}
    const order = Array.isArray(stored.order)
      ? stored.order.filter((id): id is string => typeof id === 'string')
      : []
    const disabled = Array.isArray(stored.disabled)
      ? stored.disabled.filter((id): id is string => typeof id === 'string')
      : []
    const leading = new Set<string>(NOTE_RAIL_LEADING_ORDER)
    localStorage.setItem(RAIL_STORAGE_KEY, JSON.stringify({
      ...stored,
      order: [...NOTE_RAIL_LEADING_ORDER, ...order.filter(id => !leading.has(id))],
      disabled: disabled.filter(id => !leading.has(id)),
    }))
    localStorage.setItem(RAIL_LEADING_ORDER_MIGRATION_KEY, '1')
  } catch {
    // Storage denial leaves Continuity's session-local default intact.
  }
}

/**
 * Forget the rail arrangement saved by the editor's own gear panel. Live
 * editors hold theirs in memory, and only re-read storage when their
 * `rail-storage-key` changes - so flip the attribute and drop it again to make
 * them resolve the freshly seeded swe-mux baseline without a reload.
 */
export function resetNoteRailArrangement(): void {
  try {
    localStorage.removeItem(RAIL_STORAGE_KEY)
    localStorage.removeItem(RAIL_LEADING_ORDER_MIGRATION_KEY)
    ensureNoteRailArrangement()
  } catch {
    // Private-mode storage denial: the arrangement was never persisted anyway.
  }
  for (const editor of document.querySelectorAll('continuity-editor')) {
    editor.setAttribute('rail-storage-key', 'reset')
    editor.removeAttribute('rail-storage-key')
  }
}
