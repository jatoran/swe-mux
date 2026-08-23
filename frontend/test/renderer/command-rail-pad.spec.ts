import { expect, test, type Page } from 'playwright/test'
import { RAIL_KEY_REPEAT_DELAY_MS, RAIL_KEY_REPEAT_INTERVAL_MS } from '../../src/railKeyRepeat.ts'
import { RAIL_PAD_PETAL_DELAY_MS } from '../../src/railPadGesture.ts'
import { claims, clearRecorders, dragBy, keyPoint, scrollLeft, sends, touch } from './railTouch.ts'

/**
 * What a pad chip does with a finger, in a real rail.
 *
 * Everything worth doubting about a pad is an interaction between three things no unit
 * test can hold at once: the pad's own geometry, `OverflowRail`'s pointer capture (which
 * retargets every move away from the chip that was pressed), and Chrome's touch-to-click
 * synthesis. So this drives real touches through CDP at the real components, in the same
 * overflowing strip the arrows live in.
 *
 * The claim is the load-bearing one. A pad works only because it takes the pointer at
 * pointer-down, which is what makes the rail's pan and the mobile recognizer's
 * `rail_swipe_up` (the app menu) stand down with no delay and no code of their own. That
 * claim is released on `pointerup`, *before* the `touchend` where the recognizer would
 * classify - so it is sampled mid-drag rather than asserted afterwards.
 */

test.use({ hasTouch: true })

const PAD = '.rail-pad-cardinal'
const JUMP = '.rail-pad-diagonal'
/** Well past the entry radius, and past the pan's slop many times over. */
const REACH_PX = 44
const HOLD_MS = RAIL_KEY_REPEAT_DELAY_MS + RAIL_KEY_REPEAT_INTERVAL_MS * 6

async function flick(page: Page, selector: string, dx: number, dy: number) {
  const finger = await touch(page)
  const centre = await keyPoint(page, selector)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, dx, dy)
  return { finger, centre }
}

test.beforeEach(async ({ page }) => {
  await page.goto('/command-rail-harness.html')
  await expect(page.locator('.terminal-action-scroll > button')).toHaveCount(18)
  // Park the pads mid-strip: room to drag in every direction, and scroll left over on
  // both sides so "the rail did not move" is a claim with something to prove.
  const parked = await page.evaluate(() => {
    const strip = document.querySelector<HTMLElement>('.terminal-action-scroll')!
    const pad = strip.querySelector<HTMLElement>('.rail-pad-cardinal')!
    strip.scrollLeft = pad.offsetLeft - strip.clientWidth / 2 + pad.offsetWidth / 2
    return { scrollLeft: strip.scrollLeft, maximum: strip.scrollWidth - strip.clientWidth }
  })
  expect(parked.scrollLeft).toBeGreaterThan(0)
  expect(parked.maximum).toBeGreaterThan(parked.scrollLeft)
  await clearRecorders(page)
})

test('a flick up sends the up slot once, before the finger is even lifted', async ({ page }) => {
  const start = await scrollLeft(page)
  const { finger } = await flick(page, PAD, 0, -REACH_PX)
  // Asserted *before* the lift: the whole design is that the key has already fired.
  await expect.poll(() => sends(page)).toEqual(['\x1b[A'])
  await finger.up()
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual(['\x1b[A'])
  // The pad owns the pointer, so the strip did not pan under the finger.
  expect(await scrollLeft(page)).toBe(start)
})

test('a horizontal flick works the pad rather than scrolling the rail', async ({ page }) => {
  // The trade the pad makes knowingly: a two-axis pad claims every direction, so it is the
  // one chip a pan cannot be started from. Asserted so it stays a decision.
  const start = await scrollLeft(page)
  const { finger } = await flick(page, PAD, REACH_PX, 0)
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['\x1b[C'])
  expect(await scrollLeft(page)).toBe(start)
})

