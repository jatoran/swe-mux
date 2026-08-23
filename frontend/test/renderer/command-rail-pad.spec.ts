import { expect, test, type Page } from 'playwright/test'
import { RAIL_KEY_REPEAT_DELAY_MS, RAIL_KEY_REPEAT_INTERVAL_MS } from '../../src/railKeyRepeat.ts'
import {
  RAIL_PAD_DIAL_DELAY_MS,
  RAIL_PAD_OUTER_PX,
  RAIL_PAD_RING_PX,
  railPadBands,
  railPadWedgeCentre,
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

const PAD = '.rail-pad-w3'
const FOUR = '[title="Jump"]'
const RING = '.rail-pad-r2'
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

/** Straight down the middle of one wedge, so a test never depends on where a boundary is. */
const intoWedge = (radius: number, wedge: number, wedges: number) =>
  at(radius, railPadWedgeCentre(wedge, wedges))

async function flick(page: Page, selector: string, delta: { dx: number; dy: number }) {
  const finger = await touch(page)
  const centre = await keyPoint(page, selector)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, delta.dx, delta.dy)
  return { finger, centre }
}

test.beforeEach(async ({ page }) => {
  await page.goto('/command-rail-harness.html')
  await expect(page.locator('.terminal-action-scroll > button')).toHaveCount(19)
  const parked = await page.evaluate(() => {
    const strip = document.querySelector<HTMLElement>('.terminal-action-scroll')!
    const pad = strip.querySelector<HTMLElement>('.rail-pad-w3')!
    strip.scrollLeft = pad.offsetLeft - strip.clientWidth / 2 + pad.offsetWidth / 2
    return { scrollLeft: strip.scrollLeft, maximum: strip.scrollWidth - strip.clientWidth }
  })
  expect(parked.scrollLeft).toBeGreaterThan(0)
  expect(parked.maximum).toBeGreaterThan(parked.scrollLeft)
  await clearRecorders(page)
})

test('a flick up sends the up slot once, before the finger is even lifted', async ({ page }) => {
  const start = await scrollLeft(page)
  const { finger } = await flick(page, PAD, intoWedge(NEAR_PX, 1, 3))
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
  const { finger } = await flick(page, PAD, intoWedge(NEAR_PX, 0, 3))
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['\x1b[C'])
  expect(await scrollLeft(page)).toBe(start)
})

test('a sideways flick that dips below the horizontal still lands in its wedge', async ({ page }) => {
  // The skirt, which exists because a thumb flicking right does not stay on the horizontal.
  const { finger } = await flick(page, PAD, at(NEAR_PX, -12))
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['\x1b[C'])
})

test('pulling down is the abort, and it always has room', async ({ page }) => {
  // The reason the fan gave up its lower half: a rail on the bottom edge of the screen can
  // always complete a downward drag, and nothing down there is a target.
  const { finger } = await flick(page, PAD, at(NEAR_PX, -90))
  expect(await sends(page)).toEqual([])
  await finger.up()
  await page.waitForTimeout(200)
  // Not even the centre, which is bound: an escape that ran a different action would be a
  // redirect rather than an escape.
  expect(await sends(page)).toEqual([])
})

test('the pad claims the pointer while dragging, so the pan and the menu swipe stand down', async ({ page }) => {
  const { finger } = await flick(page, PAD, intoWedge(NEAR_PX, 1, 3))
  const sampled = await claims(page)
  await finger.up()
  expect(sampled.length).toBeGreaterThan(0)
  expect(sampled.every(Boolean)).toBe(true)
})

test('holding a wedge repeats it, and only where the slot says so', async ({ page }) => {
  const held = await flick(page, PAD, intoWedge(NEAR_PX, 1, 3))
  await page.waitForTimeout(HOLD_MS)
  const repeated = await sends(page)
  await held.finger.up()
  expect(repeated.length).toBeGreaterThan(2)
  expect(repeated.every(sequence => sequence === '\x1b[A')).toBe(true)

  await clearRecorders(page)
  const once = await flick(page, PAD, intoWedge(NEAR_PX, 0, 3))
  await page.waitForTimeout(HOLD_MS)
  await once.finger.up()
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual(['\x1b[C'])
})

test('a release slot waits for the lift', async ({ page }) => {
  const { finger } = await flick(page, PAD, intoWedge(NEAR_PX, 2, 3))
  expect(await sends(page)).toEqual([])
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['KILL'])
})

