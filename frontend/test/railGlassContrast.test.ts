import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

// The rail's overflow popover is glass, and glass over a terminal is a contrast decision
// rather than a style one: the panel sits on live output that can be anything from a white
// diff to a black idle screen, and the blur behind it averages that output toward whichever
// extreme it is. So `--rail-glass` is measured here rather than chosen by eye.
//
// Both directions are pinned, and both matter:
//   * Too transparent and a theme whose chips already clear 4.5:1 stops clearing it once a
//     bright buffer washes them out. That is the failure the number exists to prevent.
//   * Too opaque and the "fix" for the first failure is to quietly stop being glass, which
//     passes this file's contrast half while deleting the feature. Hence the ceiling.
//
// What is deliberately *not* asserted: that every theme clears 4.5:1. Three shipped themes
// (game-boy, commodore-64, and catppuccin-latte at the margin) do not clear it on an opaque
// rail either, and holding this panel to a bar the rail beneath it never met would fail on
// day one for a reason that has nothing to do with translucency. The contract is that the
// glass costs nothing: a theme that reads on the rail reads in the panel.

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

/** The one opacity the panel and its chips are both mixed at. */
function glassOpacity(): number {
  const declared = /\.rail-overflow-popover\{--rail-glass:(\d+)%/.exec(CSS)
  if (!declared) throw new Error('the overflow popover must declare --rail-glass')
  return Number(declared[1]) / 100
}

/** A chip label's background: the chip over the panel over whatever the terminal is showing. */
function chipBackground(theme: { panel: Rgb; chip: Rgb }, alpha: number, buffer: Rgb): Rgb {
  return over(theme.chip, alpha, over(theme.panel, alpha, buffer))
}

test('the popover is glass rather than a panel with a blur declaration on it', () => {
  const alpha = glassOpacity()
  assert.ok(alpha < 0.9, `--rail-glass is ${alpha}: at that opacity the panel is not translucent`)
  assert.match(CSS, /\.rail-overflow-popover\{[^}]*backdrop-filter:blur\(/)
  // Both the panel and the chips on it, or the chips are opaque plates on glass.
  assert.match(CSS, /\.rail-overflow-popover\{[^}]*background:color-mix\(in srgb,var\(--panel\) var\(--rail-glass\),transparent\)/)
  assert.match(CSS, /\.rail-overflow-grid>button\{background:color-mix\(in srgb,var\(--panel2\) var\(--rail-glass\),transparent\)\}/)
})

test('glass costs no theme its chip-label contrast, over a white or a black terminal', () => {
  const alpha = glassOpacity()
  const broken: string[] = []
  for (const theme of themes()) {
    const opaque = contrast(theme.text, theme.chip)
    if (opaque < 4.5) continue
    for (const buffer of [WHITE, BLACK]) {
      const composited = contrast(theme.text, chipBackground(theme, alpha, buffer))
      if (composited < 4.5) {
        broken.push(`${theme.name} over ${buffer === WHITE ? 'white' : 'black'}: ${opaque.toFixed(2)} → ${composited.toFixed(2)}`)
      }
    }
  }
  assert.deepEqual(broken, [], `translucency dropped these below 4.5:1:\n${broken.join('\n')}`)
})

test('the shipped default themes clear 4.5:1 through the glass on both extremes', () => {
  // The three a fresh install can be on. A custom theme is the user's own choice and is
  // already gated on background/foreground contrast by `config.py`.
  const alpha = glassOpacity()
  const defaults = themes().filter(theme => theme.name === 'default' || theme.name === 'dark' || theme.name === 'light')
  assert.ok(defaults.length >= 3, 'the default, dark, and light palettes must all be readable here')
  for (const theme of defaults) {
    for (const buffer of [WHITE, BLACK]) {
      const composited = contrast(theme.text, chipBackground(theme, alpha, buffer))
      assert.ok(composited >= 4.5, `${theme.name} over ${buffer === WHITE ? 'white' : 'black'} is ${composited.toFixed(2)}:1`)
    }
  }
})

test('a browser without backdrop-filter falls back to near-solid rather than to a wash', () => {
  // With no blur the glass is a flat mix of whatever pixel happens to be behind each letter,
  // which is neither the look nor a contrast anyone can reason about.
  const fallback = /@supports not \(\(backdrop-filter:blur\(1px\)\) or \(-webkit-backdrop-filter:blur\(1px\)\)\)\{[\s\S]*?--rail-glass:(\d+)%/.exec(CSS)
  assert.ok(fallback, 'a no-backdrop-filter fallback must exist')
  assert.ok(Number(fallback[1]) >= 95)
})
