import { expect, test } from 'playwright/test'

/**
 * A saved account's name, edited where the accounts are listed (Settings → Accounts).
 *
 * The field is empty with the email as its placeholder when no alias is set, because the
 * email is the fallback rather than the value; clearing the field is how it comes back.
 * The harness derives `label` the way the daemon does, so what the row draws after the
 * response is asserted as well as what was sent.
 */
test('an account alias is set, reverted with Escape, and cleared back to the email', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/settings-harness.html')
  await page.getByRole('tab', { name: 'Accounts', exact: true }).click()

  const work = page.getByRole('textbox', { name: 'Name for claude account work@example.com' })
  const home = page.getByRole('textbox', { name: 'Name for claude account home@example.com' })
  await expect(work).toHaveValue('Work')
  await expect(home).toHaveValue('')
  await expect(home).toHaveAttribute('placeholder', 'home@example.com')

  await home.fill('Personal')
  await home.press('Escape')
  await expect(home).toHaveValue('', { timeout: 2_000 })
  await expect(page.locator('.settings-panel')).toBeVisible()

  await home.fill('  Personal  ')
  await home.press('Enter')
  await expect.poll(() => page.evaluate(() => window.settingsCalls.filter(call => call.method === 'PATCH')
    .map(call => [call.path, call.body]))).toEqual([['/api/provider-accounts/claude/claude-home', { alias: 'Personal' }]])
  await expect(home).toHaveValue('Personal')

  await work.fill('')
  await work.press('Enter')
  await expect.poll(() => page.evaluate(() => window.settingsCalls.filter(call => call.method === 'PATCH').slice(-1)[0]?.body))
    .toEqual({ alias: null })
  await expect(work).toHaveValue('')
  await expect(work).toHaveAttribute('placeholder', 'work@example.com')
})
