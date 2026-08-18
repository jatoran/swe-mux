import { expect, test } from 'playwright/test'

/**
 * The Configure Actions modal, end to end against the stubbed daemon: the
 * progressive-disclosure shape, the plain-language catalog controls, the
 * preview-as dimmer, and the project delta scope (additions overlay the live
 * global layout and revert cleanly). These are the behaviors the redesign
 * exists for, and none of them is observable from the pure model tests alone.
 */

test('first open orients once, then leads with one device’s layouts', async ({ page }) => {
  await page.goto('/action-editor-harness.html')
  const callout = page.locator('.rail-intro-callout')
  await expect(callout).toContainText('How this works')
  await callout.getByRole('button', { name: 'Got it' }).click()
  await expect(callout).toHaveCount(0)

  // One device at a time; both surfaces of that device render their rows.
  const switchGroup = page.getByRole('group', { name: 'Device layout to edit' })
  await expect(switchGroup.getByRole('button', { name: 'Desktop' })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.rail-surface')).toHaveCount(2)
  const railChips = page.locator('.rail-surface').first().locator('.rail-chip-label')
  await expect(railChips.filter({ hasText: /^Esc$/ })).toHaveCount(1)

  // Creation and the catalog are collapsed disclosures below the layouts.
  await expect(page.locator('details.rail-add')).not.toHaveAttribute('open', '')
  await expect(page.locator('details.rail-catalog')).not.toHaveAttribute('open', '')

  // The callout stays dismissed for later opens.
  await page.goto('/action-editor-harness.html?seen=1')
  await expect(page.locator('.rail-intro-callout')).toHaveCount(0)
})

test('the catalog expands into labelled placement checkboxes that edit the layout', async ({ page }) => {
  await page.goto('/action-editor-harness.html?seen=1')
  await page.locator('details.rail-catalog > summary').click()
  await page.getByLabel('Filter actions').fill('Esc')
  const row = page.locator('.rail-catalog-row', { hasText: 'Esc' }).first()
  await expect(row.locator('.rail-placement-summary')).toHaveText('Desktop rail · Mobile rail')
  await row.locator('.rail-catalog-head').click()

  const detail = row.locator('.rail-catalog-detail')
  await expect(detail).toContainText('Where it appears')
  await expect(detail).toContainText('Shown in these sessions')
  await expect(detail.locator('input[type="checkbox"]').first()).toBeVisible()

  // Unchecking "Desktop rail" removes the chip from the visible Rail rows.
  const railSurface = page.locator('.rail-surface').first()
  const escChip = railSurface.locator('.rail-chip-label', { hasText: /^Esc$/ })
  await expect(escChip).toHaveCount(1)
  await detail.locator('label.check', { hasText: 'Desktop rail' }).locator('input').click()
  await expect(escChip).toHaveCount(0)
  await expect(row.locator('.rail-placement-summary')).toHaveText('Mobile rail')
  await detail.locator('label.check', { hasText: 'Desktop rail' }).locator('input').click()
  await expect(escChip).toHaveCount(1)
})

test('preview-as dims what that session type would not show', async ({ page }) => {
  await page.goto('/action-editor-harness.html?seen=1')
  await expect(page.locator('.rail-chip.filtered')).toHaveCount(0)
  await page.locator('.rail-preview-as select').selectOption('shell')
  // Attach is agent-only, so a shell preview dims it without removing it.
  await expect(page.locator('.rail-chip.filtered', { hasText: 'Attach' })).toHaveCount(1)
  await page.locator('.rail-preview-as select').selectOption('')
  await expect(page.locator('.rail-chip.filtered')).toHaveCount(0)
})

test('a project scope overlays additions on the shared layout and reverts cleanly', async ({ page }) => {
  await page.goto('/action-editor-harness.html?seen=1')
  await page.locator('.rail-scope select').selectOption('p1')
  await expect(page.locator('.rail-scope-note').first()).toContainText('Showing the shared layout')

  // Shared rows are tagged; a project row can be added beside them.
  await expect(page.locator('.rail-origin', { hasText: /^shared$/ }).first()).toBeVisible()
  await page.locator('.rail-surface').first().getByRole('button', { name: '+ Project row' }).click()
  await expect(page.locator('.rail-row-editor.project-owned')).toHaveCount(1)

  // A custom action added in project scope defaults to "this project only".
  await page.locator('details.rail-add > summary').click()
  const addForm = page.locator('.rail-add-form')
  await expect(addForm.locator('label', { hasText: 'For' }).locator('select')).toHaveValue('project')
  await addForm.locator('label', { hasText: 'Name' }).locator('input').fill('commit')
  await addForm.getByRole('button', { name: 'Add action' }).click()
  const commitChip = page.locator('.rail-chip-label', { hasText: /^commit$/ })
  await expect(commitChip).toHaveCount(1)
  await expect(page.locator('.rail-scope select')).toContainText('Project One (has additions)')

  // The global scope never sees the addition.
  await page.locator('.rail-scope select').selectOption('')
  await expect(commitChip).toHaveCount(0)
  await page.locator('.rail-scope select').selectOption('p1')
  await expect(commitChip).toHaveCount(1)

  // Removing project additions returns the project to plain inheritance.
  await page.getByRole('button', { name: 'Remove project additions' }).click()
  await expect(commitChip).toHaveCount(0)
  await expect(page.locator('.rail-row-editor.project-owned')).toHaveCount(0)
})
