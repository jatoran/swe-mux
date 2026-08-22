import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

// The rail's overlays are glass, and glass over a terminal is a contrast decision rather
// than a style one: the panel sits on live output that can be anything from a white diff
// to a black idle screen, and the blur behind it averages that output toward whichever
// extreme it is. So the opacities are measured here rather than chosen by eye.
//
// The contract, recut 2026-08-22 after measuring the real thing on a live dark terminal:
// one number could not serve both masters. An opacity high enough to keep labels readable
// over a white buffer read as a solid panel over a dark one - 20% of near-black leaking
// through near-black is invisible - so the glass is TWO numbers now:
//   * `--rail-glass-field` is the panel field (the surface between and behind the
//     text-bearing pieces). It is what makes the glass visibly glass, so it is pinned LOW:
//     at most 50%.
//   * `--rail-glass` is every surface a label sits on - grid chips, drop-up rows, sticky
//     bars, the popover header - and those composite over the field, so text sits on the
//     two layers together.
// The floors, from the composited result: default/dark/light keep a hard 4.5:1 over both
// extremes, and every theme in the file keeps the 3:1 large-text floor. Universal 4.5:1
// is deliberately not the bar - holding it forced the opacity that deleted the feature.

const CSS = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')

type Rgb = [number, number, number]

const WHITE: Rgb = [255, 255, 255]
const BLACK: Rgb = [0, 0, 0]

function rgb(hex: string): Rgb {
  return [1, 3, 5].map(index => Number.parseInt(hex.slice(index, index + 2), 16)) as Rgb
}

function channel(value: number): number {
  const scaled = value / 255
  return scaled <= 0.04045 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4
}

function luminance([red, green, blue]: Rgb): number {
  return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)
}

function contrast(a: Rgb, b: Rgb): number {
  const first = luminance(a) + 0.05
  const second = luminance(b) + 0.05
  return first > second ? first / second : second / first
}

/** Source-over compositing, in the gamma space a browser actually blends in. */
function over(front: Rgb, alpha: number, back: Rgb): Rgb {
  return front.map((value, index) => alpha * value + (1 - alpha) * back[index]) as Rgb
}

/** Every theme's panel / chip / text triple, read from the stylesheet itself. */
function themes(): { name: string; panel: Rgb; chip: Rgb; text: Rgb }[] {
  const found: { name: string; panel: Rgb; chip: Rgb; text: Rgb }[] = []
  const blocks = /:root(?:\[data-theme="([^"]+)"\])?\s*\{([^}]*)\}/g
  let block: RegExpExecArray | null
  while ((block = blocks.exec(CSS))) {
    const body = block[2]
    const panel = /--panel:\s*(#[0-9a-fA-F]{6})/.exec(body)
    const chip = /--panel2:\s*(#[0-9a-fA-F]{6})/.exec(body)
    const text = /--text:\s*(#[0-9a-fA-F]{6})/.exec(body)
    if (!panel || !chip || !text) continue
    found.push({ name: block[1] || 'default', panel: rgb(panel[1]), chip: rgb(chip[1]), text: rgb(text[1]) })
  }
  return found
}

function declaredPercent(name: string): number {
  const declared = new RegExp(`:root\\{[^}]*${name}:(\\d+)%`).exec(CSS)
  if (!declared) throw new Error(`${name} must be declared at :root`)
  return Number(declared[1]) / 100
}

/** Every label in a rail overlay sits on the text-bearing mix composited over the field. */
function labelBackground(theme: { panel: Rgb; chip: Rgb }, buffer: Rgb): Rgb {
  const field = over(theme.panel, declaredPercent('--rail-glass-field'), buffer)
  return over(theme.chip, declaredPercent('--rail-glass'), field)
}

test('the glass is structurally glass: low field, text-bearing layers, blur on both overlays', () => {
  const field = declaredPercent('--rail-glass-field')
  const text = declaredPercent('--rail-glass')
  // The field is the glassiness guarantee, and the ceiling stops a later "contrast fix"
  // from quietly deleting the feature at either layer.
  assert.ok(field <= 0.5, `--rail-glass-field is ${field}: above 50% the field stops reading as glass`)
  assert.ok(text < 0.95, `--rail-glass is ${text}: at that opacity nothing is translucent`)
  for (const surface of [/\.rail-overflow-popover\{[^}]*/, /\.rail-dropup\{[^}]*/]) {
    const rule = surface.exec(CSS)?.[0] ?? ''
    assert.match(rule, /backdrop-filter:blur\(/)
    assert.match(rule, /background:color-mix\(in srgb,var\(--panel\) var\(--rail-glass-field\),transparent\)/)
  }
  // Every surface a label sits on carries the text-bearing mix - nothing readable sits on
  // the bare field.
  assert.match(CSS, /\.rail-overflow-grid>button\{background:color-mix\(in srgb,var\(--panel2\) var\(--rail-glass\),transparent\)\}/)
  assert.match(CSS, /\.rail-overflow-popover>header\{[^}]*background:color-mix\(in srgb,var\(--panel2\) var\(--rail-glass\),transparent\)/)
  assert.match(CSS, /\.rail-dropup-open\{[^}]*background:color-mix\(in srgb,var\(--panel2\) var\(--rail-glass\),transparent\)/)
  assert.match(CSS, /\.rail-dropup-row\{[^}]*background:color-mix\(in srgb,var\(--panel2\) var\(--rail-glass\),transparent\)/)
})

test('every theme holds the 3:1 large-text floor through the glass, over white and black', () => {
  const broken: string[] = []
  for (const theme of themes()) {
    for (const buffer of [WHITE, BLACK]) {
      const composited = contrast(theme.text, labelBackground(theme, buffer))
      if (composited < 3) {
        broken.push(`${theme.name} over ${buffer === WHITE ? 'white' : 'black'}: ${composited.toFixed(2)}`)
      }
    }
  }
  assert.deepEqual(broken, [], `translucency dropped these below 3:1:\n${broken.join('\n')}`)
})

test('the shipped default themes clear 4.5:1 through the glass on both extremes', () => {
  // The three a fresh install can be on. A custom theme is the user's own choice and is
  // already gated on background/foreground contrast by `config.py`.
  const defaults = themes().filter(theme => theme.name === 'default' || theme.name === 'dark' || theme.name === 'light')
  assert.ok(defaults.length >= 3, 'the default, dark, and light palettes must all be readable here')
  for (const theme of defaults) {
    for (const buffer of [WHITE, BLACK]) {
      const composited = contrast(theme.text, labelBackground(theme, buffer))
      assert.ok(composited >= 4.5, `${theme.name} over ${buffer === WHITE ? 'white' : 'black'} is ${composited.toFixed(2)}:1`)
    }
  }
})

test('a browser without backdrop-filter falls back to near-solid rather than to a wash', () => {
  // With no blur the glass is a flat mix of whatever pixel happens to be behind each letter,
  // which is neither the look nor a contrast anyone can reason about.
  const fallback = /@supports not \(\(backdrop-filter:blur\(1px\)\) or \(-webkit-backdrop-filter:blur\(1px\)\)\)\{[\s\S]*?--rail-glass:(\d+)%;--rail-glass-field:(\d+)%/.exec(CSS)
  assert.ok(fallback, 'a no-backdrop-filter fallback must exist and cover both layers')
  assert.ok(Number(fallback[1]) >= 95)
  assert.ok(Number(fallback[2]) >= 95)
})
