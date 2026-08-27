import { expect, test } from 'playwright/test'

// Landing, after it became part of the worktree map rather than a view beside it.
//
// Three claims are worth pinning in a real browser rather than in a unit test: that the
// act is reachable from the row without leaving it, that everything Project-wide about
// landing appears exactly **once** however many rows are open, and that a running gate
// reports something a reader can act on without any of it being invented.

test('a Map row offers the land of the branch it is showing, and nothing Project-wide', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 900 })
  await page.goto('/git-map-harness.html')
  const row = page.locator('.git-map-summary').filter({ hasText: 'sidebar-session-git-lines-fix' })
  await row.click()

  const landing = page.locator('.git-land-row-section')
  await expect(landing).toBeVisible()
  // The button names the branch, because the row it is on is the branch it lands.
  await expect(landing.getByRole('button', { name: /^Land / }))
    .toHaveText('Land sidebar-session-git-lines-fix')
  // Nothing here moves a trunk, and the copy says so where the button is.
  await expect(landing.locator('.git-land-launch small'))
    .toHaveText('fast-forward only · the daemon runs it, not this button')

  // The complaint this rework answers: the verification command's approval block used to
  // be repeated under every expansion. It is a property of the Project, not of the row.
  await expect(landing.locator('.git-land-gate')).toHaveCount(0)
  await expect(landing.locator('.git-land-authority')).toHaveCount(0)
  await expect(landing).not.toContainText('Verification')

  // Branch identity carries the row and the separate live-session action no longer
  // competes with it as another bordered badge.
  const rowChrome = await page.evaluate(() => {
    const branch = document.querySelector('.git-map-identity strong') as HTMLElement
    const live = document.querySelector('.git-map-live') as HTMLElement
    return {
      branchWeight: Number(getComputedStyle(branch).fontWeight),
      liveBorder: getComputedStyle(live).borderTopWidth,
    }
  })
  expect(rowChrome.branchWeight).toBeGreaterThanOrEqual(500)
  expect(rowChrome.liveBorder).toBe('0px')

  // Above the change groups, not below them (operator decision 2026-08-22). Those groups
  // are unbounded — this row alone carries 22 unstaged and 11 staged files — so Land, and
  // the live land state it reports, used to sit past the end of a scroller full of the
  // thing it acts on. Measured by document order rather than by pixels: the detail is a
  // scroller, so "further down the page" is the claim, not "lower on screen".
  //
  // Remove joined it above the groups 2026-08-27 (operator decision). It had been pinned
  // to the bottom so a destructive act was not the first thing under the cursor, but the
  // bottom of an unbounded list is not a fixed place — where Remove landed depended on
  // how many files this checkout happened to have, and the row's two acts ended up
  // separated by the thing they both act on. What keeps the destructive one deliberate is
  // its confirm-then-force step, which is unchanged.
  const order = await page.evaluate(() => {
    const detail = document.querySelector('.git-map-detail')!
    const land = detail.querySelector('.git-land-row-section')!
    const remove = detail.querySelector('.git-map-actions')!
    const groups = [...detail.querySelectorAll('.git-review-group')]
    return {
      groups: groups.length,
      // 4 === DOCUMENT_POSITION_FOLLOWING: every group comes after both acts.
      allAfterLand: groups.every(group => !!(land.compareDocumentPosition(group) & 4)),
      allAfterRemove: groups.every(group => !!(remove.compareDocumentPosition(group) & 4)),
      // Land first: it is the act a reader opens a branch row to reach.
      landBeforeRemove: !!(land.compareDocumentPosition(remove) & 4),
    }
  })
  expect(order.groups).toBeGreaterThan(0)
  expect(order.allAfterLand).toBe(true)
  expect(order.allAfterRemove).toBe(true)
  expect(order.landBeforeRemove).toBe(true)
})

