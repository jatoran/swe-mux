import { expect, test, type Page } from 'playwright/test'
import { chooseDropdown, dropdownValue } from './dropdown'

// The control that replaced every native `<select>` in the app, held to what the native one
// did. Three of these are the defects reported against the account-settings model picker and
// are the reason this component exists rather than a stylesheet: a scroll gesture that chose a
// row, a list that always opened at the top, and an order nobody could navigate.
//
// Everything here is behaviour a pure test cannot reach — real scroll offsets, a real portal,
// a real touch gesture, and real clipping by a real `overflow:auto` ancestor.

const open = async (page: Page, path = '/dropdown-harness.html') => {
  await page.setViewportSize({ width: 900, height: 700 })
  await page.goto(path)
  await page.locator('.dropdown-trigger').first().waitFor({ state: 'attached' })
}

const list = (page: Page) => page.locator('.dropdown-list')
const trigger = (page: Page, row: string) => page.locator(`#${row}`).locator('.dropdown-trigger')

test('the collapsed control reads as its value and opens a listbox', async ({ page }) => {
  await open(page)
  const control = trigger(page, 'short-row')
  await expect(control).toHaveText(/message/)
  await expect(control).toHaveAttribute('aria-expanded', 'false')
  await control.click()
  await expect(control).toHaveAttribute('aria-expanded', 'true')
  await expect(list(page)).toHaveAttribute('role', 'listbox')
  await expect(list(page).locator('[role="option"]')).toHaveCount(5)
  // The current value is marked as selected, which is what a screen reader reads and what
  // the opening scroll aims at.
  await expect(list(page).locator('[aria-selected="true"]')).toHaveText(/message/)
})

test('a scroll gesture scrolls the list and never chooses the row it started on', async ({ page }) => {
  await open(page)
  const control = trigger(page, 'long-row')
  const before = await dropdownValue(control)
  await control.click()
  // A point inside the panel. Taking one from a row's own box would not do: the list opens
  // scrolled, so most rows lie outside the panel they belong to, and a gesture started there
  // is a press on the page rather than on the list.
  const panel = (await list(page).boundingBox())!
  const centre = { x: panel.x + panel.width / 2, y: panel.y + panel.height / 2 }

  // Scrolling over a row moves the list and settles on nothing.
  const start = await list(page).evaluate(node => node.scrollTop)
  await page.mouse.move(centre.x, centre.y)
  await page.mouse.wheel(0, 240)
  await expect.poll(() => list(page).evaluate(node => node.scrollTop)).toBeGreaterThan(start)
  await expect(list(page)).toHaveCount(1)
  expect(await dropdownValue(control)).toBe(before)

  // And a press that travels before it lifts is a drag, not a choice — the pointer form of
  // the reported defect, and the one a browser still delivers a click for.
  await page.mouse.move(centre.x, centre.y)
  await page.mouse.down()
  await page.mouse.move(centre.x, centre.y + 60, { steps: 8 })
  await page.mouse.up()
  await expect(list(page)).toHaveCount(1)
  expect(await dropdownValue(control)).toBe(before)
})

test('a click that stays put does choose, and closes the list', async ({ page }) => {
  await open(page)
  const control = trigger(page, 'short-row')
  await chooseDropdown(page, control, 'author')
  await expect(control).toHaveText(/author/)
  await expect(page.locator('#chosen')).toContainText('|author|')
})

test('the list opens scrolled to the value in force, not at the top', async ({ page }) => {
  await open(page)
  const control = trigger(page, 'long-row')
  await control.click()
  const placement = await list(page).evaluate(node => {
    const selected = node.querySelector<HTMLElement>('[aria-selected="true"]')!
    const panel = node.getBoundingClientRect()
    const row = selected.getBoundingClientRect()
    return {
      scrollTop: node.scrollTop,
      visible: row.top >= panel.top - 1 && row.bottom <= panel.bottom + 1,
      // Centred, not merely scrolled into view: the rows on either side are the context
      // that makes a long catalogue navigable.
      offCentre: Math.abs((row.top + row.bottom) / 2 - (panel.top + panel.bottom) / 2),
      height: panel.height,
    }
  })
  expect(placement.scrollTop).toBeGreaterThan(0)
  expect(placement.visible).toBe(true)
  expect(placement.offCentre).toBeLessThan(placement.height / 4)
})

