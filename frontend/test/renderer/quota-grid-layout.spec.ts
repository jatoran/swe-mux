import { expect, test } from 'playwright/test'

/**
 * The sidebar quota breakdown is a pure-CSS grid, so nothing else can catch it.
 *
 * The regression that lives here: its tracks were cut in px for the 8px font the
 * button's own rule asks for, while `.app-shell *` forces `--ui-font-size` on
 * every descendant with `!important`. Every heading was therefore wider than its
 * own track (`23h59m` measured 38.7px inside 34px), overran the 7px gutter, and
 * left about 2px of daylight between columns. Rows were also start-aligned, so a
 * short percentage sat left of its long reset time and the provider glyph sat
 * left of the account name under it.
 */

interface Column { width: number; gapBefore: number; headMid: number; valueMid: number; clipped: boolean }

async function columns(page: import('playwright/test').Page, width: number, scale: number) {
  await page.evaluate(s => document.documentElement.style.setProperty('--ui-scale', String(s)), scale)
  return page.evaluate(w => {
    document.querySelector<HTMLElement>('.sidebar')!.style.width = `${w}px`
    return [...document.querySelectorAll('.account-summary > button')].map(button => {
      const grid = button.querySelector('.quota-grid')!
      const gridBox = grid.getBoundingClientRect()
      let previousRight = 0
      return {
        gridWidth: gridBox.width,
        columns: [...grid.querySelectorAll('.quota-grid-column')].map(column => {
          const box = column.getBoundingClientRect()
          const [head, value] = [...column.children].map(child => {
            const rect = child.getBoundingClientRect()
            return {
              mid: rect.x + rect.width / 2 - box.x,
              clipped: child.scrollWidth > child.clientWidth + 0.5,
            }
          })
          const left = box.x - gridBox.x
          const gapBefore = left - previousRight
          previousRight = left + box.width
          return {
            width: box.width,
            gapBefore: left === 0 ? Number.POSITIVE_INFINITY : gapBefore,
            headMid: head.mid,
            valueMid: value.mid,
            clipped: head.clipped || value.clipped,
          } as Column
        }),
        right: previousRight,
      }
    })
  }, width)
}

// Two sidebar widths against three chrome scales. The scales are the point: the
// defect was invisible at the font the CSS was written for and present at every
// font the app actually renders.
const CASES: Array<[number, number]> = [[232, 1], [200, 1], [232, 1.25], [232, 1.4], [180, 1.25]]

for (const [width, scale] of CASES) {
  test(`quota columns stay separated and centred at ${width}px / ${scale}x`, async ({ page }) => {
    await page.setViewportSize({ width: 900, height: 700 })
    await page.goto('/quota-grid-harness.html')
    const rows = await columns(page, width, scale)
    expect(rows.length).toBeGreaterThan(0)

    for (const row of rows) {
      expect(row.columns.length).toBeGreaterThan(1)
      for (const column of row.columns) {
        // A real gutter, always. Content that overruns its own track eats this,
        // which is what made the numbers read as one run-on string.
        expect(column.gapBefore).toBeGreaterThanOrEqual(4)
        // The value sits under the middle of the heading above it: the
        // percentage under its reset time, the glyph over the account name.
        expect(Math.abs(column.headMid - column.valueMid)).toBeLessThanOrEqual(0.5)
      }
      // Never wider than the grid it sits in, at any scale.
      expect(row.right).toBeLessThanOrEqual(row.gridWidth + 0.5)
      // Within a row every metric track is the same width, so a reading sits at
      // the same offset whichever window it belongs to. This holds at every
      // width and scale, shrunk or not.
      const within = row.columns.slice(1).map(column => column.width)
      expect(Math.max(...within) - Math.min(...within)).toBeLessThanOrEqual(0.5)
    }
  })
}

test('rows with different window counts share one track width when they fit', async ({ page }) => {
  // The Claude and Codex lines must agree even when one reports a Fable window
  // and the other does not, or the same reading sits at a different offset in
  // each. `1fr` metrics broke this by dividing a widened sidebar into a
  // different number of tracks per row.
  await page.setViewportSize({ width: 900, height: 700 })
  await page.goto('/quota-grid-harness.html')
  // Wide enough that no row is under pressure, which is where the guarantee
  // applies: a row with no room shrinks its own tracks rather than overrunning
  // the gutter, and that deliberate trade is what the narrow cases cover.
  const rows = await columns(page, 320, 1)
  const metrics = rows.flatMap(row => row.columns.slice(1).map(column => column.width))
  expect(metrics.length).toBeGreaterThan(3)
  expect(Math.max(...metrics) - Math.min(...metrics)).toBeLessThanOrEqual(0.5)

  // And at the shipped sidebar width, which is the layout anyone actually reads.
  const shipped = await columns(page, 232, 1)
  const shippedMetrics = shipped.flatMap(row => row.columns.slice(1).map(column => column.width))
  expect(Math.max(...shippedMetrics) - Math.min(...shippedMetrics)).toBeLessThanOrEqual(0.5)
})

test('a track too narrow for its text ellipsizes inside itself', async ({ page }) => {
  // Degradation matters as much as the fit: when a narrow sidebar and a large
  // chrome scale genuinely leave no room, the text must clip within its own
  // track and keep the gutter, not spill across its neighbour.
  await page.setViewportSize({ width: 900, height: 700 })
  await page.goto('/quota-grid-harness.html')
  const rows = await columns(page, 150, 1.4)
  const clipped = rows.some(row => row.columns.some(column => column.clipped))
  expect(clipped).toBe(true)
  for (const row of rows) {
    expect(row.right).toBeLessThanOrEqual(row.gridWidth + 0.5)
    for (const column of row.columns) expect(column.gapBefore).toBeGreaterThanOrEqual(4)
  }
})
