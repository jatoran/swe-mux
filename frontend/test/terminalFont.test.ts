import { withCatalogText } from './support/localizedSource.ts'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import {
  DEFAULT_TERMINAL_FONT_FAMILIES,
  DEFAULT_TERMINAL_FONT_STACK,
  TERMINAL_FONT_FAMILY_MAX_CHARS,
  cssFontFamily,
  fontFamilyAvailable,
  loadTerminalFont,
  terminalFontAvailability,
  terminalFontFamilies,
  terminalFontFamilyFrom,
  terminalFontStack,
  type TextMeasurer,
} from '../src/terminalFont.ts'
import { terminalPanePropsEqual } from '../src/terminalPaneMemo.ts'

const root = dirname(fileURLToPath(import.meta.url))
const source = (name: string) => withCatalogText(readFileSync(join(root, '..', 'src', name), 'utf8'))

test('the default stack is what the terminal has always been drawn with', () => {
  // Installing this build must change nothing on screen: blank resolves to the
  // literal that used to be hardcoded in the pane, in the same order.
  assert.deepEqual([...DEFAULT_TERMINAL_FONT_FAMILIES], ['Cascadia Mono', 'Consolas', 'monospace'])
  assert.equal(DEFAULT_TERMINAL_FONT_STACK, '"Cascadia Mono", "Consolas", monospace')
  assert.equal(terminalFontStack(''), DEFAULT_TERMINAL_FONT_STACK)
  assert.equal(terminalFontStack('   '), DEFAULT_TERMINAL_FONT_STACK)
})

test('a configured family is prepended to the default stack, never substituted for it', () => {
  assert.equal(
    terminalFontStack('JetBrainsMono Nerd Font'),
    '"JetBrainsMono Nerd Font", "Cascadia Mono", "Consolas", monospace',
  )
  // A list keeps its order, and a default family the user named is not repeated
  // behind itself.
  assert.equal(
    terminalFontStack('Iosevka, Consolas'),
    '"Iosevka", "Consolas", "Cascadia Mono", monospace',
  )
  assert.equal(terminalFontStack('monospace'), 'monospace, "Cascadia Mono", "Consolas"')
})

test('every name is quoted for the canvas shorthand and generic keywords stay bare', () => {
  assert.equal(cssFontFamily('JetBrainsMono Nerd Font'), '"JetBrainsMono Nerd Font"')
  assert.equal(cssFontFamily('monospace'), 'monospace')
  assert.equal(cssFontFamily('UI-Monospace'), 'ui-monospace')
  // A quote inside a name cannot end the string early.
  assert.equal(cssFontFamily('Foo "Bar"'), '"Foo \\"Bar\\""')
  assert.equal(cssFontFamily('Back\\slash'), '"Back\\\\slash"')
})

test('the family list is split, trimmed, unquoted, deduplicated, and bounded', () => {
  assert.deepEqual(terminalFontFamilies('"Fira Code", \'Iosevka\' ,, Fira Code , monospace'), ['Fira Code', 'Iosevka', 'monospace'])
  // What cannot be part of a name is dropped rather than refused: the daemon already
  // refused it on save, so this is only ever a hand-edited file.
  assert.deepEqual(terminalFontFamilies('Fira; color:red {x}'), ['Fira color:red x'])
  assert.deepEqual(terminalFontFamilies('Fira\nCode\x00'), ['FiraCode'])
  const long = 'x'.repeat(TERMINAL_FONT_FAMILY_MAX_CHARS + 50)
  assert.equal(terminalFontFamilies(long)[0].length, TERMINAL_FONT_FAMILY_MAX_CHARS)
})

test('a config value that is not a string is the default', () => {
  assert.equal(terminalFontFamilyFrom({ terminal_font_family: 'Iosevka, "Fira Code"' }), 'Iosevka, Fira Code')
  // A daemon older than this build sends no key at all.
  assert.equal(terminalFontFamilyFrom({}), '')
  for (const bad of [null, 12, true, ['Iosevka'], { name: 'Iosevka' }] as unknown[]) {
    assert.equal(terminalFontFamilyFrom({ terminal_font_family: bad }), '', `${String(bad)} must resolve to the default`)
  }
})

test('availability is measured against every generic fallback, and a keyword is always available', () => {
  // A font that exists changes the width on at least one baseline; one that does not
  // renders as the baseline itself on all three.
  const widths: Record<string, number> = {
    '72px monospace': 100, '72px serif': 90, '72px sans-serif': 95,
    '72px "Installed", monospace': 100, '72px "Installed", serif': 91, '72px "Installed", sans-serif': 95,
    '72px "Missing", monospace': 100, '72px "Missing", serif': 90, '72px "Missing", sans-serif': 95,
  }
  const measure: TextMeasurer = font => {
    assert.ok(font in widths, `unexpected measurement ${font}`)
    return widths[font]
  }
  assert.equal(fontFamilyAvailable('Installed', measure), true)
  assert.equal(fontFamilyAvailable('Missing', measure), false)
  assert.equal(fontFamilyAvailable('monospace', measure), true)
  // No canvas is "unknown", never "missing".
  assert.equal(fontFamilyAvailable('Installed', null), null)
  assert.equal(fontFamilyAvailable('monospace', null), true)
  assert.deepEqual(terminalFontAvailability('Installed, Missing', measure), [
    { name: 'Installed', available: true },
    { name: 'Missing', available: false },
  ])
  assert.deepEqual(terminalFontAvailability('', measure), [])
})

