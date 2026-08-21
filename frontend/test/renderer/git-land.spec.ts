import { expect, test } from 'playwright/test'

// Landing, after it moved onto the row it acts on.
//
// Two claims are worth pinning in a real browser rather than in a unit test: that the
// act is reachable from the Map row without leaving it, and that a running gate reports
// something a reader can act on without any of it being invented.

test('a Map row offers the land of the branch it is showing', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 900 })
  await page.goto('/git-map-harness.html')
  const row = page.locator('.git-map-summary').filter({ hasText: 'sidebar-session-git-lines-fix' })
  await row.click()

  const landing = page.locator('.git-land-row-section')
  await expect(landing).toBeVisible()
  await expect(landing.locator('h4')).toContainText('LANDING')
  // The button names the branch, because the row it is on is the branch it lands.
  await expect(landing.getByRole('button', { name: /^Land / }))
    .toHaveText('Land sidebar-session-git-lines-fix')
  // An approved gate is one line rather than a block: it is not blocking anything.
  await expect(landing.locator('.git-land-gate')).toHaveText(/Verification approved/)
  await expect(landing.locator('.git-land-gate')).toHaveClass(/ok/)
  // Nothing here moves a trunk, and the copy says so where the button is.
  await expect(landing.locator('.git-land-launch small'))
    .toHaveText('fast-forward only · the daemon runs it, not this button')
})

test('the main tree is never offered as something to land', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 900 })
  await page.goto('/git-map-harness.html')
  await page.locator('.git-map-summary').filter({ hasText: 'main' }).first().click()
  const detail = page.locator('.git-map-row').filter({ hasText: 'main tree' })
  await expect(detail.locator('.git-land-row-section')).toHaveCount(0)
})

test('a running gate reports the step it is on, and never a percentage', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/git-land-harness.html')

  const running = page.locator('.git-land-row').filter({ hasText: 'worktree-land-ui-rework' })
  await expect(running.locator('.git-land-state')).toHaveText('Verifying')
  // A step number, its name, and elapsed time. The total is present only because a
  // byte-identical run measured it.
  await expect(running.locator('.git-land-progress-detail'))
    .toHaveText('step 3 of 7 · mypy · 3m 10s')

  const text = await page.locator('.git-land').innerText()
  expect(text).not.toContain('%')
  // And no progress bar smuggled in as an element either.
  await expect(page.locator('.git-land progress')).toHaveCount(0)
})

test('the queue reads in the order the pipeline will reach it', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/git-land-harness.html')
  const branches = page.locator('.git-land-list .git-land-row strong')
  await expect(branches).toHaveText(['worktree-land-ui-rework', 'worktree-beta'])
  await expect(page.locator('.git-land-list .git-land-position')).toHaveText(['1', '2'])
  // A finished land is history and is folded away rather than sitting in the queue.
  await expect(page.locator('.git-land-history summary')).toHaveText('1 finished')
})

test('the verification command shows what resolved, and edits without approving', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/git-land-harness.html')

  const gate = page.locator('.git-land > .git-land-gate')
  await expect(gate).toContainText('Verification approved')
  // Which of the two mechanisms is in force - the fact that had no home on screen.
  await expect(gate).toContainText('Resolved from the executable .worktree-verify')
  // A statement about a past run, phrased as one.
  await expect(gate.locator('.git-land-plan')).toContainText('Last passing run of these exact bytes: 7 steps in 3m 32s')

  await gate.getByRole('button', { name: 'Set a command…' }).click()
  await gate.locator('.git-land-command-editor input').fill('./verify.sh')
  await gate.getByRole('button', { name: 'Save', exact: true }).click()

  // One key, and the revision it was shown, so a concurrent edit loses rather than
  // being clobbered.
  const writes = await page.evaluate(
    () => (globalThis as unknown as { __writes: { body: Record<string, unknown> }[] }).__writes,
  )
  expect(writes).toHaveLength(1)
  expect(writes[0].body.command).toBe('./verify.sh')
  expect(writes[0].body.revision).toBe('r1')

  // Editing is a proposal; the gate comes back unapproved and says what changed.
  await expect(gate).toContainText('Verification not approved')
  await expect(gate).toContainText('It changed since it was approved')
  await expect(gate).toHaveClass(/warn/)
})
