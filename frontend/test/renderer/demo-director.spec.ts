import { expect, test } from 'playwright/test'

/**
 * The demo's scenario director, end to end against the real app.
 *
 * The unit suite covers the deterministic sources and the control plane's reducers, and
 * both of those are the *inputs* to a run. What has to be checked in a browser is the run
 * itself: that a scenario finishes rather than hanging on a control that moved, that it
 * moves the control plane it claims to, that a visitor's touch stops it dead, and that
 * two runs at the same seed produce the same fixture - which is the entire claim the
 * capture rig is built on.
 *
 * The dev server serves the demo entry at `/demo.html` (the committed bundle's base is
 * `/demo/`, which is why the built artifact is verified separately, by `capture-demo.mjs`).
 */

/** The director's start delay, plus room for the app's first fetch on a loaded runner. */
const START_TIMEOUT = 30_000

type DirectorHandle = {
  snapshot: () => {
    running: boolean
    index: number
    total: number
    scenarioId: string
    say: string
    spotlight: string[] | null
  }
  fingerprint: () => string
  scenarios: () => Array<{ id: string; label: string }>
  stop: (reason: string) => void
}

declare global {
  interface Window { __demoDirector?: DirectorHandle }
}

const open = async (page: import('playwright/test').Page, query: string): Promise<string[]> => {
  const failures: string[] = []
  page.on('pageerror', error => failures.push(String(error)))
  await page.goto(`/demo.html?${query}`)
  await page.waitForSelector('.workspace', { timeout: START_TIMEOUT })
  return failures
}

const running = (page: import('playwright/test').Page): Promise<boolean> =>
  page.evaluate(() => window.__demoDirector?.snapshot().running === true)

/**
 * Wait for a run to start and then to end.
 *
 * Both halves, in that order, and the first one is the trap: the director waits out the
 * app's first paint before it starts, so "wait until it is not running" is satisfied
 * instantly by a scenario that has not begun - which passes, in under a second, having
 * asserted nothing.
 */
const playThrough = async (page: import('playwright/test').Page): Promise<void> => {
  await page.waitForFunction(() => window.__demoDirector?.snapshot().running === true, null, { timeout: START_TIMEOUT })
  await page.waitForFunction(() => window.__demoDirector?.snapshot().running === false, null, { timeout: 90_000 })
}

test('the scenario menu leads with the walkthrough', async ({ page }) => {
  // Nothing plays by itself here: no `scenario` parameter, and a visitor who has already
  // seen the tour is left alone until they choose something.
  await open(page, 'deterministic=1')
  await page.evaluate(() => localStorage.setItem('swemux-demo-coach-v1', 'done'))
  const menu = await page.evaluate(() => window.__demoDirector!.scenarios())
  expect(menu[0].id).toBe('tour')
  expect(menu.map(item => item.id)).toEqual([
    'tour', 'queue', 'orchestrate', 'preview', 'land', 'palette', 'keymap', 'voice',
  ])
  // Every entry has a label a dropdown can show; an id is not a label. The wording itself
  // is checked in `tests/test_demo_scenario_menu.py`, which also holds the landing page's
  // hand-written copy of this list to the same one.
  for (const entry of menu) expect(entry.label.length).toBeGreaterThan(3)
})

