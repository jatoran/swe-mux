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

/**
 * As the sidebar narrows the two bottom sections must slide together, meet at a
 * fixed gap, and then run off the right edge — clipped, not collapsed.
 *
 * The regression this pins: with the left section flexible and the right one
 * fixed, the right section squeezed the left one to nothing, so the branch and
 * diff vanished while the duration stayed put. That is the opposite of what a
 * narrowing sidebar should drop.
 */
test('narrowing slides the sections together and clips, never squeezes the left out', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')

  const measure = async (width: number) => {
    await page.evaluate(w => {
      document.querySelector<HTMLElement>('.sidebar')!.style.width = `${w}px`
    }, width)
    return page.evaluate(() => {
      const row = document.querySelectorAll('.session-row')[0]
      const line = row.querySelector('.row-line.bottom')!
      const box = (el: Element | null) => {
        if (!el) return null
        const r = el.getBoundingClientRect()
        return { x: r.x, width: r.width, right: r.right }
      }
      const left = line.querySelector('.row-section.left') as HTMLElement
      return {
        line: box(line),
        left: box(left),
        right: box(line.querySelector('.row-section.right')),
        leftText: left.innerText.trim(),
        // Equal means the section is showing all of its content; smaller means
        // it was squeezed and is clipping its own tokens.
        leftScroll: left.scrollWidth,
        leftClient: left.clientWidth,
      }
    })
  }

  const wide = await measure(420)
  expect(wide.left!.width).toBeGreaterThan(0)
  expect(wide.left!.right).toBeLessThanOrEqual(wide.right!.x + 0.5)

  const narrow = await measure(200)
  // The left section keeps every token it drew rather than being squeezed and
  // clipping its own content — this is the assertion the old rules failed.
  expect(narrow.leftText.length).toBeGreaterThan(0)
  // Narrower *does* legitimately shrink this section — the container queries
  // shed whole low-priority tokens. What must never happen is the section being
  // squeezed below the tokens it still draws, which is self-clipping.
  expect(narrow.leftClient).toBeGreaterThanOrEqual(narrow.leftScroll)
  // They meet with a gap preserved, and never overlap.
  expect(narrow.left!.right).toBeLessThanOrEqual(narrow.right!.x + 0.5)
  // Overflow leaves the line to the right and is clipped, not wrapped.
  expect(narrow.right!.right).toBeGreaterThan(narrow.line!.x)
  const lineStyle = await page.evaluate(() =>
    getComputedStyle(document.querySelector('.row-line.bottom')!).overflowX)
  expect(lineStyle).toBe('hidden')
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