test('dragging back out of a release slot cancels it', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  const out = intoWedge(NEAR_PX, 2, 3)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, out.dx, out.dy)
  await dragBy(finger, { x: centre.x + out.dx, y: centre.y + out.dy }, -out.dx, -out.dy)
  await finger.up()
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual([])
})

test('a tap with no travel runs the centre exactly once', async ({ page }) => {
  // The shipped arrows pad puts Down here, because an upward fan has no south wedge to hold
  // it and "the one with no direction is the one you tap" keeps the arrow pair on one chip.
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  await finger.down(centre.x, centre.y)
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['CENTRE'])
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual(['CENTRE'])
})

test('a drag does not also run the centre on the way out', async ({ page }) => {
  const { finger } = await flick(page, PAD, intoWedge(NEAR_PX, 1, 3))
  await finger.up()
  await page.waitForTimeout(200)
  expect(await sends(page)).toEqual(['\x1b[A'])
})

test('a four-wedge pad reads all four, and each fires as it is crossed', async ({ page }) => {
  const expected = ['\x1b[1;5F', '\x1b[F', '\x1b[H', '\x1b[1;5H']
  for (let wedge = 0; wedge < 4; wedge += 1) {
    await clearRecorders(page)
    const { finger } = await flick(page, FOUR, intoWedge(NEAR_PX, wedge, 4))
    // Before the lift: one ring means no transit, so nothing waits.
    await expect.poll(() => sends(page)).toEqual([expected[wedge]])
    await finger.up()
  }
})

test('reaching the far ring does not fire the near one it crossed', async ({ page }) => {
  // The reason a ringed pad commits on release, and the reason the shipped pads use a fourth
  // wedge instead: the near ring is unavoidably transit.
  const finger = await touch(page)
  const centre = await keyPoint(page, RING)
  const near = intoWedge(NEAR_PX, 1, 2)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, near.dx, near.dy)
  expect(await sends(page)).toEqual([])
  const far = intoWedge(FAR_PX, 1, 2)
  await dragBy(finger, { x: centre.x + near.dx, y: centre.y + near.dy }, far.dx - near.dx, far.dy - near.dy)
  expect(await sends(page)).toEqual([])
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['FAR-L'])
})

test('coming back inward from the far ring lands on the near slot, once', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, RING)
  const far = intoWedge(FAR_PX, 1, 2)
  const near = intoWedge(NEAR_PX, 1, 2)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, far.dx, far.dy)
  await dragBy(finger, { x: centre.x + far.dx, y: centre.y + far.dy }, near.dx - far.dx, near.dy - far.dy)
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['NEAR-L'])
})

// ---------------------------------------------------------------------------
// The soft keyboard
// ---------------------------------------------------------------------------

/** Focus a real text field, so "did the keyboard survive" is a question with an answer.
 *  On Android the keyboard is up exactly while a text field holds focus. */
async function withField(page: Page) {
  await page.evaluate(() => {
    const field = document.createElement('input')
    field.id = 'keeper'
    field.type = 'text'
    // Taken out of flow: an in-flow field pushes the rail down the page, and the touch
    // points this spec computes would then be aimed at where the chip used to be.
    field.style.cssText = 'position:fixed;top:0;left:0;width:40px;z-index:1'
    document.body.append(field)
    field.focus()
  })
  expect(await page.evaluate(() => document.activeElement?.id)).toBe('keeper')
}

test('a pad drag delivers no mouse events, which is why it cannot rely on the usual guard', async ({ page }) => {
  // The measurement the keyboard fix is built on. Every other rail chip acts on `click`, so
  // its `onMouseDown` focus refusal has necessarily already run; a pad acts on `pointermove`,
  // and a drag never produces a `mousedown` for that guard to run in.
  const seen = await page.evaluate(async () => {
    const pad = document.querySelector<HTMLElement>('.rail-pad-w3')!
    const log: string[] = []
    for (const type of ['pointerdown', 'mousedown', 'click', 'touchend']) {
      pad.addEventListener(type, () => log.push(type))
    }
    ;(window as unknown as { padEvents: string[] }).padEvents = log
    return true
  })
  expect(seen).toBe(true)
  const { finger } = await flick(page, PAD, intoWedge(NEAR_PX, 1, 3))
  await finger.up()
  await page.waitForTimeout(250)
  const events = await page.evaluate(() => (window as unknown as { padEvents: string[] }).padEvents)
  expect(events).toContain('pointerdown')
  expect(events).not.toContain('mousedown')
})

