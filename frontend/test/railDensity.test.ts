import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { MOBILE_QUERY } from '../src/deviceSettings.ts'
import {
  applyRailDensity, DEFAULT_RAIL_DENSITY, RAIL_DENSITIES, railDensityConfigKey, railDensityFrom,
} from '../src/railDensity.ts'

const CSS = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')
/** The variables one density step has to define, all of them or none. */
const GROUP = ['--rail-gap', '--rail-pad-y', '--rail-pad-x', '--rail-chip-h', '--rail-chip-pad', '--rail-text-pad', '--rail-row-h', '--rail-more-width']

// The mobile group lives inside the one `@media(max-width:760px)` block that opens with the
// bare `:root` step, which is what makes "second copy, not a multiplier" checkable.
const MOBILE_AT = CSS.search(/@media\(max-width:760px\)\{\s*:root\{--rail-gap/)

function block(selector: string, mobile: boolean): string {
  if (mobile && MOBILE_AT < 0) throw new Error('style.css has no mobile rail density group')
  const source = mobile ? CSS.slice(MOBILE_AT) : CSS
  // The *density* block for this selector, not merely the first block that shares the
  // selector: bare `:root` is also where several unrelated token groups live (the rail's
  // glass opacities among them), and taking the first match read one of those instead and
  // reported the whole group missing. Anchored the same way `MOBILE_AT` already is.
  const all = source.matchAll(new RegExp(`${selector.replace(/[[\]"]/g, String.raw`\$&`)}\\{([^}]*)\\}`, 'g'))
  for (const found of all) {
    if (found[1].includes('--rail-gap')) return found[1]
  }
  throw new Error(`style.css has no ${mobile ? 'mobile ' : ''}rail density block for ${selector}`)
}

test('a config the daemon never sent, and any value it does not know, is Comfortable', () => {
  assert.equal(railDensityFrom({}, 'desktop'), DEFAULT_RAIL_DENSITY)
  assert.equal(railDensityFrom({ rail_density_desktop: 'roomy' }, 'desktop'), 'comfortable')
  assert.equal(railDensityFrom({ rail_density_desktop: 3 }, 'desktop'), 'comfortable')
})

test('each device class reads its own key and ignores the other', () => {
  const config = { rail_density_desktop: 'dense', rail_density_mobile: 'compact' }
  assert.equal(railDensityFrom(config, 'desktop'), 'dense')
  assert.equal(railDensityFrom(config, 'mobile'), 'compact')
  assert.equal(railDensityConfigKey('desktop'), 'rail_density_desktop')
  assert.equal(railDensityConfigKey('mobile'), 'rail_density_mobile')
})

/** A root element and a `window` just real enough for `applyRailDensity` to write to. */
function withFakeRoot<T>(mobile: boolean, run: (attributes: Map<string, string>) => T): T {
  const attributes = new Map<string, string>()
  const previous = {
    window: (globalThis as { window?: unknown }).window,
    document: (globalThis as { document?: unknown }).document,
  }
  ;(globalThis as { window?: unknown }).window = {
    matchMedia: (query: string) => ({ matches: mobile && query === MOBILE_QUERY }),
  }
  ;(globalThis as { document?: unknown }).document = {
    documentElement: {
      setAttribute: (name: string, value: string) => { attributes.set(name, value) },
      removeAttribute: (name: string) => { attributes.delete(name) },
    },
  }
  try {
    return run(attributes)
  } finally {
    ;(globalThis as { window?: unknown }).window = previous.window
    ;(globalThis as { document?: unknown }).document = previous.document
  }
}

test('Comfortable writes no attribute at all, so an opted-out device is the old build', () => {
  withFakeRoot(false, attributes => {
    applyRailDensity({ rail_density_desktop: 'dense' })
    assert.equal(attributes.get('data-rail-density'), 'dense')
    // Back to Comfortable and the attribute is gone, not set to "comfortable": a device
    // that never opted in and one that opted back out must be indistinguishable.
    applyRailDensity({ rail_density_desktop: 'comfortable' })
    assert.equal(attributes.has('data-rail-density'), false)
    // A daemon too old to send either key is the same case again.
    applyRailDensity({})
    assert.equal(attributes.has('data-rail-density'), false)
  })
  // Which only holds if Comfortable's numbers are the bare `:root` ones rather than a
  // fourth block the attribute would have to select.
  assert.doesNotMatch(CSS, /\[data-rail-density="comfortable"\]/)
})

test('each device class reads its own key, so a phone cannot inherit the desktop step', () => {
  withFakeRoot(true, attributes => {
    applyRailDensity({ rail_density_desktop: 'dense', rail_density_mobile: 'compact' })
    assert.equal(attributes.get('data-rail-density'), 'compact')
  })
  withFakeRoot(false, attributes => {
    applyRailDensity({ rail_density_desktop: 'dense', rail_density_mobile: 'compact' })
    assert.equal(attributes.get('data-rail-density'), 'dense')
  })
})

test('every step defines the whole variable group, on both device classes', () => {
  for (const mobile of [false, true]) {
    for (const density of RAIL_DENSITIES) {
      const selector = density === DEFAULT_RAIL_DENSITY ? ':root' : `:root[data-rail-density="${density}"]`
      const declarations = block(selector, mobile)
      for (const name of GROUP) {
        assert.match(declarations, new RegExp(`${name}:`), `${selector}${mobile ? ' (mobile)' : ''} is missing ${name}`)
      }
    }
  }
})

test('the mobile group is its own set of numbers, not the desktop set scaled', () => {
  // A phone wants a different floor rather than a smaller desktop: Comfortable there is a
  // 44px chip, which is a touch target and not a multiple of the desktop's 27.
  assert.match(block(':root', true), /--rail-chip-h:44px/)
  assert.match(block(':root', false), /--rail-chip-h:calc\(27px\*var\(--ui-scale\)\)/)
})

test('the rail reads the group rather than repeating any of its numbers', () => {
  assert.match(CSS, /\.terminal-action-scroll\{[^}]*gap:var\(--rail-gap\);padding:var\(--rail-pad-y\) var\(--rail-pad-x\)/)
  assert.match(CSS, /\.terminal-action-rail button\{[^}]*height:var\(--rail-chip-h\)/)
  assert.match(CSS, /\.terminal-action-rows>\.rail-row\{[^}]*min-height:var\(--rail-row-h\)/)
  // The popover is part of the rail and tightens with it.
  assert.match(CSS, /\.rail-overflow-grid\{[^}]*gap:var\(--rail-gap\);padding:var\(--rail-pad-y\) var\(--rail-pad-x\)/)
})

test('configured text chips size to their label; min-width is a floor, not a target', () => {
  const rule = /\.terminal-action-rail \.rail-text\{([^}]*)\}/.exec(CSS)
  if (!rule) throw new Error('configured text chips must have their own sizing rule')
  assert.match(rule[1], /min-width:30px/)
  assert.match(rule[1], /padding:0 var\(--rail-text-pad\)/)
  // The shared 74px is right for the fixed built-in wording and wrong for a user's label.
  assert.match(CSS, /\.terminal-action-rail button\{min-width:calc\(74px\*var\(--ui-scale\)\)/)
  // Applied to the four configured types and to no built-in. Counted on the class
  // expression rather than the whole `class={…}` attribute: the pane resolves an item's
  // class through `railItemView` now, so the attribute is written once for every chip and
  // only these two branches put `rail-text` in it.
  const pane = readFileSync(new URL('../src/TerminalPane.tsx', import.meta.url), 'utf8')
  assert.equal(pane.split('`rail-text ${item.className||\'\'}`.trim()').length - 1, 2)
})

test('the daemon and the browser agree on the steps', () => {
  const config = readFileSync(new URL('../../src/swe_mux/config.py', import.meta.url), 'utf8')
  const declared = /RAIL_DENSITIES = \(([^)]*)\)/.exec(config)
  if (!declared) throw new Error('config.py must declare RAIL_DENSITIES')
  const server = declared[1].split(',').map(entry => entry.trim().replace(/"/g, '')).filter(Boolean)
  assert.deepEqual(server, [...RAIL_DENSITIES])
})
