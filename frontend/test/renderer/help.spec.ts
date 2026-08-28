import { expect, test } from 'playwright/test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * The help surface exists, opens on the topic it was asked for, and says what the design
 * document says.
 *
 * Phase 16's exit criterion has three parts and two of them are only visible in a browser:
 * the tour has to be re-openable *from here*, and the scan timeline's modal has to open on
 * its own topic and match its feature doc. The unit tests assert the registry; this asserts
 * the surface a person actually gets, including on a phone, where a two-column reader that
 * did not collapse would put the topic body off screen.
 */

const docsRoot = join(import.meta.dirname, '..', '..', '..', '.docs', 'design', 'features')

test('help opens on the index and lists every topic with a sentence of its own', async ({ page }) => {
  await page.goto('/help-harness.html')
  await expect(page.locator('.help-modal')).toBeVisible()
  const rows = page.locator('.help-index-row')
  expect(await rows.count()).toBeGreaterThan(5)
  // A row that is only a title is a link, not help. Every one carries its blurb.
  for (const text of await rows.locator('span').allTextContents()) {
    expect(text.trim().length).toBeGreaterThan(40)
  }
})

test('the scan timeline opens on its own topic and quotes its feature doc', async ({ page }) => {
  await page.goto('/help-harness.html?topic=scan-timeline')
  await expect(page.locator('.help-modal h2')).toHaveText('Scan timeline')
  await expect(page.locator('.help-source')).toContainText('.docs/design/features/scan-timeline.md')

  // Read against the doc on disk rather than against a copy pasted into this spec: a
  // literal here would be a third copy of the sentence and would be the thing that rots.
  const doc = readFileSync(join(docsRoot, 'scan-timeline.md'), 'utf8')
  const firstSentence = doc.split('## What it is')[1].trim().split('\n')[0].trim()
  expect(firstSentence.length).toBeGreaterThan(30)
  await expect(page.locator('.help-topic')).toContainText(firstSentence)

  // The gating half, which is what people arrive asking about: three switches, and an
  // empty Timeline looks identical whichever one is closed.
  await expect(page.locator('.help-doc-section h4')).toContainText(['What it is', 'Authorization and lifetime'])
  await expect(page.locator('.help-topic')).toContainText('Three independent gates')
})

test('the documentation link is a real page URL, never the retired fragment form', async ({ page }) => {
  await page.goto('/help-harness.html?topic=scan-timeline')
  const href = await page.locator('.help-more a').getAttribute('href')
  // The site slug, not the topic id: `/docs/scan-timeline/` is a page nobody publishes.
  // Trailing slash is load-bearing under the Pages Actions source, and `/docs/#slug` was
  // retired with the docs browser (`site/README.md`).
  expect(href).toBe('https://swemux.dev/docs/control-plane/')
})

test('the tour is re-openable from help', async ({ page }) => {
  // The whole reason this surface exists: before it, the only door to the tour was one
  // section of Settings → General, which nobody looking for a tour would think to open.
  await page.goto('/help-harness.html')
  await page.locator('.help-action.primary').click()
  await expect(page.locator('#tour-started')).toHaveText('yes')
})

test('the configurator button says why it cannot run instead of vanishing', async ({ page }) => {
  await page.goto('/help-harness.html?configurator=0')
  const button = page.locator('.help-action', { hasText: 'Ask an agent' })
  await expect(button).toBeDisabled()
  await expect(button).toHaveAttribute('title', 'No agent CLI is available')
})

test('filtering narrows the list and says so when nothing matches', async ({ page }) => {
  await page.goto('/help-harness.html')
  const all = await page.locator('.help-topic-list li').count()
  await page.locator('.help-search input').fill('worktree')
  expect(await page.locator('.help-topic-list li').count()).toBeLessThan(all)
  await page.locator('.help-search input').fill('zzzzz')
  await expect(page.locator('.help-empty').first()).toBeVisible()
})

test('the phone layout puts the topic body on screen, not beside it', async ({ page }) => {
  // The desktop reader is a 230px topic column beside the body. Left as a grid on a phone
  // that body would be 160px wide, which is the failure this collapse exists to avoid.
  await page.setViewportSize({ width: 390, height: 780 })
  await page.goto('/help-harness.html?topic=scan-timeline')
  const modal = (await page.locator('.help-modal').boundingBox())!
  const content = (await page.locator('.help-content').boundingBox())!
  expect(content.width).toBeGreaterThan(modal.width * 0.8)
  expect(content.x).toBeGreaterThanOrEqual(modal.x - 0.5)
  expect(content.x + content.width).toBeLessThanOrEqual(modal.x + modal.width + 0.5)
})