test('a pad drag hands the soft keyboard back after focus is taken mid-gesture', async ({ page }) => {
  // The blur is the point. Desktop Chromium never drops focus during a pad drag, so a test
  // that merely dragged and checked would pass with the fix removed - checked, it does. What
  // Android actually does is take focus away *because* nothing refused it, so that is what
  // is reproduced here: blur mid-drag, and assert the gesture puts it back on the lift.
  await withField(page)
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  const up = intoWedge(NEAR_PX, 1, 3)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, up.dx, up.dy)
  await page.evaluate(() => (document.getElementById('keeper') as HTMLInputElement).blur())
  expect(await page.evaluate(() => document.activeElement?.id)).not.toBe('keeper')
  await finger.up()
  // On a frame after the gesture ends, so it lands after the slot's own action has done
  // whatever it does with focus.
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('keeper')
  expect(await sends(page)).toEqual(['\x1b[A'])
})

test('a deliberate dismissal during a gesture still wins', async ({ page }) => {
  // The other half of `restoreSoftKeyboard`: it is gated on the dismissal counter, so a
  // keyboard the operator put away on purpose is not dragged back up by the pad.
  await withField(page)
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  const up = intoWedge(NEAR_PX, 1, 3)
  await finger.down(centre.x, centre.y)
  await dragBy(finger, centre, up.dx, up.dy)
  await page.evaluate(() => { window.railDismissKeyboard() })
  expect(await page.evaluate(() => document.activeElement?.id)).not.toBe('keeper')
  await finger.up()
  await page.waitForTimeout(250)
  expect(await page.evaluate(() => document.activeElement?.id)).not.toBe('keeper')
  expect(await sends(page)).toEqual(['\x1b[A'])
})

test('a pad tap keeps the keyboard too', async ({ page }) => {
  await withField(page)
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  await finger.down(centre.x, centre.y)
  await finger.up()
  await expect.poll(() => sends(page)).toEqual(['CENTRE'])
  expect(await page.evaluate(() => document.activeElement?.id)).toBe('keeper')
})

// ---------------------------------------------------------------------------
// The dial
// ---------------------------------------------------------------------------

test('the dial is held back by the delay, and the gesture is not', async ({ page }) => {
  const finger = await touch(page)
  const centre = await keyPoint(page, PAD)
  // A bare press, because a multi-step drag takes longer than the delay on its own.
  await finger.down(centre.x, centre.y)
  await expect(page.locator('.rail-pad-dial')).toHaveCount(1)
  expect(await page.locator('.rail-pad-dial-shown').count()).toBe(0)
  const up = intoWedge(NEAR_PX, 1, 3)
  await finger.move(centre.x + up.dx, centre.y + up.dy)
  await expect.poll(() => sends(page)).toEqual(['\x1b[A'])

  await page.waitForTimeout(RAIL_PAD_DIAL_DELAY_MS + 140)
  await expect(page.locator('.rail-pad-dial-shown')).toHaveCount(1)
  await expect(page.locator('.rail-pad-wedge-active text')).toHaveText('up')
  await finger.up()
  await expect(page.locator('.rail-pad-dial')).toHaveCount(0)
})

test('the dial draws one wedge per slot, with a real outline', async ({ page }) => {
  const { finger } = await flick(page, FOUR, intoWedge(NEAR_PX, 2, 4))
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
    expect(wedge.width).toBeGreaterThan(40)
    expect(wedge.height).toBeGreaterThan(30)
  }
})

test('a three-wedge dial draws three, and a ringed one draws four', async ({ page }) => {
  const three = await flick(page, PAD, intoWedge(NEAR_PX, 1, 3))
  await page.waitForTimeout(RAIL_PAD_DIAL_DELAY_MS + 140)
  await expect(page.locator('.rail-pad-wedge')).toHaveCount(3)
  await three.finger.up()

  const ringed = await flick(page, RING, intoWedge(NEAR_PX, 1, 2))
  await page.waitForTimeout(RAIL_PAD_DIAL_DELAY_MS + 140)
  await expect(page.locator('.rail-pad-wedge')).toHaveCount(4)
  await ringed.finger.up()
})

test('the dial is thumb-sized, and its wedges reach the radii the gesture uses', async ({ page }) => {
  const { finger } = await flick(page, RING, intoWedge(NEAR_PX, 1, 2))
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
  expect(measured.width).toBeCloseTo(RAIL_PAD_OUTER_PX * 2, 0)
  expect(measured.far).toBeGreaterThan(RAIL_PAD_RING_PX)
})