test('the verification block exists once on the tab, in the strip above the map', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 900 })
  await page.goto('/git-map-harness.html')
  await page.locator('.git-map-summary').filter({ hasText: 'sidebar-session-git-lines-fix' }).click()

  // Closed by default: this gate is approved, so nothing on the tab is blocked and the
  // tab opens on a map rather than on a panel.
  const strip = page.locator('.git-landing')
  await expect(strip.locator('.git-landing-body')).toHaveCount(0)
  // The folded control is already the pipeline, so the resting state teaches the same
  // model as a running one instead of hiding it behind the disclosure.
  const pipeline = strip.locator('.git-land-pipeline')
  await expect(pipeline).toBeVisible()
  await expect(pipeline.locator('.git-land-pipeline-step strong'))
    .toHaveText(['Gate ready', 'Idle', 'Queue clear'])
  await expect(strip.locator('.git-landing-headline')).toHaveText('nothing queued')
  const cellWidths = await pipeline.locator('.git-land-pipeline-step').evaluateAll(
    cells => cells.map(cell => cell.getBoundingClientRect().width),
  )
  expect(Math.max(...cellWidths) - Math.min(...cellWidths)).toBeLessThan(2)

  await strip.locator('.git-landing-summary').click()
  // Exactly one, with a row still expanded below it.
  await expect(page.locator('.git-land-gate')).toHaveCount(1)
  await expect(page.locator('.git-land-authority')).toHaveCount(1)
  expect(await page.locator('.git-map-detail').count()).toBeGreaterThan(0)
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

  // The folded strip names the active branch and keeps all three pipeline cells visible.
  await expect(page.locator('.git-landing-headline'))
    .toHaveText('worktree-land-ui-rework · Verifying')
  await expect(page.locator('.git-land-pipeline .run small'))
    .toHaveText('worktree-land-ui-rework · step 3 of 7 · mypy · 3m 10s')

  // The map itself is now the live index. No expansion is required to find the branch
  // currently running or the one queued behind it.
  const runningRow = page.locator('.git-map-row').filter({ hasText: 'worktree-land-ui-rework' })
  await expect(runningRow).toHaveClass(/land-active/)
  await expect(runningRow).toHaveClass(/land-verifying/)
  await expect(runningRow.locator('.git-map-land-status strong')).toHaveText('Verifying')
  await expect(runningRow.locator('.git-map-land-status small'))
    .toHaveText('step 3 of 7 · mypy · 3m 10s')
  const queuedRow = page.locator('.git-map-row').filter({ hasText: 'worktree-beta' })
  await expect(queuedRow).toHaveClass(/land-queued/)
  await expect(queuedRow.locator('.git-map-land-status strong')).toHaveText('Queued')
  await expect(queuedRow.locator('.git-map-land-status small')).toHaveText('#2 in queue')

  // The chosen concept gives active rows a full-height state rail and a dedicated third
  // line. These are geometry claims, not only class names.
  const geometry = await page.evaluate(() => {
    const active = document.querySelector('.git-map-row.land-verifying') as HTMLElement
    const idle = [...document.querySelectorAll('.git-map-row')]
      .find(row => row.textContent?.includes('master')) as HTMLElement
    const status = active.querySelector('.git-map-land-status') as HTMLElement
    const activeStyle = getComputedStyle(active)
    return {
      activeHeight: active.getBoundingClientRect().height,
      idleHeight: idle.getBoundingClientRect().height,
      statusTop: status.getBoundingClientRect().top,
      identityBottom: (active.querySelector('.git-map-identity') as HTMLElement).getBoundingClientRect().bottom,
      boxShadow: activeStyle.boxShadow,
    }
  })
  expect(geometry.activeHeight).toBeGreaterThan(geometry.idleHeight)
  expect(geometry.statusTop).toBeGreaterThanOrEqual(geometry.identityBottom)
  expect(geometry.boxShadow).not.toBe('none')

  // And the branch's own row says it too, because that is the row being landed.
  await page.locator('.git-map-summary').filter({ hasText: 'worktree-land-ui-rework' }).click()
  await expect(page.locator('.git-land-row-section .git-land-progress-detail'))
    .toHaveText('step 3 of 7 · mypy · 3m 10s')

  const text = await page.locator('.git-tab').innerText()
  expect(text).not.toContain('%')
  // And no progress bar smuggled in as an element either.
  await expect(page.locator('.git-tab progress')).toHaveCount(0)
})

test('the queue reads in the order the pipeline will reach it', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/git-land-harness.html')
  // The operation is visible before expansion and remains the one copy after it.
  const pipeline = page.locator('.git-land-pipeline')
  await expect(pipeline.locator('.git-land-pipeline-step strong'))
    .toHaveText(['Gate ready', 'Verifying', '1 waiting'])
  await expect(pipeline.locator('.git-land-pipeline-step small'))
    .toHaveText([
      '.worktree-verify',
      'worktree-land-ui-rework · step 3 of 7 · mypy · 3m 10s',
      'next worktree-beta',
    ])
  await expect(pipeline).toHaveCount(1)
  await page.locator('.git-landing-summary').click()
  await expect(pipeline).toHaveCount(1)
  const branches = page.locator('.git-land-list .git-land-row strong')
  await expect(branches).toHaveText(['worktree-land-ui-rework', 'worktree-beta'])
  await expect(page.locator('.git-land-list .git-land-position')).toHaveText(['1', '2'])
  // A finished land is history and is folded away rather than sitting in the queue.
  await expect(page.locator('.git-land-history summary')).toHaveText('2 finished')
})

test('a verify-only row is named as one and never reads as a landing', async ({ page }) => {
  // It runs every state a land does except the last, in the same words, so without a
  // label it narrates as a landing right up until it stops one step early.
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/git-land-harness.html')
  await page.locator('.git-landing-summary').click()
  await page.locator('.git-land-history summary').click()

  const row = page.locator('.git-land-row').filter({ hasText: 'worktree-delta' })
  await expect(row.locator('.git-land-kind-note')).toHaveText('verify only')
  await expect(row.locator('.git-land-state')).toHaveText('Verified')
  // Nothing moved, so there is no before/after pair to draw.
  await expect(row.locator('.git-land-oid')).toHaveCount(0)

  // And an ordinary land carries no such label, for the same reason a full gate carries
  // no gate note: it is what a row here is unless something says otherwise.
  const landed = page.locator('.git-land-row').filter({ hasText: 'worktree-gamma' })
  await expect(landed.locator('.git-land-kind-note')).toHaveCount(0)
})

