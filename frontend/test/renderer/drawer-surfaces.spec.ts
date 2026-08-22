import { expect, test } from 'playwright/test'

/**
 * Structure you can only see in a layout: where one region ends and the next begins.
 *
 * Both surfaces here failed at exactly that, in the same way, for the same reason — a
 * boundary drawn with the same weight as the rows on either side of it is not a boundary.
 * The Actions tab stacks four collapsible catalogs in one scroller and separated them with
 * the same 1px hairline every row inside them already carries. Agent → Instructions pinned
 * a file's body directly under the memory list with a background change and nothing else,
 * which made an open file read as more list.
 *
 * Nothing below the browser can check any of that: it is entirely computed style and box
 * geometry, and the components themselves are unchanged.
 */

test('each Actions section is bounded by more than a row rule', async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 1000 })
  await page.goto('/drawer-surfaces-harness.html')
  await page.waitForSelector('.actions-section')

  const sections = await page.evaluate(() => [...document.querySelectorAll('.actions-section')].map(section => {
    const header = section.querySelector<HTMLElement>('.actions-section-header')!
    const style = getComputedStyle(header)
    return {
      id: section.className.match(/actions-section-([a-z]+)/)![1],
      borderTop: Number.parseFloat(getComputedStyle(section).borderTopWidth),
      headerBackground: style.backgroundImage,
      headerEdge: style.boxShadow,
      headerBottom: style.borderBottomWidth,
    }
  }))

  expect(sections.map(item => item.id)).toEqual(['skills', 'prompts', 'clipboard'])
  // The first section needs no rule above it — the pane heading is there. Every later one
  // is separated by a rule thicker than any row rule inside a section.
  expect(sections[0].borderTop).toBe(0)
  for (const section of sections.slice(1)) expect(section.borderTop, section.id).toBeGreaterThanOrEqual(2)
  // And the header is a bar rather than another row: its own ground and a leading edge.
  for (const section of sections) {
    expect(section.headerBackground, section.id).toContain('gradient')
    expect(section.headerEdge, section.id).toContain('inset')
  }
})

test('the Actions tab names the session no more often than the pane heading does', async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 1000 })
  await page.goto('/drawer-surfaces-harness.html')
  await page.waitForSelector('.actions-section')

  // The heading immediately above this tab already says which session it is scoped to, in
  // more words. A second line saying it again pushed every section down by a row and made
  // the sticky headers stack against it.
  await expect(page.locator('.actions-tab > .drawer-status')).toHaveCount(0)
  const mentions = await page.evaluate(() =>
    [...document.querySelectorAll('.utility-drawer *')]
      .filter(el => !el.children.length && /Schedule Session Resume/.test(el.textContent || '')).length)
  expect(mentions).toBe(1)
  // Sticky section headers therefore pin to the top of the scroller rather than below a
  // line that is no longer there.
  expect(await page.evaluate(() =>
    getComputedStyle(document.querySelector('.actions-section-header')!).top)).toBe('0px')
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
