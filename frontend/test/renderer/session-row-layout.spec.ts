import { expect, test } from 'playwright/test'

/**
 * The state indicator's placement is pure CSS, so nothing else can catch it.
 *
 * Two regressions live here. The tab thread is drawn through the sessions' own
 * status dots, with its x and its gap expressed in the same variables as the
 * indicator's box — restated as pixels, it stopped covering the dot the moment
 * the indicator changed size and painted a blue stripe straight across it. And
 * the indicator belongs to the *title* line, not to the middle of a two-line
 * row, which is where centring it put it.
 */

interface Box { x: number; y: number; width: number; height: number }
const centerX = (box: Box) => box.x + box.width / 2
const centerY = (box: Box) => box.y + box.height / 2

async function geometry(page: import('playwright/test').Page) {
  return page.evaluate(() => {
    const box = (element: Element | null) => {
      const rect = (element as HTMLElement).getBoundingClientRect()
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
    }
    const branch = document.querySelectorAll('.layout-branch')[1]
    const row = branch.querySelector('.session-row')!
    const thread = getComputedStyle(branch, ':after')
    const threadTop = getComputedStyle(branch, ':before')
    const branchBox = box(branch)
    // The core path is the filled shape a user reads as "the dot"; the element
    // box around it is larger because it also has to hold the context gauge.
    const core = (row.querySelector('.ind-core') as SVGGraphicsElement).getBoundingClientRect()
    return {
      indicator: box(row.querySelector('.state-indicator')),
      core: { x: core.x, y: core.y, width: core.width, height: core.height },
      title: box(row.querySelector('.row-line.top')),
      bottom: box(row.querySelector('.row-line.bottom')),
      row: box(row),
      branch: branchBox,
      threadLeft: parseFloat(thread.left),
      threadGapTop: parseFloat(threadTop.height),
      threadGapBottom: parseFloat(thread.top),
    }
  })
}

test('the thread runs through the indicator instead of across it', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')
  const g = await geometry(page)

  const threadCenter = g.branch.x + g.threadLeft + 1
  expect(Math.abs(threadCenter - centerX(g.indicator))).toBeLessThanOrEqual(0.6)

  // The gap the thread leaves must contain the whole indicator box, or a segment
  // is drawn over the dot whose colour is the status being reported.
  const gapTop = g.branch.y + g.threadGapTop
  const gapBottom = g.branch.y + g.threadGapBottom
  expect(gapTop).toBeLessThanOrEqual(g.indicator.y + 0.6)
  expect(gapBottom).toBeGreaterThanOrEqual(g.indicator.y + g.indicator.height - 0.6)
})

test('the indicator is centred on the title line, not on the row', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')
  const g = await geometry(page)

  expect(Math.abs(centerY(g.indicator) - centerY(g.title))).toBeLessThanOrEqual(1)
  // Centring on the row would land it between the two lines instead.
  expect(centerY(g.indicator)).toBeLessThan(g.bottom.y)
})

test('the indicator sits inside its gutter and never overlaps the text', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')
  const g = await geometry(page)

  expect(g.indicator.x).toBeGreaterThanOrEqual(g.row.x)
  expect(g.indicator.x + g.indicator.width).toBeLessThanOrEqual(g.title.x + 0.5)
  expect(g.indicator.y + g.indicator.height).toBeLessThanOrEqual(g.row.y + g.row.height)
})

test('the visible dot is larger than the 6px one it replaced', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')
  const g = await geometry(page)

  expect(g.core.width).toBeGreaterThan(6.5)
  expect(g.core.height).toBeGreaterThan(6.5)
  // And still inside its own box, gauge included.
  expect(g.core.width).toBeLessThanOrEqual(g.indicator.width)
})

for (const shape of ['hexagon', 'circle', 'square'] as const) {
  test(`the ${shape} indicator stays concentric with its gauge`, async ({ page }) => {
    await page.setViewportSize({ width: 900, height: 600 })
    await page.goto(`/session-row-harness.html?shape=${shape}`)
    const g = await page.evaluate(() => {
      const row = document.querySelectorAll('.session-row')[0]
      const rect = (selector: string) => {
        const element = row.querySelector(selector) as SVGGraphicsElement | null
        if (!element) return null
        const box = element.getBoundingClientRect()
        return { x: box.x, y: box.y, width: box.width, height: box.height }
      }
      return { core: rect('.ind-core'), track: rect('.ind-track') }
    })
    expect(g.core).not.toBeNull()
    expect(g.track).not.toBeNull()
    expect(Math.abs(centerX(g.core!) - centerX(g.track!))).toBeLessThanOrEqual(0.5)
    expect(Math.abs(centerY(g.core!) - centerY(g.track!))).toBeLessThanOrEqual(0.5)
    expect(g.track!.width).toBeGreaterThan(g.core!.width)
  })
}