test('the pad claims the pointer while dragging, so the pan and the menu swipe stand down', async ({ page }) => {
  const { finger } = await flick(page, PAD, 0, -REACH_PX)
  const sampled = await claims(page)
  await finger.up()
  expect(sampled.length).toBeGreaterThan(0)
  expect(sampled.every(Boolean)).toBe(true)
  // And it is handed back, so the very next touch can pan the strip again.
  await expect.poll(() => page.evaluate(() => window.railClaims.at(-1) === true)).toBe(true)
})

test('holding a direction repeats it, and only where the slot says so', async ({ page }) => {
  const held = await flick(page, PAD, 0, -REACH_PX)
  await page.waitForTimeout(HOLD_MS)
  const repeated = await sends(page)
  await held.finger.up()
  expect(repeated.length).toBeGreaterThan(2)
  expect(repeated.every(sequence => sequence === '\x1b[A')).toBe(true)

  await clearRecorders(page)
  const once = await flick(page, PAD, 0, REACH_PX)
  await page.waitForTimeout(HOLD_MS)
  await once.finger.up()
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual(['\x1b[B'])
})

test('a release slot waits for the lift', async ({ page }) => {
  const { finger } = await flick(page, PAD, -REACH_PX, 0)
  expect(await sends(page)).toEqual([])
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['KILL'])
})

test('dragging back out of a release slot cancels it', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, -REACH_PX, 0)
  // Second thoughts: back to the middle, and lift.
  await dragBy(finger, { x: centre.x - REACH_PX, y: centre.y }, REACH_PX, 0)
  await finger.up()
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual([])
})

test('leaving a release slot sideways runs the direction it left for, and not the armed one', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, -REACH_PX, 0)
  await dragBy(finger, { x: centre.x - REACH_PX, y: centre.y }, REACH_PX, -REACH_PX)
  await finger.up()
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual(['\x1b[A'])
})

test('a tap with no travel runs the centre exactly once', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  await finger.down(centre.x, centre.y)
  await finger.up()
  // Once, not twice: the gesture fired it and then swallowed the click behind it.
  await expect.poll(() => sends(page)).toEqual(['CENTRE'])
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual(['CENTRE'])
})

test('a drag does not also run the centre on the way out', async ({ page }) => {
  const { finger } = await flick(page, PAD, 0, -REACH_PX)
  await finger.up()
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual(['\x1b[A'])
})

test('a diagonal pad reads the corners, and the axes are its boundaries', async ({ page }) => {
  const corners: Array<[number, number, string]> = [
    [-REACH_PX, -REACH_PX, '\x1b[H'],
    [REACH_PX, -REACH_PX, '\x1b[1;5H'],
    [-REACH_PX, REACH_PX, '\x1b[F'],
    [REACH_PX, REACH_PX, '\x1b[1;5F'],
  ]
  for (const [dx, dy, expected] of corners) {
    await clearRecorders(page)
    const { finger } = await flick(page, JUMP, dx, dy)
    await finger.up()
    await expect.poll(() => sends(page)).toEqual([expected])
  }
  // Barely off the vertical axis still resolves into a quadrant: a diagonal pad has no
  // dead wedge between its corners, which is what lets each axis carry one binary choice.
  await clearRecorders(page)
  const grazed = await flick(page, JUMP, -3, -REACH_PX)
  await grazed.finger.up()
  await expect.poll(() => sends(page)).toEqual(['\x1b[H'])
})

test('the petals are held back by the delay, and the gesture is not', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  // A bare press, because the six-step drag below takes longer than the delay on its own.
  await finger.down(centre.x, centre.y)
  await expect(page.locator('.rail-pad-petals')).toHaveCount(1)
  expect(await page.locator('.rail-pad-petals-shown').count()).toBe(0)
  // One step is all the gesture needs, and it fires while the labels are still hidden:
  // the delay is cosmetic and nothing in the gesture consults it.
  await finger.move(centre.x, centre.y - REACH_PX)
  await expect.poll(() => sends(page)).toEqual(['\x1b[A'])

  await page.waitForTimeout(RAIL_PAD_PETAL_DELAY_MS + 120)
  await expect(page.locator('.rail-pad-petals-shown')).toHaveCount(1)
  // The latched direction is the highlighted one.
  await expect(page.locator('.rail-pad-petal-active')).toHaveText('up')
  await finger.up()
  await expect(page.locator('.rail-pad-petals')).toHaveCount(0)
})

