import { expect, test } from 'playwright/test'

/**
 * The Files tab, after its two readings became named subtabs.
 *
 * Recent used to be a pressed clock icon inside the search row: a mode with no name in the
 * chrome, unreachable by command or by voice, and indistinguishable from the tree except by
 * inspecting a toggle's `aria-pressed`. It is a registered segment now
 * (`drawerSegments.ts`), which is a claim about DOM structure and about width - the subtabs
 * share the heading row with the Project's name, and a phone's drawer is 320px wide.
 */

test('the tree and Recent are named subtabs, not a toggle in the search row', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/files-tab-harness.html')

  const tabs = page.locator('.drawer-pane-heading .drawer-segmented-inline button')
  await expect(tabs).toHaveText(['File Explorer', 'Recent'])
  await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'true')
  // The icon it replaced is gone rather than kept alongside: two controls for one mode is
  // how the pressed state and the subtab would come to disagree.
  await expect(page.locator('.file-search-recent')).toHaveCount(0)

  // The tree is what the first subtab shows, and Recent replaces it rather than joining it.
  await expect(page.locator('.file-tree')).toHaveCount(1)
  await expect(page.locator('.file-recent')).toHaveCount(0)

  await tabs.nth(1).click()
  await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true')
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
  await expect(page.locator('.drawer-pane-heading .drawer-segmented-inline button').nth(1))
    .toHaveAttribute('aria-selected', 'true')
})

/**
 * The heading row is the one place the subtabs and the Project's name compete for width, and
 * a 320px drawer on a phone is where that competition is decided. The subtabs are the row's
 * navigation; the name is a caption, and it is also in the toolbar title and the sidebar.
 * Before this, the caption read `Project: swe-mux` and never yielded, so Git's three subtabs
 * ellipsised into stubs beside it.
 */
for (const width of [320, 380, 480]) {
  test(`the subtab labels keep their words at ${width}px, and the Project name yields`, async ({ page }) => {
    await page.setViewportSize({ width: width + 40, height: 900 })
    await page.goto(`/files-tab-harness.html?width=${width}`)
    const measured = await page.evaluate(() => {
      const box = (element: Element) => {
        const { x, width: w } = element.getBoundingClientRect()
        return { x, width: w, right: x + w }
      }
      const buttons = [...document.querySelectorAll<HTMLElement>('.drawer-pane-heading .drawer-segmented-inline button')]
      const scope = document.querySelector<HTMLElement>('.drawer-scope-context')!
      const heading = document.querySelector<HTMLElement>('.drawer-pane-heading')!
      return {
        labels: buttons.map(button => ({
          text: button.textContent,
          clipped: button.scrollWidth > button.clientWidth + 1,
          ...box(button),
        })),
        scope: { text: scope.textContent, ...box(scope) },
        heading: box(heading),
      }
    })
    // Full words, not stubs: nothing here is allowed to ellipsise.
    expect(measured.labels.map(label => label.text)).toEqual(['File Explorer', 'Recent'])
    for (const label of measured.labels) expect(label.clipped).toBe(false)
    // The bare Project name — no `Project:` in front of it, which is the eight characters
    // that used to make this row overflow.
    expect(measured.scope.text).toBe('swe-mux')
    // One row: the subtabs lead it, the caption follows, and nothing wrapped.
    expect(measured.labels[0].right).toBeLessThanOrEqual(measured.labels[1].x)
    expect(measured.labels[1].right).toBeLessThanOrEqual(measured.scope.x + 1)
    expect(measured.scope.right).toBeLessThanOrEqual(measured.heading.right + 1)
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
