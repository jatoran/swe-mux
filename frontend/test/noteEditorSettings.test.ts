import assert from 'node:assert/strict'
import test from 'node:test'
import {
  DEFAULT_NOTE_EDITOR_SETTINGS,
  DEFAULT_NOTE_SHORTCUT_OVERRIDES,
  noteEditorCssVars,
  noteEditorSettingsFrom,
  noteShortcutBindings,
} from '../src/noteEditorSettings.ts'

const config = (overrides: Record<string, unknown> = {}): Record<string, unknown> => ({
  note_spellcheck: false,
  note_syntax: 'markdown',
  note_tab_behavior: 'indent',
  note_shortcut_policy: 'browser-safe',
  note_command_rail: 'auto',
  note_indent_guides: true,
  note_font_family: '',
  note_font_size_px: 0,
  note_line_height: 0,
  note_rail_button_size_px: 0,
  note_shortcut_overrides: { ...DEFAULT_NOTE_SHORTCUT_OVERRIDES },
  ...overrides,
})

test('a default config resolves to the editor defaults', () => {
  assert.deepEqual(noteEditorSettingsFrom(config()), DEFAULT_NOTE_EDITOR_SETTINGS)
  assert.deepEqual(noteEditorCssVars(config()), {})
})

test('element settings follow the config', () => {
  const settings = noteEditorSettingsFrom(config({
    note_spellcheck: true,
    note_syntax: 'plain',
    note_tab_behavior: 'focus',
    note_shortcut_policy: 'editor-first',
    note_command_rail: 'on',
  }))
  assert.equal(settings.spellcheck, true)
  assert.equal(settings.syntax, 'plain')
  assert.equal(settings.tabBehavior, 'focus')
  assert.equal(settings.shortcutPolicy, 'editor-first')
  assert.equal(settings.commandRail, 'on')
})

test('indent guides are on unless the config says otherwise', () => {
  assert.equal(noteEditorSettingsFrom(config()).indentGuides, 'on')
  assert.equal(noteEditorSettingsFrom(config({ note_indent_guides: false })).indentGuides, 'off')
  // Only an explicit false turns them off: a daemon too old to know the key, or a junk
  // value, must not silently land on Continuity's own off-by-default.
  assert.equal(noteEditorSettingsFrom(config({ note_indent_guides: undefined })).indentGuides, 'on')
  assert.equal(noteEditorSettingsFrom(config({ note_indent_guides: 'no' })).indentGuides, 'on')
})

test('an out-of-range or unknown value falls back instead of reaching the editor', () => {
  const settings = noteEditorSettingsFrom(config({
    note_syntax: 'rich',
    note_tab_behavior: 42,
    note_shortcut_policy: 'editor-only',
    note_command_rail: null,
  }))
  assert.equal(settings.syntax, 'markdown')
  assert.equal(settings.tabBehavior, 'indent')
  assert.equal(settings.shortcutPolicy, 'browser-safe')
  assert.equal(settings.commandRail, 'auto')
})

test('a daemon older than these settings still yields working defaults', () => {
  assert.deepEqual(noteEditorSettingsFrom({ theme: 'dark' }), DEFAULT_NOTE_EDITOR_SETTINGS)
})

test('typography is emitted only when it overrides the editor default', () => {
  assert.deepEqual(noteEditorCssVars(config({ note_font_family: '  Iosevka  ', note_font_size_px: 18 })), {
    '--continuity-font-family': 'Iosevka',
    '--continuity-font-size': '18px',
  })
  assert.deepEqual(noteEditorCssVars(config({ note_line_height: 1.9 })), {
    '--continuity-line-height': '1.9',
  })
})

test('the rail height follows its button size so content is not hidden behind it', () => {
  assert.deepEqual(noteEditorCssVars(config({ note_rail_button_size_px: 56 })), {
    '--continuity-rail-button-size': '56px',
    '--continuity-rail-height': '64px',
  })
})

test('typography outside the daemon-validated range is dropped, not clamped', () => {
  // Clamping would silently disagree with the value the settings form shows.
  assert.deepEqual(noteEditorCssVars(config({ note_font_size_px: 400, note_line_height: 12 })), {})
  assert.deepEqual(noteEditorCssVars(config({ note_font_size_px: '18' })), {})
})

test('a released chord crosses from config as null, which is what unbinds it', () => {
  assert.deepEqual(noteShortcutBindings({ 'mod+r': '', 'mod+e': 'markdown.toggle_task' }), {
    'mod+r': null,
    'mod+e': 'markdown.toggle_task',
  })
})

test('a malformed chord or command is dropped rather than thrown at the editor', () => {
  // The element's `shortcutBindings` setter throws on an unparseable chord, which
  // would take the whole editor down with it.
  assert.deepEqual(noteShortcutBindings({
    'mod+e': 'markdown.toggle_task',
    'mod +e': 'markdown.toggle_task',
    'mod+q': 'not a command',
    'mod+w': 12,
    '': 'editor.undo',
  }), { 'mod+e': 'markdown.toggle_task' })
})

test('an explicitly empty overlay stays empty, but a missing one keeps the defaults', () => {
  assert.deepEqual(noteShortcutBindings({}), {})
  assert.deepEqual(noteShortcutBindings(undefined), { ...DEFAULT_NOTE_SHORTCUT_OVERRIDES })
  assert.deepEqual(noteShortcutBindings(['mod+e']), {})
})