test('the walkthrough labels the parts of a session row', async ({ page }) => {
  // The one beat that names chrome rather than pointing at it, and the reason the whole
  // callout layer exists. Asserted end to end because every interesting thing about it is
  // geometry: the labels are placed against elements this test has to have really drawn,
  // and a selector that stops matching is exactly the failure that would otherwise ship
  // as a beat with nothing on it.
  const failures = await open(page, 'deterministic=1')
  // Cut the opening card's wait short rather than sitting through it. Next is the one
  // control that does not count as a handover, so this does not stop the run.
  await page.waitForSelector('.demo-director-next', { timeout: START_TIMEOUT })
  await page.click('.demo-director-next')
  const chips = page.locator('.demo-show-chip:not(.measuring)')
  await expect(chips).toHaveCount(1, { timeout: 15_000 })

  // One at a time is the claim, and it is the whole point of the beat: this used to draw
  // all six at once, and a visitor reading any one label had to work out which of six
  // leader lines belonged to it before the label meant anything. So the assertion is not
  // "six labels appear" but "never more than one, and eventually all of them".
  //
  // Sampled for as long as *this beat* lasts rather than for a fixed count. The tour used
  // to gate here, so the beat went on until the visitor pressed something and any number
  // of samples was safe; it advances on its own clock now, and a sample taken one beat
  // later would read the next beat's empty overlay as a failure of this one.
  const beat = await page.evaluate(() => window.__demoDirector!.snapshot().index)
  const seen = new Set<string>()
  // Sampled inside the loop, because the overlay is gone by the time the beat is: the
  // placement used to be read after it and would now measure an empty frame.
  const placements: Array<{ gap: number; inside: boolean }> = []
  for (let sample = 0; sample < 24; sample += 1) {
    const drawn = await page.evaluate(() => {
      const chip = document.querySelector('.demo-show-chip:not(.measuring)')?.getBoundingClientRect()
      const mark = document.querySelector('.demo-show-mark')?.getBoundingClientRect()
      return {
        index: window.__demoDirector!.snapshot().index,
        chips: [...document.querySelectorAll('.demo-show-chip:not(.measuring)')]
          .map(item => item.textContent || ''),
        wires: document.querySelectorAll('.demo-show-wires path').length,
        marks: document.querySelectorAll('.demo-show-mark').length,
        // Placed beside its own target rather than parked somewhere generic. Nearness is
        // measured on whichever axis the gutter did not use: a side gutter shares the
        // target's line, a gutter above or below shares its column.
        place: chip && mark
          ? {
            gap: Math.min(Math.abs(chip.top - mark.top), Math.abs(chip.left - mark.left)),
            inside: chip.left >= 0 && chip.right <= innerWidth
              && chip.top >= 0 && chip.bottom <= innerHeight,
          }
          : null,
      }
    })
    if (drawn.index !== beat) break
    expect(drawn.chips.length).toBe(1)
    seen.add(drawn.chips[0])
    // A label without its leader line and its mark is a floating word.
    expect(drawn.wires).toBe(1)
    expect(drawn.marks).toBe(1)
    if (drawn.place) placements.push(drawn.place)
    await page.waitForTimeout(700)
  }
  // The walk really advances, and it covers the row rather than sticking on one part.
  expect(seen.size).toBeGreaterThan(4)

  expect(placements.length).toBeGreaterThan(4)
  for (const place of placements) {
    expect(place.inside).toBe(true)
    expect(place.gap).toBeLessThan(40)
  }
  expect(failures).toEqual([])
})

test('a scripted scenario plays to its last beat and moves the control plane', async ({ page }) => {
  const failures = await open(page, 'deterministic=1&scenario=queue')
  await page.waitForFunction(() => window.__demoDirector?.snapshot().running === true, null, { timeout: START_TIMEOUT })

  // The caption card is the only chrome the director draws that a visitor reads, and it
  // has to name the run rather than appear as an unexplained box over the app.
  await expect(page.locator('.demo-director-card')).toBeVisible()

  await page.waitForFunction(() => window.__demoDirector?.snapshot().running === false, null, { timeout: 90_000 })
  const view = await page.evaluate(() => window.__demoDirector!.snapshot())
  expect(view.running).toBe(false)
  expect(view.index).toBe(0)

  // What the scenario claims happened: a prompt was queued and delivered, auto-delivery
  // was turned on for the one session, and a notification fired. Read off the store,
  // because "the caption said so" is not evidence.
  const fingerprint = JSON.parse(await page.evaluate(() => window.__demoDirector!.fingerprint()))
  expect(fingerprint.deterministic).toBe(true)
  expect(fingerprint.autoDelivery).toEqual(['s-working'])
  expect(fingerprint.queue).toHaveLength(1)
  expect(fingerprint.queue[0][1]).toBe('sent')
  expect(fingerprint.notifications).toHaveLength(1)
  expect(fingerprint.notifications[0][1]).toBe('queue_delivery')

  // A scenario that wedged the app would still have "finished"; nothing may have thrown.
  expect(failures).toEqual([])
})

test('two runs at one seed produce the same fixture', async ({ page }) => {
  // Two full plays of a twenty-five second scenario, plus two cold boots of the app. The
  // config's 60s default is a per-test budget written for harness pages that mount one
  // component; this one drives the whole demo twice on purpose, because a determinism
  // claim checked against a partial run is a claim about the first few beats.
  test.setTimeout(180_000)
  // The capture rig's whole premise. Ids are counter-based and the clock is rebased onto a
  // fixed epoch, so a spawned session, a minted queue id and every rendered timestamp must
  // be identical - otherwise a still is a picture of one particular Tuesday.
  await open(page, 'deterministic=1&scenario=orchestrate')
  await playThrough(page)
  const first = await page.evaluate(() => window.__demoDirector!.fingerprint())

  await open(page, 'deterministic=1&scenario=orchestrate')
  await playThrough(page)
  const second = await page.evaluate(() => window.__demoDirector!.fingerprint())

  expect(second).toBe(first)
  // And it is not vacuously equal: the run must actually have spawned the two sessions.
  const parsed = JSON.parse(first)
  expect(parsed.sessions).toHaveLength(10)
  expect(parsed.spawnRequests.map((row: unknown[]) => row[1])).toEqual(['approved', 'approved'])
})

