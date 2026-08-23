import { expect, test, type Page } from 'playwright/test'
import { RAIL_KEY_REPEAT_DELAY_MS, RAIL_KEY_REPEAT_INTERVAL_MS } from '../../src/railKeyRepeat.ts'
import {
  RAIL_PAD_DIAL_DELAY_MS,
  RAIL_PAD_OUTER_PX,
  RAIL_PAD_RING_PX,
  railPadBands,
} from '../../src/railPadGesture.ts'
import { claims, clearRecorders, dragBy, keyPoint, scrollLeft, sends, touch } from './railTouch.ts'

/**
 * What a pad chip does with a finger, in a real rail.
 *
 * Everything worth doubting about a pad is an interaction between three things no unit test
 * can hold at once: the pad's own geometry, `OverflowRail`'s pointer capture (which
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
const HOLD_MS = RAIL_KEY_REPEAT_DELAY_MS + RAIL_KEY_REPEAT_INTERVAL_MS * 6
/** Comfortably inside the near ring, and comfortably past the dead radius. */
const NEAR_PX = 60
/** Comfortably past the ring boundary. */
const FAR_PX = RAIL_PAD_RING_PX + 46

/** A displacement at a distance and an angle, in the dial's own convention: degrees
 *  counter-clockwise from due east, with up positive. */
const at = (radius: number, degrees: number) => ({
  dx: radius * Math.cos(degrees * Math.PI / 180),
  dy: -radius * Math.sin(degrees * Math.PI / 180),
})

async function flick(page: Page, selector: string, radius: number, degrees: number) {
  const finger = await touch(page)
  const centre = await keyPoint(page, selector)
  const { dx, dy } = at(radius, degrees)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, dx, dy)
  return { finger, centre, to: { x: centre.x + dx, y: centre.y + dy } }
}

test.beforeEach(async ({ page }) => {
  await page.goto('/command-rail-harness.html')
  await expect(page.locator('.terminal-action-scroll > button')).toHaveCount(18)
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
  const { finger } = await flick(page, PAD, NEAR_PX, 90)
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
  const { finger } = await flick(page, PAD, NEAR_PX, 0)
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['\x1b[C'])
  expect(await scrollLeft(page)).toBe(start)
})

test('a sideways flick that dips below the horizontal still lands in its wedge', async ({ page }) => {
  // The skirt, which exists because a thumb flicking right does not stay on the horizontal.
  const { finger } = await flick(page, PAD, NEAR_PX, -12)
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['\x1b[C'])
})

test('pulling down is the abort, and it always has room', async ({ page }) => {
  // The reason the fan gave up its lower half: a rail on the bottom edge of the screen can
  // always complete a downward drag, and nothing down there is a target.
  const { finger } = await flick(page, PAD, NEAR_PX, -90)
  expect(await sends(page)).toEqual([])
  await finger.up()
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual([])
})

test('a committed direction can be abandoned by pulling down through the centre', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  await finger.down(centre.x, centre.y)
  const up = at(NEAR_PX, 90)
  await dragBy(finger, centre, up.dx, up.dy)
  await expect.poll(() => sends(page)).toEqual(['\x1b[A'])
  const down = at(NEAR_PX, -90)
  await dragBy(finger, { x: centre.x + up.dx, y: centre.y + up.dy }, down.dx - up.dx, down.dy - up.dy)
  await finger.up()
  await page.waitForTimeout(200)
  // Up already fired on the way in; the abort adds nothing and runs no centre.
  expect(await sends(page)).toEqual(['\x1b[A'])
})

test('the pad claims the pointer while dragging, so the pan and the menu swipe stand down', async ({ page }) => {
  const { finger } = await flick(page, PAD, NEAR_PX, 90)
  const sampled = await claims(page)
  await finger.up()
  expect(sampled.length).toBeGreaterThan(0)
  expect(sampled.every(Boolean)).toBe(true)
})

test('holding a direction repeats it, and only where the slot says so', async ({ page }) => {
  const held = await flick(page, PAD, NEAR_PX, 90)
  await page.waitForTimeout(HOLD_MS)
  const repeated = await sends(page)
  await held.finger.up()
  expect(repeated.length).toBeGreaterThan(2)
  expect(repeated.every(sequence => sequence === '\x1b[A')).toBe(true)

  await clearRecorders(page)
  const once = await flick(page, PAD, NEAR_PX, 0)
  await page.waitForTimeout(HOLD_MS)
  await once.finger.up()
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual(['\x1b[C'])
})

test('a release slot waits for the lift', async ({ page }) => {
  const { finger } = await flick(page, PAD, NEAR_PX, 180)
  expect(await sends(page)).toEqual([])
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['KILL'])
})

test('dragging back out of a release slot cancels it', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  const out = at(NEAR_PX, 180)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, out.dx, out.dy)
  await dragBy(finger, { x: centre.x + out.dx, y: centre.y + out.dy }, -out.dx, -out.dy)
  await finger.up()
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual([])
})

