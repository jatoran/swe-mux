import { expect, test, type Page } from 'playwright/test'

/**
 * The rail's context menu and the hover-only rail, against the real `TerminalPane` in the
 * demo app over its fake daemon.
 *
 * Neither half is reachable from a harness that mounts a piece of the rail: the menu is
 * opened by a listener the pane binds on the terminal surface, the hover decision is re-run
 * by that same pane from the pointer, the DOM, and its own state, and the switch is a
 * config write the pane sends through `App`. The pure rule is pinned in
 * `test/railHover.test.ts` and the overlay's geometry in `pane-layout.spec.ts`; what this
 * adds is that the three are wired together in the pane a visitor actually uses.
 */

const READY_TIMEOUT = 30_000
const RAIL = '.terminal-pane.focused .terminal-action-rail'
const MENU = '.rail-menu'

test.use({ viewport: { width: 1280, height: 800 } })

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => { localStorage.setItem('swemux-demo-coach-v1', 'done') })
  await page.goto('/demo.html?deterministic=1')
  await page.waitForSelector(RAIL, { timeout: READY_TIMEOUT })
})

/** The focused pane's surface and rail, as the numbers the assertions read. */
const geometry = (page: Page) => page.evaluate(() => {
  const surface = document.querySelector('.terminal-pane.focused .terminal-surface')!.getBoundingClientRect()
  const host = document.querySelector('.terminal-pane.focused .terminal-host')!.getBoundingClientRect()
  const rail = document.querySelector('.terminal-pane.focused .terminal-action-rail')!
  const box = rail.getBoundingClientRect()
  return {
    surface: { top: surface.top, bottom: surface.bottom, left: surface.left, right: surface.right },
    hostBottom: Math.round(host.bottom),
    rail: { top: Math.round(box.top), bottom: Math.round(box.bottom), height: Math.round(box.height) },
    hover: rail.classList.contains('rail-hover'),
    shown: rail.classList.contains('rail-hover-shown'),
  }
})

async function openRailMenu(page: Page) {
  await page.locator(`${RAIL} .terminal-action-scroll`).first().click({ button: 'right', position: { x: 4, y: 4 } })
  await expect(page.locator(MENU)).toBeVisible()
}

/**
 * Put the pointer in the reveal zone and wait for the rail. Nudged on every poll rather
 * than moved once: the pane binds its pointer listeners in an effect after the switch
 * flips, and a single move that lands before that effect is a move nothing records.
 */
async function hoverZone(page: Page) {
  const { surface } = await geometry(page)
  const x = (surface.left + surface.right) / 2
  await expect.poll(async () => {
    await page.mouse.move(x, surface.bottom - 6)
    await page.mouse.move(x + 1, surface.bottom - 7)
    return page.locator(`${RAIL}.rail-hover-shown`).count()
  }).toBe(1)
}

test('a right-click on the rail opens its menu, and the menu switches the hover-only rail', async ({ page }) => {
  const inFlow = await geometry(page)
  expect(inFlow.hover).toBe(false)
  // In flow: the host stops where the rail starts.
  expect(inFlow.hostBottom).toBe(inFlow.rail.top)

  await openRailMenu(page)
  await expect(page.locator(`${MENU} [role="menuitem"], ${MENU} [role="menuitemcheckbox"]`)).toHaveText([
    'Open all actions', 'Configure actions…', 'Only show on hover',
  ])
  await expect(page.locator(`${MENU} [role="menuitemcheckbox"]`)).toHaveAttribute('aria-checked', 'false')

  await page.locator(`${MENU} [role="menuitemcheckbox"]`).click()
  await expect(page.locator(MENU)).toHaveCount(0)
  // The write went through the daemon: the pane's rail is now the overlay, and the
  // terminal owns the whole surface.
  await expect(page.locator(`${RAIL}.rail-hover`)).toHaveCount(1)
  await expect.poll(async () => (await geometry(page)).hostBottom).toBe(Math.round(inFlow.surface.bottom))

  // The mouse is still where the menu was, over the rail's zone, so the rail is up; the
  // middle of the terminal is not the zone, and moving there lets it go.
  const away = await geometry(page)
  await page.mouse.move((away.surface.left + away.surface.right) / 2, (away.surface.top + away.surface.bottom) / 2)
  await expect(page.locator(`${RAIL}.rail-hover-shown`)).toHaveCount(0)
  await expect.poll(async () => (await geometry(page)).rail.top).toBeGreaterThanOrEqual(Math.round(away.surface.bottom))

  // Into the strip the rail used to occupy: it comes up, seated on the surface's bottom edge.
  await hoverZone(page)
  await expect.poll(async () => (await geometry(page)).rail.bottom).toBe(Math.round(away.surface.bottom))
  expect((await geometry(page)).hostBottom).toBe(Math.round(away.surface.bottom))

  // The menu says what it will do: checked now, and the same row turns it back off.
  await openRailMenu(page)
  await expect(page.locator(`${MENU} [role="menuitemcheckbox"]`)).toHaveAttribute('aria-checked', 'true')
  await page.locator(`${MENU} [role="menuitemcheckbox"]`).click()
  await expect(page.locator(`${RAIL}.rail-hover`)).toHaveCount(0)
  await expect.poll(async () => (await geometry(page)).hostBottom).toBe(inFlow.rail.top)
})

test('an open panel holds the hover-only rail up, and its closing lets the rail go', async ({ page }) => {
  await openRailMenu(page)
  await page.locator(`${MENU} [role="menuitemcheckbox"]`).click()
  await expect(page.locator(`${RAIL}.rail-hover`)).toHaveCount(1)

  const on = await geometry(page)
  await hoverZone(page)

  // "Open all actions" from the menu opens this row's complete-row popover.
  await openRailMenu(page)
  await page.locator(`${MENU} [role="menuitem"]`, { hasText: 'Open all actions' }).click()
  await expect(page.locator(`${RAIL} .rail-overflow-popover`)).toBeVisible()

  // The popover grows upward, out of the zone. A pointer in the middle of the terminal - or
  // off the pane entirely - is not a reason to pull the rail out from under an open panel.
  await page.mouse.move((on.surface.left + on.surface.right) / 2, (on.surface.top + on.surface.bottom) / 2)
  await page.mouse.move(2, 2)
  await page.waitForTimeout(250)
  await expect(page.locator(`${RAIL}.rail-hover-shown`)).toHaveCount(1)
  await expect(page.locator(`${RAIL} .rail-overflow-popover`)).toBeVisible()

  // The panel's own close control, activated without moving the pointer onto it: with the
  // pointer still away, the closing is what hides the rail - nothing else changed.
  await page.locator(`${RAIL} .rail-overflow-close`).dispatchEvent('click')
  await expect(page.locator(`${RAIL} .rail-overflow-popover`)).toHaveCount(0)
  await expect(page.locator(`${RAIL}.rail-hover-shown`)).toHaveCount(0)

  // The terminal's own right-click still opens nothing.
  const surface = page.locator('.terminal-pane.focused .terminal-host')
  await surface.click({ button: 'right', position: { x: 40, y: 40 } })
  await expect(page.locator(MENU)).toHaveCount(0)
})
