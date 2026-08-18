import { expect, test } from 'playwright/test'

// Continuity's first render measures inline-code affordances against `offsetParent`, which is
// null inside a `display:none` subtree. The drawer keeps its note host mounted-but-`hidden`
// across tab switches, so without a mount gate every drawer opened on another tab started an
// editor that threw "Cannot read properties of null (reading 'offsetLeft')" out of its async
// start — surfacing as an app-wide error toast, with no `continuity-ready` and so no scroll or
// undo-history restore either.

const failures = (page: import('playwright/test').Page) =>
  page.evaluate(() => (window as unknown as { harnessFailures: string[] }).harnessFailures)

test('a note editor mounted inside a hidden host defers its element instead of failing', async ({ page }) => {
  await page.goto('/hidden-note-editor-harness.html')

  // The slot stands in the editor's place until the host has a layout box.
  await expect(page.locator('.note-editor-slot')).toHaveCount(1)
  await expect(page.locator('continuity-editor')).toHaveCount(0)
  expect(await failures(page)).toEqual([])

  await page.locator('#reveal').click()
  await expect(page.locator('continuity-editor')).toHaveCount(1)
  await expect(page.locator('.note-editor-slot')).toHaveCount(0)

  const ready = await page.evaluate(async () => {
    const element = document.querySelector('continuity-editor') as (HTMLElement & { ready: Promise<unknown> }) | null
    if (!element) return 'missing'
    const outcome = await Promise.race([
      element.ready.then(() => 'ready', (error: unknown) => `rejected: ${String(error)}`),
      new Promise(resolve => setTimeout(() => resolve('timeout'), 15_000)),
    ])
    return outcome
  })
  expect(ready).toBe('ready')
  expect(await failures(page)).toEqual([])
})

test('a note editor mounted visible starts without deferring', async ({ page }) => {
  await page.goto('/hidden-note-editor-harness.html?visible=1')

  await expect(page.locator('continuity-editor')).toHaveCount(1)
  await expect(page.locator('.note-editor-slot')).toHaveCount(0)

  const ready = await page.evaluate(async () => {
    const element = document.querySelector('continuity-editor') as (HTMLElement & { ready: Promise<unknown> }) | null
    if (!element) return 'missing'
    return await Promise.race([
      element.ready.then(() => 'ready', (error: unknown) => `rejected: ${String(error)}`),
      new Promise(resolve => setTimeout(() => resolve('timeout'), 15_000)),
    ])
  })
  expect(ready).toBe('ready')
  expect(await failures(page)).toEqual([])
})
