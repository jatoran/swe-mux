import { expect, test } from 'playwright/test'
import { harnessReady } from './harnessReady'

type Harness = { __landed: string[]; __finishRemoval: () => void; __refuse: (path: string) => void }

const CLEAN = 'D:\\PROJECTS\\swe-mux\\.claude\\worktrees\\wt-clean'
const DIRTY = 'D:\\PROJECTS\\swe-mux\\.claude\\worktrees\\wt-dirty'

test('a removing worktree stays dimmed until the inventory drops it', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 })
  await page.goto('/git-map-select-harness.html')
  await page.getByRole('button', { name: 'Select', exact: true }).click()

  // "All removable" is exactly that: the checkout with a live session in it is not a
  // candidate and cannot be selected by hand either.
  await page.getByRole('button', { name: 'All removable' }).click()
  const busy = page.getByRole('checkbox', { name: 'Select wt-busy' })
  await expect(busy).toBeDisabled()
  await expect(busy).not.toBeChecked()
  await expect(page.getByRole('checkbox', { name: 'Select wt-clean' })).toBeChecked()
  await expect(page.locator('.git-map-bulk-head strong')).toHaveText('2 selected')
  await expect(page.locator('.git-map-bulk-badges')).toContainText('1 with uncommitted / unlanded work')

  await page.getByRole('button', { name: /^Remove 2…$/ }).click()
  // Without the extra consent only the clean one goes: skip-or-confirm, and the
  // confirmation names what it is discarding rather than counting it.
  await expect(page.getByRole('button', { name: 'Remove 1 ✓' })).toBeVisible()
  await page.getByRole('checkbox', { name: /also remove 1/ }).check()
  await page.getByRole('button', { name: 'Remove 2 ✓' }).click()

  // The requests have answered and the daemon has re-listed both worktrees - which is
  // the slow path exactly. Neither row may look like an ordinary one.
  const rows = page.locator('.git-map-row.removing')
  await expect(rows).toHaveCount(2)
  await expect(rows.first()).toHaveAttribute('aria-busy', 'true')
  await expect(rows.first().locator('.git-map-metrics .removing')).toContainText('removing')
  await expect(rows.first().locator('.git-map-spinner')).toHaveCount(1)
  // Collapsing and expanding does not change what the list is saying.
  await rows.first().locator('.git-map-summary').click()
  await expect(page.locator('.git-map-row.removing')).toHaveCount(2)
  await expect(page.locator('.git-map-detail .git-land-row-section')).toHaveCount(0)

  await page.evaluate(() => (globalThis as unknown as Harness).__finishRemoval())
  await page.evaluate(() => window.dispatchEvent(new Event('mux:git-changed')))
  await expect(page.locator('.git-map-row')).toHaveCount(2)
  await expect(page.locator('.git-map-row.removing')).toHaveCount(0)
})

test('a refused removal un-dims that row alone and states why', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 })
  await page.goto('/git-map-select-harness.html')
  await harnessReady(page, '__refuse')
  await page.evaluate(path => (globalThis as unknown as Harness).__refuse(path), CLEAN)
  await page.getByRole('button', { name: 'Select', exact: true }).click()
  await page.getByRole('button', { name: 'All removable' }).click()
  await page.getByRole('button', { name: /^Remove 2…$/ }).click()
  await page.getByRole('checkbox', { name: /also remove 1/ }).check()
  await page.getByRole('button', { name: 'Remove 2 ✓' }).click()

  // A refusal is the only thing that ends a pending removal early, and it ends exactly
  // its own. The refreshed inventory would otherwise have cleared the message with it.
  await expect(page.locator('.git-map-row.removing')).toHaveCount(1)
  await expect(page.locator('.git-tab > .git-state.error')).toContainText('wt-clean')
})

test('selection mode keeps the row inside the drawer at its narrowest', async ({ page }) => {
  await page.setViewportSize({ width: 180, height: 700 })
  await page.goto('/git-map-select-harness.html')
  await page.getByRole('button', { name: 'Select', exact: true }).click()
  const overflow = await page.evaluate(() => {
    const rows = [...document.querySelectorAll<HTMLElement>('.git-map-head.selecting')]
    return rows.map(row => row.scrollWidth - row.clientWidth)
  })
  expect(overflow.length).toBeGreaterThan(0)
  for (const value of overflow) expect(value).toBeLessThanOrEqual(0.5)
  // The checkbox leads the row and the identity still starts after it.
  const geometry = await page.evaluate(() => {
    const row = document.querySelector<HTMLElement>('.git-map-head.selecting')!
    const box = (selector: string) => row.querySelector<HTMLElement>(selector)!.getBoundingClientRect().toJSON()
    return { select: box('.git-map-select'), summary: box('.git-map-summary') }
  })
  expect(geometry.select.right).toBeLessThanOrEqual(geometry.summary.left + 0.5)
})

test('bulk land sends one request per branch in map order', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 })
  await page.goto('/git-map-select-harness.html')
  await page.getByRole('button', { name: 'Select', exact: true }).click()
  await page.getByRole('checkbox', { name: 'Select wt-dirty' }).check()
  await page.getByRole('checkbox', { name: 'Select wt-clean' }).check()

  await page.getByRole('button', { name: 'Land 2' }).click()
  await expect(page.locator('.git-map-bulk .git-state')).toHaveText('2 branches queued to land.')
  // Map order, not click order: what runs is what the reader saw.
  expect(await page.evaluate(() => (globalThis as unknown as Harness).__landed)).toEqual([CLEAN, DIRTY])
})
