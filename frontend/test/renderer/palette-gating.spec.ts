import { expect, test, type Page } from 'playwright/test'

// The palette's gate, watched from outside: while the palette is closed, a re-render of
// the shell must score nothing. The registry is fuzzy-scored end to end - a string build
// and a sort per command - and its only consumer is the result list, yet it used to run
// on every render of the composition root, five-second sidebar clock ticks included.
//
// A pure test cannot see this: an empty list looks identical whether the scorer ran or
// not. The harness counts the label reads `searchCommands` makes, which is the work
// itself rather than a proxy for it.

const open = async (page: Page) => {
  await page.setViewportSize({ width: 900, height: 700 })
  await page.goto('/palette-harness.html')
  await page.locator('#toggle').waitFor({ state: 'attached' })
}

const labelReads = (page: Page) => page.evaluate(() => window.paletteLabelReads)
const options = (page: Page) => page.locator('#command-results [role="option"]')

test('a closed palette scores nothing, however often the shell re-renders', async ({ page }) => {
  await open(page)
  expect(await labelReads(page)).toBe(0)
  for (let tick = 0; tick < 5; tick += 1) await page.locator('#rerender').click()
  await expect(page.locator('#renders')).toHaveText('5')
  expect(await labelReads(page)).toBe(0)
})

test('opening the palette lists every command, and scoring starts only then', async ({ page }) => {
  await open(page)
  await page.locator('#toggle').click()
  await expect(options(page)).toHaveCount(10)
  expect(await labelReads(page)).toBeGreaterThan(0)
})

test('typing filters the list through the real fuzzy scorer', async ({ page }) => {
  await open(page)
  await page.locator('#toggle').click()
  await page.locator('#palette-input').fill('broadcast')
  await expect(options(page)).toHaveCount(1)
  await expect(options(page).first()).toContainText('start-broadcasting-input')
  // A subsequence still matches, and the closest label sorts first.
  await page.locator('#palette-input').fill('focus')
  await expect(options(page).first()).toContainText('focus-next-workspace-tab')
  await page.locator('#palette-input').fill('zzzz')
  await expect(options(page)).toHaveCount(0)
})

test('closing the palette stops the scoring again', async ({ page }) => {
  await open(page)
  await page.locator('#toggle').click()
  await expect(options(page)).toHaveCount(10)
  await page.locator('#toggle').click()
  await expect(options(page)).toHaveCount(0)
  const settled = await labelReads(page)
  for (let tick = 0; tick < 5; tick += 1) await page.locator('#rerender').click()
  await expect(page.locator('#renders')).toHaveText('5')
  expect(await labelReads(page)).toBe(settled)
})