test('the arrows, Home, End, type-ahead, Enter, and Escape all work', async ({ page }) => {
  await open(page)
  const control = trigger(page, 'short-row')
  await control.focus()
  await page.keyboard.press('ArrowDown')
  await expect(list(page)).toHaveCount(1)
  // Opening a closed control puts the highlight on the value in force rather than stepping
  // off it, so the first ArrowDown reads as "the next one" instead of skipping a row.
  await expect(list(page).locator('.dropdown-option.active')).toHaveText(/message/)
  await page.keyboard.press('ArrowDown')
  await expect(list(page).locator('.dropdown-option.active')).toHaveText(/author/)
  // `path` is disabled, so the arrows step over it exactly as a native select does.
  await page.keyboard.press('ArrowDown')
  await expect(list(page).locator('.dropdown-option.active')).toHaveText(/sonnet/)
  await page.keyboard.press('End')
  await expect(list(page).locator('.dropdown-option.active')).toHaveText(/summary/)
  await page.keyboard.press('Home')
  await expect(list(page).locator('.dropdown-option.active')).toHaveText(/message/)
  await page.keyboard.press('a')
  await expect(list(page).locator('.dropdown-option.active')).toHaveText(/author/)
  await page.keyboard.press('Enter')
  await expect(list(page)).toHaveCount(0)
  await expect(control).toHaveText(/author/)
  await expect(control).toBeFocused()

  // Escape leaves without choosing, and hands focus back to the trigger.
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Escape')
  await expect(list(page)).toHaveCount(0)
  await expect(control).toHaveText(/author/)
  await expect(control).toBeFocused()
})

test('a disabled row cannot be chosen by click either', async ({ page }) => {
  await open(page)
  const control = trigger(page, 'short-row')
  await control.click()
  // `force`, because Playwright refuses an `aria-disabled` target on its own — and refusing
  // it is exactly what this test needs the *component* to do, not the driver.
  await list(page).locator('.dropdown-option[data-value="path"]').click({ force: true })
  // The list stays open rather than accepting it, which is how a native select refuses too.
  await expect(list(page)).toHaveCount(1)
  expect(await dropdownValue(control)).toBe('message')
})

test('the list escapes an overflow:auto ancestor instead of being clipped by it', async ({ page }) => {
  await open(page)
  const control = trigger(page, 'scrolled-row')
  await control.click()
  const clipping = await page.evaluate(() => {
    const panel = document.querySelector<HTMLElement>('.dropdown-list')!
    const scroller = document.querySelector<HTMLElement>('#scroller')!
    return {
      parent: panel.parentElement?.tagName,
      panel: panel.getBoundingClientRect().height,
      // The panel is taller than the scroller it was opened inside: proof it is not living
      // in that box, which is the whole reason the list is portalled.
      scroller: scroller.getBoundingClientRect().height,
    }
  })
  expect(clipping.parent).toBe('BODY')
  expect(clipping.panel).toBeGreaterThan(clipping.scroller)
})

test('a trigger near the fold opens upward rather than off the screen', async ({ page }) => {
  await open(page)
  const control = trigger(page, 'low-row')
  await control.click()
  const geometry = await page.evaluate(() => {
    const panel = document.querySelector<HTMLElement>('.dropdown-list')!.getBoundingClientRect()
    const button = document.querySelector<HTMLElement>('#low-row .dropdown-trigger')!.getBoundingClientRect()
    return { panelBottom: panel.bottom, buttonTop: button.top, panelTop: panel.top, view: window.innerHeight }
  })
  expect(geometry.panelBottom).toBeLessThanOrEqual(geometry.buttonTop + 1)
  expect(geometry.panelTop).toBeGreaterThanOrEqual(0)
  expect(geometry.panelBottom).toBeLessThanOrEqual(geometry.view)
})

test('a press outside closes the list without choosing', async ({ page }) => {
  await open(page)
  const control = trigger(page, 'short-row')
  await control.click()
  await expect(list(page)).toHaveCount(1)
  await page.mouse.click(10, 10)
  await expect(list(page)).toHaveCount(0)
  expect(await dropdownValue(control)).toBe('message')
})

test('a wrapping label still opens the control it labels, once', async ({ page }) => {
  await open(page)
  // `<label>Search field<Dropdown/></label>`: clicking the words forwards to the button, the
  // way it did to the `<select>` this replaced. The list must not flicker shut and open again.
  await page.locator('#short-row').getByText('Search field').click()
  await expect(list(page)).toHaveCount(1)
  await page.locator('#short-row').getByText('Search field').click()
  await expect(list(page)).toHaveCount(0)
})

test('a disabled control does not open', async ({ page }) => {
  await open(page)
  await page.locator('#long-picker').evaluate(node => { (node as HTMLButtonElement).disabled = true })
  await page.locator('#long-picker').click({ force: true })
  await expect(list(page)).toHaveCount(0)
})
