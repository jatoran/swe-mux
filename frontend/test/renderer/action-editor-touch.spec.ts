import { expect, test, type Page } from 'playwright/test'
import { dragBy, touch } from './railTouch'

/**
 * The Configure Actions editor's hold-to-drag on a phone.
 *
 * These are renderer specs rather than unit tests for the same reason the rail's are: the
 * failure this file exists for is not in any pure function. `beginRailDrag` used to be a
 * second, independent copy of the workspace drag engine that never received the hardening
 * the workspace one did, and the result on a real phone was a reorder that worked about a
 * third of the time. Every mechanism involved — whether the browser latches a pan off a
 * held finger's jitter, whether that pan cancels the pointer, what a touch's implicit
 * pointer capture does — belongs to Chrome, not to us, so only real touches through CDP
 * reproduce it.
 *
 * The three failure shapes, one test each:
 *  - a held finger jitters, the browser starts scrolling, and the drag dies unfelt;
 *  - the drop lands a few pixels outside the row's box and commits nothing;
 *  - a lift with no travel writes the layout back unchanged.
 */

test.use({ hasTouch: true, viewport: { width: 390, height: 780 } })

const HOLD_MS = 450

/** The editor opens on this device's own layout, which at this viewport is Mobile.
 *  `compact=1` seeds a five-chip saved row: these specs exercise drag *mechanics*,
 *  and the shipped mobile default is two dense rows whose wrapping would couple
 *  chip geometry to the layout under test. */
const open = async (page: Page) => {
  await page.goto('/action-editor-harness.html?seen=1&compact=1')
  await expect(page.locator('.rail-surface')).toHaveCount(1)
  // Exactly the compact seed: if this reads the shipped default instead, the
  // geometry assumptions below are void and every failure would mislead.
  await expect(page.locator('.rail-chips').first().locator('.rail-chip:not(.ghost)')).toHaveCount(5)
}

const row = (page: Page) => page.locator('.rail-chips').first()

/** The first row's buttons, in the order they are drawn. */
const order = (page: Page): Promise<string[]> =>
  row(page).locator('.rail-chip:not(.ghost) .rail-chip-label').allTextContents()

const centreOf = async (page: Page, index: number) => {
  const box = await row(page).locator('.rail-chip:not(.ghost)').nth(index).boundingBox()
  if (!box) throw new Error(`chip ${index} has no box`)
  return { x: box.x + box.width / 2, y: box.y + box.height / 2, box }
}

/** Where a finger presses a chip: the left side of its label, not the chip's centre. A
 *  chip is the label plus a trailing remove button, and on a short label the centre sits
 *  close enough to the `×` that Chrome's fat-finger target adjustment picks the button -
 *  which removes instead of lifting, and is also not where a person aims. */
const pressPoint = async (page: Page, index: number) => {
  const box = await row(page).locator('.rail-chip:not(.ghost)').nth(index).locator('.rail-chip-label').boundingBox()
  if (!box) throw new Error(`chip ${index} has no label box`)
  return { x: box.x + Math.min(4, box.width / 2), y: box.y + box.height / 2, box }
}

const ghost = (page: Page) => page.locator('.mux-pointer-drag-ghost')

test('a held chip lifts through the jitter of a resting finger, then reorders', async ({ page }) => {
  await open(page)
  const before = await order(page)
  expect(before.length, 'this test needs a row with something to reorder').toBeGreaterThan(1)

  const finger = await touch(page)
  const first = await pressPoint(page, 0)
  const second = await centreOf(page, 1)
  const scrollBefore = await page.evaluate(() => document.querySelector('.settings-content')!.scrollTop)

  await finger.down(first.x, first.y)
  // Past Chrome's own 8px touch slop and well inside the drag's 16px: a finger resting on a
  // phone moves this much, and before the fix it was enough for the browser to latch a pan,
  // which then ignores every `preventDefault` and cancels the pointer out from under the hold.
  await dragBy(finger, first, 0, 12, 4)
  await page.waitForTimeout(HOLD_MS)
  await expect(ghost(page), 'the hold should have lifted the chip').toHaveCount(1)

  // Past the second chip's midpoint, so the drop resolves to "after it".
  await dragBy(finger, { x: first.x, y: first.y + 12 }, second.box.x + second.box.width - first.x, second.y - (first.y + 12))
  await finger.up()

  await expect(ghost(page)).toHaveCount(0)
  expect(await order(page)).toEqual([before[1], before[0], ...before.slice(2)])
  expect(
    await page.evaluate(() => document.querySelector('.settings-content')!.scrollTop),
    'the hold must not have scrolled the editor under the finger',
  ).toBe(scrollBefore)
})

test('a drop that lands just outside the row still goes into it', async ({ page }) => {
  await open(page)
  const before = await order(page)
  expect(before.length).toBeGreaterThan(1)

  const finger = await touch(page)
  const first = await pressPoint(page, 0)
  const rowBox = (await row(page).boundingBox())!
  const last = await centreOf(page, before.length - 1)

  await finger.down(first.x, first.y)
  await page.waitForTimeout(HOLD_MS)
  await expect(ghost(page)).toHaveCount(1)
  // A fingertip covers the strip it is aiming at, so the release lands a little below the
  // row's own box. An exact hit test reads that as "off every row" and commits nothing —
  // the drag visibly worked and then did nothing, which is the worst of the failure shapes.
  await dragBy(finger, first, last.box.x + last.box.width - 2 - first.x, rowBox.y + rowBox.height + 10 - first.y)
  await finger.up()

  await expect(ghost(page)).toHaveCount(0)
  expect(await order(page)).toEqual([...before.slice(1), before[0]])
})

test('lifting a chip and letting it go where it sat leaves the layout alone', async ({ page }) => {
  await open(page)
  const before = await order(page)

  const finger = await touch(page)
  const first = await pressPoint(page, 0)
  await finger.down(first.x, first.y)
  await page.waitForTimeout(HOLD_MS)
  await expect(ghost(page)).toHaveCount(1)
  await finger.up()

  await expect(ghost(page)).toHaveCount(0)
  await expect(page.locator('.rail-chip.dragging')).toHaveCount(0)
  expect(await order(page)).toEqual(before)
})

test('a finger that travels before the hold scrolls instead of dragging', async ({ page }) => {
  await open(page)
  const before = await order(page)

  const finger = await touch(page)
  const first = await pressPoint(page, 0)
  await finger.down(first.x, first.y)
  // Past the hold's slop and long before its delay: this gesture is a scroll the drag never
  // owned, and it must stay one even though it started on a chip.
  await dragBy(finger, first, 0, -70, 7)
  await page.waitForTimeout(HOLD_MS)
  await expect(ghost(page), 'a pre-hold scroll must not lift the chip').toHaveCount(0)
  await finger.up()

  expect(await order(page)).toEqual(before)
})
