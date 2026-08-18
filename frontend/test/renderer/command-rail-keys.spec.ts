import { expect, test, type Page } from 'playwright/test'
import { RAIL_KEY_REPEAT_DELAY_MS, RAIL_KEY_REPEAT_INTERVAL_MS } from '../../src/railKeyRepeat.ts'

/**
 * What an Action rail key does with a finger that is not simply tapping it.
 *
 * The rail scrolls by owning the touch itself: `OverflowRail` calls `setPointerCapture` on
 * its wrapper as soon as one lands on an overflowing strip, and suppresses the click that
 * would otherwise follow a drag. Ordinary rail buttons ride that for free because they
 * send on `click`. The arrow keys did not — they sent on `pointerdown`, and were then
 * excluded from the pan so their hold-to-repeat kept priority. The result was the one part
 * of the rail you could not push off of: touching an arrow to flick the rail sideways
 * fired the key immediately and scrolled nothing.
 *
 * Three things have to hold together here, and none of them is visible to a unit test:
 * real pointer capture, real click suppression, and Chrome's own touch-to-click synthesis.
 * So these drive real touches through CDP at the real components — synthetic
 * `PointerEvent`s cannot be captured, and would prove nothing about any of it.
 */

test.use({ hasTouch: true })

/** Comfortably past the first repetition and several more, without being a slow test. */
const HOLD_MS = RAIL_KEY_REPEAT_DELAY_MS + RAIL_KEY_REPEAT_INTERVAL_MS * 6
/** Six steps of this clear the pan's slop many times over without leaving the viewport. */
const DRAG_STEP_PX = 14
const UP_KEY = '.rail-key-repeat[title^="Up"]'
const PLAIN_KEY = '[data-key="restoreInput"]'
const UP_SEQUENCE = '\x1b[A'

async function touch(page: Page) {
  const cdp = await page.context().newCDPSession(page)
  type TouchType = 'touchStart' | 'touchMove' | 'touchEnd'
  const send = (type: TouchType, points: Array<{ x: number; y: number }>) =>
    cdp.send('Input.dispatchTouchEvent', { type, touchPoints: points.map(point => ({ x: point.x, y: point.y })) })
  return {
    down: (x: number, y: number) => send('touchStart', [{ x, y }]),
    move: (x: number, y: number) => send('touchMove', [{ x, y }]),
    up: () => send('touchEnd', []),
  }
}

/**
 * The centre of one rail key, clamped into the part of the strip a finger can actually
 * reach. The strip is scrolled and clipped, and the overflow controls are drawn *over* its
 * ends, so a key's layout box can sit somewhere a touch aimed at its centre would land on
 * an edge button instead.
 */
async function keyPoint(page: Page, selector: string) {
  const point = await page.evaluate((keySelector: string) => {
    const strip = document.querySelector<HTMLElement>('.terminal-action-scroll')!
    const key = strip.querySelector<HTMLElement>(keySelector)
    if (!key) return null
    const stripBox = strip.getBoundingClientRect()
    const keyBox = key.getBoundingClientRect()
    let left = Math.max(keyBox.left, stripBox.left)
    let right = Math.min(keyBox.right, stripBox.right)
    const before = document.querySelector<HTMLElement>('.overflow-rail-left')
    const after = document.querySelector<HTMLElement>('.overflow-rail-right')
    if (before) left = Math.max(left, before.getBoundingClientRect().right)
    if (after) right = Math.min(right, after.getBoundingClientRect().left)
    return right - left < 8 ? null : { x: (left + right) / 2, y: keyBox.top + keyBox.height / 2 }
  }, selector)
  if (!point) throw new Error(`rail key ${selector} is not reachable inside the strip`)
  return point
}

const sends = (page: Page) => page.evaluate(() => window.railSends)
const scrollLeft = (page: Page) => page.evaluate(() => document.querySelector('.terminal-action-scroll')!.scrollLeft)

async function swipeLeftFrom(page: Page, selector: string) {
  const finger = await touch(page)
  const centre = await keyPoint(page, selector)
  await finger.down(centre.x, centre.y)
  for (let step = 1; step <= 6; step += 1) await finger.move(centre.x - step * DRAG_STEP_PX, centre.y)
  return finger
}

test.beforeEach(async ({ page }) => {
  await page.goto('/command-rail-harness.html')
  await expect(page.locator('.terminal-action-scroll > button')).toHaveCount(16)
  // Park the arrows in the middle of the visible window: room to drag towards on the left,
  // room to scroll into on the right. Both are prerequisites for the swipe tests meaning
  // anything, so they are asserted rather than assumed.
  const parked = await page.evaluate(() => {
    const strip = document.querySelector<HTMLElement>('.terminal-action-scroll')!
    const up = strip.querySelector<HTMLElement>('.rail-key-repeat')!
    strip.scrollLeft = up.offsetLeft - strip.clientWidth / 2 + up.offsetWidth / 2
    return {
      scrollLeft: strip.scrollLeft,
      maximum: strip.scrollWidth - strip.clientWidth,
    }
  })
  expect(parked.scrollLeft).toBeGreaterThan(0)
  expect(parked.maximum).toBeGreaterThan(parked.scrollLeft)
})

test('a swipe that begins on an arrow key scrolls the rail and sends nothing', async ({ page }) => {
  const start = await scrollLeft(page)
  const finger = await swipeLeftFrom(page, UP_KEY)
  // Held past the repeat delay *after* moving: the hold is called off by the motion, not
  // by the finger lifting in time.
  await page.waitForTimeout(HOLD_MS)
  await finger.up()

  expect(await sends(page)).toEqual([])
  expect(await scrollLeft(page)).toBeGreaterThan(start)
})

test('a swipe that begins on an ordinary rail key sends nothing either', async ({ page }) => {
  const start = await scrollLeft(page)
  const finger = await swipeLeftFrom(page, PLAIN_KEY)
  await finger.up()

  expect(await sends(page)).toEqual([])
  expect(await scrollLeft(page)).toBeGreaterThan(start)
})

test('a clean tap on an arrow key sends it exactly once', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, UP_KEY)
  const start = await scrollLeft(page)

  await finger.down(centre.x, centre.y)
  await finger.up()

  await expect.poll(() => sends(page)).toEqual([UP_SEQUENCE])
  expect(await scrollLeft(page)).toBe(start)
})

test('holding an arrow key repeats it, and lifting adds no extra tap', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, UP_KEY)
  const start = await scrollLeft(page)

  await finger.down(centre.x, centre.y)
  await page.waitForTimeout(HOLD_MS)
  const held = await sends(page)
  await finger.up()

  expect(held.length).toBeGreaterThan(2)
  expect(held.every(sequence => sequence === UP_SEQUENCE)).toBe(true)
  // The click a hold ends with was already answered by the hold itself. Give it time to
  // arrive before concluding that nothing followed.
  await page.waitForTimeout(200)
  expect(await sends(page)).toHaveLength(held.length)
  // A committed hold owns its pointer, so the strip stays where it was rather than
  // drifting under the key being spammed.
  expect(await scrollLeft(page)).toBe(start)
})

test('a tap immediately after a hold is not swallowed by it', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, UP_KEY)

  await finger.down(centre.x, centre.y)
  await page.waitForTimeout(HOLD_MS)
  await finger.up()
  await page.waitForTimeout(200)
  const afterHold = (await sends(page)).length

  await finger.down(centre.x, centre.y)
  await finger.up()

  await expect.poll(async () => (await sends(page)).length).toBe(afterHold + 1)
})
