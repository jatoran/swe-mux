import { expect, test } from 'playwright/test'

/**
 * The Files tab, after its two readings became named subtabs.
 *
 * Recent used to be a pressed clock icon inside the search row: a mode with no name in the
 * chrome, unreachable by command or by voice, and indistinguishable from the tree except by
 * inspecting a toggle's `aria-pressed`. It is a registered segment now
 * (`drawerSegments.ts`), which is a claim about DOM structure and about width - the view rail
 * owns the full drawer width, including on a 320px phone.
 */

test('the tree and Recent are named subtabs, not a toggle in the search row', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/files-tab-harness.html')

  const tabs = page.locator('.drawer-view-tabs button')
  await expect(tabs).toHaveText(['File Explorer', 'Recent'])
  await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'true')
  // The icon it replaced is gone rather than kept alongside: two controls for one mode is
  // how the pressed state and the subtab would come to disagree.
  await expect(page.locator('.file-search-recent')).toHaveCount(0)

  // The tree is what the first subtab shows, and Recent replaces it rather than joining it.
  await expect(page.locator('.file-tree')).toHaveCount(1)
  await expect(page.locator('.file-recent')).toHaveCount(0)

  await tabs.nth(0).press('ArrowRight')
  await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true')
  await expect(tabs.nth(1)).toBeFocused()
  await expect(page.locator('.file-recent')).toHaveCount(1)
  await expect(page.locator('.file-tree')).toHaveCount(0)
  await expect(page.locator('.file-recent .file-result-row').first()).toContainText('style.css')

  // The search field survives both, because searching is an explicit act that outranks
  // whichever reading is selected behind it.
  await expect(page.locator('.file-search-input')).toBeVisible()
})

test('the second subtab can be the one selected on arrival', async ({ page }) => {
  // The segment is persisted per Project, so the tab is entered on whichever reading was
  // last used. A default that always won would make that persistence unobservable.
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/files-tab-harness.html?view=recent')
  await expect(page.locator('.file-recent')).toHaveCount(1)
  await expect(page.locator('.drawer-view-tabs button').nth(1))
    .toHaveAttribute('aria-selected', 'true')
})

/**
 * The view rail owns the full width. Project identity is already present in the workspace
 * toolbar, sidebar, and the file browser's root-path header.
 */
for (const width of [320, 380, 480]) {
  test(`the subtab labels share the full rail at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width: width + 40, height: 900 })
    await page.goto(`/files-tab-harness.html?width=${width}`)
    const measured = await page.evaluate(() => {
      const box = (element: Element) => {
        const { x, width: w } = element.getBoundingClientRect()
        return { x, width: w, right: x + w }
      }
      const buttons = [...document.querySelectorAll<HTMLElement>('.drawer-view-tabs button')]
      const rail = document.querySelector<HTMLElement>('.drawer-view-tabs')!
      return {
        labels: buttons.map(button => ({
          text: button.textContent,
          clipped: button.scrollWidth > button.clientWidth + 1,
          ...box(button),
        })),
        rail: box(rail),
      }
    })
    // Full words, not stubs: nothing here is allowed to ellipsise.
    expect(measured.labels.map(label => label.text)).toEqual(['File Explorer', 'Recent'])
    for (const label of measured.labels) expect(label.clipped).toBe(false)
    // One row, split evenly across the full rail, with no redundant pane heading.
    expect(measured.labels[0].right).toBeLessThanOrEqual(measured.labels[1].x)
    expect(measured.labels[0].width).toBeCloseTo(measured.labels[1].width, 0)
    expect(measured.labels[0].x).toBeCloseTo(measured.rail.x, 0)
    expect(measured.labels[1].right).toBeCloseTo(measured.rail.right, 0)
    await expect(page.locator('.drawer-pane-heading')).toHaveCount(0)
  })
}

test('the header offers the ignore patterns that decide what the tree contains', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/files-tab-harness.html')
  const link = page.locator('.file-browser .file-browser-ignores')
  await expect(link).toBeVisible()
  await expect(link).toContainText('ignores')

  // It asks for the real target rather than merely looking like a button: `App` routes
  // `mux:open-setting`, and a link that dispatched nothing would look identical here.
  await link.click()
  expect(await page.evaluate(() => (globalThis as { __settingRequests?: string[] }).__settingRequests))
    .toEqual(['projects.ignorePatterns'])
})

test('a new query replaces stale rows with visible progress until its own results arrive', async ({ page }) => {
  await page.goto('/files-tab-harness.html')
  const search = page.getByLabel('Search files')

  await search.fill('r')
  await expect(page.locator('.file-result-row')).toHaveCount(2)
  await expect(page.locator('.file-results')).toContainText('README.md')

  await search.fill('roadmap')
  await expect(page.locator('.file-result-row')).toHaveCount(0)
  await expect(page.getByRole('status')).toHaveText('Searching for “roadmap”…')

  await expect(page.locator('.file-result-row')).toHaveCount(1)
  await expect(page.locator('.file-results')).toContainText('.docs/development/ROADMAP.md')
})

test('a superseded search is aborted and cannot overwrite the current query', async ({ page }) => {
  await page.goto('/files-tab-harness.html')
  const search = page.getByLabel('Search files')

  await search.fill('road')
  await expect.poll(async () => page.evaluate(() => (
    globalThis as { __fileSearchRequests?: Array<{ query: string }> }
  ).__fileSearchRequests?.some(request => request.query === 'road'))).toBe(true)
  await search.fill('roadmap')

  await expect(page.locator('.file-result-row')).toHaveCount(1)
  await expect(page.locator('.file-results')).toContainText('.docs/development/ROADMAP.md')
  await expect.poll(async () => page.evaluate(() => (
    globalThis as { __fileSearchRequests?: Array<{ query: string; aborted: boolean }> }
  ).__fileSearchRequests?.find(request => request.query === 'road')?.aborted)).toBe(true)
  await page.waitForTimeout(850)
  await expect(page.locator('.file-results')).not.toContainText('old-road.txt')
})
