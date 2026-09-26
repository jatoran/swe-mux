import { expect, test, type Page } from 'playwright/test'

// A Preview whose port another process also holds. The notice has its own grid row: it
// must push the frame down rather than cover it, and it must disappear entirely - row and
// all - when the port is not shared, so an ordinary preview keeps its whole height.

const open = async (page: Page, conflict: 'wrong' | 'none', width = 1100) => {
  await page.setViewportSize({ width, height: 700 })
  await page.goto(`/preview-pane-harness.html?conflict=${conflict}`)
  await page.locator('.preview-pane').waitFor()
}

const box = async (page: Page, selector: string) => {
  const found = await page.locator(selector).boundingBox()
  expect(found, selector).not.toBeNull()
  return found!
}

test('a foreign process answering is named above the frame, which it does not cover', async ({ page }) => {
  await open(page, 'wrong')
  const notice = page.getByTestId('preview-port-conflict')
  await expect(notice).toBeVisible()
  await expect(notice).toHaveClass(/wrong_server/)
  await expect(notice).toContainText('showing PID 136796')

  const header = await box(page, '.preview-pane>header')
  const banner = await box(page, '.preview-port-conflict')
  const frame = await box(page, '.preview-frame')
  const rail = await box(page, '.preview-rail')
  expect(banner.y).toBeGreaterThanOrEqual(header.y + header.height - 1)
  expect(frame.y).toBeGreaterThanOrEqual(banner.y + banner.height - 1)
  expect(rail.y).toBeGreaterThanOrEqual(frame.y + frame.height - 1)
  // The frame keeps most of the pane: the notice is a line, not a takeover.
  expect(frame.height).toBeGreaterThan(400)
})

test('details list every process on the port and mark the one answering', async ({ page }) => {
  await open(page, 'wrong')
  const notice = page.getByTestId('preview-port-conflict')
  await notice.getByRole('button', { name: 'details' }).click()
  const rows = notice.locator('li')
  await expect(rows).toHaveCount(2)
  await expect(rows.filter({ hasText: 'PID 136796' })).toHaveClass(/answering/)
  await expect(rows.filter({ hasText: 'PID 136796' })).toContainText('not started by any live swe-mux session')
  await expect(rows.filter({ hasText: 'PID 25604' })).toContainText("this preview's session")
  await expect(notice).toContainText('run this server on a free port')
})

test('an unshared port draws no notice and gives the frame the row back', async ({ page }) => {
  await open(page, 'none')
  await expect(page.getByTestId('preview-port-conflict')).toHaveCount(0)
  const header = await box(page, '.preview-pane>header')
  const frame = await box(page, '.preview-frame')
  expect(Math.abs(frame.y - (header.y + header.height))).toBeLessThanOrEqual(1)
})

test('on a phone-width pane the notice stays inside the width', async ({ page }) => {
  await open(page, 'wrong', 390)
  const pane = await box(page, '.preview-pane')
  const banner = await box(page, '.preview-port-conflict')
  expect(banner.x).toBeGreaterThanOrEqual(pane.x - 1)
  expect(banner.x + banner.width).toBeLessThanOrEqual(pane.x + pane.width + 1)
})
