import { expect, test } from 'playwright/test'
import { settingsTabs } from '../../src/settingsTabs'

/**
 * The panel-wide Settings search, against the real panel.
 *
 * The alias audit lives here rather than in a unit test because the thing an alias can
 * collide with is a *rendered* label, and a third of the panel's labels are rendered by
 * child components a vnode walk cannot see. Every tab is visited so each one's live
 * harvest exists, then the index the search box would build is audited as a whole.
 * `npm run check:settings-aliases` runs just this file.
 */

const PHONE = { width: 390, height: 844 }
const DESKTOP = { width: 1280, height: 900 }

type Page = import('playwright/test').Page

async function visitEveryTab(page: Page) {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  for (const tab of settingsTabs) {
    await page.getByRole('tab', { name: tab.label, exact: true }).click()
    // The live harvest lands shortly after a tab settles; leaving first would lose it.
    await expect.poll(() => page.evaluate(id => window.settingsSearchAudit().harvested.includes(id), tab.id),
      { message: `the ${tab.label} tab was never harvested` }).toBe(true)
  }
}

test('every search alias resolves to one real entry and shadows none', async ({ page }) => {
  await visitEveryTab(page)
  const problems = await page.evaluate(() => window.settingsSearchAudit().problems)
  expect(problems, problems.join('\n')).toEqual([])
})

test('keyboard-shortcut rows stay out of the panel search, which hands off to their own filter', async ({ page }) => {
  await visitEveryTab(page)
  const entries = await page.evaluate(() => window.settingsSearchAudit().entries)
  // The harness binds "Open command palette"; no row of the shortcut table is indexed.
  expect(entries.filter(entry => /command palette|set shortcut|clear shortcut/i.test(entry.label))).toEqual([])
  // The gesture pickers are still indexed, by their label and current value - never by the
  // command catalogue they choose from. Swipe left is bound to a command the harness does
  // not list, so neither of the harness's two commands belongs in its keywords; the
  // Dropdown's sizer (its widest option) used to put one there.
  const swipeLeft = entries.find(entry => entry.tab === 'input' && entry.label === 'Swipe left')
  expect(swipeLeft?.keywords).toBeTruthy()
  expect(swipeLeft?.keywords).not.toMatch(/command palette|navigation sidebar/)

  const search = page.locator('.settings-search input')
  await search.fill('palette')
  const handoff = page.locator('#settings-search-results .settings-search-handoff')
  await expect(handoff).toContainText('Keyboard shortcuts matching “palette”')
  await expect(handoff).toContainText('1 command')
  await handoff.click()
  await expect(page.locator('.keybinding-filter')).toHaveValue('palette')
  await expect(page.locator('.keybinding-list article:not([hidden])')).toHaveCount(1)
  await expect(page.locator('.keybinding-list article:not([hidden]) .keybinding-command')).toContainText('Open command palette')
})

test('an alias ranks its target first', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await page.goto('/settings-harness.html')
  const search = page.locator('.settings-search input')
  await search.fill('dark mode')
  await expect(page.locator('#settings-search-results [role="option"]').first()).toContainText('Theme')
  await search.fill('hotkeys')
  await expect(page.locator('#settings-search-results [role="option"]').first()).toContainText('Keyboard shortcuts')
})

test('on a phone the result list spans the panel, not the narrow box', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await page.goto('/settings-harness.html')
  const search = page.locator('.settings-panel>header .settings-search input')
  await search.fill('font')
  const list = page.locator('#settings-search-results')
  await expect(list).toBeVisible()
  const [box, panel, results] = await Promise.all([
    search.boundingBox(), page.locator('.settings-panel').boundingBox(), list.boundingBox(),
  ])
  expect(box && panel && results).toBeTruthy()
  expect(results!.width).toBeGreaterThan(box!.width + 100)
  expect(results!.x).toBeGreaterThanOrEqual(panel!.x)
  expect(results!.x + results!.width).toBeLessThanOrEqual(panel!.x + panel!.width + 0.5)
  expect(results!.width).toBeGreaterThan(panel!.width - 20)
})
