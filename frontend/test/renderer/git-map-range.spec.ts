import { expect, type Page, test } from 'playwright/test'

// Shift-click range selection, driven through the real DOM rather than the reducer.
//
// The reducer is unit-tested; what only a browser can answer is whether the press even
// arrives carrying Shift. It nearly does not: a checkbox's `change` event is not a mouse
// event and has no `shiftKey` at all, so a handler written the obvious way reads every
// range press as an ordinary one. These specs click the *label* - the 26px hit area a
// reader actually hits, one forwarded click away from the input - because that is the
// path where the modifier could plausibly be dropped.

const box = (page: Page, name: string) => page.getByRole('checkbox', { name: `Select wt-${name}` })
/** The label around the checkbox: the real hit area, and the only way to press one with
 *  a modifier through Playwright's checkbox helpers. */
const hit = (page: Page, name: string) =>
  page.locator('.git-map-select').filter({ has: box(page, name) })

async function checked(page: Page, name: string) {
  return await box(page, name).isChecked()
}

async function selection(page: Page) {
  return await page.evaluate(() =>
    [...document.querySelectorAll<HTMLInputElement>('.git-map-select input')]
      .filter(input => input.checked)
      .map(input => (input.getAttribute('aria-label') || '').replace('Select ', '')),
  )
}

async function openSelect(page: Page) {
  await page.setViewportSize({ width: 380, height: 900 })
  await page.goto('/git-map-range-harness.html')
  await expect(page.locator('.git-map-row')).toHaveCount(7)
  await page.getByRole('button', { name: 'Select', exact: true }).click()
}

test('shift-clicking selects every row between the last press and this one', async ({ page }) => {
  await openSelect(page)
  await hit(page, 'alpha').click()
  await hit(page, 'delta').click({ modifiers: ['Shift'] })

  // Inclusive at both ends, and `wt-busy` sits between them with a live session in it -
  // a range is a faster way to press the same checkboxes, not a way past what they refuse.
  expect(await selection(page)).toEqual(['wt-alpha', 'wt-bravo', 'wt-delta'])
  await expect(box(page, 'busy')).toBeDisabled()
  await expect(box(page, 'busy')).not.toBeChecked()
  await expect(page.locator('.git-map-bulk-head strong')).toHaveText('3 selected')
})

test('the range runs upwards too, and reaches the main tree no more than a click does', async ({ page }) => {
  await openSelect(page)
  await hit(page, 'foxtrot').click()
  await hit(page, 'bravo').click({ modifiers: ['Shift'] })
  expect(await selection(page)).toEqual(['wt-bravo', 'wt-delta', 'wt-echo', 'wt-foxtrot'])
  await expect(page.getByRole('checkbox', { name: 'Select trunk' })).toBeDisabled()
})

test('a run of shift-clicks walks the list from each press', async ({ page }) => {
  await openSelect(page)
  await hit(page, 'alpha').click()
  await hit(page, 'bravo').click({ modifiers: ['Shift'] })
  await hit(page, 'echo').click({ modifiers: ['Shift'] })
  expect(await selection(page)).toEqual(['wt-alpha', 'wt-bravo', 'wt-delta', 'wt-echo'])
})

test('shift-clicking a checked box un-selects back over an overshoot', async ({ page }) => {
  await openSelect(page)
  await hit(page, 'alpha').click()
  await hit(page, 'foxtrot').click({ modifiers: ['Shift'] })
  expect(await selection(page)).toEqual(['wt-alpha', 'wt-bravo', 'wt-delta', 'wt-echo', 'wt-foxtrot'])
  await hit(page, 'delta').click({ modifiers: ['Shift'] })
  expect(await selection(page)).toEqual(['wt-alpha', 'wt-bravo'])
})

test('a range never reaches past the search box into rows the reader cannot see', async ({ page }) => {
  await openSelect(page)
  await page.getByRole('searchbox', { name: /worktree/i }).fill('a')
  // alpha, bravo, delta all match "a"; echo and foxtrot do not.
  await expect(page.locator('.git-map-row')).toHaveCount(3)
  await hit(page, 'alpha').click()
  await hit(page, 'delta').click({ modifiers: ['Shift'] })

  await page.getByRole('searchbox', { name: /worktree/i }).fill('')
  await expect(page.locator('.git-map-row')).toHaveCount(7)
  // `wt-busy` sits between alpha and delta in the unfiltered order and was hidden while
  // the range was drawn. Sweeping it would have selected a checkout the reader could not
  // see - the worst possible outcome for a control whose next press removes things.
  expect(await selection(page)).toEqual(['wt-alpha', 'wt-bravo', 'wt-delta'])
})

test('an anchor the filter has hidden degrades to a plain click', async ({ page }) => {
  await openSelect(page)
  await hit(page, 'foxtrot').click()
  await page.getByRole('searchbox', { name: /worktree/i }).fill('a')
  await hit(page, 'delta').click({ modifiers: ['Shift'] })
  await page.getByRole('searchbox', { name: /worktree/i }).fill('')
  expect(await selection(page)).toEqual(['wt-delta', 'wt-foxtrot'])
})

test('leaving and re-entering select mode forgets the anchor', async ({ page }) => {
  await openSelect(page)
  await hit(page, 'alpha').click()
  await page.getByRole('button', { name: 'Select', exact: true }).click()
  await page.getByRole('button', { name: 'Select', exact: true }).click()
  await hit(page, 'delta').click({ modifiers: ['Shift'] })
  // No origin survives the mode, so this is one press on one row.
  expect(await selection(page)).toEqual(['wt-delta'])
})

test('"All removable" leaves no anchor for a shift-click to extend from', async ({ page }) => {
  await openSelect(page)
  await page.getByRole('button', { name: 'All removable' }).click()
  expect(await selection(page)).toEqual(['wt-alpha', 'wt-bravo', 'wt-delta', 'wt-echo', 'wt-foxtrot'])
  await hit(page, 'bravo').click({ modifiers: ['Shift'] })
  // A sweep of the whole list is not a row anyone pointed at, so this un-checks one box.
  expect(await selection(page)).toEqual(['wt-alpha', 'wt-delta', 'wt-echo', 'wt-foxtrot'])
})

test('keyboard selection still works and sets the anchor', async ({ page }) => {
  await openSelect(page)
  await box(page, 'alpha').focus()
  await page.keyboard.press('Space')
  expect(await checked(page, 'alpha')).toBe(true)
  await hit(page, 'delta').click({ modifiers: ['Shift'] })
  expect(await selection(page)).toEqual(['wt-alpha', 'wt-bravo', 'wt-delta'])
})
