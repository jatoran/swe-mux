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

/** Pan the rail sideways; true when it actually moved. */
async function panRail(page: Page): Promise<boolean> {
  return page.locator('.overflow-rail-strip').evaluate(el => {
    const start = el.scrollLeft
    el.scrollLeft = start + 160
    el.dispatchEvent(new Event('scroll', { bubbles: false }))
    return el.scrollLeft !== start
  })
}

const placement = async (page: Page) => page.evaluate(() => {
  const panel = (document.querySelector('.rail-dropup') as HTMLElement).getBoundingClientRect()
  const trigger = (document.querySelector('[data-key="clip"]') as HTMLElement).getBoundingClientRect()
  // The desktop rule, restated: right edge on the trigger's, clamped into the viewport. The
  // clamp is what binds when the trigger is nearer an edge than the panel is wide, so
  // asserting the rule rather than raw equality is what keeps this about the *live* rect.
  const left = Math.max(8, Math.min(trigger.right - panel.width, window.innerWidth - panel.width - 8))
  return {
    right: Math.round(panel.right),
    left: Math.round(panel.left),
    expectedRight: Math.round(left + panel.width),
    triggerRight: Math.round(trigger.right),
    triggerLeft: Math.round(trigger.left),
    screenRight: Math.round(window.innerWidth),
  }
})

test('panning the rail repositions the panel against the trigger it now sits at', async ({ page }) => {
  // Wide enough for the desktop rule and narrow enough that the harness's rail still
  // overflows, which is what gives the pan something to move.
  await page.setViewportSize({ width: 900, height: 700 })
  await open(page)
  // On a pane with room, the panel hangs off its own trigger — which is how it says which
  // control opened it. A `scroll` listener without capture never hears a rail pan, and the
  // panel is left pointing at where the trigger used to be.
  const before = await placement(page)
  expect(before.right).toBe(before.expectedRight)

  expect(await panRail(page)).toBe(true)
  await expect.poll(async () => {
    const after = await placement(page)
    return after.triggerRight !== before.triggerRight && after.right === after.expectedRight
  }).toBe(true)
})

test('a phone pins the panel to the screen edge instead, so a pan does not move it', async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 700 })
  await open(page)
  // Half a phone's screen hanging off a trigger in the middle of the rail lands in the
  // middle of the screen, and two pickers opened a second apart appear in two places. Below
  // the device-class breakpoint every rail overlay goes to the same trailing edge instead.
  const before = await placement(page)
  expect(before.right).toBe(before.screenRight - 8)
  expect(before.right - before.left).toBeLessThanOrEqual(480 / 2)

  expect(await panRail(page)).toBe(true)
  await expect.poll(async () => (await placement(page)).triggerLeft).not.toBe(before.triggerLeft)
  const after = await placement(page)
  expect(after.right).toBe(before.right)
})
