import { expect, test } from 'playwright/test'

/**
 * The in-place rail editor: the customize surface the pane gear opens. What
 * matters here is that the one-reorder/one-removal path works entirely inside
 * the rail area — rows render as chips, the per-row picker adds, × removes,
 * backend-hidden items stay visible but dimmed — and both hand-offs (Done, All
 * options…) fire.
 */

test('rows render as removable chips with a per-row picker', async ({ page }) => {
  await page.goto('/rail-inline-harness.html')
  await expect(page.locator('.rail-inline-head strong')).toHaveText('Customize rail')

  const row = page.locator('.rail-inline-row').first()
  await expect(row.locator('.rail-chip-label', { hasText: /^Esc$/ })).toHaveCount(1)

  // Home ships in the Drawer, so the strip starts without it; the picker adds it.
  await expect(row.locator('.rail-chip-label', { hasText: /^Home$/ })).toHaveCount(0)
  await row.locator('.rail-inline-add').click()
  await page.getByLabel('Find an action to add').fill('Home')
  await page.locator('.rail-inline-picker-list button', { hasText: 'Home' }).first().click()
  const homeChip = row.locator('.rail-chip', { has: page.locator('.rail-chip-label', { hasText: /^Home$/ }) })
  await expect(homeChip).toHaveCount(1)

  // × takes it back off.
  await homeChip.locator('.rail-chip-remove').click()
  await expect(homeChip).toHaveCount(0)
})

test('backend-hidden items stay visible but dimmed', async ({ page }) => {
  await page.goto('/rail-inline-harness.html?backend=shell')
  // Attach is agent-only: a shell rail never shows it, so the editor dims it
  // instead of hiding it — this surface exists to answer "where did it go".
  await expect(page.locator('.rail-chip.filtered', { hasText: 'Attach' })).toHaveCount(1)
  await page.goto('/rail-inline-harness.html?backend=claude')
  await expect(page.locator('.rail-chip.filtered', { hasText: 'Attach' })).toHaveCount(0)
})

test('Done and All options… hand back to the pane', async ({ page }) => {
  await page.goto('/rail-inline-harness.html')
  await page.getByRole('button', { name: 'All options…' }).click()
  await expect.poll(() => page.evaluate(() => window.railInlineOpenedFull)).toBe(true)
  await page.getByRole('button', { name: 'Done' }).click()
  await expect.poll(() => page.evaluate(() => window.railInlineClosed)).toBe(true)
})
