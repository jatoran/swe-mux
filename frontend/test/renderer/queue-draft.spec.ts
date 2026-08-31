import { expect, test } from 'playwright/test'

/**
 * Composing in the Queue tab, after the footer composer was replaced by a `+` that opens a
 * draft row.
 *
 * The report this came from is a sentence: "sometimes u might be typing and swipe away the
 * right sidebar without thinking and then u lose what u were typing". Every property below
 * is one half of making that impossible, and none of them is visible to a source-shape
 * assertion — they are about what a real debounce sends to a real endpoint, and about what
 * is still on screen once a field is open.
 *
 * The harness stores what it is sent, so "it saved" is asserted against the daemon's copy
 * rather than against the textarea.
 */

const writesOf = (page: import('playwright/test').Page) =>
  page.evaluate(() => (window as unknown as { __writes: () => string[] }).__writes())

/** The daemon's copy, projected to the two fields every assertion here is about. */
const rowsOf = (page: import('playwright/test').Page) =>
  page.evaluate(() => (window as unknown as { __rows: () => { body: string; state: string }[] })
    .__rows().map(row => ({ body: row.body, state: row.state })))

test('the tab opens with no field, and + is the way in', async ({ page }) => {
  await page.goto('/queue-draft-harness.html')
  await page.waitForSelector('.queue-pane')
  // Nothing to type into until something is asked for: the permanent composer is gone.
  await expect(page.locator('.queue-pane textarea')).toHaveCount(0)
  await expect(page.locator('.queue-new')).toBeVisible()
  await expect(page.locator('.queue-empty')).toContainText('New message')

  await page.locator('.queue-new').click()
  const field = page.locator('.queue-edit-field')
  await expect(field).toBeFocused()
  await expect(field).toHaveValue('')
  // An empty draft is local. Pressing `+` must not leave a row on the daemon.
  expect(await writesOf(page)).toEqual([])
})

test('a draft is at least four rows tall before anything is typed', async ({ page }) => {
  // Two rows taller than the composer it replaces: a queued prompt is a paragraph, and a
  // field that shows two lines of it makes every edit a scroll.
  await page.goto('/queue-draft-harness.html')
  await page.locator('.queue-new').click()
  const box = await page.locator('.queue-edit-field').boundingBox()
  expect(box!.height).toBeGreaterThanOrEqual(112)
})

test('typing saves with no Save button pressed', async ({ page }) => {
  await page.goto('/queue-draft-harness.html')
  await page.locator('.queue-new').click()
  await page.locator('.queue-edit-field').fill('check the build')

  await expect(page.locator('.queue-edit-status')).toHaveText('saved')
  expect(await writesOf(page)).toEqual(['create:check the build'])
  expect(await rowsOf(page)).toEqual([{ body: 'check the build', state: 'draft' }])

  // And the second body is a PATCH of the same item, not a second row.
  await page.locator('.queue-edit-field').fill('check the build twice')
  await expect(page.locator('.queue-edit-status')).toHaveText('saved')
  expect(await writesOf(page)).toEqual(['create:check the build', 'update:check the build twice'])
  expect((await rowsOf(page)).length).toBe(1)
})

test('the editor keeps its focus through the save that creates the row', async ({ page }) => {
  // The create makes the item appear in the list mid-sentence. Moving the field into that
  // new row would replace the node under the caret half a second after typing started.
  await page.goto('/queue-draft-harness.html')
  await page.locator('.queue-new').click()
  await page.locator('.queue-edit-field').pressSequentially('one', { delay: 20 })
  await expect(page.locator('.queue-edit-status')).toHaveText('saved')
  await page.locator('.queue-edit-field').pressSequentially(' two', { delay: 20 })
  await expect(page.locator('.queue-edit-field')).toBeFocused()
  await expect(page.locator('.queue-edit-field')).toHaveValue('one two')
  // One row, drawn once: the created item must not appear beside its own editor.
  await expect(page.locator('.queue-item')).toHaveCount(1)
})