test('leaving a release slot sideways runs the wedge it left for, and not the armed one', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  const armed = at(NEAR_PX, 180)
  const chosen = at(NEAR_PX, 0)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, armed.dx, armed.dy)
  await dragBy(finger, { x: centre.x + armed.dx, y: centre.y + armed.dy }, chosen.dx - armed.dx, chosen.dy - armed.dy)
  await finger.up()
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual(['\x1b[C'])
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
  const { finger } = await flick(page, PAD, NEAR_PX, 90)
  await finger.up()
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual(['\x1b[A'])
})

test('a diagonal pad reads two wedges over two rings', async ({ page }) => {
  const targets: Array<[number, number, string]> = [
    [NEAR_PX, 145, '\x1b[H'],
    [NEAR_PX, 35, '\x1b[1;5H'],
    [FAR_PX, 145, '\x1b[F'],
    [FAR_PX, 35, '\x1b[1;5F'],
  ]
  for (const [radius, degrees, expected] of targets) {
    await clearRecorders(page)
    const { finger } = await flick(page, JUMP, radius, degrees)
    await finger.up()
    await expect.poll(() => sends(page)).toEqual([expected])
  }
})

test('reaching the far ring does not fire the near one it crossed', async ({ page }) => {
  // The reason a ringed pad commits on release: the near ring is unavoidably transit, so a
  // near slot that fired on entry would fire every single time somebody went past it.
  const finger = await touch(page)
  const centre = await keyPoint(page, JUMP)
  const near = at(NEAR_PX, 145)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, near.dx, near.dy)
  expect(await sends(page)).toEqual([])
  const far = at(FAR_PX, 145)
  await dragBy(finger, { x: centre.x + near.dx, y: centre.y + near.dy }, far.dx - near.dx, far.dy - near.dy)
  expect(await sends(page)).toEqual([])
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['\x1b[F'])
})

test('coming back inward from the far ring lands on the near slot, once', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, JUMP)
  const far = at(FAR_PX, 145)
  const near = at(NEAR_PX, 145)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, far.dx, far.dy)
  await dragBy(finger, { x: centre.x + far.dx, y: centre.y + far.dy }, near.dx - far.dx, near.dy - far.dy)
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['\x1b[H'])
})

test('the dial is held back by the delay, and the gesture is not', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  // A bare press, because a multi-step drag takes longer than the delay on its own.
  await finger.down(centre.x, centre.y)
  await expect(page.locator('.rail-pad-dial')).toHaveCount(1)
  expect(await page.locator('.rail-pad-dial-shown').count()).toBe(0)
  // One step is all the gesture needs, and it fires while the dial is still hidden.
  const up = at(NEAR_PX, 90)
  await finger.move(centre.x + up.dx, centre.y + up.dy)
  await expect.poll(() => sends(page)).toEqual(['\x1b[A'])

  await page.waitForTimeout(RAIL_PAD_DIAL_DELAY_MS + 140)
  await expect(page.locator('.rail-pad-dial-shown')).toHaveCount(1)
  await expect(page.locator('.rail-pad-wedge-active text')).toHaveText('up')
  await finger.up()
  await expect(page.locator('.rail-pad-dial')).toHaveCount(0)
})

test('the dial draws one wedge per direction, with a real outline', async ({ page }) => {
  const { finger } = await flick(page, JUMP, NEAR_PX, 145)
  await page.waitForTimeout(RAIL_PAD_DIAL_DELAY_MS + 140)
  await expect(page.locator('.rail-pad-wedge')).toHaveCount(4)
  const drawn = await page.evaluate(() => {
    const paths = Array.from(document.querySelectorAll<SVGPathElement>('.rail-pad-wedge > path'))
    return paths.map(path => {
      const style = getComputedStyle(path)
      const box = path.getBBox()
      return { stroke: style.strokeWidth, filled: style.fill !== 'none', width: box.width, height: box.height }
    })
  })
  await finger.up()
  expect(drawn).toHaveLength(4)
  for (const wedge of drawn) {
    expect(wedge.filled).toBe(true)
    expect(parseFloat(wedge.stroke)).toBeGreaterThan(0)
    // A real area, not a hairline: this is the complaint the dial replaced.
    expect(wedge.width).toBeGreaterThan(60)
    expect(wedge.height).toBeGreaterThan(30)
  }
})

test('the dial is thumb-sized, and its wedges reach the radii the gesture uses', async ({ page }) => {
  const { finger } = await flick(page, JUMP, NEAR_PX, 145)
  await page.waitForTimeout(RAIL_PAD_DIAL_DELAY_MS + 140)
  const measured = await page.evaluate(() => {
    const svg = document.querySelector<SVGSVGElement>('.rail-pad-dial-svg')!
    const box = svg.getBoundingClientRect()
    const far = Array.from(document.querySelectorAll<SVGGElement>('.rail-pad-wedge'))
      .map(node => node.querySelector('path')!.getBBox())
      .reduce((widest, current) => Math.max(widest, Math.hypot(current.x, current.y)), 0)
    return { width: box.width, far }
  })
  await finger.up()
  // The whole dial spans the outer radius either side of the finger.
  expect(measured.width).toBeCloseTo(RAIL_PAD_OUTER_PX * 2, 0)
  expect(measured.far).toBeGreaterThan(RAIL_PAD_RING_PX)
})

