import { expect, test, type Page } from 'playwright/test'

/**
 * What a rail drop-up does with real geometry.
 *
 * Everything asserted here is decided outside the component: `anchoredPopoverStyle`
 * against a live `getBoundingClientRect`, the browser's own sticky positioning, and
 * CSS row heights against the five-row cap. A unit test can reach none of it, and the
 * three failures that matter are all silent — a panel that opens downward off the
 * bottom of a pane, a sticky link that scrolls away with the list, and a cap that
 * quietly truncates instead of scrolling.
 */

const TRIGGER = '[data-key="clip"]'
const PANEL = '.rail-dropup'
const LIST = '.rail-dropup-list'
const STICKY = '.rail-dropup-open'
const ROW = '.rail-dropup-row'

test.beforeEach(async ({ page }) => {
  await page.goto('/rail-dropup-harness.html')
  await page.waitForSelector(TRIGGER)
})

/**
 * Open the drop-up and wait until it is *placed*, not merely present.
 *
 * Placement and the dismissal listeners are installed by the same effect, and Preact
 * flushes effects after the render that made the panel visible. A spec that acted the
 * instant the element existed could therefore press Escape into a panel that had not
 * yet started listening — a race no human can win, but one a loaded CI machine hits.
 */
async function open(page: Page) {
  await page.click(TRIGGER)
  await expect(page.locator(PANEL)).toBeVisible()
  await expect.poll(async () => page.locator(PANEL).evaluate(el => (el as HTMLElement).style.left)).not.toBe('')
}

test('the panel opens above its trigger and stays inside the viewport', async ({ page }) => {
  await open(page)
  const panel = page.locator(PANEL)
  const [box, trigger, viewport] = await Promise.all([
    panel.boundingBox(),
    page.locator(TRIGGER).boundingBox(),
    page.evaluate(() => ({ height: window.innerHeight, width: window.innerWidth })),
  ])
  expect(box).not.toBeNull()
  expect(trigger).not.toBeNull()
  // Above the trigger, not merely near it: the rail is the bottom edge of a pane, so
  // a panel that opened downward would be off-screen entirely.
  expect(box!.y + box!.height).toBeLessThanOrEqual(trigger!.y + 1)
  expect(box!.y).toBeGreaterThanOrEqual(0)
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1)
})

test('the list caps at five rows and scrolls the rest rather than dropping them', async ({ page }) => {
  await open(page)
  const rows = page.locator(ROW)
  // Every row is present; the cap is a height, never a slice. A sixth entry reachable
  // only through the sticky link would be worse than a scroll.
  await expect(rows).toHaveCount(12)
  const list = page.locator(LIST)
  // The cap is read back from CSS rather than from a TypeScript copy of it: a height
  // cap can only be enforced in CSS, so that is the one place the number is true.
  const metrics = await list.evaluate(el => ({
    client: el.clientHeight,
    scroll: el.scrollHeight,
    row: (el.querySelector('.rail-dropup-row') as HTMLElement).getBoundingClientRect().height,
    declared: Number(getComputedStyle(el).getPropertyValue('--rail-dropup-rows')),
  }))
  expect(metrics.declared).toBe(5)
  expect(metrics.scroll).toBeGreaterThan(metrics.client)
  expect(Math.round(metrics.client / metrics.row)).toBe(metrics.declared)
})

test('the way out to the full section stays on screen while the list scrolls', async ({ page }) => {
  await open(page)
  const sticky = page.locator(STICKY)
  const before = await sticky.boundingBox()
  await page.locator(LIST).evaluate(el => { el.scrollTop = el.scrollHeight })
  await expect(sticky).toBeInViewport()
  const after = await sticky.boundingBox()
  expect(Math.round(after!.y)).toBe(Math.round(before!.y))
  await sticky.click()
  await expect(page.locator(PANEL)).toHaveCount(0)
  expect(await page.evaluate(() => window.dropupSticky)).toBe(1)
})

test('a row acts once and takes the panel down with it', async ({ page }) => {
  await open(page)
  await page.locator('[data-row="row-2"]').click()
  await expect(page.locator(PANEL)).toHaveCount(0)
  expect(await page.evaluate(() => window.dropupPicks)).toEqual(['row-2'])
})

test('Escape and a click outside both dismiss it', async ({ page }) => {
  await open(page)
  await page.keyboard.press('Escape')
  await expect(page.locator(PANEL)).toHaveCount(0)

  await open(page)
  await page.locator('.terminal-host').click({ position: { x: 20, y: 20 } })
  await expect(page.locator(PANEL)).toHaveCount(0)
  expect(await page.evaluate(() => window.dropupPicks)).toEqual([])
})

test('panning the rail repositions the panel against the trigger it now sits at', async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 700 })
  await open(page)
  // The property is that the panel is placed from the trigger's *live* rectangle, not
  // that it moves by the same delta: `anchoredPopoverStyle` also clamps to the viewport,
  // and on a narrow pane that clamp is what is binding. Asserting the placement rule
  // holds after the pan catches the failure that matters — a `scroll` listener without
  // capture never hears a rail pan, and the panel is left placed against where the
  // trigger used to be.
  const placement = async () => page.evaluate(() => {
    const panel = document.querySelector('.rail-dropup') as HTMLElement
    const trigger = document.querySelector('[data-key="clip"]') as HTMLElement
    const panelBox = panel.getBoundingClientRect()
    const triggerBox = trigger.getBoundingClientRect()
    return {
      left: Math.round(panelBox.left),
      expected: Math.round(Math.max(8, Math.min(triggerBox.left, window.innerWidth - panelBox.width - 8))),
      triggerLeft: Math.round(triggerBox.left),
    }
  })
  const before = await placement()
  expect(before.left).toBe(before.expected)

  const moved = await page.locator('.overflow-rail-strip').evaluate(el => {
    const start = el.scrollLeft
    el.scrollLeft = start + 160
    el.dispatchEvent(new Event('scroll', { bubbles: false }))
    return el.scrollLeft !== start
  })
  expect(moved).toBe(true)
  await expect.poll(async () => {
    const after = await placement()
    return after.triggerLeft !== before.triggerLeft && after.left === after.expected
  }).toBe(true)
})
