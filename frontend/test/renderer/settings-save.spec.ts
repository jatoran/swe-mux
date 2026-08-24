import { expect, test } from 'playwright/test'

/**
 * The two destructive Settings paths, driven through the real panel.
 *
 * Restore defaults rewrites the whole saved configuration on one click, is not staged
 * behind Save, and cannot be undone — and its failure used to be an unhandled rejection
 * that left the panel showing a draft the daemon no longer held. Save used to be two
 * requests reported by one catch, which said "nothing was changed" even for the failure
 * that could only happen *after* its sibling had committed.
 *
 * Both claims are about what a click sends and what the panel then says, so both need the
 * panel, a daemon that can refuse, and a record of the requests actually made. The harness
 * provides all three (`window.settingsCalls`, and `?fail=` to make the daemon say no).
 */

type Page = import('playwright/test').Page

const DESKTOP = { width: 1280, height: 900 }

const calls = (page: Page, method: string, path: string) => page.evaluate(
  ([wanted, target]) => window.settingsCalls.filter(
    call => call.method === wanted && call.path === target,
  ).length,
  [method, path] as const,
)

async function openGeneral(page: Page, query = '') {
  await page.setViewportSize(DESKTOP)
  await page.goto(`/settings-harness.html${query}`)
  await expect(page.locator('.settings-panel')).toBeVisible()
  await page.locator('.settings-tabs button', { hasText: 'General' }).first().click()
  await expect(page.getByRole('button', { name: 'Restore defaults' })).toBeVisible()
}

test('Restore defaults asks first, and asking sends nothing', async ({ page }) => {
  await openGeneral(page)
  expect(await calls(page, 'POST', '/api/config/reset')).toBe(0)

  await page.getByRole('button', { name: 'Restore defaults' }).click()
  const dialog = page.getByRole('alertdialog', { name: 'Restore default settings' })
  await expect(dialog).toBeVisible()
  // Nothing has been written; the panel behind it is out of the accessibility tree.
  expect(await calls(page, 'POST', '/api/config/reset')).toBe(0)
  await expect(page.locator('.settings-panel')).toHaveAttribute('aria-hidden', 'true')

  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toHaveCount(0)
  expect(await calls(page, 'POST', '/api/config/reset')).toBe(0)
})

test('Escape backs out of the confirmation without writing', async ({ page }) => {
  // The dialog is a dismiss level like every other layer of this panel, so back and
  // Escape reach it before they reach the panel — which must still be open afterwards.
  await openGeneral(page)
  await page.getByRole('button', { name: 'Restore defaults' }).click()
  const dialog = page.getByRole('alertdialog', { name: 'Restore default settings' })
  await expect(dialog).toBeVisible()
  // Membership of the dismiss stack is claimed in an effect, in the same commit that moves
  // focus into the dialog. Waiting for the focus is waiting for the level to be armed;
  // without it a fast Escape reaches the panel's own level and closes Settings instead.
  await expect(dialog.getByRole('button', { name: 'Keep current settings' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('alertdialog', { name: 'Restore default settings' })).toHaveCount(0)
  await expect(page.locator('.settings-panel')).toBeVisible()
  expect(await calls(page, 'POST', '/api/config/reset')).toBe(0)
})

test('confirming restores the defaults and says so', async ({ page }) => {
  await openGeneral(page)
  await page.getByRole('button', { name: 'Restore defaults' }).click()
  const dialog = page.getByRole('alertdialog', { name: 'Restore default settings' })
  await dialog.getByRole('button', { name: 'Restore defaults' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.locator('.settings-panel>footer span')).toHaveText('defaults restored')
  expect(await calls(page, 'POST', '/api/config/reset')).toBe(1)
})

test('a refused restore leaves a visible failure instead of nothing', async ({ page }) => {
  await openGeneral(page, '?fail=reset')
  await page.getByRole('button', { name: 'Restore defaults' }).click()
  const dialog = page.getByRole('alertdialog', { name: 'Restore default settings' })
  await dialog.getByRole('button', { name: 'Restore defaults' }).click()

  const status = page.locator('.settings-panel>footer span')
  await expect(status).toContainText('restore failed')
  await expect(status).toContainText('read-only')
  // ...and the reason is also in the errors block, where every other rejected write goes.
  await expect(page.locator('.settings-errors')).toContainText('read-only')
})

test('Save is one request, and a conflict says nothing was changed truthfully', async ({ page }) => {
  await openGeneral(page, '?fail=apply')
  await page.getByLabel('Startup directory').fill('D:/elsewhere')
  const save = page.locator('.settings-panel>footer button.primary')
  await expect(save).toBeEnabled()
  await save.click()

  await expect(page.locator('.settings-panel>footer span')).toHaveText('invalid · nothing was changed')
  // The claim is only true because the whole save was one request. Two would leave the
  // keybindings PUT committed while the config half 409d — the original defect.
  expect(await calls(page, 'POST', '/api/settings/apply')).toBe(1)
  expect(await calls(page, 'PUT', '/api/keybindings')).toBe(0)
  expect(await calls(page, 'PATCH', '/api/config')).toBe(0)
})

test('a successful save commits both halves in one request', async ({ page }) => {
  await openGeneral(page)
  await page.getByLabel('Startup directory').fill('D:/elsewhere')
  await page.locator('.settings-panel>footer button.primary').click()

  await expect(page.locator('.settings-panel>footer span')).toContainText('saved')
  expect(await calls(page, 'POST', '/api/settings/apply')).toBe(1)
  expect(await calls(page, 'PUT', '/api/keybindings')).toBe(0)
  expect(await calls(page, 'PATCH', '/api/config')).toBe(0)
})
