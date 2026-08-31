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

/**
 * Picking a file opens it here.
 *
 * It used to close the panel and drop a tab into the workspace layout on top of the terminal
 * you were reading - and that layout is persisted server-side and shared, so doing it on a
 * phone rearranged the desktop's panes. The rail below is the fix and its selection rules are
 * DOM structure: two rails over one body have to agree on which of them is showing something.
 */
test('picking a file opens it in the tab, as a chip in the rail below', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/files-tab-harness.html')
  await expect(page.locator('.drawer-file-subtabs-row')).toHaveCount(0)

  await page.locator('.file-tree-row.file', { hasText: 'README.md' }).click()
  const chips = page.locator('.drawer-file-subtabs [role="tab"]')
  await expect(chips).toHaveText(['README.md'])
  await expect(chips.first()).toHaveAttribute('aria-selected', 'true')
  // The file is what the body draws, and the browser it was picked from is still mounted
  // behind it rather than torn down and refetched on the way back.
  await expect(page.locator('.project-resource.file-editor header strong')).toHaveText('README.md')
  await expect(page.locator('.drawer-files-browser')).toHaveAttribute('hidden', '')
  // Nothing was asked of the workspace.
  expect(await page.evaluate(() => (globalThis as { __paneRequests?: string[] }).__paneRequests)).toEqual([])
})

test('the index rail stands down while a file is showing, and takes the selection back', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/files-tab-harness.html')
  const views = page.locator('.drawer-view-tabs button')
  await page.locator('.file-tree-row.file', { hasText: 'README.md' }).click()

  // One highlighted chip over one body. The index is still named - it is where "back" goes -
  // but it does not claim to be what is drawn.
  await expect(page.locator('.drawer-view-tabs')).toHaveClass(/standing-down/)
  await expect(views.nth(0)).toHaveAttribute('aria-selected', 'false')
  await expect(views.nth(0)).toHaveAttribute('tabindex', '0')

  await views.nth(1).click()
  await expect(page.locator('.drawer-view-tabs')).not.toHaveClass(/standing-down/)
  await expect(views.nth(1)).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.file-recent')).toHaveCount(1)
  await expect(page.locator('.project-resource.file-editor')).toHaveCount(0)
  // Returning to the index closes nothing: the chip is still there to go back to.
  await expect(page.locator('.drawer-file-subtabs [role="tab"]')).toHaveText(['README.md'])
})

test('a second file joins the rail and the first keeps its place', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/files-tab-harness.html?view=recent')
  await page.locator('.file-recent .file-result-row', { hasText: 'style.css' }).click()
  await page.locator('.drawer-view-tabs button').nth(1).click()
  await page.locator('.file-recent .file-result-row', { hasText: 'gitLand.ts' }).click()

  const chips = page.locator('.drawer-file-subtabs [role="tab"]')
  await expect(chips).toHaveText(['style.css', 'gitLand.ts'])
  await expect(chips.nth(1)).toHaveAttribute('aria-selected', 'true')

  // Re-selecting the first does not reorder the rail under the pointer.
  await chips.nth(0).click()
  await expect(chips).toHaveText(['style.css', 'gitLand.ts'])
  await expect(chips.nth(0)).toHaveAttribute('aria-selected', 'true')
})

test('a chip closes, and closing the one showing lands on its neighbour', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/files-tab-harness.html?view=recent')
  await page.locator('.file-recent .file-result-row', { hasText: 'style.css' }).click()
  await page.locator('.drawer-view-tabs button').nth(1).click()
  await page.locator('.file-recent .file-result-row', { hasText: 'gitLand.ts' }).click()

  await page.locator('.drawer-file-close').nth(1).click()
  const chips = page.locator('.drawer-file-subtabs [role="tab"]')
  await expect(chips).toHaveText(['style.css'])
  await expect(chips.first()).toHaveAttribute('aria-selected', 'true')

  // The last one closing takes the rail with it and hands the body back to the index.
  await page.locator('.drawer-file-close').first().click()
  await expect(page.locator('.drawer-file-subtabs-row')).toHaveCount(0)
  await expect(page.locator('.file-recent')).toHaveCount(1)
})

/**
 * The pane placement has to stay one gesture away, because it stopped being the default.
 * Three ways lead there and this covers two of them; the third is the row drag, which
 * `pane-layout.spec.ts` already owns.
 */
test('a pane is still one gesture away, by modifier and by the rail control', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/files-tab-harness.html')

  await page.locator('.file-tree-row.file', { hasText: 'README.md' }).click({ modifiers: ['ControlOrMeta'] })
  expect(await page.evaluate(() => (globalThis as { __paneRequests?: string[] }).__paneRequests))
    .toEqual(['README.md'])
  // Mod-click never opened it here, so no rail appeared.
  await expect(page.locator('.drawer-file-subtabs-row')).toHaveCount(0)

  await page.locator('.file-tree-row.file', { hasText: 'README.md' }).click()
  await expect(page.locator('.drawer-file-pop')).toBeEnabled()
  await page.locator('.drawer-file-pop').click()
  expect(await page.evaluate(() => (globalThis as { __paneRequests?: string[] }).__paneRequests))
    .toEqual(['README.md', 'README.md'])
  // Moving it to a pane closes its drawer tab: one live editor per file per browser.
  await expect(page.locator('.drawer-file-subtabs-row')).toHaveCount(0)
})

test('the tree row menu names the pane placement now that a click does not', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/files-tab-harness.html')
  await page.locator('.file-tree-row.file', { hasText: 'README.md' }).click({ button: 'right' })
  const open = page.locator('.project-file-menu [role="menuitem"]', { hasText: 'Open in a pane' })
  await expect(open).toHaveCount(1)
  await open.click()
  expect(await page.evaluate(() => (globalThis as { __paneRequests?: string[] }).__paneRequests))
    .toEqual(['README.md'])
})

test('a chip carries its own actions menu', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/files-tab-harness.html')
  await page.locator('.file-tree-row.file', { hasText: 'README.md' }).click()

  await page.locator('.drawer-file-subtabs [role="tab"]').first().click({ button: 'right' })
  const menu = page.locator('.drawer-file-menu')
  await expect(menu).toBeVisible()
  // Close others is offered but honest about being a no-op with one tab open.
  await expect(menu.locator('[role="menuitem"]', { hasText: 'Close others' })).toBeDisabled()
  await menu.locator('[role="menuitem"]', { hasText: 'Open in a pane' }).click()
  expect(await page.evaluate(() => (globalThis as { __paneRequests?: string[] }).__paneRequests))
    .toEqual(['README.md'])
})

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