test('the petals escape the strip that clips everything else', async ({ page }) => {
  // They are portalled to the body for exactly this reason: the rail's scroller sits
  // between the pane's transform and the chip, so a `position:fixed` child of the chip
  // would still be clipped at the edge of the strip.
  const { finger } = await flick(page, PAD, -REACH_PX, 0)
  await page.waitForTimeout(RAIL_PAD_PETAL_DELAY_MS + 120)
  const escaped = await page.evaluate(() => {
    const petals = document.querySelector('.rail-pad-petals')
    return !!petals && petals.parentElement === document.body
  })
  await finger.up()
  expect(escaped).toBe(true)
})

test('the petals take no pointer events, so they cannot interrupt the gesture', async ({ page }) => {
  const { finger } = await flick(page, PAD, 0, -REACH_PX)
  await page.waitForTimeout(RAIL_PAD_PETAL_DELAY_MS + 120)
  const inert = await page.evaluate(() => {
    const petals = document.querySelector<HTMLElement>('.rail-pad-petals')!
    return [petals, ...Array.from(petals.querySelectorAll<HTMLElement>('.rail-pad-petal'))]
      .every(node => getComputedStyle(node).pointerEvents === 'none')
  })
  await finger.up()
  expect(inert).toBe(true)
})

test('a pad marks its populated directions without costing a pixel of width', async ({ page }) => {
  const marks = await page.locator('.rail-pad-cardinal .rail-pad-mark').count()
  expect(marks).toBe(4)
  await expect(page.locator('.rail-pad-diagonal .rail-pad-mark-upLeft')).toHaveCount(1)
  await expect(page.locator('.rail-pad-diagonal .rail-pad-mark-up')).toHaveCount(0)
  // The marks are drawn inside the chip, not laid out: a pad is exactly as wide as the
  // same chip would be with the marks removed.
  const grew = await page.evaluate(() => {
    const pad = document.querySelector<HTMLElement>('.rail-pad-cardinal')!
    const before = pad.getBoundingClientRect()
    const marksNode = pad.querySelector<HTMLElement>('.rail-pad-marks')!
    marksNode.style.display = 'none'
    const after = pad.getBoundingClientRect()
    marksNode.style.display = ''
    return { before: before.width, after: after.width, height: before.height }
  })
  expect(grew.after).toBe(grew.before)
  expect(grew.height).toBeGreaterThan(0)
})

test('a pad keeps its keyboard route: arrows on a cardinal pad, the nav cluster on a diagonal one', async ({ page }) => {
  await page.locator(PAD).focus()
  await page.keyboard.press('ArrowUp')
  await expect.poll(() => sends(page)).toEqual(['\x1b[A'])
  await page.keyboard.press('ArrowLeft')
  // `left` is a release slot; the keyboard path runs it outright, having no drag to leave.
  await expect.poll(() => sends(page)).toEqual(['\x1b[A', 'KILL'])

  await clearRecorders(page)
  await page.locator(JUMP).focus()
  await page.keyboard.press('Home')
  await page.keyboard.press('PageUp')
  await expect.poll(() => sends(page)).toEqual(['\x1b[H', '\x1b[1;5H'])
  // An arrow on a diagonal pad is not one of its keys and must not be invented.
  await page.keyboard.press('ArrowUp')
  await page.waitForTimeout(150)
  expect(await sends(page)).toEqual(['\x1b[H', '\x1b[1;5H'])
})

test('Enter on a focused pad runs its centre', async ({ page }) => {
  await page.locator(PAD).focus()
  await page.keyboard.press('Enter')
  await expect.poll(() => sends(page)).toEqual(['CENTRE'])
})
