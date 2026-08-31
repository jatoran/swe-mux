import { expect, test } from 'playwright/test'

/**
 * Structure you can only see in a layout: where one region ends and the next begins.
 *
 * Both surfaces here failed at exactly that, in the same way, for the same reason — a
 * boundary drawn with the same weight as the rows on either side of it is not a boundary.
 * The Actions tab stacks three collapsible catalogs in one scroller and separated them with
 * the same 1px hairline every row inside them already carries. Agent → Instructions pinned
 * a file's body directly under the memory list with a background change and nothing else,
 * which made an open file read as more list.
 *
 * The Actions separators now also carry a hue per section, which is the same problem one
 * step on: the headers are sticky, so a boundary that is drawn correctly is still not on
 * screen when the reader is halfway down a catalog.
 *
 * Nothing below the browser can check any of that: it is entirely computed style and box
 * geometry, and the components themselves are unchanged.
 */

test('Actions exposes three named views and one body at a time', async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 1000 })
  await page.goto('/drawer-surfaces-harness.html')
  await page.waitForSelector('.actions-view-tabs')
  await expect(page.locator('.actions-view-tabs [role="tab"]')).toHaveText([/Skills/, 'Prompts', 'Clipboard'])
  await expect(page.locator('.actions-view')).toHaveCount(1)
  await page.locator('.actions-view-tabs [role="tab"]',{hasText:'Prompts'}).click()
  await expect(page.locator('.actions-view-tabs [role="tab"]',{hasText:'Prompts'})).toHaveAttribute('aria-selected','true')
  await expect(page.locator('[data-setting="drawer.actions.prompts"]')).toBeVisible()
  await expect(page.locator('[data-setting="drawer.actions.skills"]')).toHaveCount(0)
  await page.locator('.actions-view-tabs [role="tab"]',{hasText:'Clipboard'}).click()
  await expect(page.locator('[data-setting="drawer.actions.clipboard"]')).toBeVisible()
})

test('Actions removes the pane heading and keeps its target only where the terminal is covered', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 1000 })
  await page.goto('/drawer-surfaces-harness.html')
  await page.waitForSelector('.actions-view-tabs')

  await expect(page.locator('.drawer-pane-heading')).toHaveCount(0)
  await expect(page.locator('.actions-tab > .drawer-status')).toHaveCount(0)
  await expect(page.locator('.actions-target')).toBeHidden()
  expect(await page.evaluate(() => getComputedStyle(document.querySelector('.actions-view-tabs')!).top)).toBe('0px')

  await page.setViewportSize({ width: 400, height: 1000 })
  await expect(page.locator('.actions-target')).toBeVisible()
  await expect(page.locator('.actions-target')).toHaveText('Target: Schedule Session Resume')
})

test('the instruction viewer is a labelled region, selected or not', async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 1000 })
  await page.goto('/drawer-surfaces-harness.html?tab=agent')
  await page.waitForSelector('.agent-context-viewer')

  // Nothing is opened for you. The tab used to select whichever file sorted first, which
  // read as a decision it had made on your behalf and left the viewer's contents ambiguous.
  await expect(page.locator('.agent-context-source.selected')).toHaveCount(0)
  await expect(page.locator('.agent-context-viewer')).toHaveClass(/empty/)
  await expect(page.locator('.agent-context-viewer-kicker')).toHaveText('Preview')
  await expect(page.locator('.agent-context-viewer > header strong')).toHaveText('No file selected')

  // The region exists whether or not it holds a file, and is divided from the list above it
  // by a rule heavier than the ones between the disclosures.
  const geometry = await page.evaluate(() => {
    const viewer = document.querySelector<HTMLElement>('.agent-context-viewer')!
    const memories = document.querySelector<HTMLElement>('.agent-context-memories')!
    return {
      borderTop: Number.parseFloat(getComputedStyle(viewer).borderTopWidth),
      disclosureRule: Number.parseFloat(getComputedStyle(memories).borderBottomWidth),
      below: viewer.getBoundingClientRect().top >= memories.getBoundingClientRect().bottom - 0.5,
    }
  })
  expect(geometry.below).toBe(true)
  expect(geometry.borderTop).toBeGreaterThan(geometry.disclosureRule)

  // The Agent Context title is gone for the same reason the Actions session line is: the
  // pane heading above already carries it. The cwd line below it is kept, because which
  // harness and directory the inventory resolved against is a fact the heading has not got.
  await expect(page.locator('.agent-context-header strong')).toHaveCount(0)
  await expect(page.locator('.agent-context-header small')).toContainText('D:/PROJECTS/swe-mux')
})

test('picking a file fills the viewer and keeps it a separate region', async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 1000 })
  await page.goto('/drawer-surfaces-harness.html?tab=agent')
  await page.waitForSelector('.agent-context-viewer')

  await page.locator('.agent-context-source', { hasText: 'CLAUDE.md' }).first().click()
  await expect(page.locator('.agent-context-viewer')).not.toHaveClass(/empty/)
  await expect(page.locator('.agent-context-viewer > header strong')).toHaveText('CLAUDE.md')
  await expect(page.locator('.agent-context-source.selected')).toHaveCount(1)
  await expect(page.locator('.agent-context-viewer pre')).toContainText('Body of CLAUDE.md')

  // And it can be closed back to empty, which is what makes the empty state a place you can
  // choose to be rather than only the state you arrived in.
  await page.locator('.agent-context-viewer-clear').click()
  await expect(page.locator('.agent-context-viewer')).toHaveClass(/empty/)
  await expect(page.locator('.agent-context-source.selected')).toHaveCount(0)
})

test('a selection survives a remount of the tab, per Project', async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 1000 })
  await page.goto('/drawer-surfaces-harness.html?tab=agent')
  await page.waitForSelector('.agent-context-viewer')
  await page.locator('.agent-context-source', { hasText: 'AGENTS.md' }).first().click()
  await expect(page.locator('.agent-context-viewer > header strong')).toHaveText('AGENTS.md')

  // Device-local and keyed by Project: a reading position, not a setting. Without it the
  // tab forgets what you were reading every time the drawer is rebuilt, which is what made
  // "select nothing by default" feel like losing your place rather than keeping it.
  await page.reload()
  await page.waitForSelector('.agent-context-viewer')
  await expect(page.locator('.agent-context-viewer')).not.toHaveClass(/empty/)
  await expect(page.locator('.agent-context-viewer > header strong')).toHaveText('AGENTS.md')
})