test('the dial escapes the strip that clips everything else', async ({ page }) => {
  // Portalled to the body for exactly this reason: the rail's scroller sits between the
  // pane's transform and the chip, so a `position:fixed` child of the chip would still be
  // clipped at the edge of the strip.
  const { finger } = await flick(page, PAD, NEAR_PX, 180)
  await page.waitForTimeout(RAIL_PAD_DIAL_DELAY_MS + 140)
  const escaped = await page.evaluate(() => {
    const dial = document.querySelector('.rail-pad-dial')
    return !!dial && dial.parentElement === document.body
  })
  await finger.up()
  expect(escaped).toBe(true)
})

test('nothing in the dial takes pointer events', async ({ page }) => {
  const { finger } = await flick(page, PAD, NEAR_PX, 90)
  await page.waitForTimeout(RAIL_PAD_DIAL_DELAY_MS + 140)
  const inert = await page.evaluate(() => {
    const dial = document.querySelector<HTMLElement>('.rail-pad-dial')!
    return [dial, ...Array.from(dial.querySelectorAll<HTMLElement>('*'))]
      .every(node => getComputedStyle(node).pointerEvents === 'none')
  })
  await finger.up()
  expect(inert).toBe(true)
})

test('the dial draws a wash over the workspace', async ({ page }) => {
  const { finger } = await flick(page, PAD, NEAR_PX, 90)
  await page.waitForTimeout(RAIL_PAD_DIAL_DELAY_MS + 140)
  const scrim = await page.evaluate(() => {
    const node = document.querySelector<HTMLElement>('.rail-pad-dial-scrim')
    if (!node) return null
    const box = node.getBoundingClientRect()
    return { background: getComputedStyle(node).backgroundColor, width: box.width, height: box.height }
  })
  await finger.up()
  expect(scrim).not.toBeNull()
  expect(scrim!.width).toBeGreaterThan(0)
  expect(scrim!.height).toBeGreaterThan(0)
  // Semi-transparent rather than opaque: the terminal has to stay legible underneath.
  // Matched on the fractional alpha alone, because `color-mix` resolves to `color(srgb …)`
  // in Chrome and to `rgba(…)` elsewhere, and the assertion is about neither spelling.
  expect(scrim!.background).toMatch(/0\.\d+/)
})

test('a squeezed pad shrinks its dial and its ring together', async ({ page }) => {
  // A pad with little room above it cannot ask for the full reach, and the drawing has to
  // move with the threshold or it would be describing a boundary that is not there.
  const squeezed = await page.evaluate(() => {
    const rail = document.querySelector<HTMLElement>('.terminal-action-rail')!
    rail.style.position = 'fixed'
    rail.style.top = '0px'
    rail.style.left = '0px'
    rail.style.right = '0px'
    return true
  })
  expect(squeezed).toBe(true)
  const { finger } = await flick(page, JUMP, 30, 145)
  await page.waitForTimeout(RAIL_PAD_DIAL_DELAY_MS + 140)
  const width = await page.evaluate(() =>
    document.querySelector<SVGSVGElement>('.rail-pad-dial-svg')!.getBoundingClientRect().width)
  await finger.up()
  expect(width).toBeLessThan(RAIL_PAD_OUTER_PX * 2)
  expect(width).toBeGreaterThan(railPadBands('diagonal', 0.45).outer)
})

test('a pad marks its populated directions without costing a pixel of width', async ({ page }) => {
  expect(await page.locator('.rail-pad-cardinal .rail-pad-mark').count()).toBe(3)
  await expect(page.locator('.rail-pad-diagonal .rail-pad-mark-upLeftFar')).toHaveCount(1)
  await expect(page.locator('.rail-pad-cardinal .rail-pad-mark-upLeft')).toHaveCount(0)
  // The marks are drawn inside the chip, not laid out: a pad is exactly as wide as the same
  // chip would be with the marks removed.
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
  // There is no downward slot to reach, so the key does nothing rather than inventing one.
  await page.keyboard.press('ArrowDown')
  await page.waitForTimeout(150)
  expect(await sends(page)).toEqual(['\x1b[A', 'KILL'])

  await clearRecorders(page)
  await page.locator(JUMP).focus()
  await page.keyboard.press('Home')
  await page.keyboard.press('PageUp')
  await page.keyboard.press('End')
  await page.keyboard.press('PageDown')
  await expect.poll(() => sends(page)).toEqual(['\x1b[H', '\x1b[1;5H', '\x1b[F', '\x1b[1;5F'])
})

test('Enter on a focused pad runs its centre', async ({ page }) => {
  await page.locator(PAD).focus()
  await page.keyboard.press('Enter')
  await expect.poll(() => sends(page)).toEqual(['CENTRE'])
})