test('the verification command shows what resolved, and edits without approving', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/git-land-harness.html')
  await page.locator('.git-landing-summary').click()

  const gate = page.locator('.git-land-gate')
  // Approved configuration stays secondary to the pipeline until requested.
  await expect(gate).not.toHaveAttribute('open', '')
  expect(await gate.evaluate(element => getComputedStyle(element).borderTopWidth)).toBe('0px')
  await gate.locator('.git-land-gate-summary').click()
  await expect(gate).toContainText('Verification approved')
  // Which of the two mechanisms is in force, and that it answers for every worktree -
  // the fact that had no home on screen, and the reason it is not drawn per row.
  await expect(gate).toContainText('Resolved from the executable .worktree-verify, for every worktree of this Project')
  // A statement about a past run, phrased as one.
  await expect(gate.locator('.git-land-plan')).toContainText('Last passing run of these exact bytes: 7 steps in 3m 32s')

  await gate.getByRole('button', { name: 'Use a different command…' }).click()
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
  // And the pipeline above it agrees without flattening back into prose.
  await expect(page.locator('.git-land-pipeline .gate strong')).toHaveText('Needs approval')
})

test('the setup prompt is offered beside the gate, and hands over no authority', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/git-land-harness.html')
  await page.locator('.git-landing-summary').click()
  await page.locator('.git-land-gate-summary').click()

  // Collapsed by default: it answers a question about *another* repository, so it must
  // not push this Project's own gate down the pane.
  const setup = page.locator('.git-land-setup-prompt')
  await expect(setup.locator('pre')).toBeHidden()
  await setup.locator('summary').click()

  // Shown as well as copyable. It is an instruction being handed to an agent that will
  // write somebody's gate, so a payload nobody can read before pressing is the wrong shape.
  await expect(setup.getByRole('button', { name: 'Copy setup prompt' })).toBeVisible()
  const prompt = setup.locator('pre')
  await expect(prompt).toContainText('exit code is the only verdict')
  await expect(prompt).toContainText('parallel-safe')
  await expect(prompt).toContainText('[worktree] verify_command')

  // The whole reason this button is not an authority leak: the prompt ends by telling the
  // agent it cannot approve what it wrote, and the approve control stays here.
  await expect(prompt).toContainText('You cannot approve this')
  await expect(prompt).toContainText('A human presses approve.')
})

test('a blocked gate opens the strip by itself, and a deliberate collapse still says so', async ({ page }) => {
  // A surface that cannot work must not render as merely quiet (`setting-links.md`), so
  // an unapproved gate opens the strip on arrival - the approval act is inside it.
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/git-land-harness.html?blocked=1')
  await expect(page.locator('.git-landing-body')).toHaveCount(1)
  await expect(page.locator('.git-landing-summary')).toHaveAttribute('aria-expanded', 'true')
  await expect(page.locator('.git-land-gate')).toContainText('Verification not approved')
  await expect(page.locator('.git-land-gate')).toHaveAttribute('open', '')

  // A collapse the reader asked for is honoured — nothing re-opens under them. What
  // keeps that safe is that the folded pipeline goes on stating the block, so the surface
  // is never merely quiet even while it is closed.
  await page.locator('.git-landing-summary').click()
  await expect(page.locator('.git-landing-body')).toHaveCount(0)
  await expect(page.locator('.git-land-pipeline .gate strong')).toHaveText('Needs approval')
  await expect(page.locator('.git-land-pipeline .gate')).toHaveClass(/warn/)
})

test('a repository that never set up verification opens on its map, not on the strip', async ({ page }) => {
  // Operator decision 2026-08-22. This used to open too, on the reading that nothing can
  // land here so the surface must announce itself. But that fires on the resting state of
  // every repository that never opted into the land queue, and unfolding a landing panel
  // over the map on each of them reports an emergency that does not exist. Nothing is
  // stuck: the queue was never set up. The folded gate cell still says so in warn tone.
  await page.setViewportSize({ width: 420, height: 900 })
  await page.goto('/git-land-harness.html?unconfigured=1')
  await expect(page.locator('.git-landing-summary')).toBeVisible()
  await expect(page.locator('.git-landing-summary')).toHaveAttribute('aria-expanded', 'false')
  await expect(page.locator('.git-landing-body')).toHaveCount(0)
  await expect(page.locator('.git-land-pipeline .gate strong')).toHaveText('Not configured')
  await expect(page.locator('.git-land-pipeline .gate')).toHaveClass(/warn/)

  // Folded, never removed: the setup is one click behind the same summary line, which is
  // what keeps "stays quiet" from becoming "cannot be found".
  await page.locator('.git-landing-summary').click()
  await expect(page.locator('.git-landing-body')).toHaveCount(1)
  await expect(page.locator('.git-land-setup-prompt')).toHaveCount(1)
})
