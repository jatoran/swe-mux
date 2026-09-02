import { expect, test, type Page } from 'playwright/test'
import { MOBILE_HOLD_DRAG } from '../../src/dragReorder.ts'
import { dragBy, touch, type Finger } from './railTouch.ts'

/**
 * Rearranging the Action rail with a real finger and a real mouse, in a real rail.
 *
 * The three things this exists to hold, none of which a unit test can reach:
 *
 *   * The chips are `pointer-events:none` while arranging, so nothing under the finger is
 *     what receives the press. The chip is found by rectangle instead, against a real
 *     `getBoundingClientRect` in a real scrolling strip.
 *   * `OverflowRail` takes pointer capture on the same touch, which retargets every later
 *     move away from what was pressed - and the arrange drag stands it down through the
 *     pointer-drag claim rather than by asking it to behave.
 *   * The rendered rail is a *filtered projection* of its rows. The seeded row draws `a c d`
 *     over stored slots 0, 2, 3, so any drop measured in pixels has to come back as an index
 *     into the stored row. That translation is the only part of this feature whose failure is
 *     invisible: the drag looks right and the layout saves wrong.
 */

test.use({ hasTouch: true })

/** Comfortably past the hold, plus a frame for the lift to land. Read off the shipped
 *  activation so retuning the hold retunes the spec rather than reddening it. */
const HELD_MS = (MOBILE_HOLD_DRAG.mode === 'hold' ? MOBILE_HOLD_DRAG.delayMs : 0) + 140

const rows = (page: Page): Promise<string[][]> => page.evaluate(() => window.railArrangeRows())
const fires = (page: Page): Promise<string[]> => page.evaluate(() => window.railArrangeFires)