test('loading waits for a web font and reports only a face that actually arrived', async () => {
  const loads: string[] = []
  let readyAwaited = false
  const fonts = (faces: unknown[]) => ({
    load: async (font: string) => { loads.push(font); return faces },
    get ready() { readyAwaited = true; return Promise.resolve() },
  })
  // An installed font: nothing to load, the fit already taken stands.
  assert.equal(await loadTerminalFont('"Iosevka", monospace', '600', 12, fonts([])), false)
  assert.deepEqual(loads, ['600 12px "Iosevka", monospace'])
  assert.equal(readyAwaited, true)
  // A @font-face that this call loaded: the caller re-fits.
  assert.equal(await loadTerminalFont('"Web Mono", monospace', '600', 12, fonts([{}])), true)
  // A browser without the API, or one that rejects the shorthand, is "nothing to wait for".
  assert.equal(await loadTerminalFont('"Iosevka"', '600', 12, undefined), false)
  assert.equal(await loadTerminalFont('"Iosevka"', '600', 12, {
    load: () => Promise.reject(new Error('SyntaxError')),
    ready: Promise.resolve(),
  }), false)
})

test('a font family change reaches live panes without rebuilding them', () => {
  const app = source('App.tsx')
  const pane = source('TerminalPane.tsx')
  const settings = source('Settings.tsx')
  // The same three inert halves as the chrome scale and the width envelope: the prop
  // reaches the pane, the memo lets a change through, and the family is not a
  // dependency of the construction effect - listing it there would drop the socket
  // and replay the whole buffer to change a font.
  assert.match(app, /fontFamily=\{terminalFontFamily\}/)
  assert.match(app, /setTerminalFontFamily\(terminalFontFamilyFrom\(config\)\)/)
  const base = { session: { id: 's', backend: 'shell', state: 'running' }, broadcast: false, scrollback: 1, rendererPreference: 'auto', mobileInput: {}, uiScale: 1, visible: true, claudeMaxColumns: 120 }
  assert.equal(terminalPanePropsEqual({ ...base, fontFamily: 'Iosevka' } as never, { ...base, fontFamily: 'Iosevka' } as never), true)
  assert.equal(terminalPanePropsEqual({ ...base, fontFamily: 'Iosevka' } as never, { ...base, fontFamily: 'Fira Code' } as never), false)
  // Absent is the default, so a daemon predating the key compares equal to one sending it.
  assert.equal(terminalPanePropsEqual({ ...base } as never, { ...base, fontFamily: '' } as never), true)
  const lifetime = /\n  \}, \[session\.id, scrollback,[^\]]*\]\)/.exec(pane)?.[0] ?? ''
  assert.ok(lifetime, 'the terminal lifetime effect dep list has moved')
  assert.doesNotMatch(lifetime, /fontFamily|fontStack/)
  assert.match(pane, /useEffect\(\(\) => \{ applyFontFamilyRef\.current\(\) \}, \[fontStack\]\)/)
  // Both places the pane names a font read the resolved stack: xterm's option and the
  // host box under a Claude width cap, whose `ch` unit must resolve against the same
  // cells xterm draws. A literal left behind in either is the setting silently
  // applying to one and not the other.
  assert.doesNotMatch(pane, /Cascadia Mono/)
  assert.match(pane, /fontFamily: fontStackRef\.current/)
  assert.match(pane, /fontFamily:fontStack,/)
  // Applying the family invalidates the cached fit, or the grid the daemon holds for
  // this device is one measured against the previous font.
  const apply = /applyFontFamilyRef\.current = \(\) => \{[\s\S]*?\n    \}/.exec(pane)?.[0] ?? ''
  assert.ok(apply, 'applyFontFamilyRef is no longer assigned in the construction effect')
  assert.match(apply, /term\.options\.fontFamily = fontStackRef\.current/)
  assert.match(apply, /localFit = null/)
  assert.match(apply, /runViewportPass\(\)/)
  // The control lives on Appearance, is marked for setting links, and carries the words
  // Settings search needs to find it - "font" and "Nerd Font" being the ones a
  // reporter actually typed.
  assert.match(settings, /data-setting="terminal_font_family"/)
  assert.match(settings, /change\('terminal_font_family'/)
  assert.match(settings, /Nerd Font/)
  // The note editor's own typography now says what it is for, so nobody types a
  // terminal font into it and restarts waiting for something to change.
  assert.match(settings, /<h3\b[^>]*>Note editor typography<\/h3>/)
  assert.match(settings, /placeholder="notes only;/)
})