test('the dial escapes the strip that clips everything else', async ({ page }) => {
  const { finger } = await flick(page, PAD, intoWedge(NEAR_PX, 2, 3))
  await page.waitForTimeout(RAIL_PAD_DIAL_DELAY_MS + 140)
  const escaped = await page.evaluate(() => {
    const dial = document.querySelector('.rail-pad-dial')
    return !!dial && dial.parentElement === document.body
  })
  await finger.up()
  expect(escaped).toBe(true)
})

test('nothing in the dial takes pointer events', async ({ page }) => {
  const { finger } = await flick(page, PAD, intoWedge(NEAR_PX, 1, 3))
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
  const { finger } = await flick(page, PAD, intoWedge(NEAR_PX, 1, 3))
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
  // Semi-transparent rather than opaque: the terminal has to stay legible underneath.
  // Matched on the fractional alpha alone, because `color-mix` resolves to `color(srgb …)`
  // in Chrome and to `rgba(…)` elsewhere, and the assertion is about neither spelling.
  expect(scrim!.background).toMatch(/0\.\d+/)
})

test('a squeezed pad shrinks its dial and its ring together', async ({ page }) => {
  await page.evaluate(() => {
    const rail = document.querySelector<HTMLElement>('.terminal-action-rail')!
    rail.style.position = 'fixed'
    rail.style.top = '0px'
    rail.style.left = '0px'
    rail.style.right = '0px'
  })
  const { finger } = await flick(page, RING, at(30, 145))
  await page.waitForTimeout(RAIL_PAD_DIAL_DELAY_MS + 140)
  const width = await page.evaluate(() =>
    document.querySelector<SVGSVGElement>('.rail-pad-dial-svg')!.getBoundingClientRect().width)
  await finger.up()
  expect(width).toBeLessThan(RAIL_PAD_OUTER_PX * 2)
  expect(width).toBeGreaterThan(railPadBands(2, 0.45).outer)
})

test('a pad marks its populated wedges without costing a pixel of width', async ({ page }) => {
  expect(await page.locator('.rail-pad-w3 .rail-pad-mark').count()).toBe(3)
  expect(await page.locator(`${FOUR} .rail-pad-mark`).count()).toBe(4)
  expect(await page.locator('.rail-pad-r2 .rail-pad-mark').count()).toBe(4)
  // The marks are drawn inside the chip, not laid out: a pad is exactly as wide as the same
  // chip would be with the marks removed.
  const grew = await page.evaluate(() => {
    const pad = document.querySelector<HTMLElement>('.rail-pad-w3')!
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

test('a pad keeps a keyboard route: number keys everywhere, arrows on a three-wedge pad', async ({ page }) => {
  await page.locator(PAD).focus()
  // `1` is the leftmost wedge, reading the dial as drawn.
  await page.keyboard.press('1')
  await expect.poll(() => sends(page)).toEqual(['KILL'])
  await page.keyboard.press('2')
  await expect.poll(() => sends(page)).toEqual(['KILL', '\x1b[A'])
  // Arrows stay as a shorthand for the three-wedge case, where left/up/right *are* the wedges.
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => sends(page)).toEqual(['KILL', '\x1b[A', '\x1b[C'])
  // There is no downward wedge to reach, so the key does nothing rather than inventing one.
  await page.keyboard.press('ArrowDown')
  await page.waitForTimeout(150)
  expect(await sends(page)).toHaveLength(3)

  await clearRecorders(page)
  await page.locator(FOUR).focus()
  for (const key of ['1', '2', '3', '4']) await page.keyboard.press(key)
  await expect.poll(() => sends(page)).toEqual(['\x1b[1;5H', '\x1b[H', '\x1b[F', '\x1b[1;5F'])
  // Four wedges are not three, so the arrow shorthand does not apply and must not guess.
  await page.keyboard.press('ArrowUp')
  await page.waitForTimeout(150)
  expect(await sends(page)).toHaveLength(4)
})

test('the number keys continue into a second ring', async ({ page }) => {
  await page.locator(RING).focus()
  for (const key of ['1', '2', '3', '4']) await page.keyboard.press(key)
  await expect.poll(() => sends(page)).toEqual(['NEAR-L', 'NEAR-R', 'FAR-L', 'FAR-R'])
  // Nothing beyond the slots the pad actually has.
  await page.keyboard.press('5')
  await page.waitForTimeout(150)
  expect(await sends(page)).toHaveLength(4)
})

test('Enter on a focused pad runs its centre', async ({ page }) => {
  await page.locator(PAD).focus()
  await page.keyboard.press('Enter')
  await expect.poll(() => sends(page)).toEqual(['CENTRE'])
})