const centreOf = async (page: Page, selector: string) => {
  const box = await page.locator(selector).first().boundingBox()
  if (!box) throw new Error(`${selector} has no box`)
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

/** Enter arrange mode the way a thumb does: hold the row's drawer control. */
async function enterArranging(page: Page) {
  const more = await centreOf(page, '.rail-more')
  const finger = await touch(page)
  await finger.down(more.x, more.y)
  await page.waitForTimeout(700)
  await finger.up()
  await expect(page.locator('.rail-arrange')).toBeVisible()
}

/** Hold a chip until it lifts, drag it onto a target, and let go. */
async function holdDrag(page: Page, from: { x: number; y: number }, to: { x: number; y: number }) {
  const finger: Finger = await touch(page)
  await finger.down(from.x, from.y)
  await page.waitForTimeout(HELD_MS)
  await dragBy(finger, from, to.x - from.x, to.y - from.y, 8)
  await finger.up()
}

test.beforeEach(async ({ page }) => {
  await page.goto('/rail-arrange-harness.html')
  await expect(page.locator('.terminal-action-rail')).toBeVisible()
})

test('holding a row drawer control opens the arrange panel, and Escape closes it', async ({ page }) => {
  await expect(page.locator('.rail-arrange')).toHaveCount(0)
  await enterArranging(page)
  // The panel names the scope before anything is dragged, because a drag is not a moment at
  // which a scope question can be answered.
  await expect(page.locator('.rail-arrange-scope')).toHaveText('Editing the global rail')
  await page.keyboard.press('Escape')
  await expect(page.locator('.rail-arrange')).toHaveCount(0)
})

test('a chip fires nothing while the rail is being arranged', async ({ page }) => {
  await page.locator('[data-key="a"]').first().click()
  expect(await fires(page)).toEqual(['a'])
  await enterArranging(page)
  // Forced, because the chip genuinely has no pointer events: that is the mechanism under
  // test, and the assertion is that the press reaches the row instead of the button.
  await page.locator('.terminal-action-scroll [data-key="a"]').first().click({ force: true })
  expect(await fires(page)).toEqual(['a'])
})

test('a held chip dropped further along its row moves in the stored layout', async ({ page }) => {
  await enterArranging(page)
  const a = await centreOf(page, '.rail-arrange-grid [data-key="a"]')
  const d = await centreOf(page, '.rail-arrange-grid [data-key="d"]')
  await holdDrag(page, a, { x: d.x + 24, y: d.y })
  // `hidden` is at stored index 1 and was never drawn; it must not have moved.
  await expect.poll(() => rows(page)).toEqual([['hidden', 'c', 'd', 'a'], ['b']])
})

test('a drop between the two chips either side of a filtered one lands after it', async ({ page }) => {
  await enterArranging(page)
  // Row 1 draws `a c d` over stored slots 0, 2, 3. Dropping `b` between `a` and `c` is
  // rendered index 1, which is stored index 2 - after the invisible `hidden`, exactly where
  // it looks. A hit test that used its own index would put it at 1 and displace `hidden`.
  const a = await centreOf(page, '.rail-arrange-grid [data-key="a"]')
  const c = await centreOf(page, '.rail-arrange-grid [data-key="c"]')
  const b = await centreOf(page, '.rail-arrange-grid [data-key="b"]')
  await holdDrag(page, b, { x: (a.x + c.x) / 2, y: a.y })
  await expect.poll(() => rows(page)).toEqual([['a', 'hidden', 'b', 'c', 'd']])
})

test('a mouse rearranges by click-hold-drag, with no hold to wait out', async ({ page }) => {
  await enterArranging(page)
  const a = await centreOf(page, '.rail-arrange-grid [data-key="a"]')
  const c = await centreOf(page, '.rail-arrange-grid [data-key="c"]')
  const d = await centreOf(page, '.rail-arrange-grid [data-key="d"]')
  const between = (c.x + d.x) / 2
  await page.mouse.move(a.x, a.y)
  await page.mouse.down()
  for (let step = 1; step <= 8; step += 1) await page.mouse.move(a.x + ((between - a.x) * step) / 8, a.y)
  await page.mouse.up()
  await expect.poll(() => rows(page)).toEqual([['hidden', 'c', 'a', 'd'], ['b']])
})

test('the bin removes a chip and Undo puts it back where it was', async ({ page }) => {
  await enterArranging(page)
  const c = await centreOf(page, '.rail-arrange-grid [data-key="c"]')
  const finger = await touch(page)
  await finger.down(c.x, c.y)
  await page.waitForTimeout(HELD_MS)
  // One move first, so the zones are mounted before the drop is aimed at one: they are drawn
  // only while a chip is in the air.
  await finger.move(c.x, c.y - 12)
  await expect(page.locator('.rail-arrange-zones-live')).toBeVisible()
  const bin = await centreOf(page, '[data-rail-arrange-zone="remove"]')
  await dragBy(finger, { x: c.x, y: c.y - 12 }, bin.x - c.x, bin.y - (c.y - 12), 8)
  await finger.up()
  await expect.poll(() => rows(page)).toEqual([['a', 'hidden', 'd'], ['b']])
  await page.locator('.rail-arrange-head-actions button', { hasText: 'Undo' }).click()
  await expect.poll(() => rows(page)).toEqual([['a', 'hidden', 'c', 'd'], ['b']])
})

test('the new-row target makes a row and puts the chip in it', async ({ page }) => {
  await enterArranging(page)
  const c = await centreOf(page, '.rail-arrange-grid [data-key="c"]')
  const finger = await touch(page)
  await finger.down(c.x, c.y)
  await page.waitForTimeout(HELD_MS)
  await finger.move(c.x, c.y - 12)
  const target = await centreOf(page, '[data-rail-arrange-zone="new-row"]')
  await dragBy(finger, { x: c.x, y: c.y - 12 }, target.x - c.x, target.y - (c.y - 12), 8)
  await finger.up()
  await expect.poll(() => rows(page)).toEqual([['a', 'hidden', 'd'], ['b'], ['c']])
})

test('an action off the rail is dragged on from the tray', async ({ page }) => {
  await enterArranging(page)
  await page.locator('.rail-arrange-head-actions button', { hasText: 'Add' }).click()
  const spare = await centreOf(page, '[data-rail-catalog-item="spare"]')
  const a = await centreOf(page, '.rail-arrange-grid [data-key="a"]')
  await holdDrag(page, spare, { x: a.x - 20, y: a.y })
  await expect.poll(() => rows(page)).toEqual([['spare', 'a', 'hidden', 'c', 'd'], ['b']])
})

test('a chip is dragged onto the live rail as well as inside the panel', async ({ page }) => {
  await enterArranging(page)
  const b = await centreOf(page, '.rail-arrange-grid [data-key="b"]')
  const rail = await centreOf(page, '.terminal-action-scroll [data-key="a"]')
  await holdDrag(page, b, { x: rail.x - 20, y: rail.y })
  await expect.poll(() => rows(page)).toEqual([['b', 'a', 'hidden', 'c', 'd']])
})