test('swiping the drawer shut mid-sentence saves instead of discarding', async ({ page }) => {
  // The whole reason this replaced a Save button. The pane is unmounted with characters
  // the debounce has not fired for.
  await page.goto('/queue-draft-harness.html')
  await page.locator('.queue-new').click()
  await page.locator('.queue-edit-field').fill('half a thought')
  await expect(page.locator('.queue-edit-status')).toHaveText('saved')
  await page.locator('.queue-edit-field').fill('half a thought, finished')

  await page.evaluate(() => (window as unknown as { __unmount: () => void }).__unmount())
  await expect.poll(() => rowsOf(page)).toEqual([{ body: 'half a thought, finished', state: 'draft' }])

  await page.evaluate(() => (window as unknown as { __remount: () => void }).__remount())
  await expect(page.locator('.queue-item-body')).toHaveText('half a thought, finished')
})

test('the staging controls stay on screen while the field is open', async ({ page }) => {
  // Losing sight of the arm toggle and the delivery mode behind a Save/Cancel pair is what
  // made staging an armed mid-turn message a four-step act.
  await page.goto('/queue-draft-harness.html')
  await page.locator('.queue-new').click()
  const actions = page.locator('.queue-edit-actions')
  await expect(actions.locator('.queue-edit-interrupt')).toBeVisible()
  await expect(actions.getByRole('button', { name: 'Arm', exact: true })).toBeVisible()
  await expect(actions.getByRole('button', { name: 'Done' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save' })).toHaveCount(0)
})

test('save and arm is one press, and it arms the text on screen', async ({ page }) => {
  await page.goto('/queue-draft-harness.html')
  await page.locator('.queue-new').click()
  await page.locator('.queue-edit-field').fill('rerun the gate')
  await page.locator('.queue-edit-actions').getByRole('button', { name: 'Arm', exact: true }).click()

  await expect.poll(() => rowsOf(page)).toEqual([{ body: 'rerun the gate', state: 'armed' }])
  await expect(page.locator('.queue-edit-actions').getByRole('button', { name: 'Unarm' })).toBeVisible()
})

test('mid-turn asked for before the row exists rides the create', async ({ page }) => {
  await page.goto('/queue-draft-harness.html')
  await page.locator('.queue-new').click()
  await page.locator('.queue-edit-interrupt input').check()
  await page.locator('.queue-edit-field').fill('urgent')
  await expect(page.locator('.queue-edit-status')).toHaveText('saved')
  expect(await page.evaluate(
    () => (window as unknown as { __rows: () => { constraints: unknown }[] }).__rows()[0].constraints,
  )).toEqual({ delivery: 'now' })
})

test('delete is drawn once, whether or not the tray is open', async ({ page }) => {
  await page.goto('/queue-draft-harness.html')
  await page.locator('.queue-new').click()
  await page.locator('.queue-edit-field').fill('a message')
  await expect(page.locator('.queue-edit-status')).toHaveText('saved')
  await page.locator('.queue-edit-actions').getByRole('button', { name: 'Done' }).click()

  const row = page.locator('.queue-item').first()
  await expect(row.locator('.queue-item-delete')).toHaveCount(1)
  await row.getByRole('button', { name: 'More actions for this message' }).click()
  await expect(row.locator('.queue-item-more')).toBeVisible()
  // The tray used to carry a worded second copy of the same destructive act.
  await expect(row.locator('.queue-item-delete')).toHaveCount(1)
  await expect(row.getByRole('button', { name: 'Delete this message' })).toHaveCount(1)
})

test('edit and copy are marks on the row, not rows in the tray', async ({ page }) => {
  await page.goto('/queue-draft-harness.html')
  await page.locator('.queue-new').click()
  await page.locator('.queue-edit-field').fill('a message')
  await expect(page.locator('.queue-edit-status')).toHaveText('saved')
  await page.locator('.queue-edit-actions').getByRole('button', { name: 'Done' }).click()

  const row = page.locator('.queue-item').first()
  await expect(row.getByRole('button', { name: 'Edit this message' })).toBeVisible()
  await expect(row.getByRole('button', { name: 'Copy this message' })).toBeVisible()
  await row.getByRole('button', { name: 'More actions for this message' }).click()
  await expect(row.locator('.queue-item-more').getByRole('button', { name: 'Edit' })).toHaveCount(0)
  await expect(row.locator('.queue-item-more').getByRole('button', { name: 'Copy' })).toHaveCount(0)

  // And the pencil opens the editor on that row rather than a new draft.
  await row.getByRole('button', { name: 'Edit this message' }).click()
  await expect(page.locator('.queue-edit-field')).toHaveValue('a message')
  await expect(page.locator('.queue-edit-field')).toBeFocused()
})

test('editing an existing row PATCHes it rather than staging a second one', async ({ page }) => {
  await page.goto('/queue-draft-harness.html')
  await page.locator('.queue-new').click()
  await page.locator('.queue-edit-field').fill('first draft')
  await expect(page.locator('.queue-edit-status')).toHaveText('saved')
  await page.locator('.queue-edit-actions').getByRole('button', { name: 'Done' }).click()

  await page.locator('.queue-item').first().getByRole('button', { name: 'Edit this message' }).click()
  await page.locator('.queue-edit-field').fill('second draft')
  await expect(page.locator('.queue-edit-status')).toHaveText('saved')
  expect(await rowsOf(page)).toEqual([{ body: 'second draft', state: 'draft' }])
  expect(await writesOf(page)).toEqual(['create:first draft', 'update:second draft'])
})

test('sending from an open editor sends the text on screen', async ({ page }) => {
  // Two ways this goes wrong, and both are what autosave introduced: the send can carry a
  // body the debounce has not written yet, and it can quote the revision the *fetch*
  // reported rather than the one the save advanced - which the daemon refuses outright.
  await page.goto('/queue-draft-harness.html')
  await page.locator('.queue-new').click()
  await page.locator('.queue-edit-field').fill('first wording')
  await expect(page.locator('.queue-edit-status')).toHaveText('saved')

  // Edited and sent without pausing for the debounce.
  await page.locator('.queue-edit-field').fill('final wording')
  await page.locator('.queue-edit-actions').getByRole('button', { name: 'Send now' }).click()

  await expect.poll(() => rowsOf(page)).toEqual([{ body: 'final wording', state: 'sent' }])
  await expect(page.locator('.queue-pane-error')).toHaveCount(0)
  expect(await writesOf(page)).toEqual([
    'create:first wording',
    'update:final wording',
    'send:final wording',
  ])
})

test('an empty draft that is abandoned leaves nothing behind', async ({ page }) => {
  await page.goto('/queue-draft-harness.html')
  await page.locator('.queue-new').click()
  await page.locator('.queue-edit-actions').getByRole('button', { name: 'Discard this draft' }).click()
  await expect(page.locator('.queue-edit-field')).toHaveCount(0)
  await expect(page.locator('.queue-empty')).toBeVisible()
  expect(await writesOf(page)).toEqual([])
})

test('everything fits the drawer minimum without a horizontal scrollbar', async ({ page }) => {
  // 300px is the documented floor, and the row's action strip is what breaks it first.
  await page.goto('/queue-draft-harness.html')
  await page.locator('.queue-new').click()
  await page.locator('.queue-edit-field').fill('a message that has to fit')
  await expect(page.locator('.queue-edit-status')).toHaveText('saved')
  await page.locator('.queue-edit-actions').getByRole('button', { name: 'Done' }).click()

  const overflow = await page.evaluate(() => {
    const list = document.querySelector('.queue-list')!
    return { scroll: list.scrollWidth, client: list.clientWidth }
  })
  expect(overflow.scroll).toBeLessThanOrEqual(overflow.client)
})
