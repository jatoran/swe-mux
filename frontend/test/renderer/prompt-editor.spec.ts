import { expect, test } from 'playwright/test'

/**
 * A prompt template must be writable from the Actions drawer itself.
 *
 * The point of moving authoring here is that the frequent edit is a wording fix on
 * the template you are already looking at, so what matters is not that a form
 * renders but that the *write* it produces is the right one: the right method, the
 * right scope and owner, and the revision the row was read at. A form that draws
 * correctly and posts a global template into a Project (or a create where an update
 * belonged) fails exactly the way the modal-only flow could not.
 */

const HARNESS = '/prompt-editor-harness.html'

test('a new template is created from the drawer, at drawer width', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 700 })
  await page.goto(HARNESS)
  await expect(page.getByRole('button', { name: /^Template number 1/ })).toBeVisible()

  await page.getByRole('button', { name: 'New' }).click()
  // The list is replaced, not pushed down: the form owns the column.
  await expect(page.getByText('New template')).toBeVisible()
  await expect(page.getByRole('button', { name: /^Template number 1/ })).toHaveCount(0)

  await page.getByLabel('Title').fill('Written in the drawer')
  await page.getByLabel('Template body').fill('Check {{area}} and report back.')
  // Placeholder fields are derived from the body as it is typed, before any save.
  await expect(page.getByText('Fields: area')).toBeVisible()

  // Nothing is written until Create is pressed.
  expect(await page.evaluate(() => window.promptWrites.length)).toBe(0)
  await page.getByRole('button', { name: 'Create', exact: true }).click()

  const writes = await page.evaluate(() => window.promptWrites)
  expect(writes).toHaveLength(1)
  expect(writes[0].method).toBe('POST')
  const body = writes[0].body as Record<string, unknown>
  expect(body.title).toBe('Written in the drawer')
  expect(body.body).toBe('Check {{area}} and report back.')
  // The focused Project accepts Project templates, so that is where a new one lands.
  expect(body.scope).toBe('project')
  expect(body.project_id).toBe('p1')

  // A completed save returns to the list, which now holds the new template.
  await expect(page.getByRole('button', { name: /^Written in the drawer/ })).toBeVisible()
})

test('editing a row updates that template against the revision it was read at', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 700 })
  await page.goto(HARNESS)

  await page.getByRole('button', { name: 'Edit Template number 2' }).click()
  await expect(page.getByLabel('Title')).toHaveValue('Template number 2')
  // Save is inert until something actually changed.
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled()

  await page.getByLabel('Title').fill('Template number two')
  await page.getByRole('button', { name: 'Save', exact: true }).click()

  const writes = await page.evaluate(() => window.promptWrites)
  expect(writes).toHaveLength(1)
  expect(writes[0].method).toBe('PUT')
  expect(writes[0].path).toContain('/api/prompts/global/')
  const body = writes[0].body as Record<string, unknown>
  expect(body.title).toBe('Template number two')
  expect(body.revision).toBe('rev-2')
})

test('an unsaved draft survives the drawer being dismissed', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 700 })
  await page.goto(HARNESS)

  await page.getByRole('button', { name: 'Edit Template number 1' }).click()
  await page.getByLabel('Template body').fill('half-written thought')
  // The mirror is taken from an effect, a frame after the keystroke. Waiting for it
  // is not test scaffolding: a dismissal that beat the first frame would have nothing
  // to restore either, and this is the boundary the guarantee actually starts at.
  await expect.poll(() => page.evaluate(() => sessionStorage.length)).toBeGreaterThan(0)
  // The drawer has no dismissal it can intercept; a remount is what a close and
  // reopen looks like to this tab.
  await page.reload()
  await page.getByRole('button', { name: 'Edit Template number 1' }).click()
  await expect(page.getByLabel('Template body')).toHaveValue('half-written thought')

  // Revert drops both the draft and its stash, so the next open is clean.
  await page.getByRole('button', { name: 'Revert' }).click()
  await expect(page.getByLabel('Template body')).toHaveValue('Body of template 1.')
  await page.reload()
  await page.getByRole('button', { name: 'Edit Template number 1' }).click()
  await expect(page.getByLabel('Template body')).toHaveValue('Body of template 1.')
})
