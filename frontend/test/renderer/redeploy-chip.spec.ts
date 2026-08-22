import { expect, test } from 'playwright/test'

/**
 * Where the redeploy indicator sits, which is CSS and only CSS.
 *
 * On a phone it used to be a fixed card anchored 54px below `.mobile-toolbar`, so for the
 * whole multi-minute build it covered the top of the workspace it was reporting on. It is a
 * control inside that bar now: spinner and clock, with the phase word and the build log
 * behind a tap. Desktop keeps the floating card under `.app-topbar`, which has the room.
 */

const PHONE = { width: 390, height: 844 }

test('on a phone the chip is in the toolbar row, not under it', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await page.goto('/redeploy-chip-harness.html')
  await expect(page.locator('.redeploy-chip')).toBeVisible()

  const measured = await page.evaluate(() => {
    const box = (selector: string) => {
      const rect = document.querySelector(selector)!.getBoundingClientRect()
      return { x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom, height: rect.height, width: rect.width }
    }
    const chip = document.querySelector<HTMLElement>('.redeploy-chip')!
    return {
      bar: box('.mobile-toolbar'),
      chip: box('.redeploy-chip'),
      run: box('.mobile-run-trigger'),
      drawer: box('.mobile-drawer-toggle'),
      name: box('.mobile-project-name'),
      position: getComputedStyle(chip).position,
      // The phase word stays in the DOM for `role="status"`; only its pixels go.
      label: chip.querySelector('strong')!.textContent,
      labelHidden: chip.querySelector('strong')!.getBoundingClientRect().width <= 1,
      elapsed: chip.querySelector('small')!.textContent,
      // The toolbar is one row, and adding a control must not make it two. Measured by
      // shared vertical centre rather than by shared `top`: the bar centres items of
      // different heights, so equal tops were never the invariant.
      offRow: [...document.querySelectorAll<HTMLElement>('.mobile-toolbar>*')]
        .filter(child => {
          const rect = child.getBoundingClientRect()
          const bar = document.querySelector('.mobile-toolbar')!.getBoundingClientRect()
          return Math.abs((rect.top + rect.bottom) / 2 - (bar.top + bar.bottom) / 2) > 2
            || rect.top < bar.top - 1 || rect.bottom > bar.bottom + 1
        })
        .map(child => child.className),
    }
  })

  // Inside the bar, vertically and horizontally, rather than floating beneath it.
  expect(measured.position).not.toBe('fixed')
  expect(measured.chip.y).toBeGreaterThanOrEqual(measured.bar.y - 1)
  expect(measured.chip.bottom).toBeLessThanOrEqual(measured.bar.bottom + 1)
  // Between Run and the side-panel toggle, and the toggle keeps its own edge.
  expect(measured.run.right).toBeLessThanOrEqual(measured.chip.x + 1)
  expect(measured.chip.right).toBeLessThanOrEqual(measured.drawer.x + 1)
  expect(measured.drawer.right).toBeLessThanOrEqual(measured.bar.right + 1)
  // One row still.
  expect(measured.offRow).toEqual([])
  // The Project title is what gave up the width; it is also in the sidebar.
  expect(measured.name.width).toBeGreaterThan(0)

  expect(measured.label).toBe('Rebuilding app')
  expect(measured.labelHidden).toBe(true)
  expect(measured.elapsed).toMatch(/^\d+:\d\d$/)
})

test('the phone chip expands under the bar rather than growing it', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await page.goto('/redeploy-chip-harness.html')
  const barBefore = await page.evaluate(() => document.querySelector('.mobile-toolbar')!.getBoundingClientRect().height)

  await page.locator('.redeploy-chip-summary').click()
  const open = await page.evaluate(() => {
    const body = document.querySelector('.redeploy-chip-body')!.getBoundingClientRect()
    const bar = document.querySelector('.mobile-toolbar')!.getBoundingClientRect()
    return { body: { x: body.x, y: body.y, right: body.right, width: body.width }, bar: { height: bar.height, bottom: bar.bottom }, viewport: innerWidth }
  })

  // The bar did not grow a second row to hold the detail.
  expect(open.bar.height).toBeCloseTo(barBefore, 0)
  // The panel drops below the bar and takes the screen's width, because a spinner-sized
  // chip cannot hold a build log.
  expect(open.body.y).toBeGreaterThanOrEqual(open.bar.bottom - 6)
  expect(open.body.width).toBeGreaterThan(open.viewport * 0.8)
  expect(open.body.right).toBeLessThanOrEqual(open.viewport + 1)
  await expect(page.locator('.redeploy-chip-body pre')).toContainText('PyInstaller')
  await expect(page.locator('.redeploy-chip-body p').first()).not.toBeEmpty()
})

test('on desktop the chip is still the floating card under the top bar', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/redeploy-chip-harness.html?inline=0')
  const measured = await page.evaluate(() => {
    const chip = document.querySelector<HTMLElement>('.redeploy-chip')!
    const rect = chip.getBoundingClientRect()
    const bar = document.querySelector('.app-topbar')!.getBoundingClientRect()
    return {
      position: getComputedStyle(chip).position,
      inline: chip.classList.contains('redeploy-chip-inline'),
      // The phase word is drawn here: there is room for it.
      labelWidth: chip.querySelector('strong')!.getBoundingClientRect().width,
      belowBar: rect.y >= bar.bottom - 1,
    }
  })
  expect(measured.position).toBe('fixed')
  expect(measured.inline).toBe(false)
  expect(measured.labelWidth).toBeGreaterThan(20)
  expect(measured.belowBar).toBe(true)
})