test('a real press stops a playing scenario at once, and does not restart it', async ({ page }) => {
  await open(page, 'deterministic=1&scenario=land')
  await page.waitForFunction(() => window.__demoDirector?.snapshot().running === true, null, { timeout: START_TIMEOUT })

  // A trusted pointerdown, which is the discriminator: the director's own presses are
  // `element.click()` and produce no pointerdown at all, so a scenario cannot abort itself.
  const stoppedAt = await page.evaluate(() => window.__demoDirector!.snapshot().index)
  await page.mouse.click(400, 500)
  await expect.poll(() => running(page), { timeout: 5_000 }).toBe(false)

  // "Leaves the state where it got to" is the contract, so nothing is rolled back - and
  // nothing starts again on its own either.
  await page.waitForTimeout(2_000)
  expect(await running(page)).toBe(false)
  await expect(page.locator('.demo-director-card')).toHaveCount(0)

  // What it leaves instead of nothing: an offer. The first autoplaying cut stopped with
  // no chrome at all, which read as a dead end even though the menu could replay it.
  const chip = page.locator('.demo-director-resume')
  await expect(chip).toBeVisible()
  await chip.getByRole('button', { name: 'resume' }).click()
  await expect.poll(() => running(page), { timeout: 10_000 }).toBe(true)
  // Resumed on the beat it stopped on rather than from the top.
  expect(await page.evaluate(() => window.__demoDirector!.snapshot().index)).toBe(stoppedAt)
})

test('a press on the chrome a beat is pointing at is joining in, not leaving', async ({ page }) => {
  // The rule the first autoplaying cut got wrong. The tour spends two minutes saying
  // "here is a thing you can press", and ending on the press it invited is the one thing
  // about driving itself that read as fragile.
  await page.addInitScript(() => { localStorage.removeItem('swemux-demo-coach-v1') })
  await open(page, 'deterministic=1&scenario=tour')
  // The fleet stop, whose ring is the sidebar the session rows live in.
  await page.waitForFunction(
    () => window.__demoDirector?.snapshot().spotlight?.[0] === '.sidebar',
    null, { timeout: START_TIMEOUT },
  )
  await page.locator('[data-sidebar-session-id="s-shell"]').click()
  await page.waitForTimeout(1_500)
  expect(await running(page)).toBe(true)

  // A press outside it still hands over, and a keypress hands over wherever it lands -
  // the composer sits inside the pane the talking beats point at, and somebody typing has
  // unambiguously taken the demo.
  await page.mouse.click(900, 300)
  await expect.poll(() => running(page), { timeout: 5_000 }).toBe(false)
  await page.locator('.demo-director-resume').getByRole('button', { name: 'resume' }).click()
  await expect.poll(() => running(page), { timeout: 10_000 }).toBe(true)
  await page.keyboard.press('a')
  await expect.poll(() => running(page), { timeout: 5_000 }).toBe(false)
})

test('the walkthrough drives itself, and its card offers a way out', async ({ page }) => {
  await page.addInitScript(() => { localStorage.removeItem('swemux-demo-coach-v1') })
  await open(page, 'deterministic=1&scenario=tour')
  await page.waitForFunction(() => window.__demoDirector?.snapshot().running === true, null, { timeout: START_TIMEOUT })

  const card = page.locator('.demo-director-card')
  await expect(card).toBeVisible()
  // The whole point of the rewrite: nobody is asked for anything, so the tour advances on
  // its own clock. It used to gate here and this assertion was its mirror image - the
  // index had to *stay* put, because the beat was waiting for a press that a visitor who
  // had not decided to care yet was never going to make.
  const before = await page.evaluate(() => window.__demoDirector!.snapshot().index)
  await expect
    .poll(() => page.evaluate(() => window.__demoDirector!.snapshot().index), { timeout: 10_000 })
    .toBeGreaterThan(before)

  // Next is an offer rather than a demand: it cuts the current beat's wait short. A real
  // click anywhere else would stop the run, so this also pins the distinction - the card
  // is the director's own chrome and pressing it is not a handover.
  const stepped = await page.evaluate(() => window.__demoDirector!.snapshot().index)
  await card.getByRole('button', { name: 'Next' }).click()
  await expect.poll(() => page.evaluate(() => window.__demoDirector!.snapshot().index))
    .toBeGreaterThan(stepped)
  expect(await running(page)).toBe(true)

  // The explicit stop affordance, which every run carries.
  await card.getByRole('button', { name: 'Stop the demo walkthrough' }).click()
  await expect.poll(() => running(page)).toBe(false)
})
