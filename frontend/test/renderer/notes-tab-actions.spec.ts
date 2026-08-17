import { expect, test, type Page } from 'playwright/test'

/**
 * A note's actions live on its tab, and the tab lives inside a rail that takes the pointer
 * away from it.
 *
 * `OverflowRail` calls `setPointerCapture` on its wrapper the moment a touch lands on an
 * overflowing strip, so every later pointer event for that finger is retargeted to the rail.
 * A long-press implemented with the tab's own `pointermove`/`pointerup` handlers therefore
 * never learns that the finger moved or lifted: it opens its menu partway through someone's
 * scroll, and cannot be cancelled. Only trusted input reproduces that — synthetic
 * `PointerEvent`s cannot be captured — so these drive real touches through CDP.
 *
 * The two directions are both regressions waiting to happen: a hold must still reach the
 * menu *through* the capture, and a drag must not.
 */

test.use({ hasTouch: true })

const HOLD_MS = 700

async function touch(page: Page) {
  const cdp = await page.context().newCDPSession(page)
  type TouchType = 'touchStart' | 'touchMove' | 'touchEnd' | 'touchCancel'
  const send = (type: TouchType, points: Array<{ x: number; y: number }>) =>
    cdp.send('Input.dispatchTouchEvent', {
      type,
      touchPoints: points.map(point => ({ x: point.x, y: point.y })),
    })
  return {
    down: (x: number, y: number) => send('touchStart', [{ x, y }]),
    move: (x: number, y: number) => send('touchMove', [{ x, y }]),
    up: () => send('touchEnd', []),
  }
}

/**
 * A point on the first Project-note tab — the one after Scratchpad — that is genuinely
 * inside the rail's visible window. The strip is scrolled and clipped, so a tab's layout
 * box can sit past the strip's right edge and a touch aimed at its centre lands on
 * whatever button follows the rail instead.
 */
async function noteTabPoint(page: Page) {
  const point = await page.evaluate(() => {
    const strip = document.querySelector<HTMLElement>('.notes-subtabs')!
    const tab = strip.children[1] as HTMLElement
    const stripBox = strip.getBoundingClientRect()
    const tabBox = tab.getBoundingClientRect()
    const left = Math.max(tabBox.left, stripBox.left)
    const right = Math.min(tabBox.right, stripBox.right)
    return right - left < 8 ? null : { x: (left + right) / 2, y: tabBox.top + tabBox.height / 2 }
  })
  if (!point) throw new Error('the first Project-note tab is not visible in the rail')
  return point
}

test.beforeEach(async ({ page }) => {
  await page.goto('/notes-tab-harness.html')
  await expect(page.locator('.notes-subtabs > button')).toHaveCount(9)
  // The rail has to actually overflow, or it never claims the pointer and the test
  // silently stops covering the thing it exists for.
  const overflowing = await page.evaluate(() => {
    const strip = document.querySelector<HTMLElement>('.notes-subtabs')!
    return strip.scrollWidth > strip.clientWidth + 1
  })
  expect(overflowing).toBe(true)
})

test('holding a rail tab opens its actions menu even while the rail owns the pointer', async ({ page }) => {
  const finger = await touch(page)
  const centre = await noteTabPoint(page)

  await finger.down(centre.x, centre.y)
  await page.waitForTimeout(HOLD_MS)
  await finger.up()

  const menu = page.locator('.note-row-menu')
  await expect(menu).toBeVisible()
  await expect(menu.getByRole('menuitem', { name: 'Rename…' })).toBeVisible()
  await expect(menu.getByRole('menuitem', { name: 'Delete note' })).toBeEnabled()
  // The hold must not also activate the tab it was held on.
  await expect(page.locator('.notes-subtabs > button.active')).toHaveCount(1)
})

test('dragging along the rail scrolls it and opens no menu', async ({ page }) => {
  const finger = await touch(page)
  const centre = await noteTabPoint(page)

  await finger.down(centre.x, centre.y)
  for (let step = 1; step <= 6; step += 1) await finger.move(centre.x - step * 14, centre.y)
  // Held past the long-press threshold *after* moving: the timer is cancelled by the
  // motion, not by the finger lifting in time.
  await page.waitForTimeout(HOLD_MS)
  await finger.up()

  await expect(page.locator('.note-row-menu')).toHaveCount(0)
  const scrolled = await page.evaluate(() => document.querySelector('.notes-subtabs')!.scrollLeft)
  expect(scrolled).toBeGreaterThan(0)
})

test("a Project's only note offers a disabled delete and says why", async ({ page }) => {
  await page.goto('/notes-tab-harness.html?notes=one')
  await expect(page.locator('.notes-subtabs > button')).toHaveCount(2)

  await page.locator('.notes-subtabs > button').nth(1).click({ button: 'right' })

  const menu = page.locator('.note-row-menu')
  await expect(menu.getByRole('menuitem', { name: 'Delete note' })).toBeDisabled()
  await expect(menu.locator('.context-note')).toContainText('keeps at least one note')
})
